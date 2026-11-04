"""
Módulo de Verificación y Auto-Actualización Automática para LoL Spectator HUD.
- Comprueba si existe una versión más nueva en GitHub al iniciar la aplicación.
- Compara versiones semánticas (ej: 1.0.1 > 1.0.0).
- Descarga el nuevo ejecutable versionado (ej: LoL_Spectator_HUD_1_0_1.exe) con barra de progreso.
- Inicia la nueva versión y cierra la actual sin conflictos de bloqueo de Windows.
"""

from __future__ import annotations
import os
import re
import sys
import subprocess
import threading
import webbrowser
from typing import Optional, Callable
import requests
import customtkinter as ctk

CURRENT_VERSION = "1.0.2"
VERSION_INFO_URL = "https://raw.githubusercontent.com/xzyprogrammer/lol_tracker/main/version.json"
REPO_RELEASES_URL = "https://github.com/xzyprogrammer/lol_tracker"


def parse_version_tuple(v_str: str) -> tuple[int, ...]:
    """Convierte cadenas como '1.0.1' o 'v1_0_1' a tuplas comparables (1, 0, 1)."""
    clean = re.sub(r"[^\d.]", "", v_str.replace("_", "."))
    parts = [int(p) for p in clean.split(".") if p.isdigit()]
    return tuple(parts) if parts else (0, 0, 0)


