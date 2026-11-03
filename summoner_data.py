"""
Base de datos de Hechizos de Invocador, fórmulas de Summoner Spell Haste
y analizador semántico de líneas del chat de League of Legends.
100% compatible con las políticas de Riot Games y Vanguard.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

# Constantes de Haste de Hechizos de Invocador (Riot Games)
COSMIC_INSIGHT_HASTE = 18       # Perspicacia Cósmica (Runa Inspiración 8347 / Árbol 8300)
LUCIDITY_BOOTS_HASTE = 10       # Botas Jonias de la Lucidez (Item ID 3158)
CRIMSON_LUCIDITY_HASTE = 20     # Lucidez Carmesí - Botas mejoradas T3 (Item ID 3171)

LUCIDITY_ITEM_IDS = {3158}
CRIMSON_LUCIDITY_ITEM_IDS = {3171}
INSPIRATION_TREE_IDS = {"8300", "8347"}
INSPIRATION_KEYWORDS = ("inspiration", "inspiración", "inspiracion", "whimsy", "8300", "8347")


@dataclass(frozen=True)
class SummonerSpellInfo:
    key: str
    name_es: str
    name_en: str
    short_tag: str
    base_cooldown: int
    color: str
    aliases: tuple[str, ...]


SUMMONER_SPELLS: dict[str, SummonerSpellInfo] = {
    "Flash": SummonerSpellInfo(
        key="Flash",
        name_es="Destello",
        name_en="Flash",
        short_tag="F",
        base_cooldown=300,
        color="#F5C542",
        aliases=("destello", "flash", "summonerflash", "f"),
    ),
    "Teleport": SummonerSpellInfo(
        key="Teleport",
        name_es="Teleportar",
        name_en="Teleport",
        short_tag="TP",
        base_cooldown=360,
        color="#A349A4",
        aliases=(
            "teleportar",
            "teletransportación",
            "teletransportacion",
            "teleport",
            "tp",
            "summonerteleport",
            "unleashed teleport",
            "teleportar desatado",
        ),
    ),
    "Ignite": SummonerSpellInfo(
        key="Ignite",
        name_es="Prender",
        name_en="Ignite",
        short_tag="IGN",
        base_cooldown=180,
        color="#E85D3F",
        aliases=("prender", "ignición", "ignicion", "ignite", "summonerdot", "ign"),
    ),
    "Exhaust": SummonerSpellInfo(
        key="Exhaust",
        name_es="Extenuar",
        name_en="Exhaust",
        short_tag="EXH",
        base_cooldown=240,
        color="#D49A35",
        aliases=("extenuar", "agotar", "exhaust", "summonerexhaust", "exh"),
    ),
    "Heal": SummonerSpellInfo(
        key="Heal",
        name_es="Curar",
        name_en="Heal",
        short_tag="HEAL",
        base_cooldown=240,
        color="#52C462",
        aliases=("curar", "curación", "curacion", "heal", "summonerheal"),
    ),
    "Barrier": SummonerSpellInfo(
        key="Barrier",
        name_es="Barrera",
        name_en="Barrier",
        short_tag="BAR",
        base_cooldown=180,
        color="#E6B84D",
        aliases=("barrera", "barrier", "summonerbarrier"),
    ),
    "Cleanse": SummonerSpellInfo(
        key="Cleanse",
        name_es="Purificar",
        name_en="Cleanse",
        short_tag="CLN",
        base_cooldown=240,
        color="#4FC3D9",
        aliases=("purificar", "limpiar", "cleanse", "summonerboost"),
    ),
    "Ghost": SummonerSpellInfo(
        key="Ghost",
        name_es="Fantasmal",
        name_en="Ghost",
        short_tag="GHOST",
        base_cooldown=240,
        color="#5DA9E9",
        aliases=("fantasmal", "fantasma", "ghost", "summonerhaste"),
    ),
    "Smite": SummonerSpellInfo(
        key="Smite",
        name_es="Aplastar",
        name_en="Smite",
        short_tag="SMITE",
        base_cooldown=90,
        color="#E08B3E",
        aliases=("aplastar", "castigo", "smite", "summonersmite", "aplastar desatado", "aplastar primitivo"),
    ),
}


def resolve_spell(raw_name: str) -> SummonerSpellInfo:
    """Identifica el hechizo de invocador a partir del nombre del cliente en cualquier idioma."""
    if not raw_name:
        return SUMMONER_SPELLS["Flash"]
    lowered = raw_name.strip().lower()
    for info in SUMMONER_SPELLS.values():
        for alias in info.aliases:
            if alias in lowered:
                return info
    return SUMMONER_SPELLS["Flash"]


def calculate_summoner_haste(
    has_cosmic_insight: bool = False,
    has_lucidity_boots: bool = False,
    has_crimson_lucidity: bool = False,
) -> int:
    """Suma el Summoner Spell Haste total del enemigo."""
    haste = 0
    if has_cosmic_insight:
        haste += COSMIC_INSIGHT_HASTE
    if has_crimson_lucidity:
        haste += CRIMSON_LUCIDITY_HASTE
    elif has_lucidity_boots:
        haste += LUCIDITY_BOOTS_HASTE
    return haste


def calculate_cooldown(
    base_cooldown: int,
    has_cosmic_insight: bool = False,
    has_lucidity_boots: bool = False,
    has_crimson_lucidity: bool = False,
) -> int:
    """
    Aplica la fórmula oficial de Riot Games para Summoner Spell Haste:
    Cooldown = Base * (100 / (100 + Haste))
    """
    haste = calculate_summoner_haste(
        has_cosmic_insight=has_cosmic_insight,
        has_lucidity_boots=has_lucidity_boots,
        has_crimson_lucidity=has_crimson_lucidity,
    )
    return round(base_cooldown * (100.0 / (100.0 + haste)))


def format_mmss(seconds: float, compact: bool = False) -> str:
    """Formatea segundos a 'MM:SS' o 'MMSS' (formato rápido de chat competitivo)."""
    total = max(0, int(round(seconds)))
    mins = total // 60
    secs = total % 60
    if compact:
        return f"{mins:02d}{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


@dataclass
class ChatPingParseResult:
    raw_text: str
    is_confirmed_use: bool
    champion_matched: Optional[str]
    spell_info: Optional[SummonerSpellInfo]
    reason: str
    timestamp_seconds: Optional[int] = None


# Patrones de verbos que el sistema de LoL inserta SOLO cuando el hechizo se usó en visión
# Ejemplo válido: "Skarner usó Destello" / "Skarner ha usado Destello" / "Skarner used Flash"
# Ejemplo rechazado (ping manual sin visión): "Skarner - Destello" / "Skarner Destello"
CONFIRMED_VERB_REGEX = re.compile(
    r"\b(?:us[oó]|ha\s+usado|utiliz[oó]|used|cast|gast[oó])\b",
    re.IGNORECASE,
)

TIMESTAMP_REGEX = re.compile(r"\[?(\d{1,2}):(\d{2})\]?")


def parse_lol_chat_ping(
    text: str,
    known_champions: list[str],
) -> ChatPingParseResult:
    """
    Analiza una línea del chat de League of Legends y distingue entre:
    1) Uso confirmado por el sistema: "Skarner usó Destello" / "Skarner used Flash"
    2) Ping normal sin confirmación: "Skarner - Destello" / "Skarner Flash"
    """
    cleaned = text.strip()
    if not cleaned:
        return ChatPingParseResult(
            raw_text=text,
            is_confirmed_use=False,
            champion_matched=None,
            spell_info=None,
            reason="Texto vacío.",
        )

    # Extraer timestamp si existe en el mensaje (ej. [12:45] o 12:45)
    ts_seconds: Optional[int] = None
    ts_match = TIMESTAMP_REGEX.search(cleaned)
    if ts_match:
        mins, secs = int(ts_match.group(1)), int(ts_match.group(2))
        ts_seconds = mins * 60 + secs

    # Detectar hechizo mencionado
    detected_spell: Optional[SummonerSpellInfo] = None
    lower_text = cleaned.lower()
    for info in SUMMONER_SPELLS.values():
        for alias in info.aliases:
            if len(alias) >= 2 and re.search(rf"\b{re.escape(alias)}\b", lower_text):
                detected_spell = info
                break
        if detected_spell:
            break

    # Detectar campeón entre la lista de enemigos (o cualquier nombre antes del verbo)
    matched_champ: Optional[str] = None
    for champ in known_champions:
        if champ and champ.lower() in lower_text:
            matched_champ = champ
            break

    # Verificar si contiene el verbo de confirmación del sistema ("usó" / "used")
    verb_match = CONFIRMED_VERB_REGEX.search(cleaned)

    if not detected_spell:
        return ChatPingParseResult(
            raw_text=text,
            is_confirmed_use=False,
            champion_matched=matched_champ,
            spell_info=None,
            reason="No se detectó ningún hechizo de invocador en la línea.",
            timestamp_seconds=ts_seconds,
        )

    if not verb_match:
        return ChatPingParseResult(
            raw_text=text,
            is_confirmed_use=False,
            champion_matched=matched_champ,
            spell_info=detected_spell,
            reason=(
                f"Ignorado: '{cleaned}' es un ping normal (falta el verbo 'usó'/'used' "
                f"que confirma que realmente gastó {detected_spell.name_es})."
            ),
            timestamp_seconds=ts_seconds,
        )

    # Si no estaba en la lista de 5 enemigos pero hay un nombre antes de "usó"/"used", extraerlo
    if not matched_champ:
        before_verb = cleaned[: verb_match.start()].strip()
        # Limpiar posibles prefijos de chat como "[12:30] [Equipo] Jugador (Leona): Skarner"
        if ":" in before_verb:
            before_verb = before_verb.split(":")[-1].strip()
        before_verb = re.sub(r"[\[\]\(\)\-]", " ", before_verb).strip()
        if before_verb:
            matched_champ = before_verb.split()[-1].capitalize()

    return ChatPingParseResult(
        raw_text=text,
        is_confirmed_use=True,
        champion_matched=matched_champ or "Enemigo",
        spell_info=detected_spell,
        reason=(
            f"Confirmado por el sistema: {matched_champ or 'Enemigo'} "
            f"usó {detected_spell.name_es} ({detected_spell.name_en})."
        ),
        timestamp_seconds=ts_seconds,
    )
