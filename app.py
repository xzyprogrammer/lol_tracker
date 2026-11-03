"""
LoL Spectator HUD Summoner & Ultimate Tracker + Auto-Accept Queue
1) Cuando no hay partida (o al terminar un game), vuelve automáticamente a una píldora mínima
   "🟠 ESPERANDO PARTIDA" con Auto-Accept de Cola (reposicionando el OCR al centro + LCU API).
2) Al iniciar la partida, carga automáticamente a los 5 campeones rivales en un HUD Vertical
   minimalista estilo Modo Espectador a la izquierda de la pantalla (< 25% vh de alto).
3) Muestra Retrato+Nivel, Summoner 1, Summoner 2 y Ultimate (R) con contadores en tiempo real
   dibujados directamente sobre cada icono gastado.
"""

from __future__ import annotations
import json
import os
import queue
import threading
import time
import tkinter as tk
import customtkinter as ctk
from dataclasses import dataclass, field
from typing import Optional
from PIL import ImageTk

from summoner_data import (
    SummonerSpellInfo,
    calculate_cooldown,
    format_mmss,
    resolve_spell,
)
from live_client_api import (
    LiveMatchSnapshot,
    fetch_live_match_snapshot,
    fetch_spectator_v5_cosmic_insight,
)
from chat_ocr import AutoChatScanner
from ddragon_assets import AssetManager
from updater import CURRENT_VERSION, check_for_updates, UpdateDialog

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".lol_scrim_tracker_config.json")


def load_config() -> dict:
    default_cfg = {
        "riot_api_key": "",
        "platform": "la2",
        "chat_region": [0.0, 0.52, 0.40, 0.94],
        "hud_x": 6,
        "hud_y": 240,
        "auto_accept_queue": True,
    }
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_cfg.update(data)
    except Exception:
        pass
    return default_cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


@dataclass
class ActiveTimer:
    started_at_game_time: float
    expires_at_game_time: float
    total_cooldown: int

    def remaining(self, current_game_time: float) -> float:
        return max(0.0, self.expires_at_game_time - current_game_time)

    def is_expired(self, current_game_time: float) -> bool:
        return current_game_time >= self.expires_at_game_time


@dataclass
class EnemySlotState:
    slot_index: int
    role_label: str
    champion_name: str
    riot_id: str
    level: int
    spell1: SummonerSpellInfo
    spell2: SummonerSpellInfo
    ability_haste: int = 0
    has_cosmic_insight: bool = False
    has_lucidity_boots: bool = False
    has_crimson_lucidity: bool = False
    manual_cosmic_override: Optional[bool] = None
    manual_boots_override: Optional[bool] = None
    # Guardamos los timers por clave de hechizo ('Flash', 'Teleport', 'Ignite', etc.)
    # para soportar 'Libro de Hechizos Abierto' (Unsealed Spellbook): si cambia TP por Ignite,
    # Ignite aparece LISTO y cuando vuelve a TP conserva el tiempo restante real del TP.
    spell_timers: dict[str, ActiveTimer] = field(default_factory=dict)
    timer_ult: Optional[ActiveTimer] = None

    @property
    def timer1(self) -> Optional[ActiveTimer]:
        return self.spell_timers.get(self.spell1.key)

    @timer1.setter
    def timer1(self, val: Optional[ActiveTimer]) -> None:
        if val is None:
            self.spell_timers.pop(self.spell1.key, None)
        else:
            self.spell_timers[self.spell1.key] = val

    @property
    def timer2(self) -> Optional[ActiveTimer]:
        return self.spell_timers.get(self.spell2.key)

    @timer2.setter
    def timer2(self, val: Optional[ActiveTimer]) -> None:
        if val is None:
            self.spell_timers.pop(self.spell2.key, None)
        else:
            self.spell_timers[self.spell2.key] = val


