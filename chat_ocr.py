"""
Escáner inteligente dual:
1) EN PARTIDA: Vigila la zona del chat (abajo a la izquierda) para detectar tanto Summoner Spells
   ("<Campeón> used Flash" / "usó Destello") como Ultimates ("<Campeón> used R" / nombre de su Ultimate).
2) ESPERANDO PARTIDA (En Cola): Reposiciona automáticamente el OCR al centro de la pantalla
   (y combina con LCU Ready-Check API) para detectar y aceptar la partida automáticamente.
"""

from __future__ import annotations
import asyncio
import ctypes
import difflib
import re
import threading
import time
from typing import Callable, Optional

import mss
import numpy as np
from PIL import Image

from summoner_data import SUMMONER_SPELLS, SummonerSpellInfo
from live_client_api import try_lcu_auto_accept_ready_check

try:
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
    from winrt.windows.storage.streams import DataWriter
    from winrt.windows.globalization import Language

    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False


def _create_ocr_engine() -> Optional["OcrEngine"]:
    if not OCR_AVAILABLE:
        return None
    for lang_tag in ("en-US", "es-ES", "es-MX"):
        try:
            lang = Language(lang_tag)
            if OcrEngine.is_language_supported(lang):
                eng = OcrEngine.try_create_from_language(lang)
                if eng is not None:
                    return eng
        except Exception:
            continue
    return OcrEngine.try_create_from_user_profile_languages()


def preprocess_lol_chat_image(img: Image.Image) -> Image.Image:
    w, h = img.size
    upscaled = img.resize((int(w * 2.5), int(h * 2.5)), Image.Resampling.BICUBIC)
    arr = np.asarray(upscaled.convert("RGB"), dtype=np.uint8)
    max_channel = arr.max(axis=2)
    binary = np.where(max_channel > 135, 0, 255).astype(np.uint8)
    return Image.fromarray(binary, mode="L").convert("RGB")


async def _run_winrt_ocr(engine: "OcrEngine", img: Image.Image) -> list[str]:
    bgra = img.convert("RGBA")
    r, g, b, a = bgra.split()
    raw = Image.merge("RGBA", (b, g, r, a)).tobytes()
    writer = DataWriter()
    writer.write_bytes(raw)
    buf = writer.detach_buffer()
    bmp = SoftwareBitmap.create_copy_from_buffer(
        buf, BitmapPixelFormat.BGRA8, img.width, img.height
    )
    res = await engine.recognize_async(bmp)
    return [line.text.strip() for line in res.lines if line.text.strip()]


USED_VERB_PATTERN = re.compile(
    r"\b(?:used|us[oó0]|ha\s+usado|utiliz[oó0]|gast[oó0]|cast)\b",
    re.IGNORECASE,
)

ULT_KEYWORD_PATTERN = re.compile(
    r"(?:\b(?:used|us[oó0]|gast[oó0])\s+r\b|\b(?:ultimate|ulti|definitiva)\b)",
    re.IGNORECASE,
)

QUEUE_ACCEPT_KEYWORDS = (
    "accept",
    "aceptar",
    "match found",
    "partida encontrada",
)


def _match_champion_in_line(lower_line: str, enemy_champions: list[str]) -> Optional[tuple[int, str]]:
    words = re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ']+", lower_line)
    clean_full = re.sub(r"[^a-z]", "", lower_line)
    for idx, champ in enumerate(enemy_champions):
        if not champ:
            continue
        c_low = champ.lower()
        c_clean = re.sub(r"[^a-z]", "", c_low)
        if c_low in lower_line or (len(c_clean) >= 3 and c_clean in clean_full):
            return (idx, champ)
        for w in words:
            if len(w) >= 3 and difflib.SequenceMatcher(None, c_clean, w).ratio() >= 0.75:
                return (idx, champ)
    return None


