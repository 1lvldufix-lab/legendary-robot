"""Сверка карточки со страницей RenderZ (renderz.app) — только по ссылке от пользователя.

Правила сайта: robots.txt запрещает /api/*, ToS запрещает перепубликацию данных. Поэтому:
никакого поиска и обхода — одна страница /player/<id>-<slug> по действию пользователя,
из неё берём имя/OVR/позицию/сборную только для сравнения; кэш 24 ч, у карточки остаются
лишь ссылка, id и итог сверки.
"""
import json
import logging
import re
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html import unescape
from urllib.parse import urlsplit

import db as appdb

log = logging.getLogger("bot.renderz")

HOSTS = {"renderz.app", "www.renderz.app", "fifarenderz.com", "www.fifarenderz.com"}
USER_AGENT = "KurilkaSigarkiBot/1.0 (FC Mobile tournament; single card check on user request)"
TIMEOUT = 10
MAX_BYTES = 3 * 1024 * 1024
CACHE_HOURS = 24
NAME_OK = 0.85

_PATH_RE = re.compile(r"^/player/(\d{1,12})(?:-([a-z0-9-]{1,80}))?/?$", re.I)
_OG_TITLE_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', re.I)
_TITLE_RE = re.compile(r"^(.+?)\s+[—–-]\s+(\d{2,3})\s+OVR", re.I)
_LDJSON_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.I | re.S)


class RenderzError(Exception):
    pass


def parse_url(url: str) -> tuple[str, int]:
    """Ссылка пользователя → (канонический https://renderz.app/player/<id>-<slug>, id)."""
    raw = (url or "").strip()
    if not raw:
        raise RenderzError("Вставь ссылку на карточку RenderZ")
    if len(raw) > 300:
        raise RenderzError("Слишком длинная ссылка")
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise RenderzError("Не похоже на ссылку")
    if parts.scheme != "https":
        raise RenderzError("Нужна ссылка https://renderz.app/player/…")
    host = (parts.hostname or "").lower()
    if host not in HOSTS or parts.username or parts.password or parts.port not in (None, 443):
        raise RenderzError("Принимаются только ссылки renderz.app")
    m = _PATH_RE.match(parts.path or "")
    if not m:
        raise RenderzError("Нужна ссылка на карточку игрока: https://renderz.app/player/<id>-<имя>")
    rid, slug = int(m.group(1)), (m.group(2) or "").lower()
    return f"https://renderz.app/player/{rid}" + (f"-{slug}" if slug else ""), rid