class SpectatorEnemyRow:
    """
    Fila ultracompacta estilo Espectador (40px de alto):
    [Retrato 34x34 + Nivel] [Summoner 1 (28x28)] [Summoner 2 (28x28)] [Ultimate R (28x28)] [C/J]
    """

    def __init__(self, parent: ctk.CTkFrame, slot_index: int, app: "SpectatorHudApp") -> None:
        self.slot_index = slot_index
        self.app = app
        self._tk_champ: Optional[ImageTk.PhotoImage] = None
        self._tk_sp1: Optional[ImageTk.PhotoImage] = None
        self._tk_sp2: Optional[ImageTk.PhotoImage] = None
        self._tk_ult: Optional[ImageTk.PhotoImage] = None
        self._last_sig: tuple = ()

        self.frame = ctk.CTkFrame(
            parent,
            corner_radius=5,
            fg_color="#0F131C",
            border_width=1,
            border_color="#232B3A",
        )

        inner = ctk.CTkFrame(self.frame, fg_color="transparent")
        inner.pack(padx=3, pady=2)

        # 1. Retrato del Campeón con badge de Nivel (34x34)
        self.champ_lbl = tk.Label(
            inner, bg="#0F131C", bd=0, highlightthickness=0, width=34, height=34
        )
        self.champ_lbl.pack(side="left", padx=(0, 3))

        # 2. Icono Summoner Spell 1 (Flash - 28x28)
        self.sp1_lbl = tk.Label(
            inner, bg="#0F131C", bd=0, highlightthickness=0, cursor="hand2", width=28, height=28
        )
        self.sp1_lbl.pack(side="left", padx=1)
        self.sp1_lbl.bind("<Button-1>", lambda _e: self.app.trigger_or_adjust(self.slot_index, "sp1"))
        self.sp1_lbl.bind("<Button-3>", lambda _e: self.app.clear_timer(self.slot_index, "sp1"))

        # 3. Icono Summoner Spell 2 (TP / Ignite / Smite - 28x28)
        self.sp2_lbl = tk.Label(
            inner, bg="#0F131C", bd=0, highlightthickness=0, cursor="hand2", width=28, height=28
        )
        self.sp2_lbl.pack(side="left", padx=1)
        self.sp2_lbl.bind("<Button-1>", lambda _e: self.app.trigger_or_adjust(self.slot_index, "sp2"))
        self.sp2_lbl.bind("<Button-3>", lambda _e: self.app.clear_timer(self.slot_index, "sp2"))

        # 4. Icono Ultimate R (28x28)
        self.ult_lbl = tk.Label(
            inner, bg="#0F131C", bd=0, highlightthickness=0, cursor="hand2", width=28, height=28
        )
        self.ult_lbl.pack(side="left", padx=(2, 2))
        self.ult_lbl.bind("<Button-1>", lambda _e: self.app.trigger_or_adjust(self.slot_index, "ult"))
        self.ult_lbl.bind("<Button-3>", lambda _e: self.app.clear_timer(self.slot_index, "ult"))

        # 5. Mini-indicadores verticales C (Cósmica) y J (Botas Jonias)
        dots_col = ctk.CTkFrame(inner, fg_color="transparent")
        dots_col.pack(side="left", padx=(1, 0))

        self.cosmic_dot = ctk.CTkButton(
            dots_col,
            text="C",
            width=15,
            height=14,
            corner_radius=3,
            font=ctk.CTkFont(size=8, weight="bold"),
            fg_color="#1A202C",
            hover_color="#553C9A",
            text_color="#718096",
            command=lambda: self.app.toggle_cosmic(self.slot_index),
        )
        self.cosmic_dot.pack(pady=(0, 2))

        self.boots_dot = ctk.CTkButton(
            dots_col,
            text="J",
            width=15,
            height=14,
            corner_radius=3,
            font=ctk.CTkFont(size=8, weight="bold"),
            fg_color="#1A202C",
            hover_color="#2B6CB0",
            text_color="#718096",
            command=lambda: self.app.toggle_boots(self.slot_index),
        )
        self.boots_dot.pack()

    def update_row(self, slot: EnemySlotState, game_time: float, assets: AssetManager) -> None:
        rem1 = slot.timer1.remaining(game_time) if slot.timer1 else 0.0
        rem2 = slot.timer2.remaining(game_time) if slot.timer2 else 0.0
        rem_u = slot.timer_ult.remaining(game_time) if slot.timer_ult else 0.0

        cd1 = slot.timer1.total_cooldown if slot.timer1 else slot.spell1.base_cooldown
        cd2 = slot.timer2.total_cooldown if slot.timer2 else slot.spell2.base_cooldown
        ult_info = assets.get_ultimate_info(slot.champion_name)
        cd_u = (
            slot.timer_ult.total_cooldown
            if slot.timer_ult
            else ult_info.cooldown_for_level(slot.level, slot.ability_haste)
        )

        sig = (
            slot.champion_name,
            slot.level,
            slot.ability_haste,
            slot.spell1.key,
            slot.spell2.key,
            ult_info.image_full,
            int(round(rem1)),
            int(round(rem2)),
            int(round(rem_u)),
            slot.has_cosmic_insight,
            slot.has_lucidity_boots or slot.has_crimson_lucidity,
            assets._init_done,
        )
        if sig == self._last_sig:
            return
        self._last_sig = sig

        champ_img = assets.get_champion_spectator_portrait(
            slot.champion_name, level=slot.level, size=(34, 34)
        )
        self._tk_champ = ImageTk.PhotoImage(champ_img)
        self.champ_lbl.configure(image=self._tk_champ)

        sp1_img = assets.render_spell_icon(slot.spell1.key, rem1, cd1, size=(28, 28))
        self._tk_sp1 = ImageTk.PhotoImage(sp1_img)
        self.sp1_lbl.configure(image=self._tk_sp1)

        sp2_img = assets.render_spell_icon(slot.spell2.key, rem2, cd2, size=(28, 28))
        self._tk_sp2 = ImageTk.PhotoImage(sp2_img)
        self.sp2_lbl.configure(image=self._tk_sp2)

        ult_img = assets.render_ultimate_icon(slot.champion_name, rem_u, cd_u, size=(28, 28))
        self._tk_ult = ImageTk.PhotoImage(ult_img)
        self.ult_lbl.configure(image=self._tk_ult)

        if slot.has_cosmic_insight:
            self.cosmic_dot.configure(fg_color="#6B46C1", text_color="#FFFFFF")
        else:
            self.cosmic_dot.configure(fg_color="#1A202C", text_color="#718096")

        if slot.has_lucidity_boots or slot.has_crimson_lucidity:
            self.boots_dot.configure(fg_color="#2B6CB0", text_color="#FFFFFF")
        else:
            self.boots_dot.configure(fg_color="#1A202C", text_color="#718096")


class SpectatorHudApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self.assets = AssetManager()

        self.title(f"LoL Spectator HUD v{CURRENT_VERSION}")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.92)
        self.configure(fg_color="#090C12")

        # Posicionado verticalmente a la izquierda de la pantalla (< 25% vh de alto)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        default_x = int(self.cfg.get("hud_x", 6))
        default_y = int(self.cfg.get("hud_y", int(sh * 0.24)))
        default_x = max(0, min(sw - 180, default_x))
        default_y = max(0, min(sh - 260, default_y))
        self.geometry(f"+{default_x}+{default_y}")

        self._drag_start_x = 0
        self._drag_start_y = 0

        self.is_live_connected: bool = False
        self._missed_poll_count: int = 0
        self.active_enemy_count: int = 0
        self.preview_hud_when_waiting: bool = False
        self.game_time_seconds: float = 0.0
        self.last_api_poll_wall: float = 0.0
        self.spectator_queried_for_match: bool = False
        self.spectator_cosmic_by_riot_id: dict[str, bool] = {}
        self.auto_accept_var = tk.BooleanVar(value=bool(self.cfg.get("auto_accept_queue", True)))

        default_roles = ["TOP", "JG", "MID", "ADC", "SUP"]
        self.slots: list[EnemySlotState] = [
            EnemySlotState(
                slot_index=i,
                role_label=default_roles[i],
                champion_name="",
                riot_id="",
                level=1,
                spell1=resolve_spell("Flash"),
                spell2=resolve_spell("Ignite"),
            )
            for i in range(5)
        ]

        # Cola de eventos detectados por OCR (Summoners + Ultimates)
        self.detected_events_queue: "queue.Queue[tuple[int, Optional[SummonerSpellInfo], bool, str]]" = (
            queue.Queue()
        )
        self.scanner = AutoChatScanner(
            is_in_game=lambda: self.is_live_connected or self.preview_hud_when_waiting,
            get_enemy_champions=lambda: [
                s.champion_name for s in self.slots[: self.active_enemy_count] if s.champion_name
            ],
            get_enemy_ult_names=self._get_enemy_ult_names,
            is_spell_already_on_cooldown=self._is_spell_on_cooldown,
            on_confirmed_spell=lambda idx, sp, is_ult, raw: self.detected_events_queue.put(
                (idx, sp, is_ult, raw)
            ),
            on_queue_accepted=self._on_queue_accepted_notify,
            get_game_time=lambda: self.game_time_seconds,
        )
        reg = self.cfg.get("chat_region", [0.0, 0.52, 0.40, 0.94])
        if isinstance(reg, list) and len(reg) == 4:
            self.scanner.region_fractions = (
                float(reg[0]),
                float(reg[1]),
                float(reg[2]),
                float(reg[3]),
            )
        self.scanner.auto_accept_queue = bool(self.auto_accept_var.get())
        self.scanner.enabled = True
        self.scanner.start()

        self.rows: list[SpectatorEnemyRow] = []
        self._current_view_mode: str = ""
        self._build_ui()
        self._update_view_mode()
        self.after(200, self._main_loop)
        self.after(800, self._check_updates_bg)

    def _check_updates_bg(self) -> None:
        def _worker() -> None:
            up = check_for_updates(timeout=3.5)
            if up:
                self.after(0, lambda: self._prompt_update(up))

        threading.Thread(target=_worker, daemon=True).start()

    def _prompt_update(self, up_info: dict) -> None:
        try:
            UpdateDialog(self, up_info)
        except Exception:
            pass

    def _get_enemy_ult_names(self) -> list[tuple[str, str]]:
        res: list[tuple[str, str]] = []
        for s in self.slots[: self.active_enemy_count]:
            if s.champion_name:
                u = self.assets.get_ultimate_info(s.champion_name)
                res.append((u.name_en, u.name_es))
            else:
                res.append(("", ""))
        return res

    def _reset_all_slots(self) -> None:
        """Limpia por completo todos los campeones, runas, botas y contadores de la partida anterior."""
        default_roles = ["TOP", "JG", "MID", "ADC", "SUP"]
        for i, s in enumerate(self.slots):
            s.role_label = default_roles[i]
            s.champion_name = ""
            s.riot_id = ""
            s.level = 1
            s.ability_haste = 0
            s.has_cosmic_insight = False
            s.has_lucidity_boots = False
            s.has_crimson_lucidity = False
            s.manual_cosmic_override = None
            s.manual_boots_override = None
            s.spell_timers.clear()
            s.timer_ult = None
        self.active_enemy_count = 0
        self.scanner._consumed_event_signatures.clear()
        self.scanner._last_trigger_wall.clear()

    def _on_queue_accepted_notify(self) -> None:
        try:
            self.wait_status_lbl.configure(text="✅ ¡COLA ACEPTADA!", text_color="#68D391")
        except Exception:
            pass

    def _is_spell_on_cooldown(self, slot_index: int, spell_key: str) -> bool:
        """Verifica si un spell (o la ult) ya tiene un timer activo no expirado."""
        if slot_index < 0 or slot_index >= len(self.slots):
            return False
        slot = self.slots[slot_index]
        if spell_key == "ULT":
            return bool(slot.timer_ult and not slot.timer_ult.is_expired(self.game_time_seconds))
        # Buscar en el diccionario de spell_timers por clave exacta
        timer = slot.spell_timers.get(spell_key)
        if timer and not timer.is_expired(self.game_time_seconds):
            return True
        return False

    def _build_ui(self) -> None:
        self.root_box = ctk.CTkFrame(
            self,
            corner_radius=7,
            fg_color="#090C12",
            border_width=1,
            border_color="#252E3E",
        )
        self.root_box.pack(fill="both", expand=True)

        # Barra superior ultracompacta (20px de alto)
        self.header_bar = ctk.CTkFrame(self.root_box, corner_radius=5, fg_color="#121722", height=20)
        self.header_bar.pack(fill="x", padx=2, pady=(2, 1))

        self.drag_lbl = ctk.CTkLabel(
            self.header_bar,
            text="⠿",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#90CDF4",
            cursor="fleur",
        )
        self.drag_lbl.pack(side="left", padx=(4, 2))
        for w in (self.header_bar, self.drag_lbl):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._stop_drag)

        self.clock_lbl = ctk.CTkLabel(
            self.header_bar,
            text="EN ESPERA",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color="#F6AD55",
        )
        self.clock_lbl.pack(side="left", padx=2)
        self.clock_lbl.bind("<ButtonPress-1>", self._start_drag)
        self.clock_lbl.bind("<B1-Motion>", self._on_drag)

        # Botón Cerrar
        ctk.CTkButton(
            self.header_bar,
            text="✕",
            width=18,
            height=16,
            corner_radius=3,
            fg_color="#742A2A",
            hover_color="#9B2C2C",
            font=ctk.CTkFont(size=9, weight="bold"),
            command=self.destroy,
        ).pack(side="right", padx=(1, 2), pady=1)

        # Botón Calibrar Chat
        ctk.CTkButton(
            self.header_bar,
            text="🎯",
            width=20,
            height=16,
            corner_radius=3,
            fg_color="#2B6CB0",
            hover_color="#2C5282",
            font=ctk.CTkFont(size=9),
            command=self._open_chat_calibrator,
        ).pack(side="right", padx=1, pady=1)

        # Botón Vista Previa (para ver/ocultar el HUD cuando estás fuera de partida)
        self.preview_btn = ctk.CTkButton(
            self.header_bar,
            text="👁",
            width=20,
            height=16,
            corner_radius=3,
            fg_color="#2D3748",
            hover_color="#4A5568",
            font=ctk.CTkFont(size=9),
            command=self._toggle_preview_hud,
        )
        self.preview_btn.pack(side="right", padx=1, pady=1)

        # --- VISTA 1: INTERFAZ INICIAL "ESPERANDO PARTIDA" ---
        self.waiting_frame = ctk.CTkFrame(self.root_box, fg_color="transparent")

        self.wait_status_lbl = ctk.CTkLabel(
            self.waiting_frame,
            text="🟠 Esperando partida...",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#F6AD55",
        )
        self.wait_status_lbl.pack(padx=8, pady=(4, 2), anchor="w")

        self.auto_accept_cb = ctk.CTkCheckBox(
            self.waiting_frame,
            text="Auto-Aceptar Cola",
            variable=self.auto_accept_var,
            command=self._toggle_auto_accept,
            checkbox_width=14,
            checkbox_height=14,
            font=ctk.CTkFont(size=10),
            fg_color="#38A169",
            hover_color="#2F855A",
        )
        self.auto_accept_cb.pack(padx=8, pady=(0, 2), anchor="w")

        self.ver_lbl = ctk.CTkLabel(
            self.waiting_frame,
            text=f"v{CURRENT_VERSION}",
            font=ctk.CTkFont(size=8),
            text_color="#4A5568",
        )
        self.ver_lbl.pack(padx=8, pady=(0, 4), anchor="w")

        # --- VISTA 2: HUD VERTICAL ESTILO ESPECTADOR (Solo visible en partida o preview) ---
        self.hud_frame = ctk.CTkFrame(self.root_box, fg_color="transparent")
        for i in range(5):
            r = SpectatorEnemyRow(self.hud_frame, i, self)
            r.frame.pack(fill="x", padx=2, pady=1)
            self.rows.append(r)

    def _update_view_mode(self) -> None:
        """
        Alterna entre la pantalla mínima de espera ('waiting') y el HUD de Espectador ('hud')
        cuando inicia o termina una partida.
        """
        target_mode = "hud" if (self.is_live_connected or self.preview_hud_when_waiting) else "waiting"
        if target_mode == self._current_view_mode:
            return
        self._current_view_mode = target_mode

        if target_mode == "waiting":
            self.hud_frame.pack_forget()
            self.waiting_frame.pack(fill="both", expand=True)
            self.clock_lbl.configure(text="EN COLA", text_color="#F6AD55")
            self.wait_status_lbl.configure(text="🟠 Esperando partida...", text_color="#F6AD55")
        else:
            self.waiting_frame.pack_forget()
            self.hud_frame.pack(fill="both", expand=True, pady=(0, 2))

    def _toggle_preview_hud(self) -> None:
        self.preview_hud_when_waiting = not self.preview_hud_when_waiting
        if self.preview_hud_when_waiting and not self.is_live_connected:
            demo_champs = ["Corki", "Skarner", "Ahri", "Jinx", "Nautilus"]
            for i, c in enumerate(demo_champs):
                self.slots[i].champion_name = c
                self.slots[i].level = 6
                self.slots[i].ability_haste = 0
            self.active_enemy_count = 5
        elif not self.is_live_connected:
            self._reset_all_slots()
        self._update_view_mode()
        self._refresh_ui()

    def _toggle_auto_accept(self) -> None:
        val = bool(self.auto_accept_var.get())
        self.scanner.auto_accept_queue = val
        self.cfg["auto_accept_queue"] = val
        save_config(self.cfg)

    def _start_drag(self, event) -> None:
        self._drag_start_x = event.x_root - self.winfo_x()
        self._drag_start_y = event.y_root - self.winfo_y()

    def _on_drag(self, event) -> None:
        nx = event.x_root - self._drag_start_x
        ny = event.y_root - self._drag_start_y
        self.geometry(f"+{nx}+{ny}")

    def _stop_drag(self, _event) -> None:
        self.cfg["hud_x"] = self.winfo_x()
        self.cfg["hud_y"] = self.winfo_y()
        save_config(self.cfg)

    def toggle_cosmic(self, slot_index: int) -> None:
        slot = self.slots[slot_index]
        new_val = not slot.has_cosmic_insight
        slot.manual_cosmic_override = new_val
        slot.has_cosmic_insight = new_val
        self._recalculate_slot(slot)
        self._refresh_ui()

    def toggle_boots(self, slot_index: int) -> None:
        slot = self.slots[slot_index]
        new_val = not (slot.has_lucidity_boots or slot.has_crimson_lucidity)
        slot.manual_boots_override = new_val
        slot.has_lucidity_boots = new_val
        self._recalculate_slot(slot)
        self._refresh_ui()

    def _recalculate_slot(self, slot: EnemySlotState) -> None:
        for attr, spell in (("timer1", slot.spell1), ("timer2", slot.spell2)):
            t: Optional[ActiveTimer] = getattr(slot, attr)
            if t is not None:
                new_cd = calculate_cooldown(
                    spell.base_cooldown,
                    has_cosmic_insight=slot.has_cosmic_insight,
                    has_lucidity_boots=slot.has_lucidity_boots,
                    has_crimson_lucidity=slot.has_crimson_lucidity,
                )
                t.total_cooldown = new_cd
                t.expires_at_game_time = t.started_at_game_time + new_cd
        if slot.timer_ult is not None and slot.champion_name:
            ult_data = self.assets.get_ultimate_info(slot.champion_name)
            new_u_cd = ult_data.cooldown_for_level(slot.level, slot.ability_haste)
            slot.timer_ult.total_cooldown = new_u_cd
            slot.timer_ult.expires_at_game_time = slot.timer_ult.started_at_game_time + new_u_cd

    def trigger_from_auto_chat(
        self,
        slot_index: int,
        spell_info: Optional[SummonerSpellInfo],
        is_ult: bool,
        raw_line: str,
    ) -> None:
        slot = self.slots[slot_index]

        if is_ult:
            if slot.timer_ult and not slot.timer_ult.is_expired(self.game_time_seconds):
                return
            ult_data = self.assets.get_ultimate_info(slot.champion_name)
            cd_u = ult_data.cooldown_for_level(slot.level, slot.ability_haste)
            slot.timer_ult = ActiveTimer(
                started_at_game_time=self.game_time_seconds,
                expires_at_game_time=self.game_time_seconds + cd_u,
                total_cooldown=cd_u,
            )
            self._refresh_ui()
            return

        if spell_info is None:
            return

        which = "sp1"
        if slot.spell2.key == spell_info.key and slot.spell1.key != spell_info.key:
            which = "sp2"
        elif slot.spell1.key != spell_info.key and spell_info.key != "Flash":
            slot.spell2 = spell_info
            which = "sp2"

        existing = slot.timer1 if which == "sp1" else slot.timer2
        if existing is not None and not existing.is_expired(self.game_time_seconds):
            return

        spell = slot.spell1 if which == "sp1" else slot.spell2
        cd = calculate_cooldown(
            spell.base_cooldown,
            has_cosmic_insight=slot.has_cosmic_insight,
            has_lucidity_boots=slot.has_lucidity_boots,
            has_crimson_lucidity=slot.has_crimson_lucidity,
        )
        new_t = ActiveTimer(
            started_at_game_time=self.game_time_seconds,
            expires_at_game_time=self.game_time_seconds + cd,
            total_cooldown=cd,
        )
        if which == "sp1":
            slot.timer1 = new_t
        else:
            slot.timer2 = new_t
        self._refresh_ui()

    def trigger_or_adjust(self, slot_index: int, kind: str) -> None:
        slot = self.slots[slot_index]
        attr = {"sp1": "timer1", "sp2": "timer2", "ult": "timer_ult"}[kind]
        current: Optional[ActiveTimer] = getattr(slot, attr)

        if current and not current.is_expired(self.game_time_seconds):
            current.started_at_game_time -= 5.0
            current.expires_at_game_time -= 5.0
            self._refresh_ui()
            return

        if kind == "ult":
            ult_data = self.assets.get_ultimate_info(slot.champion_name)
            cd = ult_data.cooldown_for_level(slot.level, slot.ability_haste)
        else:
            spell = slot.spell1 if kind == "sp1" else slot.spell2
            cd = calculate_cooldown(
                spell.base_cooldown,
                has_cosmic_insight=slot.has_cosmic_insight,
                has_lucidity_boots=slot.has_lucidity_boots,
                has_crimson_lucidity=slot.has_crimson_lucidity,
            )

        setattr(
            slot,
            attr,
            ActiveTimer(
                started_at_game_time=self.game_time_seconds,
                expires_at_game_time=self.game_time_seconds + cd,
                total_cooldown=cd,
            ),
        )
        self._refresh_ui()

    def clear_timer(self, slot_index: int, kind: str) -> None:
        slot = self.slots[slot_index]
        attr = {"sp1": "timer1", "sp2": "timer2", "ult": "timer_ult"}[kind]
        setattr(slot, attr, None)
        self._refresh_ui()

    def clear_all_timers(self) -> None:
        for s in self.slots:
            s.spell_timers.clear()
            s.timer_ult = None
        self.scanner._consumed_event_signatures.clear()
        self.scanner._last_trigger_wall.clear()

    def _open_chat_calibrator(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Calibrar Zona de Lectura del Chat")
        win.geometry("480x420")
        win.attributes("-topmost", True)
        win.configure(fg_color="#12161F")

        ctk.CTkLabel(
            win,
            text="En espera de partida mira el CENTRO (Aceptar Cola).\n"
            "Dentro de partida mira el CHAT (abajo a la izquierda):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#F6AD55",
            justify="left",
        ).pack(padx=10, pady=(8, 4), anchor="w")

        preview_lbl = ctk.CTkLabel(win, text="Capturando...", width=440, height=200)
        preview_lbl.pack(padx=10, pady=4)

        ocr_read_lbl = ctk.CTkLabel(
            win, text="Texto leído: ...", font=ctk.CTkFont(size=11), text_color="#68D391"
        )
        ocr_read_lbl.pack(padx=10, pady=2)

        sliders_frame = ctk.CTkFrame(win, fg_color="#181D29")
        sliders_frame.pack(fill="x", padx=10, pady=6)

        x0, y0, x1, y1 = self.scanner.region_fractions

        def _update_region(_val=None) -> None:
            nx0 = round(sx0.get(), 2)
            ny0 = round(sy0.get(), 2)
            nx1 = max(nx0 + 0.15, round(sx1.get(), 2))
            ny1 = max(ny0 + 0.15, round(sy1.get(), 2))
            self.scanner.region_fractions = (nx0, ny0, nx1, ny1)
            self.cfg["chat_region"] = [nx0, ny0, nx1, ny1]
            save_config(self.cfg)

        ctk.CTkLabel(sliders_frame, text="Chat Izq/Der:", font=ctk.CTkFont(size=11)).grid(
            row=0, column=0, padx=6, pady=3, sticky="w"
        )
        sx0 = ctk.CTkSlider(
            sliders_frame, from_=0.0, to=0.7, number_of_steps=70, command=_update_region, width=140
        )
        sx0.set(x0)
        sx0.grid(row=0, column=1, padx=4, pady=3)
        sx1 = ctk.CTkSlider(
            sliders_frame, from_=0.2, to=1.0, number_of_steps=80, command=_update_region, width=140
        )
        sx1.set(x1)
        sx1.grid(row=0, column=2, padx=4, pady=3)

        ctk.CTkLabel(sliders_frame, text="Chat Arr/Aba:", font=ctk.CTkFont(size=11)).grid(
            row=1, column=0, padx=6, pady=3, sticky="w"
        )
        sy0 = ctk.CTkSlider(
            sliders_frame, from_=0.2, to=0.85, number_of_steps=65, command=_update_region, width=140
        )
        sy0.set(y0)
        sy0.grid(row=1, column=1, padx=4, pady=3)
        sy1 = ctk.CTkSlider(
            sliders_frame, from_=0.4, to=1.0, number_of_steps=60, command=_update_region, width=140
        )
        sy1.set(y1)
        sy1.grid(row=1, column=2, padx=4, pady=3)

        def _refresh_preview() -> None:
            if not win.winfo_exists():
                return
            img = self.scanner.last_snapshot_image
            if img is not None:
                thumb = img.copy()
                thumb.thumbnail((440, 190))
                tk_img = ImageTk.PhotoImage(thumb)
                preview_lbl.configure(image=tk_img, text="")
                preview_lbl._image_ref = tk_img
            ocr_read_lbl.configure(
                text=f"Último texto visto: {self.scanner.last_raw_preview or '(vacío)'}"
            )
            win.after(500, _refresh_preview)

        _refresh_preview()

    def _poll_live_game(self) -> None:
        snap: Optional[LiveMatchSnapshot] = fetch_live_match_snapshot(timeout=0.65)
        if snap is None:
            if self.is_live_connected:
                self._missed_poll_count += 1
                # Solo declaramos fin de partida tras 5 fallos consecutivos (~6 segundos sin respuesta)
                if self._missed_poll_count >= 5:
                    self.is_live_connected = False
                    self.preview_hud_when_waiting = False
                    self.spectator_queried_for_match = False
                    self.spectator_cosmic_by_riot_id.clear()
                    self._reset_all_slots()
                    self._update_view_mode()
            return

        self._missed_poll_count = 0
        was_connected = self.is_live_connected
        if not was_connected:
            # Inició una partida nueva: limpiar cualquier dato residual antes de cargar los rivales actuales
            self._reset_all_slots()

        self.is_live_connected = True
        self.game_time_seconds = snap.game_time_seconds
        num_enemies = min(5, len(snap.enemies))
        self.active_enemy_count = num_enemies

        if not was_connected:
            self._update_view_mode()

        if (
            not self.spectator_queried_for_match
            and self.cfg.get("riot_api_key")
            and snap.active_riot_id
        ):
            self.spectator_queried_for_match = True
            threading.Thread(
                target=self._query_spectator_bg,
                args=(snap.active_riot_id,),
                daemon=True,
            ).start()

        for idx in range(5):
            slot = self.slots[idx]
            if idx < num_enemies:
                enemy = snap.enemies[idx]
                if slot.champion_name != enemy.champion_name:
                    slot.manual_cosmic_override = None
                    slot.manual_boots_override = None
                    slot.spell_timers.clear()
                    slot.timer_ult = None

                slot.role_label = enemy.role_label
                slot.champion_name = enemy.champion_name
                slot.riot_id = enemy.riot_id
                slot.level = enemy.level
                slot.spell1 = enemy.spell1
                slot.spell2 = enemy.spell2
                slot.has_crimson_lucidity = enemy.has_crimson_lucidity
                slot.ability_haste = self.assets.get_items_ability_haste(enemy.items)

                self.assets.get_ultimate_info(enemy.champion_name)

                if slot.manual_cosmic_override is None:
                    rid_lower = enemy.riot_id.lower()
                    if rid_lower and rid_lower in self.spectator_cosmic_by_riot_id:
                        slot.has_cosmic_insight = self.spectator_cosmic_by_riot_id[rid_lower]
                    else:
                        slot.has_cosmic_insight = enemy.has_inspiration_tree

                if slot.manual_boots_override is None:
                    slot.has_lucidity_boots = (
                        enemy.has_lucidity_boots or enemy.has_crimson_lucidity
                    )

                self._recalculate_slot(slot)
            else:
                # Si es 1v1, 2v2 o 3v3, limpiar los slots sobrantes para que no queden rivales viejos
                slot.champion_name = ""
                slot.riot_id = ""
                slot.level = 1
                slot.ability_haste = 0
                slot.has_cosmic_insight = False
                slot.has_lucidity_boots = False
                slot.has_crimson_lucidity = False
                slot.manual_cosmic_override = None
                slot.manual_boots_override = None
                slot.spell_timers.clear()
                slot.timer_ult = None

    def _query_spectator_bg(self, active_riot_id: str) -> None:
        res = fetch_spectator_v5_cosmic_insight(
            riot_api_key=self.cfg.get("riot_api_key", ""),
            platform=self.cfg.get("platform", "la2"),
            active_riot_id=active_riot_id,
        )
        if res:
            self.spectator_cosmic_by_riot_id.update(res)

    def _refresh_ui(self) -> None:
        self._update_view_mode()
        if self._current_view_mode == "waiting":
            return

        if self.is_live_connected:
            self.clock_lbl.configure(
                text=f"🟢 {format_mmss(self.game_time_seconds)}",
                text_color="#68D391",
            )
        else:
            self.clock_lbl.configure(
                text=f"👁 {format_mmss(self.game_time_seconds)}",
                text_color="#90CDF4",
            )

        for idx, slot in enumerate(self.slots):
            row = self.rows[idx]
            if idx < self.active_enemy_count and slot.champion_name:
                if not row.frame.winfo_manager():
                    row.frame.pack(fill="x", padx=2, pady=1)
                for sp_k, sp_t in list(slot.spell_timers.items()):
                    if sp_t.is_expired(self.game_time_seconds):
                        slot.spell_timers.pop(sp_k, None)
                if slot.timer_ult and slot.timer_ult.is_expired(self.game_time_seconds):
                    slot.timer_ult = None
                row.update_row(slot, self.game_time_seconds, self.assets)
            else:
                if row.frame.winfo_manager():
                    row.frame.pack_forget()

    def _drain_detected_chat_events(self) -> None:
        while True:
            try:
                slot_idx, spell_info, is_ult, raw_line = self.detected_events_queue.get_nowait()
            except queue.Empty:
                break
            self.trigger_from_auto_chat(slot_idx, spell_info, is_ult, raw_line)

    def _main_loop(self) -> None:
        now = time.monotonic()
        if now - self.last_api_poll_wall >= 1.2:
            self.last_api_poll_wall = now
            self._poll_live_game()

        if not self.is_live_connected and self.preview_hud_when_waiting:
            self.game_time_seconds += 0.2

        self._drain_detected_chat_events()
        self._refresh_ui()
        self.after(200, self._main_loop)


def main() -> None:
    app = SpectatorHudApp()
    app.mainloop()


if __name__ == "__main__":
    main()
