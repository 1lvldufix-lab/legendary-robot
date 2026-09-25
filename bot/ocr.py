"""OCR-каскад результатов FC27 Mobile (план 06).

Каскад провайдеров: OpenRouter VL (несколько free-моделей) → Groq → Gemini →
NIM → OCR.space → tesseract. Провайдер без ключа в .env пропускается.
Счёт берём ТОЛЬКО из шапки; голы — дедупом по (имя, минута) со всех скринов;
пенальти — в скобках у счёта, отдельным полем (кейс 120:00 обязателен).

JSON-схема ответа и правила — план 06. Финализация без подтверждения — план 09.
"""
import base64
import json
import logging
import re
import shutil
import subprocess
import tempfile
import urllib.request

import config

log = logging.getLogger("bot.ocr")

# ===== промпт (план 06, переработка елобота под FC27 Mobile) =====

SYSTEM_PROMPT = """Ты разбираешь скриншоты FC Mobile (FC27) — экран итоговой статистики матча и экран голов.

На входе 1–3 скрина одного матча (таблица прокручиваемая — видны не все строки!).

Два варианта шапки:
A) карточки игроков (ник + лига) слева/справа, счёт крупно «2 | 3», под ним таймер «90:00»;
Б) карточки с эмблемами, счёт «3 - 1», таймер под счётом.

Правила:
1. Счёт (score_home/score_away) — ТОЛЬКО крупные цифры шапки, НИКОГДА не сумма голов из таблицы.
2. Овертайм: таймер «120:00», рядом со счётом пенальти в скобках «(5) 3 (4)» → penalties {"home":5,"away":4}. Пенальти НЕ входят в счёт.
3. Голы/ассисты собираешь со ВСЕХ скринов пачки. Голы одного игрока на разных скринах — не суммируй между скринами: у строки таблицы «Г» — счётчик голов игрока; в ленте голов — одна запись на гол с минутой.
3a. Ник игрока — верхняя строка карточки в шапке («temiyy», «quete-основа»). Строка ПОД ником —
    игровая лига/гильдия («Логово фифарей», «НЕТ ЛИГИ», «Army_Guys») — это НЕ ник, клади её в league_*.
    player_home — карточка слева, player_away — справа. Ник переписывай посимвольно, как есть.
4. В таблице колонки слева: ПОЗ|ИГРОКИ|ОБЩ|ИС|Г|А (хозяева), справа зеркально (гости). 👑 = капитан.
5. В ленте голов: минута + имя; сторона определяется цветом иконки/позицией (слева = хозяева... определи по счёту: сумма голов каждой стороны должна быть ≤ счёта шапки).
6. Если сумма распознанных голов стороны меньше счёта шапки — добавь warning "не все бомбардиры видны" (таблица прокручиваемая).
7. Минуты: в таблице статистики минут НЕТ (поставь minute=null); в ленте голов минута есть («45' +4'» → 49).

Ответ — ТОЛЬКО валидный JSON без markdown, по схеме:
{"team_home": str|null, "team_away": str|null,
 "player_home": str|null, "player_away": str|null,
 "league_home": str|null, "league_away": str|null,
 "score_home": int, "score_away": int,
 "timer": "90:00"|"120:00"|str|null,
 "penalties": null | {"home": int, "away": int},
 "confidence": 0.0-1.0,
 "players": [{"side": "home"|"away", "name": str, "pos": str|null,
              "rating": int|null, "goals": int, "assists": int, "captain": bool}],
 "goal_events": [{"side": "home"|"away", "name": str, "minute": int|null}],
 "warnings": [str]}

goal_events — из ленты голов (одна запись на гол). players — из таблицы статистики.
Если поле не распознано — null. Не выдумывай данные."""

# free VL-модели OpenRouter (ротация каскадом, план 06); переопределяется env OPENROUTER_MODELS
_DEFAULT_OPENROUTER_MODELS = [
    "qwen/qwen2.5-vl-72b-instruct:free",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "moonshotai/kimi-vl-a3b-thinking:free",
    "agentica-org/deepseek-vl2-small:free",
]
GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
NIM_MODEL = "meta/llama-3.2-90b-vision-instruct"
OPENROUTER_MODELS = config.OPENROUTER_MODELS or _DEFAULT_OPENROUTER_MODELS
GEMINI_MODEL = config.GEMINI_MODEL

_TIMEOUT = 90
_LOCAL_TIMEOUT = 240  # CPU-инференс 3–4B модели на один скрин — десятки секунд


def _data_uri(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def _post_json(url: str, payload: dict, headers: dict, timeout: int = _TIMEOUT) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ===== провайдеры =====

def _openai_style_vl(url: str, key: str, model: str, extra_headers: dict | None = None,
                     max_tokens: int = 2000) -> callable:
    def call(image_bytes: bytes) -> str:
        payload = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": SYSTEM_PROMPT},
                    {"type": "image_url", "image_url": {"url": _data_uri(image_bytes)}},
                ],
            }],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        data = _post_json(url, payload, {"Authorization": f"Bearer {key}", **(extra_headers or {})})
        return data["choices"][0]["message"]["content"]
    return call


