# ⚡ LoL Spectator HUD — Summoner & Ultimate Tracker

[![Descargar Ejecutable (.exe)](https://img.shields.io/badge/⬇_DESCARGAR_EJECUTABLE-LoL__Spectator__HUD__1__0__3.exe-2b6cb0?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/xzyprogrammer/lol_tracker/raw/main/dist/LoL_Spectator_HUD_1_0_3.exe)
[![Versión](https://img.shields.io/badge/VERSIÓN-v1.0.3-68d391?style=for-the-badge)](https://github.com/xzyprogrammer/lol_tracker/releases)
[![Sitio Web Oficial](https://img.shields.io/badge/🌐_WEB_DEL_PROYECTO-GitHub_Pages-38a169?style=for-the-badge)](https://xzyprogrammer.github.io/lol_tracker/)
[![Código Abierto](https://img.shields.io/badge/CÓDIGO_ABIERTO-Python_3-ecc94b?style=for-the-badge&logo=python&logoColor=black)](https://github.com/xzyprogrammer/lol_tracker)

**LoL Spectator HUD** es una herramienta visual ultraligera y de **código abierto** diseñada para jugadores competitivos (**Scrims, Tier 2 / Tier 3, Clash, SoloQ y Flex**). Superpone un HUD vertical minimalista estilo **Modo Espectador** a la izquierda de tu pantalla que rastrea en tiempo real los **Hechizos de Invocador (Summoner Spells)** y las **Definitivas (Ultimates `R`)** del equipo enemigo.

---

## 📥 Descarga Directa e Instalación Rápida

No necesitas instalar Python ni configurar nada complicado si solo quieres usar la herramienta:

1. 👉 **[Haz clic aquí para descargar `LoL_Spectator_HUD_1_0_3.exe` directamente](https://github.com/xzyprogrammer/lol_tracker/raw/main/dist/LoL_Spectator_HUD_1_0_3.exe)** *(Ejecutable portable para Windows 10/11)*.
2. Abre `LoL_Spectator_HUD_1_0_3.exe` antes o durante tu partida.
3. Si Windows SmartScreen muestra un aviso (por ser un ejecutable independiente sin firma empresarial de pago), haz clic en **"Más información" → "Ejecutar de todas formas"**.
4. 🚀 **Auto-Actualización Automática:** Cuando haya una nueva versión disponible en GitHub, la aplicación te avisará al arrancar y te permitirá actualizarla automáticamente con un solo clic.

---

## ⚠️ 2 Requisitos Obligatorios dentro de League of Legends

Para que la detección automática del chat y la superposición visual funcionen al 100%, asegúrate de cumplir estas dos reglas en tu cliente de LoL:

| Requisito | Configuración necesaria | ¿Por qué es obligatorio? |
| :--- | :--- | :--- |
| **1. Idioma del Juego** | **Inglés (`English`)** o **Español (`Español LATAM / España`)** | El motor semántico detecta las confirmaciones oficiales del sistema en estos dos idiomas (`"Corki used Flash"` / `"Corki usó Destello"`, `"Lux used R"` / `"Lux usó Chispa Final"`). |
| **2. Modo de Video** | **Sin Bordes (`Borderless`)** *(en Opciones → Video)* | En modo *Pantalla Completa Exclusiva*, Windows bloquea cualquier ventana superpuesta y la lectura del chat sale en negro. En modo **Sin Bordes**, el HUD flota suavemente encima del juego. |

---

## 🔥 Características Principales

### 1. 🎯 Detección Inteligente en el Chat (`"used Flash"` vs Ping normal)
* Cuando un enemigo gasta un hechizo de invocador en visión (o aparece en el resumen de muerte) y lo marcas en el chat, el sistema de League of Legends imprime el verbo de confirmación:
  * ✅ **`"Corki used Flash"`** / **`"Corki usó Destello"`** → **Activa el contador automáticamente.**
  * ❌ **`"Corki - Flash"`** / **`"Corki - Destello"`** *(ping manual en tabulador sin confirmación)* → **Lo ignora** para evitar falsos positivos.
* **Protección Anti-Reinicio:** Si un enemigo (ej. *Lux*) ya tiene el Flash en enfriamiento desde el minuto 1 y otro enemigo (ej. *Udyr*) gasta el Flash al minuto 2 haciendo que el chat vuelva a mostrar ambos mensajes, el sistema **jamás reinicia el contador activo de Lux**.

### 2. 🖥️ HUD Vertical Estilo Modo Espectador (`< 23% vh`)
* Diseñado para no estorbar tu visión del mapa: se ubica verticalmente en el borde izquierdo de la pantalla ocupando **menos de una cuarta parte de la altura del monitor**.
* Cada fila muestra en tiempo real:
  * **Retrato oficial del campeón enemigo** + su **Nivel actual (`1–18`)** actualizado en vivo.
  * **Iconos de sus 2 Hechizos de Invocador** (`Flash`, `TP`, `Ignite`, `Smite`, `Exhaust`, `Barrier`, `Cleanse`, `Heal`, `Ghost`).
  * **Icono oficial de su Ultimate (`R`)** con el enfriamiento exacto según su rango actual (`Nivel 6 / 11 / 16`).
  * Cuando un hechizo o definitiva está en enfriamiento, **el icono se oscurece y dibuja encima los segundos restantes en tiempo real** hasta que vuelve a iluminarse.

### 3. 🧮 Cálculo Automático de Runas, Botas y *Libro de Hechizos*
El programa se conecta pasivamente a la API local oficial del cliente en vivo (`https://127.0.0.1:2999/liveclientdata/allgamedata`) y aplica la fórmula oficial de Riot Games:

$$\text{Enfriamiento Real} = \text{Base} \times \frac{100}{100 + \text{Summoner Spell Haste}}$$

| Configuración del Rival | Indicador en HUD | Haste Total | Enfriamiento de Flash (`300s`) | Enfriamiento de Ignite/Barrier (`180s`) |
| :--- | :---: | :---: | :---: | :---: |
| **Base (Sin reducción)** | — | `0` | **`300s`** *(5:00)* | **`180s`** *(3:00)* |
| **Botas Jonias de la Lucidez** *(ID 3158)* | **`J`** *(Azul)* | `+10` | **`273s`** *(4:33)* | **`164s`** *(2:44)* |
| **Perspicacia Cósmica** *(Rama Inspiración)* | **`C`** *(Morado)* | `+18` | **`254s`** *(4:14)* | **`153s`** *(2:33)* |
| **Perspicacia Cósmica + Botas Jonias** | **`C` + `J`** | `+28` | **`234s`** *(3:54)* | **`141s`** *(2:21)* |
| **Perspicacia Cósmica + Lucidez Carmesí** *(ID 3171)* | **`C` + `J`** | `+38` | **`217s`** *(3:37)* | **`130s`** *(2:10)* |

* **Soporte para *Libro de Hechizos Abierto* (*Unsealed Spellbook*):** Guarda relojes independientes por cada hechizo. Si un rival gasta `Teleport`, lo cambia por `Ignite` usando Libro de Hechizos y luego regresa a `Teleport`, el HUD muestra `Ignite` disponible y conserva de fondo los segundos exactos que le faltaban a su `Teleport`.

### 4. 🔄 Reinicio Automático entre Partidas y Adaptación 1v1 / 2v2 / 5v5
* Antes de entrar a partida —y apenas termina un juego—, el HUD se limpia por completo y se contrae en una pequeña píldora de espera (`🟠 Esperando partida...`).
* Adapta sus filas dinámicamente según la cantidad real de enemigos en la partida (**1 fila en 1v1**, **2 en 2v2**, **5 en 5v5**).

### 5. ⚡ Auto-Aceptar Partida en Cola (*Opcional*)
* Mientras estás en la pantalla de espera con la casilla **`☑ Auto-Aceptar Cola`** activa, el programa detecta cuando salta cola y acepta la partida automáticamente (por si fuiste por agua entre partidas).

---

## 🖱️ Controles Manuales Rápidos en el HUD

Además de la detección automática por chat, puedes interactuar directamente con los iconos del HUD con el ratón:
* **Clic Izquierdo sobre cualquier hechizo o `R`:** Inicia manualmente la cuenta regresiva (si ya estaba corriendo, le **resta `-5 segundos`** por si lo marcaste tarde).
* **Clic Derecho sobre cualquier hechizo o `R`:** Cancela/reinicia ese enfriamiento y lo devuelve a estado disponible.
* **Clic en `C` / `J`:** Activa o desactiva manualmente *Perspicacia Cósmica* (`C`) o *Botas Jonias* (`J`) recalculando los timers activos al instante.
* **Botón `🎯`:** Abre la cámara de calibración para verificar que el recuadro de lectura apunte exactamente a donde tienes ubicado tu chat dentro de LoL.
* **Botón `👁`:** Muestra una vista previa del HUD cuando estás fuera de partida para que puedas arrastrarlo desde `⠿` a tu posición favorita.

---

## 🛠️ Compilar o Ejecutar desde el Código Fuente

Todo el código fuente es **100% público y auditable**:

* [`app.py`](./app.py): Interfaz gráfica del HUD estilo Espectador, ciclo de vida de la partida y gestión de contadores.
* [`updater.py`](./updater.py): Sistema de auto-actualización automática conectado al repositorio de GitHub.
* [`live_client_api.py`](./live_client_api.py): Cliente de solo lectura para `https://127.0.0.1:2999/liveclientdata/allgamedata`, `Spectator-V5` y `LCU Ready-Check`.
* [`ddragon_assets.py`](./ddragon_assets.py): Descarga y caché de iconos oficiales de Riot Data Dragon (campeones, hechizos y definitivas `R` por nivel).
* [`chat_ocr.py`](./chat_ocr.py): Motor OCR con filtro de alto contraste y coincidencia difusa contra los campeones enemigos de la partida.
* [`summoner_data.py`](./summoner_data.py): Base de datos de enfriamientos y fórmulas matemáticas de Summoner Spell Haste.

```bash
# 1. Clonar el repositorio
git clone https://github.com/xzyprogrammer/lol_tracker.git
cd lol_tracker

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Ejecutar la aplicación
python app.py
```
