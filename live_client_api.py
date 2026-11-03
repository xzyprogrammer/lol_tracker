"""
Cliente para Riot Live Client Data API (127.0.0.1:2999), Spectator-V5 API
y Auto-Accept de Cola mediante LCU API local (LeagueClientUx.exe).
"""

from __future__ import annotations
import base64
from dataclasses import dataclass
from typing import Optional
import psutil
import urllib3
import requests

from summoner_data import (
    INSPIRATION_KEYWORDS,
    LUCIDITY_ITEM_IDS,
    CRIMSON_LUCIDITY_ITEM_IDS,
    SummonerSpellInfo,
    resolve_spell,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LIVE_CLIENT_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
COSMIC_INSIGHT_PERK_ID = 8347

ROLE_ORDER = {
    "TOP": 0,
    "JUNGLE": 1,
    "MIDDLE": 2,
    "MID": 2,
    "BOTTOM": 3,
    "BOT": 3,
    "ADC": 3,
    "UTILITY": 4,
    "SUPPORT": 4,
    "SUPP": 4,
}

ROLE_SHORT_NAMES = {
    "TOP": "TOP",
    "JUNGLE": "JG",
    "MIDDLE": "MID",
    "BOTTOM": "ADC",
    "UTILITY": "SUP",
}

PLATFORM_TO_AMERICAS = {"la1", "la2", "na1", "br1"}
PLATFORM_TO_EUROPE = {"euw1", "eun1", "tr1", "ru"}


@dataclass
class EnemyPlayerSnapshot:
    slot_index: int
    role_label: str
    champion_name: str
    summoner_name: str
    riot_id: str
    level: int
    spell1: SummonerSpellInfo
    spell2: SummonerSpellInfo
    has_inspiration_tree: bool
    has_lucidity_boots: bool
    has_crimson_lucidity: bool


@dataclass
class LiveMatchSnapshot:
    is_live_game: bool
    game_time_seconds: float
    game_mode: str
    active_riot_id: str
    enemies: list[EnemyPlayerSnapshot]


def _detect_inspiration_tree(runes_dict: dict) -> bool:
    if not runes_dict:
        return False
    for tree_key in ("primaryRuneTree", "secondaryRuneTree", "keystone"):
        tree = runes_dict.get(tree_key) or {}
        combined = " ".join(
            str(tree.get(field, "")).lower()
            for field in ("id", "displayName", "rawDisplayName")
        )
        if any(kw in combined for kw in INSPIRATION_KEYWORDS):
            return True
    return False


def _detect_boots_haste(items_list: list[dict]) -> tuple[bool, bool]:
    has_lucidity = False
    has_crimson = False
    for item in items_list or []:
        item_id = int(item.get("itemID", 0) or 0)
        if item_id in CRIMSON_LUCIDITY_ITEM_IDS:
            has_crimson = True
        elif item_id in LUCIDITY_ITEM_IDS:
            has_lucidity = True
    return has_lucidity, has_crimson


def fetch_live_match_snapshot(timeout: float = 0.45) -> Optional[LiveMatchSnapshot]:
    """Lee el estado en vivo de la partida desde 127.0.0.1:2999."""
    try:
        resp = requests.get(LIVE_CLIENT_URL, verify=False, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    if not isinstance(data, dict) or "allPlayers" not in data or "gameData" not in data:
        return None

    game_data = data.get("gameData") or {}
    game_time = float(game_data.get("gameTime", 0.0) or 0.0)
    game_mode = str(game_data.get("gameMode", "CLASSIC"))

    active_player = data.get("activePlayer") or {}
    active_name = (
        active_player.get("riotIdGameName")
        or active_player.get("summonerName")
        or ""
    ).strip().lower()
    active_riot_id = str(active_player.get("riotId") or "").strip()

    all_players = data.get("allPlayers") or []
    if not all_players:
        return None

    my_team = "ORDER"
    for p in all_players:
        p_name = (p.get("riotIdGameName") or p.get("summonerName") or "").strip().lower()
        p_riot_id = str(p.get("riotId") or "").strip().lower()
        if (active_name and p_name == active_name) or (
            active_riot_id and p_riot_id == active_riot_id.lower()
        ):
            my_team = p.get("team", "ORDER")
            if not active_riot_id and p.get("riotId"):
                active_riot_id = str(p.get("riotId"))
            break

    enemy_raw = [p for p in all_players if p.get("team") and p.get("team") != my_team]
    if not enemy_raw:
        enemy_raw = all_players[:5]

    fallback_roles = ["TOP", "JG", "MID", "ADC", "SUP"]

    def sort_key(idx_player: tuple[int, dict]) -> tuple[int, int]:
        idx, player = idx_player
        pos = str(player.get("position", "") or "").upper()
        return (ROLE_ORDER.get(pos, 99), idx)

    sorted_enemies = [p for _, p in sorted(enumerate(enemy_raw[:5]), key=sort_key)]

    enemies: list[EnemyPlayerSnapshot] = []
    for idx, p in enumerate(sorted_enemies):
        raw_pos = str(p.get("position", "") or "").upper()
        role_label = ROLE_SHORT_NAMES.get(raw_pos, fallback_roles[idx % 5])

        champ_name = str(p.get("championName") or f"Rival {idx + 1}")
        sum_name = str(p.get("riotIdGameName") or p.get("summonerName") or champ_name)
        riot_id = str(p.get("riotId") or "")
        level = max(1, int(p.get("level", 1) or 1))

        spells = p.get("summonerSpells") or {}
        s1_raw = (
            (spells.get("summonerSpellOne") or {}).get("rawDisplayName")
            or (spells.get("summonerSpellOne") or {}).get("displayName")
            or "SummonerFlash"
        )
        s2_raw = (
            (spells.get("summonerSpellTwo") or {}).get("rawDisplayName")
            or (spells.get("summonerSpellTwo") or {}).get("displayName")
            or "SummonerTeleport"
        )
        spell1 = resolve_spell(s1_raw)
        spell2 = resolve_spell(s2_raw)

        if spell2.key == "Flash" and spell1.key != "Flash":
            spell1, spell2 = spell2, spell1

        has_insp = _detect_inspiration_tree(p.get("runes") or {})
        has_lucidity, has_crimson = _detect_boots_haste(p.get("items") or [])

        enemies.append(
            EnemyPlayerSnapshot(
                slot_index=idx,
                role_label=role_label,
                champion_name=champ_name,
                summoner_name=sum_name,
                riot_id=riot_id,
                level=level,
                spell1=spell1,
                spell2=spell2,
                has_inspiration_tree=has_insp,
                has_lucidity_boots=has_lucidity,
                has_crimson_lucidity=has_crimson,
            )
        )

    return LiveMatchSnapshot(
        is_live_game=True,
        game_time_seconds=game_time,
        game_mode=game_mode,
        active_riot_id=active_riot_id,
        enemies=enemies,
    )


def fetch_spectator_v5_cosmic_insight(
    riot_api_key: str,
    platform: str,
    active_riot_id: str,
    timeout: float = 2.5,
) -> Optional[dict[str, bool]]:
    api_key = (riot_api_key or "").strip()
    if not api_key or "#" not in active_riot_id:
        return None

    plat = platform.strip().lower()
    if plat in PLATFORM_TO_AMERICAS:
        regional = "americas"
    elif plat in PLATFORM_TO_EUROPE:
        regional = "europe"
    else:
        regional = "americas"

    game_name, tag_line = active_riot_id.split("#", 1)
    headers = {"X-Riot-Token": api_key}

    try:
        acc_url = (
            f"https://{regional}.api.riotgames.com/riot/account/v1/accounts/"
            f"by-riot-id/{requests.utils.quote(game_name)}/{requests.utils.quote(tag_line)}"
        )
        r_acc = requests.get(acc_url, headers=headers, timeout=timeout)
        if r_acc.status_code != 200:
            return None
        puuid = r_acc.json().get("puuid")
        if not puuid:
            return None

        spec_url = (
            f"https://{plat}.api.riotgames.com/lol/spectator/v5/active-games/"
            f"by-summoner/{puuid}"
        )
        r_spec = requests.get(spec_url, headers=headers, timeout=timeout)
        if r_spec.status_code != 200:
            return None
        spec_data = r_spec.json()
        participants = spec_data.get("participants") or []
        result_by_riot_id: dict[str, bool] = {}
        for part in participants:
            p_riot_id = str(part.get("riotId") or "").strip().lower()
            perks = (part.get("perks") or {}).get("perkIds") or []
            has_cosmic = COSMIC_INSIGHT_PERK_ID in perks
            if p_riot_id:
                result_by_riot_id[p_riot_id] = has_cosmic
        return result_by_riot_id
    except Exception:
        return None


def try_lcu_auto_accept_ready_check() -> bool:
    """
    Si el cliente de lobby de League of Legends (LeagueClientUx.exe) tiene una partida
    encontrada en cola ('ReadyCheck' InProgress), la acepta automáticamente mediante
    el endpoint local LCU (/lol-matchmaking/v1/ready-check/accept).
    """
    try:
        port = None
        token = None
        for proc in psutil.process_iter(["name", "cmdline"]):
            if (proc.info.get("name") or "").lower() == "leagueclientux.exe":
                for arg in proc.info.get("cmdline") or []:
                    if arg.startswith("--app-port="):
                        port = arg.split("=", 1)[1]
                    elif arg.startswith("--remoting-auth-token="):
                        token = arg.split("=", 1)[1]
                break
        if not port or not token:
            return False

        auth = base64.b64encode(f"riot:{token}".encode("utf-8")).decode("ascii")
        headers = {"Authorization": f"Basic {auth}"}
        base_url = f"https://127.0.0.1:{port}"

        rc = requests.get(
            f"{base_url}/lol-matchmaking/v1/ready-check",
            headers=headers,
            verify=False,
            timeout=0.4,
        )
        if rc.status_code == 200:
            info = rc.json()
            if (
                info.get("state") == "InProgress"
                and info.get("playerResponse") in ("None", None, "")
            ):
                requests.post(
                    f"{base_url}/lol-matchmaking/v1/ready-check/accept",
                    headers=headers,
                    verify=False,
                    timeout=0.4,
                )
                return True
    except Exception:
        pass
    return False
