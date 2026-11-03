"""
Descarga y caché local de iconos oficiales de Riot Data Dragon:
- Retratos de campeones con badge de nivel estilo Espectador.
- Iconos de Summoner Spells con contador regresivo encima del hechizo.
- Iconos y tiempos reales de Ultimates (R) por nivel (Niveles 6/11/16) y nombres en inglés/español.
"""

from __future__ import annotations
from dataclasses import dataclass
import io
import json
import os
import re
import threading
from typing import Optional
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".lol_scrim_tracker_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

SPELL_DDRAGON_IMAGE: dict[str, str] = {
    "Flash": "SummonerFlash.png",
    "Teleport": "SummonerTeleport.png",
    "Ignite": "SummonerDot.png",
    "Exhaust": "SummonerExhaust.png",
    "Heal": "SummonerHeal.png",
    "Barrier": "SummonerBarrier.png",
    "Cleanse": "SummonerBoost.png",
    "Ghost": "SummonerHaste.png",
    "Smite": "SummonerSmite.png",
}

SPECIAL_CHAMP_MAP: dict[str, str] = {
    "wukong": "MonkeyKing",
    "monkeyking": "MonkeyKing",
    "k'sante": "KSante",
    "ksante": "KSante",
    "kai'sa": "Kaisa",
    "kaisa": "Kaisa",
    "kha'zix": "Khazix",
    "khazix": "Khazix",
    "cho'gath": "Chogath",
    "chogath": "Chogath",
    "vel'koz": "Velkoz",
    "velkoz": "Velkoz",
    "bel'veth": "Belveth",
    "belveth": "Belveth",
    "rek'sai": "RekSai",
    "reksai": "RekSai",
    "kog'maw": "KogMaw",
    "kogmaw": "KogMaw",
    "leblanc": "Leblanc",
    "nunu & willump": "Nunu",
    "nunu": "Nunu",
    "renata glasc": "Renata",
    "renata": "Renata",
    "dr. mundo": "DrMundo",
    "drmundo": "DrMundo",
    "master yi": "MasterYi",
    "masteryi": "MasterYi",
    "miss fortune": "MissFortune",
    "missfortune": "MissFortune",
    "tahm kench": "TahmKench",
    "tahmkench": "TahmKench",
    "twisted fate": "TwistedFate",
    "twistedfate": "TwistedFate",
    "xin zhao": "XinZhao",
    "xinzhao": "XinZhao",
    "jarvan iv": "JarvanIV",
    "jarvaniv": "JarvanIV",
    "aurelion sol": "AurelionSol",
    "aurelionsol": "AurelionSol",
    "lee sin": "LeeSin",
    "leesin": "LeeSin",
}


@dataclass
class ChampUltimateInfo:
    champ_id: str
    spell_id: str
    name_en: str
    name_es: str
    image_full: str
    cooldowns: tuple[int, int, int]  # (Rank 1 @ lvl 6, Rank 2 @ lvl 11, Rank 3 @ lvl 16)

    def cooldown_for_level(self, level: int, ability_haste: int = 0) -> int:
        if level >= 16:
            base = max(15, self.cooldowns[2])
        elif level >= 11:
            base = max(15, self.cooldowns[1])
        else:
            base = max(15, self.cooldowns[0])
        if ability_haste > 0:
            return max(10, round(base * (100.0 / (100.0 + ability_haste))))
        return base


# Diccionario inicial con los valores de Aceleración de Habilidad (AH) más comunes
# para que funcione de inmediato incluso sin conexión a internet.
DEFAULT_ITEM_HASTE: dict[int, int] = {
    3158: 10,   # Botas Jonias de la Lucidez (+10 AH)
    3108: 10,   # Códice Diabólico (+10 AH)
    3067: 10,   # Gema Viva (+10 AH)
    3802: 10,   # Capítulo Perdido (+10 AH)
    3118: 35,   # Malignidad (+15 AH + 20 Ultimate AH)
    3100: 10,   # Perdición del Liche (+10 AH)
    4629: 25,   # Impulso Cósmico (+25 AH)
    3071: 20,   # Cuchilla Negra (+20 AH)
    3152: 15,   # Cinturón Cohete Hextech (+15 AH)
    3137: 15,   # Criptoflorecencia (+15 AH)
    6655: 20,   # Compañero de Luden (+20 AH)
    3078: 20,   # Fuerza de la Trinidad (+20 AH)
    3110: 20,   # Corazón de Hielo (+20 AH)
    3065: 10,   # Rostro Espiritual (+10 AH)
    3165: 15,   # Morellonomicon (+15 AH)
    3115: 15,   # Diente de Nashor (+15 AH)
    3004: 15,   # Manamune (+15 AH)
    3003: 20,   # Bastón del Arcángel (+20 AH)
    6692: 15,   # Eclipse (+15 AH)
    6694: 15,   # Rencor de Serylda (+15 AH)
    6696: 20,   # Arco Axiomático (+20 AH)
    6698: 20,   # Hidra Profana (+20 AH)
    6699: 15,   # Soberbia (+15 AH)
    3074: 20,   # Hidra Voraz (+20 AH)
    3133: 10,   # Martillo de Guerra de Caulfield (+10 AH)
}


