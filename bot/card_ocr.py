"""Распознавание карточки игрока FC Mobile со скрина/фото (тот же каскад, что bot/ocr.py,
но только vision-модели: текстовые OCR.space/tesseract промпт не понимают)."""
import logging

import ocr

log = logging.getLogger("bot.card_ocr")

CARD_PROMPT = """Ты читаешь карточку футболиста из игры EA SPORTS FC Mobile (скриншот или фото экрана).

На карточке: общий рейтинг OVR (крупное число, в FC Mobile бывает до 150), позиция (ST, CF, LW, RW, CAM,
CM, CDM, LM, RM, LB, RB, LWB, RWB, CB, GK; в русской версии ВРТ, ЦЗ, ЛЗ, ПЗ, ЦОП, ЦП, ЦАП, ЛП, ПП, ЛВ, ПВ,
ФРВ, НАП), имя игрока, флаг сборной, эмблема лиги и клуба, название программы/события (TOTY, Icons,
UCL, Heroes…). Шесть характеристик: PAC/СКР (скорость), SHO/УДР (удар), PAS/ПАС (пас), DRI/ДРБ (дриблинг),
DEF/ЗАЩ (защита), PHY/ФИЗ (физика); у вратаря DIV/HAN/KIC/REF/SPD/POS — тогда pac..phy = null.
Если открыт экран деталей: финты (skill moves, звёзды 1–5), слабая нога (1–5), рабочая нога, рост (см),
дополнительные позиции.

Ответ — ТОЛЬКО валидный JSON без markdown:
{"name": str|null, "rating": int|null, "position": str|null, "alt_positions": [str],
 "pac": int|null, "sho": int|null, "pas": int|null, "dri": int|null, "def": int|null, "phy": int|null,
 "skill_moves": int|null, "weak_foot": int|null, "foot": "L"|"R"|null, "height_cm": int|null,
 "nation": str|null, "league": str|null, "club": str|null, "program": str|null,
 "confidence": 0.0-1.0, "warnings": [str]}

Позицию пиши английским кодом. Имя — как на карточке. Чего не видно — null. Не выдумывай данные."""


def build_cascade() -> list[tuple[str, callable]]:
    return [(n, call) for n, call in ocr.build_cascade() if n not in ocr.TEXT_PROVIDERS]


def _clean_int(v, lo: int, hi: int):
    try:
        n = int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def normalize_fields(data: dict) -> tuple[dict, list[str]]:
    """Ответ модели → поля формы карточки (только валидные). → (fields, warnings)."""
    from cards import STATS, normalize_position, parse_positions
    fields, warns = {}, []
    name = " ".join(str(data.get("name") or "").split())
    if 2 <= len(name) <= 40:
        fields["name"] = name
    rating = _clean_int(data.get("rating"), 40, 150)
    if rating is not None:
        fields["rating"] = rating
    elif data.get("rating") is not None:
        warns.append(f"OVR «{data.get('rating')}» вне диапазона 40–150 — проверь вручную")
    pos = normalize_position(data.get("position"))
    if pos:
        fields["position"] = pos
    elif data.get("position"):
        warns.append(f"Позиция «{data.get('position')}» не распознана")
    alts = [p for p in parse_positions(data.get("alt_positions")) if p != pos]
    if alts:
        fields["alt_positions"] = alts[:4]
    for s in STATS:
        v = _clean_int(data.get(s), 0, 200)
        if v is not None:
            fields[s] = v
    for key in ("skill_moves", "weak_foot"):
        v = _clean_int(data.get(key), 1, 5)
        if v is not None:
            fields[key] = v
    h = _clean_int(data.get("height_cm"), 140, 230)
    if h is not None:
        fields["height_cm"] = h
    foot = str(data.get("foot") or "").strip().upper()[:1]
    if foot in ("L", "R"):
        fields["foot"] = foot
    for src, dst in (("nation", "nation"), ("league", "league"), ("club", "real_club"), ("program", "program")):
        v = " ".join(str(data.get(src) or "").split())[:40]
        if v:
            fields[dst] = v
    warns += [str(w)[:200] for w in (data.get("warnings") or []) if w][:5]
    return fields, warns


def parse_card(image: bytes, cascade: list[tuple[str, callable]] | None = None) -> dict:
    """Фото карточки → {fields, confidence, warnings, provider}. Сбой — пустые fields + причина."""
    cascade = cascade if cascade is not None else build_cascade()
    if not cascade:
        return {"fields": {}, "confidence": 0.0, "provider": None,
                "warnings": ["Распознавание недоступно: не настроен ни один vision-провайдер — заполни вручную"]}
    errors = []
    for name, call in cascade:
        try:
            raw = call(image, prompt=CARD_PROMPT)
        except Exception as e:
            log.warning("[card_ocr] %s: %s", name, e)
            errors.append(name)
            continue
        data = ocr.extract_json(raw or "")
        if not data:
            errors.append(name)
            continue
        fields, warns = normalize_fields(data)
        if not fields.get("name") and fields.get("rating") is None:
            errors.append(name)
            continue
        try:
            conf = max(0.0, min(1.0, float(data.get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 0.0
        if "name" not in fields or "rating" not in fields or "position" not in fields:
            warns.append("Не все основные поля распознаны — дополни вручную")
        return {"fields": fields, "confidence": conf, "warnings": warns, "provider": name}
    return {"fields": {}, "confidence": 0.0, "provider": None,
            "warnings": ["Не удалось распознать карточку (" + ", ".join(errors) + ") — заполни поля вручную"]}