def check_for_updates(timeout: float = 3.5) -> Optional[dict]:
    """
    Consulta GitHub en segundo plano para verificar si hay una nueva versión.
    Retorna el diccionario con la información si hay actualización, o None si está al día.
    """
    try:
        resp = requests.get(VERSION_INFO_URL, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        remote_version_str = str(data.get("version", "")).strip()
        if not remote_version_str:
            return None

        cur_v = parse_version_tuple(CURRENT_VERSION)
        rem_v = parse_version_tuple(remote_version_str)

        if rem_v > cur_v:
            return data
    except Exception:
        pass
    return None


class UpdateDialog(ctk.CTkToplevel):
    """
    Ventana modal de actualización automática para el usuario:
    - Informa sobre la nueva versión y el registro de cambios (changelog).
    - Descarga el ejecutable con barra de progreso en vivo.
    - Lanza la nueva versión automáticamente y cierra la actual.
    """

    def __init__(self, parent: ctk.CTk, update_info: dict) -> None:
        super().__init__(parent)
        self.update_info = update_info
        self.new_version = str(update_info.get("version", "Nueva"))
        self.download_url = str(update_info.get("download_url", ""))
        self.changelog = str(update_info.get("changelog", "Mejoras de rendimiento y corrección de errores."))
        self.is_mandatory = bool(update_info.get("mandatory", False))
        self.filename = str(
            update_info.get(
                "filename",
                f"LoL_Spectator_HUD_{self.new_version.replace('.', '_')}.exe",
            )
        )

        self.title("Actualización Disponible - LoL Spectator HUD")
        self.geometry("450x330")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(fg_color="#0D1117")

        # Centrar en la pantalla
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, int((sw - 450) / 2))
        y = max(0, int((sh - 330) / 2))
        self.geometry(f"+{x}+{y}")

        self._is_downloading = False
        self._build_ui()

    def _build_ui(self) -> None:
        container = ctk.CTkFrame(self, fg_color="#121824", corner_radius=10, border_width=1, border_color="#2D3748")
        container.pack(fill="both", expand=True, padx=12, pady=12)

        # Encabezado
        header_frame = ctk.CTkFrame(container, fg_color="transparent")
        header_frame.pack(fill="x", padx=16, pady=(14, 6))

        title_lbl = ctk.CTkLabel(
            header_frame,
            text="🚀 ¡Nueva versión disponible!",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#63B3ED",
        )
        title_lbl.pack(anchor="w")

        sub_lbl = ctk.CTkLabel(
            header_frame,
            text=f"Tu versión: v{CURRENT_VERSION}  ➔  Nueva: v{self.new_version}",
            font=ctk.CTkFont(size=11),
            text_color="#A0AEC0",
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        # Cuadro de notas de versión (Changelog)
        ctk.CTkLabel(
            container,
            text="Novedades:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#E2E8F0",
        ).pack(anchor="w", padx=16, pady=(6, 2))

        self.log_box = ctk.CTkTextbox(
            container,
            height=90,
            fg_color="#0A0E17",
            border_width=1,
            border_color="#1E2638",
            text_color="#CBD5E0",
            font=ctk.CTkFont(size=10),
            wrap="word",
        )
        self.log_box.pack(fill="x", padx=16, pady=(0, 8))
        self.log_box.insert("1.0", self.changelog)
        self.log_box.configure(state="disabled")

        # Barra de progreso (inicialmente oculta/en cero)
        self.progress_frame = ctk.CTkFrame(container, fg_color="transparent")
        self.progress_frame.pack(fill="x", padx=16, pady=(0, 8))

        self.progress_bar = ctk.CTkProgressBar(
            self.progress_frame,
            height=10,
            corner_radius=5,
            progress_color="#3182CE",
            fg_color="#1A202C",
        )
        self.progress_bar.set(0.0)
        self.progress_bar.pack(fill="x", pady=(2, 2))

        self.status_lbl = ctk.CTkLabel(
            self.progress_frame,
            text="Listo para descargar e instalar.",
            font=ctk.CTkFont(size=10),
            text_color="#718096",
        )
        self.status_lbl.pack(anchor="w")

        # Botones de acción
        btn_frame = ctk.CTkFrame(container, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(6, 12))

        self.update_btn = ctk.CTkButton(
            btn_frame,
            text="⚡ Actualizar ahora",
            fg_color="#2B6CB0",
            hover_color="#2C5282",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32,
            command=self._start_download_thread,
        )
        self.update_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        if not self.is_mandatory:
            self.skip_btn = ctk.CTkButton(
                btn_frame,
                text="Continuar",
                fg_color="#2D3748",
                hover_color="#4A5568",
                text_color="#E2E8F0",
                font=ctk.CTkFont(size=11),
                width=80,
                height=32,
                command=self.destroy,
            )
            self.skip_btn.pack(side="left", padx=(0, 6))

        self.github_btn = ctk.CTkButton(
            btn_frame,
            text="GitHub",
            fg_color="#1A202C",
            hover_color="#2D3748",
            text_color="#A0AEC0",
            font=ctk.CTkFont(size=11),
            width=65,
            height=32,
            command=lambda: webbrowser.open(REPO_RELEASES_URL),
        )
        self.github_btn.pack(side="right")

    def _start_download_thread(self) -> None:
        if self._is_downloading:
            return
        self._is_downloading = True
        self.update_btn.configure(state="disabled", text="Descargando...")
        if hasattr(self, "skip_btn"):
            self.skip_btn.configure(state="disabled")

        threading.Thread(target=self._download_and_launch_worker, daemon=True).start()

    def _download_and_launch_worker(self) -> None:
        try:
            url = self.download_url
            if not url:
                url = f"https://github.com/xzyprogrammer/lol_tracker/raw/main/dist/{self.filename}"

            # Determinar la carpeta donde se encuentra el .exe actual
            if getattr(sys, "frozen", False):
                current_dir = os.path.dirname(sys.executable)
            else:
                current_dir = os.path.dirname(os.path.abspath(__file__))

            target_exe_path = os.path.join(current_dir, self.filename)
            temp_path = target_exe_path + ".tmp"

            self.status_lbl.configure(text=f"Conectando con GitHub...")

            # Descargar con streaming y chunks
            resp = requests.get(url, stream=True, timeout=20.0)
            if resp.status_code != 200:
                raise RuntimeError(f"Error HTTP {resp.status_code} al descargar.")

            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(temp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = downloaded / total_size
                        self.progress_bar.set(pct)
                        mb_down = downloaded / (1024 * 1024)
                        mb_tot = total_size / (1024 * 1024)
                        self.status_lbl.configure(
                            text=f"Descargando {self.filename}: {int(pct * 100)}% ({mb_down:.1f} / {mb_tot:.1f} MB)"
                        )
                    else:
                        mb_down = downloaded / (1024 * 1024)
                        self.status_lbl.configure(text=f"Descargando: {mb_down:.1f} MB")

            # Reemplazar o renombrar archivo final
            if os.path.exists(target_exe_path):
                try:
                    os.remove(target_exe_path)
                except Exception:
                    pass

            os.rename(temp_path, target_exe_path)

            self.status_lbl.configure(text="✅ ¡Descarga completa! Reiniciando...", text_color="#48BB78")
            self.progress_bar.set(1.0)

            # Iniciar el nuevo ejecutable
            if os.path.exists(target_exe_path):
                subprocess.Popen([target_exe_path], cwd=current_dir)
                # Cerrar aplicación actual
                self.after(600, self._exit_app)

        except Exception as e:
            self._is_downloading = False
            self.status_lbl.configure(text=f"Error: {str(e)[:45]}", text_color="#E53E3E")
            self.update_btn.configure(state="normal", text="Reintentar")
            if hasattr(self, "skip_btn"):
                self.skip_btn.configure(state="normal")

    def _exit_app(self) -> None:
        try:
            self.master.destroy()
        except Exception:
            pass
        sys.exit(0)