def match_confirmed_chat_line(
    line: str,
    enemy_champions: list[str],
    enemy_ult_names: Optional[list[tuple[str, str]]] = None,
) -> Optional[tuple[int, str, Optional[SummonerSpellInfo], bool]]:
    """
    Analiza una línea del chat de LoL:
    - Si es un Summoner Spell confirmado ("Corki used Flash"):
      devuelve (slot_index, champ_name, SummonerSpellInfo, False)
    - Si es una Ultimate confirmada ("Corki used R" o "Corki used Missile Barrage"):
      devuelve (slot_index, champ_name, None, True)
    """
    if not line or len(line) < 5:
        return None

    if not USED_VERB_PATTERN.search(line):
        return None

    lower_line = line.lower()
    champ_match = _match_champion_in_line(lower_line, enemy_champions)
    if champ_match is None:
        return None
    slot_idx, champ_name = champ_match

    # 1. ¿Es un Summoner Spell?
    detected_spell: Optional[SummonerSpellInfo] = None
    for spell_info in SUMMONER_SPELLS.values():
        for alias in spell_info.aliases:
            if len(alias) >= 4 and alias in lower_line:
                detected_spell = spell_info
                break
        if detected_spell:
            break

    if detected_spell is None:
        if any(w in lower_line for w in ("flas", "f1ash", "fiash", "destell", "deste11")):
            detected_spell = SUMMONER_SPELLS["Flash"]
        else:
            for sp_key, kw_part in (
                ("Teleport", "telep"),
                ("Ignite", "ignit"),
                ("Ignite", "prend"),
                ("Exhaust", "exhau"),
                ("Exhaust", "exten"),
                ("Barrier", "barri"),
                ("Heal", "curar"),
                ("Cleanse", "clean"),
                ("Cleanse", "purif"),
                ("Ghost", "ghost"),
                ("Ghost", "fantas"),
                ("Smite", "smit"),
                ("Smite", "aplast"),
            ):
                if kw_part in lower_line:
                    detected_spell = SUMMONER_SPELLS[sp_key]
                    break

    if detected_spell is not None:
        return (slot_idx, champ_name, detected_spell, False)

    # 2. ¿Es la Ultimate (R) de ese campeón?
    if ULT_KEYWORD_PATTERN.search(lower_line):
        return (slot_idx, champ_name, None, True)

    if enemy_ult_names and slot_idx < len(enemy_ult_names):
        ult_en, ult_es = enemy_ult_names[slot_idx]
        for u_name in (ult_en, ult_es):
            u_low = (u_name or "").strip().lower()
            if len(u_low) >= 4 and u_low in lower_line:
                return (slot_idx, champ_name, None, True)

    return None


def _click_screen_pos(x: int, y: int) -> None:
    """Hace clic en el botón Aceptar de la cola cuando el OCR central lo detecta."""
    try:
        user32 = ctypes.windll.user32
        old_pos = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(old_pos))
        user32.SetCursorPos(x, y)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        user32.SetCursorPos(old_pos.x, old_pos.y)
    except Exception:
        pass