class AssetManager:
    def __init__(self) -> None:
        self.version: str = "16.21.1"
        self.champ_name_to_id: dict[str, str] = dict(SPECIAL_CHAMP_MAP)
        self.ult_info_by_cid: dict[str, ChampUltimateInfo] = {}
        self.item_haste_by_id: dict[int, int] = dict(DEFAULT_ITEM_HASTE)
        self._fetching_cids: set[str] = set()
        self._mem_cache: dict[str, Image.Image] = {}
        self._lock = threading.Lock()
        self._init_done = False
        threading.Thread(target=self._init_ddragon_metadata, daemon=True).start()

    def get_items_ability_haste(self, items: list[dict]) -> int:
        """Suma la Aceleración de Habilidad (Ability Haste) total de los objetos del jugador."""
        total = 0
        with self._lock:
            for it in items or []:
                try:
                    iid = int(it.get("itemID", 0) or 0)
                    total += self.item_haste_by_id.get(iid, 0)
                except Exception:
                    pass
        return total

    def _init_ddragon_metadata(self) -> None:
        try:
            r = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=3.0)
            if r.status_code == 200:
                versions = r.json()
                if versions and isinstance(versions, list):
                    self.version = str(versions[0])

            # 1. Nombres e IDs de campeones
            c_url = f"https://ddragon.leagueoflegends.com/cdn/{self.version}/data/en_US/champion.json"
            rc = requests.get(c_url, timeout=4.0)
            if rc.status_code == 200:
                cdata = rc.json().get("data") or {}
                with self._lock:
                    for cid, info in cdata.items():
                        name_low = str(info.get("name", "")).lower()
                        id_low = str(cid).lower()
                        clean_low = re.sub(r"[^a-z]", "", name_low)
                        self.champ_name_to_id[name_low] = cid
                        self.champ_name_to_id[id_low] = cid
                        self.champ_name_to_id[clean_low] = cid

            # 2. Descargar datos de objetos para extraer Ability Haste y Ultimate Haste de todos
            i_url = f"https://ddragon.leagueoflegends.com/cdn/{self.version}/data/en_US/item.json"
            ri = requests.get(i_url, timeout=4.0)
            if ri.status_code == 200:
                idata = ri.json().get("data") or {}
                with self._lock:
                    for iid_str, it_info in idata.items():
                        try:
                            iid = int(iid_str)
                            desc = str(it_info.get("description", ""))
                            h_match = re.search(
                                r"<attention>(\d+)</attention>\s*(?:Ability Haste|Aceleraci[oó]n de Habilidad)",
                                desc,
                                re.I,
                            )
                            u_match = re.search(
                                r"<attention>(\d+)</attention>\s*(?:Ultimate Haste|Habilidad Definitiva)",
                                desc,
                                re.I,
                            )
                            haste = 0
                            if h_match:
                                haste += int(h_match.group(1))
                            if u_match:
                                haste += int(u_match.group(1))
                            if haste > 0:
                                self.item_haste_by_id[iid] = haste
                        except Exception:
                            pass

            for sp_key in SPELL_DDRAGON_IMAGE:
                self.get_spell_base_image(sp_key, size=(28, 28))
        except Exception:
            pass
        finally:
            self._init_done = True

    def resolve_champ_ddragon_id(self, champ_name: str) -> str:
        if not champ_name:
            return "Corki"
        low = champ_name.strip().lower()
        clean = re.sub(r"[^a-z]", "", low)
        with self._lock:
            if low in self.champ_name_to_id:
                return self.champ_name_to_id[low]
            if clean in self.champ_name_to_id:
                return self.champ_name_to_id[clean]
        parts = re.findall(r"[A-Za-z]+", champ_name)
        return "".join(p.capitalize() for p in parts) or "Corki"

    def get_ultimate_info(self, champ_name: str) -> ChampUltimateInfo:
        """
        Devuelve la información del Ultimate (R) del campeón (enfriamientos exactos por nivel 6/11/16,
        nombres en inglés/español e icono). Si aún no está en caché, intenta un fetch rápido síncrono
        antes de devolver un valor por defecto.
        """
        cid = self.resolve_champ_ddragon_id(champ_name)
        with self._lock:
            if cid in self.ult_info_by_cid:
                return self.ult_info_by_cid[cid]

        # Revisar caché en disco
        json_cache_path = os.path.join(CACHE_DIR, f"ult_{cid}.json")
        if os.path.exists(json_cache_path):
            try:
                with open(json_cache_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                info = ChampUltimateInfo(
                    champ_id=cid,
                    spell_id=d["spell_id"],
                    name_en=d["name_en"],
                    name_es=d["name_es"],
                    image_full=d["image_full"],
                    cooldowns=tuple(d["cooldowns"]),  # type: ignore
                )
                with self._lock:
                    self.ult_info_by_cid[cid] = info
                return info
            except Exception:
                pass

        # Intentar fetch síncrono rápido (timeout bajo) para no usar fallback genérico
        try:
            url_en = f"https://ddragon.leagueoflegends.com/cdn/{self.version}/data/en_US/champion/{cid}.json"
            r_en = requests.get(url_en, timeout=1.5)
            if r_en.status_code == 200:
                c_en = r_en.json().get("data", {}).get(cid, {})
                spells_en = c_en.get("spells") or []
                if len(spells_en) >= 4:
                    ult_en = spells_en[3]
                    raw_cds = ult_en.get("cooldown") or [100, 85, 70]
                    if len(raw_cds) == 1:
                        cds = (int(raw_cds[0]), int(raw_cds[0]), int(raw_cds[0]))
                    elif len(raw_cds) == 2:
                        cds = (int(raw_cds[0]), int(raw_cds[1]), int(raw_cds[1]))
                    else:
                        cds = (int(raw_cds[0]), int(raw_cds[1]), int(raw_cds[2]))
                    name_en = str(ult_en.get("name") or "R")
                    img_full = str((ult_en.get("image") or {}).get("full") or "")
                    spell_id = str(ult_en.get("id") or f"{cid}R")
                    name_es = name_en
                    try:
                        url_es = f"https://ddragon.leagueoflegends.com/cdn/{self.version}/data/es_MX/champion/{cid}.json"
                        r_es = requests.get(url_es, timeout=1.0)
                        if r_es.status_code == 200:
                            spells_es = (r_es.json().get("data", {}).get(cid, {}).get("spells") or [])
                            if len(spells_es) >= 4:
                                name_es = str(spells_es[3].get("name") or name_en)
                    except Exception:
                        pass
                    info = ChampUltimateInfo(
                        champ_id=cid, spell_id=spell_id, name_en=name_en,
                        name_es=name_es, image_full=img_full, cooldowns=cds,
                    )
                    # Guardar en caché de disco y memoria
                    try:
                        with open(json_cache_path, "w", encoding="utf-8") as f:
                            json.dump({"spell_id": spell_id, "name_en": name_en,
                                       "name_es": name_es, "image_full": img_full,
                                       "cooldowns": list(cds)}, f)
                    except Exception:
                        pass
                    with self._lock:
                        self.ult_info_by_cid[cid] = info
                    return info
        except Exception:
            pass

        # Si ni el disco ni la red funcionaron, lanzar fetch en segundo plano y devolver fallback
        with self._lock:
            if cid not in self._fetching_cids:
                self._fetching_cids.add(cid)
                threading.Thread(target=self._fetch_ult_metadata_bg, args=(cid,), daemon=True).start()

        return ChampUltimateInfo(
            champ_id=cid,
            spell_id=f"{cid}R",
            name_en="R",
            name_es="R",
            image_full="",
            cooldowns=(100, 85, 70),
        )

    def _fetch_ult_metadata_bg(self, cid: str) -> None:
        try:
            url_en = f"https://ddragon.leagueoflegends.com/cdn/{self.version}/data/en_US/champion/{cid}.json"
            r_en = requests.get(url_en, timeout=3.5)
            if r_en.status_code != 200:
                return
            c_en = r_en.json().get("data", {}).get(cid, {})
            spells_en = c_en.get("spells") or []
            if len(spells_en) < 4:
                return
            ult_en = spells_en[3]
            raw_cds = ult_en.get("cooldown") or [100, 85, 70]
            if len(raw_cds) == 1:
                cds = (int(raw_cds[0]), int(raw_cds[0]), int(raw_cds[0]))
            elif len(raw_cds) == 2:
                cds = (int(raw_cds[0]), int(raw_cds[1]), int(raw_cds[1]))
            else:
                cds = (int(raw_cds[0]), int(raw_cds[1]), int(raw_cds[2]))

            name_en = str(ult_en.get("name") or "R")
            img_full = str((ult_en.get("image") or {}).get("full") or "")
            spell_id = str(ult_en.get("id") or f"{cid}R")

            # Intentar obtener también el nombre en español para el detector de chat
            name_es = name_en
            try:
                url_es = f"https://ddragon.leagueoflegends.com/cdn/{self.version}/data/es_MX/champion/{cid}.json"
                r_es = requests.get(url_es, timeout=3.0)
                if r_es.status_code == 200:
                    spells_es = (r_es.json().get("data", {}).get(cid, {}).get("spells") or [])
                    if len(spells_es) >= 4:
                        name_es = str(spells_es[3].get("name") or name_en)
            except Exception:
                pass

            info = ChampUltimateInfo(
                champ_id=cid,
                spell_id=spell_id,
                name_en=name_en,
                name_es=name_es,
                image_full=img_full,
                cooldowns=cds,
            )
            json_cache_path = os.path.join(CACHE_DIR, f"ult_{cid}.json")
            with open(json_cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "spell_id": spell_id,
                        "name_en": name_en,
                        "name_es": name_es,
                        "image_full": img_full,
                        "cooldowns": list(cds),
                    },
                    f,
                )
            with self._lock:
                self.ult_info_by_cid[cid] = info
        except Exception:
            pass
        finally:
            with self._lock:
                self._fetching_cids.discard(cid)

    def _load_or_fetch_image(self, cache_filename: str, url: str) -> Optional[Image.Image]:
        local_path = os.path.join(CACHE_DIR, cache_filename)
        if os.path.exists(local_path):
            try:
                return Image.open(local_path).convert("RGBA")
            except Exception:
                pass
        try:
            resp = requests.get(url, timeout=2.5)
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                img.save(local_path, format="PNG")
                return img
        except Exception:
            pass
        return None

    def get_champion_spectator_portrait(
        self, champ_name: str, level: int = 1, size: tuple[int, int] = (36, 36)
    ) -> Image.Image:
        """Retrato compacto del campeón estilo Espectador con el nivel en la esquina."""
        cid = self.resolve_champ_ddragon_id(champ_name)
        base_key = f"champ_raw_{cid}_{size[0]}x{size[1]}"
        with self._lock:
            base = self._mem_cache.get(base_key)

        if base is None:
            url = f"https://ddragon.leagueoflegends.com/cdn/{self.version}/img/champion/{cid}.png"
            raw = self._load_or_fetch_image(f"champ_{cid}.png", url)
            if raw is None:
                raw = Image.new("RGBA", size, (28, 36, 52, 255))
            base = raw.resize(size, Image.Resampling.LANCZOS)
            with self._lock:
                self._mem_cache[base_key] = base

        out = base.copy()
        w, h = size
        draw = ImageDraw.Draw(out, "RGBA")
        draw.rectangle([0, 0, w - 1, h - 1], outline=(185, 155, 85, 255), width=1)

        # Cajita de nivel en esquina inferior derecha
        lvl_str = str(max(1, min(18, level)))
        draw.rectangle([w - 15, h - 13, w - 1, h - 1], fill=(10, 14, 20, 235), outline=(185, 155, 85, 220))
        try:
            fnt = ImageFont.truetype("arialbd.ttf", 9)
        except Exception:
            fnt = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), lvl_str, font=fnt)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((w - 8 - tw / 2, h - 7 - th / 2 - 1), lvl_str, font=fnt, fill=(247, 250, 252, 255))
        return out

    def get_spell_base_image(self, spell_key: str, size: tuple[int, int] = (28, 28)) -> Image.Image:
        cache_key = f"spell_{spell_key}_{size[0]}x{size[1]}"
        with self._lock:
            if cache_key in self._mem_cache:
                return self._mem_cache[cache_key]

        fname = SPELL_DDRAGON_IMAGE.get(spell_key, "SummonerFlash.png")
        url = f"https://ddragon.leagueoflegends.com/cdn/{self.version}/img/spell/{fname}"
        base = self._load_or_fetch_image(f"spell_{fname}", url)
        if base is None:
            base = Image.new("RGBA", size, (45, 55, 72, 255))
        resized = base.resize(size, Image.Resampling.LANCZOS)
        with self._lock:
            self._mem_cache[cache_key] = resized
        return resized

    def get_ultimate_base_image(self, champ_name: str, size: tuple[int, int] = (28, 28)) -> Image.Image:
        ult_info = self.get_ultimate_info(champ_name)
        cache_key = f"ult_img_{ult_info.champ_id}_{size[0]}x{size[1]}"
        with self._lock:
            if cache_key in self._mem_cache and ult_info.image_full:
                return self._mem_cache[cache_key]

        if ult_info.image_full:
            url = f"https://ddragon.leagueoflegends.com/cdn/{self.version}/img/spell/{ult_info.image_full}"
            raw = self._load_or_fetch_image(f"ult_{ult_info.image_full}", url)
            if raw is not None:
                resized = raw.resize(size, Image.Resampling.LANCZOS)
                with self._lock:
                    self._mem_cache[cache_key] = resized
                return resized

        # Fallback elegante con letra "R" dorada mientras descarga
        fallback = Image.new("RGBA", size, (25, 32, 48, 255))
        draw = ImageDraw.Draw(fallback)
        try:
            fnt = ImageFont.truetype("arialbd.ttf", 13)
        except Exception:
            fnt = ImageFont.load_default()
        draw.text((size[0] / 2 - 5, size[1] / 2 - 7), "R", font=fnt, fill=(246, 224, 94, 255))
        return fallback

    def _draw_cooldown_overlay(
        self,
        base: Image.Image,
        remaining_seconds: float,
        total_cooldown: int,
        ready_border_color: tuple[int, int, int, int],
    ) -> Image.Image:
        w, h = base.size
        if remaining_seconds <= 0:
            out = base.copy()
            draw = ImageDraw.Draw(out)
            draw.rectangle([0, 0, w - 1, h - 1], outline=ready_border_color, width=1)
            return out

        darkened = ImageEnhance.Brightness(base).enhance(0.26)
        draw = ImageDraw.Draw(darkened, "RGBA")

        # Barra inferior de progreso
        frac = min(1.0, max(0.0, 1.0 - (remaining_seconds / max(1, total_cooldown))))
        bar_w = int((w - 2) * frac)
        draw.rectangle([1, h - 4, w - 1, h - 1], fill=(15, 15, 15, 210))
        if bar_w > 0:
            draw.rectangle([1, h - 4, 1 + bar_w, h - 1], fill=(246, 173, 85, 240))

        draw.rectangle([0, 0, w - 1, h - 1], outline=(229, 62, 62, 255), width=1)

        rem_int = int(round(remaining_seconds))
        text_str = f"{rem_int}"
        font_size = 11 if rem_int >= 100 else 12

        try:
            fnt = ImageFont.truetype("arialbd.ttf", font_size)
        except Exception:
            fnt = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text_str, font=fnt)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (w - tw) / 2
        ty = (h - th) / 2 - 2

        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            draw.text((tx + dx, ty + dy), text_str, font=fnt, fill=(0, 0, 0, 255))

        text_color = (255, 235, 90, 255) if rem_int <= 15 else (255, 255, 255, 255)
        draw.text((tx, ty), text_str, font=fnt, fill=text_color)
        return darkened

    def render_spell_icon(
        self,
        spell_key: str,
        remaining_seconds: float,
        total_cooldown: int,
        size: tuple[int, int] = (28, 28),
    ) -> Image.Image:
        base = self.get_spell_base_image(spell_key, size=size)
        return self._draw_cooldown_overlay(
            base, remaining_seconds, total_cooldown, ready_border_color=(72, 187, 120, 255)
        )

    def render_ultimate_icon(
        self,
        champ_name: str,
        remaining_seconds: float,
        total_cooldown: int,
        size: tuple[int, int] = (28, 28),
    ) -> Image.Image:
        base = self.get_ultimate_base_image(champ_name, size=size)
        return self._draw_cooldown_overlay(
            base, remaining_seconds, total_cooldown, ready_border_color=(236, 201, 75, 255)
        )
