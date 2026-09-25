"""API карточек игроков: добавление в состав, фото, OCR карточки, сверка с RenderZ,
очередь судьи. Контракт — docs/API-cards.md. Подключается автоматически (server._setup_feature_routes)."""
import asyncio
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))

from aiohttp import web  # noqa: E402

import card_ocr  # noqa: E402
import cards  # noqa: E402
import renderz  # noqa: E402
import settings as appsettings  # noqa: E402

from .helpers import require_active_user  # noqa: E402

RENDERZ_PER_HOUR = 30
_renderz_hits: dict[int, deque] = defaultdict(deque)


def _j(data, status=200):
    return web.json_response(data, status=status)


def _err(text, status=400, code=None):
    return web.json_response({"status": "error", "error": text, "code": code}, status=status)


def _card_err(e: cards.CardError):
    return _err(str(e), e.status, e.code)


def _card_id(request) -> int:
    return int(request.match_info["card_id"])


async def _json(request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise cards.CardError("BAD_INPUT", "Нужен JSON")
    if not isinstance(body, dict):
        raise cards.CardError("BAD_INPUT", "Нужен JSON-объект")
    return body


async def _read_image(request) -> bytes | None:
    """Поле image из multipart потоково (лимит aiohttp 1 МБ на post() тут не годится)."""
    if not request.content_type.startswith("multipart/"):
        return None
    try:
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                return None
            if part.name != "image":
                continue
            buf = bytearray()
            while True:
                chunk = await part.read_chunk(256 * 1024)
                if not chunk:
                    return bytes(buf)
                buf += chunk
                if len(buf) > cards.MAX_UPLOAD_BYTES:
                    raise cards.CardError("TOO_BIG", "Файл больше 8 МБ")
    except cards.CardError:
        raise
    except Exception:
        raise cards.CardError("BAD_IMAGE", "Не удалось прочитать файл")


# ===== фото и распознавание =====

async def api_upload(request):
    u = require_active_user(request)
    try:
        data = await _read_image(request)
        if not data:
            return _err("Прикрепи картинку в поле image", 400, "BAD_IMAGE")
        r = await asyncio.to_thread(cards.save_upload, u["telegram_id"], data)
    except cards.CardError as e:
        return _card_err(e)
    return _j({"token": r["token"], "image_url": r["image_url"]})


async def api_ocr(request):
    u = require_active_user(request)
    tg = u["telegram_id"]
    try:
        if not cards.ocr_allowed(tg):
            return _err("Лимит распознаваний — 20 в час, попробуй позже", 429, "RATE_LIMIT")
        if request.content_type.startswith("multipart/"):
            data = await _read_image(request)
            if not data:
                return _err("Прикрепи картинку в поле image", 400, "BAD_IMAGE")
            up = await asyncio.to_thread(cards.save_upload, tg, data)
            info = await asyncio.to_thread(cards.upload_info, up["token"], tg)
        else:
            body = await _json(request)
            if not body.get("token"):
                return _err("Нужна картинка (multipart image) или token", 400, "BAD_INPUT")
            info = await asyncio.to_thread(cards.upload_info, body["token"], tg)
    except cards.CardError as e:
        return _card_err(e)
    try:
        res = await asyncio.to_thread(card_ocr.parse_card, info["bytes"])
    except Exception as e:  # сбой распознавания — не ошибка запроса
        res = {"fields": {}, "confidence": 0.0, "provider": None, "warnings": [f"Распознавание упало: {e}"[:200]]}
    cards.mark_ocr(info["token"], res["fields"])
    return _j({"token": info["token"], "image_url": info["image_url"], "fields": res["fields"],
               "confidence": res["confidence"], "warnings": res["warnings"], "provider": res["provider"]})


async def api_renderz_check(request):
    u = require_active_user(request)
    try:
        body = await _json(request)
    except cards.CardError as e:
        return _card_err(e)
    if appsettings.setting_int("renderz_verify", 1) != 1:
        return _err("Проверка по RenderZ выключена админом", 400, "DISABLED")
    try:
        canon, _ = renderz.parse_url(body.get("url"))
    except renderz.RenderzError as e:
        return _err(str(e), 400, "BAD_URL")
    hits, now = _renderz_hits[u["telegram_id"]], time.time()
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= RENDERZ_PER_HOUR:
        return _err("Слишком много проверок — попробуй через час", 429, "RATE_LIMIT")
    hits.append(now)
    try:
        rz = await asyncio.to_thread(renderz.lookup, canon)
    except renderz.RenderzError as e:
        return _err(str(e), 400, "RENDERZ")
    fields = body.get("fields")
    match = None
    if isinstance(fields, dict) and fields:
        m = renderz.compare(fields, rz)
        match = {k: m[k] for k in ("name", "rating", "position", "all")}
    return _j({"ok": True, "renderz": renderz.public(rz), "match": match})


async def media_card(request):
    f = cards.media_file(request.match_info.get("name", ""))
    if not f:
        raise web.HTTPNotFound()
    return web.FileResponse(f, headers={"Cache-Control": "public, max-age=604800",
                                        "X-Content-Type-Options": "nosniff"})


# ===== карточки =====

async def api_my(request):
    u = require_active_user(request)
    return _j(await asyncio.to_thread(cards.my_cards, u["telegram_id"]))


async def api_create(request):
    u = require_active_user(request)
    tg = u["telegram_id"]
    try:
        body = await _json(request)
        my = cards.my_cards(tg)
        if "club_id" in body:
            club_id = int(body["club_id"]) if body["club_id"] not in (None, "", "null") else None
        elif my["club"]:
            club_id = my["club"]["id"]
        else:
            return _err("У тебя нет клуба — попроси root выдать", 400, "NO_CLUB")
        r = await asyncio.to_thread(cards.create_card, tg, club_id, body, body.get("token") or None,
                                    body.get("renderz_url") or None)
    except (TypeError, ValueError):
        return _err("club_id: нужно число или null", 400, "BAD_INPUT")
    except cards.CardError as e:
        return _card_err(e)
    return _j(r)


async def api_update(request):
    u = require_active_user(request)
    try:
        card_id = _card_id(request)
        body = await _json(request)
        kw = {"renderz_url": body.get("renderz_url") or None} if "renderz_url" in body else {}
        r = await asyncio.to_thread(cards.update_card, u["telegram_id"], card_id, body,
                                    body.get("token") or None, **kw)
    except cards.CardError as e:
        return _card_err(e)
    return _j(r)


async def api_delete(request):
    u = require_active_user(request)
    try:
        return _j(await asyncio.to_thread(cards.delete_card, u["telegram_id"], _card_id(request)))
    except cards.CardError as e:
        return _card_err(e)


async def api_detail(request):
    u = require_active_user(request)
    try:
        return _j(await asyncio.to_thread(cards.card_detail, _card_id(request), u["telegram_id"]))
    except cards.CardError as e:
        return _card_err(e)


async def api_queue(request):
    u = require_active_user(request)
    return _j({"cards": await asyncio.to_thread(cards.queue, u["telegram_id"])})


async def api_decide(request):
    u = require_active_user(request)
    try:
        body = await _json(request)
        if not isinstance(body.get("approve"), bool):
            return _err("Нужно approve: true/false", 400, "BAD_INPUT")
        r = await asyncio.to_thread(cards.decide, _card_id(request), body["approve"], u["telegram_id"],
                                    body.get("note"))
    except cards.CardError as e:
        return _card_err(e)
    return _j(r)


def setup(app: web.Application) -> None:
    r = app.router
    r.add_post("/api/cards/upload", api_upload)
    r.add_post("/api/cards/ocr", api_ocr)
    r.add_post("/api/cards/renderz-check", api_renderz_check)
    r.add_get("/api/cards/my", api_my)
    r.add_get("/api/cards/queue", api_queue)
    r.add_post("/api/cards", api_create)
    r.add_get(r"/api/cards/{card_id:\d+}", api_detail)
    r.add_post(r"/api/cards/{card_id:\d+}", api_update)
    r.add_delete(r"/api/cards/{card_id:\d+}", api_delete)
    r.add_post(r"/api/cards/{card_id:\d+}/decide", api_decide)
    r.add_get("/media/cards/{name}", media_card)