def _gemini_call(image_bytes: bytes) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}")
    payload = {
        "contents": [{
            "parts": [
                {"text": SYSTEM_PROMPT},
                {"inline_data": {"mime_type": "image/jpeg",
                                 "data": base64.b64encode(image_bytes).decode()}},
            ]
        }],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 2000},
    }
    data = _post_json(url, payload, {})
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _ollama_call(image_bytes: bytes) -> str:
    """Локальная vision-модель (Ollama /api/chat, format=json — ответ сразу JSON)."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "user", "content": SYSTEM_PROMPT,
                      "images": [base64.b64encode(image_bytes).decode()]}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    data = _post_json(f"{config.OLLAMA_URL}/api/chat", payload, {}, timeout=_LOCAL_TIMEOUT)
    return data["message"]["content"]


def _ocrspace_call(image_bytes: bytes) -> str:
    """OCR.space: сырой текст → эвристический разбор (без ИИ-vision)."""
    boundary = "----kurilkaocr"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"apikey\"\r\n\r\n"
        f"{config.OCRSPACE_API_KEY}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"OCREngine\"\r\n\r\n2\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nrus\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"shot.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n"
    ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        "https://api.ocr.space/parse/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        data = json.loads(r.read().decode())
    return data.get("ParsedResults", [{}])[0].get("ParsedText", "")


def _tesseract_call(image_bytes: bytes) -> str:
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract не установлен")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(image_bytes)
        path = f.name
    try:
        out = subprocess.run(
            ["tesseract", path, "stdout", "-l", "rus+eng"],
            capture_output=True, timeout=_TIMEOUT,
        )
        return out.stdout.decode("utf-8", errors="replace")
    finally:
        import os
        os.unlink(path)


def _openrouter_provider(model: str):
    if not config.OPENROUTER_API_KEY:
        return None
    return _openai_style_vl("https://openrouter.ai/api/v1/chat/completions",
                            config.OPENROUTER_API_KEY, model)


def build_cascade() -> list[tuple[str, callable]]:
    """Список (имя, callable(image_bytes)->str) доступных провайдеров по порядку."""
    cascade: list[tuple[str, callable]] = []
    local = [(f"ollama:{config.OLLAMA_MODEL}", _ollama_call)] if config.OLLAMA_URL else []
    if config.OLLAMA_FIRST:
        cascade += local
    for model in OPENROUTER_MODELS:
        p = _openrouter_provider(model.strip())
        if p:
            cascade.append((f"openrouter:{model.strip()}", p))
    if config.GROQ_API_KEY:
        cascade.append(("groq", _openai_style_vl(
            "https://api.groq.com/openai/v1/chat/completions",
            config.GROQ_API_KEY, GROQ_MODEL)))
    if config.GEMINI_API_KEY:
        cascade.append(("gemini", _gemini_call))
    if config.NIM_API_KEY:
        cascade.append(("nim", _openai_style_vl(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            config.NIM_API_KEY, NIM_MODEL)))
    if not config.OLLAMA_FIRST:
        cascade += local
    if config.OCRSPACE_API_KEY:
        cascade.append(("ocrspace", _ocrspace_call))
    if shutil.which("tesseract"):
        cascade.append(("tesseract", _tesseract_call))
    return cascade


# ===== разбор ответа =====

def extract_json(text: str) -> dict | None:
    """Достаёт JSON из ответа модели (терпит ```json-заборы и мусор вокруг)."""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        text = text[start:end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


_TIMER_RE = re.compile(r"\b(\d{2,3}:\d{2})\b")
_SCORE_PIPE_RE = re.compile(r"\(?(\d{1,2})\)?\s*[|]\s*(\d{1,2})\s*\((\d{1,2})\)?")
_SCORE_DASH_RE = re.compile(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b")
_SCORE_SPACED_RE = re.compile(r"(?:\((\d{1,2})\)\s*)?(\d{1,2})\s+(?:FC|Ф)\s+(\d{1,2})(?:\s*\((\d{1,2})\))?")


def heuristic_parse(raw_text: str) -> dict | None:
    """Фолбэк для текстовых OCR (OCR.space/tesseract): счёт/таймер/пенальти из шапки."""
    if not raw_text:
        return None
    timer = None
    m = _TIMER_RE.search(raw_text)
    if m:
        timer = m.group(1)
    pens = None
    score = None
    # паттерн с пенальти: (5) 3 | 3 (4)
    m = re.search(r"\((\d{1,2})\)\s*(\d{1,2})\s*[|‑-–]\s*(\d{1,2})\s*\((\d{1,2})\)", raw_text)
    if m:
        pens = {"home": int(m.group(1)), "away": int(m.group(4))}
        score = (int(m.group(2)), int(m.group(3)))
    if score is None:
        m = _SCORE_DASH_RE.search(raw_text)
        if m:
            score = (int(m.group(1)), int(m.group(2)))
    if score is None:
        m = re.search(r"(\d{1,2})\s*\n\s*(\d{1,2})", raw_text)
        if m and timer:
            score = (int(m.group(1)), int(m.group(2)))
    if score is None:
        return None
    return {
        "score_home": score[0], "score_away": score[1],
        "timer": timer, "penalties": pens,
        "players": [], "goal_events": [],
        "warnings": ["текстовый OCR: только счёт, без бомбардиров"],
        "confidence": 0.4,
    }


def normalize_result(data: dict) -> dict:
    """Нормализация ответа провайдера: типы, дефолты, warnings."""
    out = {
        "team_home": data.get("team_home"),
        "team_away": data.get("team_away"),
        "player_home": data.get("player_home"),
        "player_away": data.get("player_away"),
        "score_home": int(data.get("score_home") or 0),
        "score_away": int(data.get("score_away") or 0),
        "timer": data.get("timer"),
        "penalties": None,
        "players": [],
        "goal_events": [],
        "warnings": list(data.get("warnings") or []),
        "confidence": float(data.get("confidence") or 0.5),
    }
    pens = data.get("penalties")
    if isinstance(pens, dict) and pens.get("home") is not None:
        out["penalties"] = {"home": int(pens["home"]), "away": int(pens["away"])}
    for p in data.get("players") or []:
        try:
            out["players"].append({
                "side": "home" if p.get("side") == "home" else "away",
                "name": (p.get("name") or "").strip(),
                "pos": p.get("pos"),
                "rating": p.get("rating"),
                "goals": int(p.get("goals") or 0),
                "assists": int(p.get("assists") or 0),
                "captain": bool(p.get("captain")),
            })
        except (TypeError, ValueError):
            continue
    for g in data.get("goal_events") or []:
        try:
            out["goal_events"].append({
                "side": "home" if g.get("side") == "home" else "away",
                "name": (g.get("name") or "").strip(),
                "minute": int(g["minute"]) if g.get("minute") is not None else None,
            })
        except (TypeError, ValueError):
            continue
    # правило суммы Г ≤ счёта (план 06)
    for side, score in (("home", out["score_home"]), ("away", out["score_away"])):
        table_goals = sum(p["goals"] for p in out["players"] if p["side"] == side)
        if table_goals > score:
            out["warnings"].append(f"сумма Г таблицы ({table_goals}) больше счёта ({score}) — верим шапке")
    return out


def merge_results(results: list[dict]) -> dict:
    """Объединение 1–3 скринов одного матча: счёт из первого уверенного шапки,
    голы — дедуп по (сторона, имя, минута); для таблицы (без минут) — max по имени."""
    merged: dict | None = None
    players: dict[tuple[str, str], dict] = {}
    events: list[dict] = []
    for r in results:
        if merged is None or (r.get("confidence", 0) > merged.get("confidence", 0)):
            base = dict(r)
            base["players"], base["goal_events"] = [], []
            merged = base
        for p in r.get("players") or []:
            key = (p["side"], p["name"].lower())
            if key not in players or p["goals"] > players[key]["goals"]:
                players[key] = p
        for g in r.get("goal_events") or []:
            key = (g["side"], g["name"].lower(), g["minute"])
            if key not in {(e["side"], e["name"].lower(), e["minute"]) for e in events}:
                events.append(g)
    if merged is None:
        return None
    # ники нужны для поиска матча: берём с любого скрина пачки, где они прочитались
    for key in ("player_home", "player_away", "team_home", "team_away"):
        if not merged.get(key):
            merged[key] = next((r.get(key) for r in results if r.get(key)), None)
    merged["players"] = list(players.values())
    merged["goal_events"] = events
    return merged


def parse_screenshots(images: list[bytes], cascade: list[tuple[str, callable]] | None = None) -> dict | None:
    """Прогоняет пачку скринов через каскад. Возвращает нормализованный merged-JSON
    или None, если всё прошло мимо."""
    cascade = cascade if cascade is not None else build_cascade()
    results = []
    used = []
    for image in images:
        for name, call in cascade:
            try:
                raw = call(image)
            except Exception as e:
                log.warning("[ocr] %s: %s", name, e)
                continue
            # модели часто пишут текст перед JSON — extract_json терпит мусор вокруг
            data = extract_json(raw) if raw else None
            if data is None:
                data = heuristic_parse(raw or "")
            if data and (data.get("score_home") is not None):
                results.append(normalize_result(data))
                used.append(name)
                break
    if not results:
        return None
    merged = merge_results(results)
    if used:
        merged.setdefault("warnings", []).append("провайдеры: " + ", ".join(used))
    return merged