class AutoChatScanner:
    """
    Escáner inteligente que cambia automáticamente su posición según el estado:
    - Cuando NO estás en partida (is_in_game() == False) y auto_accept_queue == True:
      Reposiciona el OCR al CENTRO de la pantalla (0.35..0.65, 0.45..0.82) y usa LCU + OCR
      para aceptar la cola automáticamente.
    - Cuando SÍ estás en partida (is_in_game() == True):
      Posiciona el OCR sobre la zona del chat (abajo a la izquierda) para detectar Summoners y Ultimates.
    """

    def __init__(
        self,
        is_in_game: Callable[[], bool],
        get_enemy_champions: Callable[[], list[str]],
        get_enemy_ult_names: Callable[[], list[tuple[str, str]]],
        is_spell_already_on_cooldown: Callable[[int, str], bool],
        on_confirmed_spell: Callable[[int, Optional[SummonerSpellInfo], bool, str], None],
        on_queue_accepted: Optional[Callable[[], None]] = None,
    ) -> None:
        self.is_in_game = is_in_game
        self.get_enemy_champions = get_enemy_champions
        self.get_enemy_ult_names = get_enemy_ult_names
        self.is_spell_already_on_cooldown = is_spell_already_on_cooldown
        self.on_confirmed_spell = on_confirmed_spell
        self.on_queue_accepted = on_queue_accepted

        self.enabled: bool = True
        self.auto_accept_queue: bool = True
        self.region_fractions: tuple[float, float, float, float] = (0.0, 0.52, 0.40, 0.94)
        self.center_queue_fractions: tuple[float, float, float, float] = (0.36, 0.48, 0.64, 0.82)

        self.last_detected_text: str = ""
        self.last_raw_preview: str = ""
        self.last_snapshot_image: Optional[Image.Image] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._still_visible_in_chat: set[tuple[int, str]] = set()
        self._missing_frames_count: dict[tuple[int, str], int] = {}
        self._last_accept_wall: float = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        engine = _create_ocr_engine()
        if engine is None:
            self.last_raw_preview = "OCR de Windows no disponible"
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        with mss.mss() as sct:
            mon = sct.monitors[1]
            while not self._stop.is_set():
                time.sleep(0.6)
                if not self.enabled:
                    continue

                try:
                    in_game = self.is_in_game()

                    # MODO 1: EN ESPERA DE PARTIDA -> Auto-Accept en Cola (LCU API + OCR en el Centro)
                    if not in_game:
                        if self.auto_accept_queue:
                            # 1A) Vía directa oficial del cliente LCU (funciona incluso minimizado)
                            if try_lcu_auto_accept_ready_check():
                                self.last_raw_preview = "¡Cola aceptada automáticamente (LCU)!"
                                if self.on_queue_accepted:
                                    self.on_queue_accepted()
                                time.sleep(1.5)
                                continue

                            # 1B) Vía OCR reposicionado en el CENTRO de la pantalla
                            cx0, cy0, cx1, cy1 = self.center_queue_fractions
                            c_box = {
                                "left": mon["left"] + int(mon["width"] * cx0),
                                "top": mon["top"] + int(mon["height"] * cy0),
                                "width": max(50, int(mon["width"] * (cx1 - cx0))),
                                "height": max(50, int(mon["height"] * (cy1 - cy0))),
                            }
                            c_shot = sct.grab(c_box)
                            c_img = Image.frombytes("RGB", c_shot.size, c_shot.rgb)
                            self.last_snapshot_image = c_img
                            c_lines = loop.run_until_complete(_run_winrt_ocr(engine, c_img))
                            if c_lines:
                                self.last_raw_preview = " [Centro] " + " | ".join(c_lines[-2:])[:65]
                            joined_low = " ".join(c_lines).lower()
                            if any(k in joined_low for k in QUEUE_ACCEPT_KEYWORDS):
                                now_w = time.monotonic()
                                if now_w - self._last_accept_wall > 3.0:
                                    self._last_accept_wall = now_w
                                    # Si LCU aún no aceptó, hacer clic en el botón Aceptar del centro
                                    if not try_lcu_auto_accept_ready_check():
                                        click_x = mon["left"] + int(mon["width"] * 0.50)
                                        click_y = mon["top"] + int(mon["height"] * 0.705)
                                        _click_screen_pos(click_x, click_y)
                                    if self.on_queue_accepted:
                                        self.on_queue_accepted()
                        continue

                    # MODO 2: EN PARTIDA -> OCR sobre la zona del Chat (Summoners + Ultimates)
                    x0, y0, x1, y1 = self.region_fractions
                    box = {
                        "left": mon["left"] + int(mon["width"] * x0),
                        "top": mon["top"] + int(mon["height"] * y0),
                        "width": max(50, int(mon["width"] * (x1 - x0))),
                        "height": max(50, int(mon["height"] * (y1 - y0))),
                    }
                    shot = sct.grab(box)
                    raw_img = Image.frombytes("RGB", shot.size, shot.rgb)
                    self.last_snapshot_image = raw_img

                    up_img = raw_img.resize(
                        (raw_img.width * 2, raw_img.height * 2), Image.Resampling.BILINEAR
                    )
                    lines_raw = loop.run_until_complete(_run_winrt_ocr(engine, up_img))

                    hi_img = preprocess_lol_chat_image(raw_img)
                    lines_hi = loop.run_until_complete(_run_winrt_ocr(engine, hi_img))

                    all_lines = list(dict.fromkeys(lines_raw + lines_hi))
                    if all_lines:
                        self.last_raw_preview = " | ".join(all_lines[-2:])[:75]

                    enemies = self.get_enemy_champions()
                    ult_names = self.get_enemy_ult_names()
                    seen_this_frame: set[tuple[int, str]] = set()

                    for ln in all_lines:
                        matched = match_confirmed_chat_line(ln, enemies, ult_names)
                        if matched is not None:
                            slot_idx, champ_name, spell_info, is_ult = matched
                            spell_key = "ULT" if is_ult else (spell_info.key if spell_info else "Flash")
                            key = (slot_idx, spell_key)
                            seen_this_frame.add(key)

                            if self.is_spell_already_on_cooldown(slot_idx, spell_key):
                                self._still_visible_in_chat.add(key)
                                self._missing_frames_count[key] = 0
                                continue

                            if key not in self._still_visible_in_chat:
                                self._still_visible_in_chat.add(key)
                                self._missing_frames_count[key] = 0
                                self.on_confirmed_spell(slot_idx, spell_info, is_ult, ln)

                    for key in list(self._still_visible_in_chat):
                        if self.is_spell_already_on_cooldown(key[0], key[1]):
                            self._missing_frames_count[key] = 0
                        elif key not in seen_this_frame:
                            self._missing_frames_count[key] = self._missing_frames_count.get(key, 0) + 1
                            if self._missing_frames_count[key] >= 6:
                                self._still_visible_in_chat.discard(key)
                                self._missing_frames_count.pop(key, None)
                        else:
                            self._missing_frames_count[key] = 0

                except Exception as e:
                    self.last_raw_preview = f"Err: {str(e)[:40]}"