def _fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            final = urlsplit(r.geturl())
            # редирект не должен увести с сайта или в запрещённый /api
            if (final.hostname or "").lower() not in HOSTS or final.path.startswith("/api"):
                raise RenderzError("RenderZ перенаправил на чужую страницу")
            return r.read(MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RenderzError("Карточка на RenderZ не найдена (404)")
        raise RenderzError(f"RenderZ ответил ошибкой {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RenderzError(f"RenderZ недоступен: {getattr(e, 'reason', e)}")


def parse_html(html: str, renderz_id: int | None = None) -> dict:
    """Страница карточки → {name, rating, position, nation, aliases}. Бросает RenderzError."""
    out = {"name": None, "rating": None, "position": None, "nation": None, "aliases": []}
    m = _OG_TITLE_RE.search(html)
    if m:
        t = _TITLE_RE.match(unescape(m.group(1)).strip())
        if t:
            out["name"], out["rating"] = t.group(1).strip(), int(t.group(2))
    for block in _LDJSON_RE.findall(html):
        try:
            data = json.loads(unescape(block) if "&quot;" in block else block)
        except ValueError:
            continue
        items = data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "Person":
                out["name"] = it.get("name") or out["name"]
                nat = it.get("nationality")
                if isinstance(nat, dict):
                    out["nation"] = nat.get("name")
                out["aliases"] += [x for x in (it.get("givenName"), it.get("familyName")) if x]
    # данные SvelteKit: id:<id>,cardName:"…",firstName:"…",lastName:"…",commonName:"…",rating:N,position:"RW"
    # порядок ключей у них может меняться — берём окно после id:<id>, и ищем ключи внутри
    pat = (rf"(?<![A-Za-z])id:{int(renderz_id)}," if renderz_id else r"(?<![A-Za-z])id:\d+,") + r'(?=.{0,200}cardName:)(.{0,1500})'
    sm = re.search(pat, html, re.S)
    if sm:
        tail = sm.group(1)
        nxt = re.search(r"[{,]id:\d+,", tail)  # не залезать в данные соседней карточки
        if nxt:
            tail = tail[:nxt.start()]
        cn = re.search(r'cardName:"([^"]*)"', tail)
        if cn:
            out["aliases"].append(cn.group(1))
        for key in ("firstName", "lastName", "commonName"):
            km = re.search(rf'{key}:"([^"]*)"', tail)
            if km and km.group(1):
                out["aliases"].append(km.group(1))
        rm = re.search(r"rating:(\d{2,3})", tail)
        pm = re.search(r'position:"([A-Z]{2,3})"', tail)
        if rm and out["rating"] is None:
            out["rating"] = int(rm.group(1))
        if pm:
            out["position"] = pm.group(1)
    out["aliases"] = [a for a in dict.fromkeys(a.strip() for a in out["aliases"]) if a]
    if not out["name"] or out["rating"] is None:
        raise RenderzError("Не удалось прочитать карточку на странице RenderZ")
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def lookup(url: str, use_cache: bool = True, fetch=None) -> dict:
    """Проверка ссылки: кэш 24 ч → одна страница. → {name, rating, position, nation, url, renderz_id, aliases}."""
    canon, rid = parse_url(url)
    c = appdb.db()
    try:
        if use_cache:
            row = c.execute("SELECT data, fetched_at FROM renderz_cache WHERE url=?", (canon,)).fetchone()
            if row:
                try:
                    at = datetime.strptime(str(row["fetched_at"])[:19], "%Y-%m-%d %H:%M:%S")
                    if _now() - at < timedelta(hours=CACHE_HOURS):
                        return {**json.loads(row["data"]), "url": canon, "renderz_id": rid}
                except (TypeError, ValueError):
                    pass
        info = parse_html((fetch or _fetch_html)(canon), rid)
        c.execute("DELETE FROM renderz_cache WHERE url=? OR fetched_at<?",
                  (canon, (_now() - timedelta(hours=CACHE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")))
        c.execute("INSERT INTO renderz_cache (url, renderz_id, data, fetched_at) VALUES (?,?,?,?)",
                  (canon, rid, json.dumps(info, ensure_ascii=False), _now().strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
        return {**info, "url": canon, "renderz_id": rid}
    finally:
        c.close()


# ===== сравнение =====

_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s",
                 "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def norm_name(s: str | None) -> str:
    """«Kylian Mbappé» → «kylianmbappe», «Мбаппе» → «mbappe» (диакритика и кириллица)."""
    s = unicodedata.normalize("NFKD", (s or "").casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = "".join(_CYR.get(ch, ch) for ch in s)
    return re.sub(r"[^0-9a-z]", "", s)


def _tokens(s: str | None) -> list[str]:
    return [norm_name(t) for t in re.split(r"[\s.\-']+", s or "") if norm_name(t)]


def name_similarity(card_name: str | None, rz: dict) -> float:
    mine = norm_name(card_name)
    if not mine:
        return 0.0
    card_toks = _tokens(card_name)
    cands = [rz.get("name")] + list(rz.get("aliases") or [])
    best = 0.0
    for cand in cands:
        n = norm_name(cand)
        if not n:
            continue
        best = max(best, 1.0 if n == mine else SequenceMatcher(None, mine, n).ratio())
        toks = _tokens(cand)
        # «Mbappe» против «Kylian Mbappé»: имя карточки — целое слово полного имени
        if len(mine) >= 4 and mine in toks:
            best = 1.0
        # «G. Bale» против «Gareth Bale»: фамилия совпала целиком
        elif len(toks) > 1 and len(toks[-1]) >= 4 and toks[-1] in card_toks:
            best = max(best, 0.95)
    return best


def compare(fields: dict, rz: dict) -> dict:
    """Карточка против RenderZ → {name, rating, position, all, name_score}."""
    from cards import normalize_position, parse_positions
    pos = normalize_position(fields.get("position"))
    alts = parse_positions(fields.get("alt_positions"))
    score = name_similarity(fields.get("name"), rz)
    try:
        rating_ok = rz.get("rating") is not None and int(fields.get("rating")) == int(rz["rating"])
    except (TypeError, ValueError):
        rating_ok = False
    rz_pos = normalize_position(rz.get("position"))
    # позиция не прочиталась со страницы → «неизвестно» (None), а не расхождение; авто-одобрения нет
    pos_ok = None if not rz_pos else (rz_pos == pos or rz_pos in alts)
    res = {"name": score >= NAME_OK, "rating": rating_ok, "position": pos_ok, "name_score": round(score, 3)}
    res["all"] = bool(res["name"] and res["rating"] and pos_ok)
    res["partial"] = bool(res["name"] and res["rating"] and pos_ok is None)
    return res


def public(rz: dict) -> dict:
    return {k: rz.get(k) for k in ("name", "rating", "position", "nation", "url", "renderz_id")}
