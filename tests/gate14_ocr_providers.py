"""ГЕЙТ 14: свои OCR-провайдеры из мини-аппа — валидация URL, маска ключа, CRUD,
место в каскаде (first/last, выключенные не участвуют), API только для root, ключ
не утекает в ответы, «Проверить» на эталонном скрине (провайдер подменён локальным
OpenAI-совместимым сервером)."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bot"))
os.environ["BOT_TOKEN"] = "123456:TEST"
os.environ["ADMIN_IDS"] = "900001"
os.environ["DB_PATH"] = "/tmp/gate14.db"
for k in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "NIM_API_KEY", "OCRSPACE_API_KEY", "OLLAMA_URL"):
    os.environ[k] = ""
if os.path.exists("/tmp/gate14.db"):
    os.remove("/tmp/gate14.db")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
import ocr  # noqa: E402
import ocr_providers as op  # noqa: E402
from references import REFERENCE_SHOTS  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


db.init_db()
config.GEMINI_API_KEY = config.OPENROUTER_API_KEY = config.NIM_API_KEY = config.OCRSPACE_API_KEY = ""
config.OLLAMA_URL = ""

# ===== модуль =====
check("URL: хвост /chat/completions срезается",
      op.normalize_base_url("https://api.example.com/v1/chat/completions/") == "https://api.example.com/v1")
for bad in ("ftp://x/v1", "api.example.com/v1", "http://example.com/v1", "javascript:alert(1)"):
    try:
        op.normalize_base_url(bad)
        check(f"URL отклонён: {bad}", False)
    except op.ProviderError:
        check(f"URL отклонён: {bad}", True)
check("http для локального Ollama/LAN можно", op.normalize_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1")
check("маска ключа", op.mask_key("sk-secret-1234abcd") == "••••••abcd" and op.mask_key("abc") == "•••")

SECRET = "sk-live-VERY-SECRET-9f2b"
a = op.create_provider("AMD", "https://llm.example.com/v1", "DeepSeek-V4.1-Flash", SECRET, "first")
b = op.create_provider("", "https://b.example.com/v1", "model-b", "k2", "last")
check("имя по умолчанию = модель", b["name"] == "model-b")
check("публичный вид без ключа", SECRET not in json.dumps(op.list_providers()))
names = [n for n, _ in ocr.build_cascade()]
check("каскад: first впереди, last — после облачных", names[0] == "custom:AMD" and "custom:model-b" in names, str(names))
op.update_provider(a["id"], enabled=False)
check("выключенный не в каскаде", "custom:AMD" not in [n for n, _ in ocr.build_cascade()])
op.update_provider(a["id"], enabled=True, api_key="")
check("пустой ключ при обновлении не стирает старый", op.get_provider(a["id"])["api_key"] == SECRET)
op.delete_provider(b["id"])
check("удаление", [p["name"] for p in op.list_providers()] == ["AMD"])


# ===== локальный «OpenAI-совместимый» провайдер для кнопки «Проверить» =====
seen = {}


async def fake_chat(request):
    seen["auth"] = request.headers.get("Authorization")
    body = await request.json()
    seen["model"] = body["model"]
    seen["has_image"] = any(part.get("type") == "image_url" for part in body["messages"][0]["content"])
    gt = REFERENCE_SHOTS[ocr.TEST_SHOT]
    return web.json_response({"choices": [{"message": {"content": "Вот JSON:\n" + json.dumps(gt, ensure_ascii=False)}}]})


def sign(user_id: int) -> str:
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id, "first_name": "T"})}
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


async def run_api():
    fake = web.Application()
    fake.router.add_post("/v1/chat/completions", fake_chat)
    fake_srv = TestServer(fake, host="127.0.0.1")
    await fake_srv.start_server()

    from miniapp.server import build_app
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    root, player = {"X-Telegram-Init-Data": sign(900001)}, {"X-Telegram-Init-Data": sign(900002)}

    r = await client.get("/api/admin/ocr-providers", headers=player)
    check("API: не-root → 403", r.status == 403)
    r = await client.get("/api/admin/ocr-providers", headers=root)
    data = await r.json()
    check("API: root видит список", r.status == 200 and data["providers"][0]["name"] == "AMD")
    check("API: ключ не утекает", SECRET not in json.dumps(data))
    r = await client.post("/api/admin/ocr-providers", headers=root,
                          data=json.dumps({"base_url": "http://evil.example.com/v1", "model": "x"}))
    check("API: внешний http отклонён", r.status == 400)
    r = await client.post("/api/admin/ocr-providers", headers=root, data=json.dumps({
        "name": "Local", "base_url": f"http://127.0.0.1:{fake_srv.port}/v1", "model": "vision-x", "api_key": "loc-key"}))
    pid = (await r.json())["provider"]["id"]
    r = await client.post(f"/api/admin/ocr-providers/{pid}/test", headers=root)
    t = await r.json()
    check("«Проверить»: эталонный скрин распознан", t.get("ok") is True and "2:3" in t["text"], str(t))
    check("«Проверить»: ник Rusli узнан без замечаний", "не узнаются" not in t["text"], t["text"])
    check("провайдеру ушли ключ, модель и картинка",
          seen == {"auth": "Bearer loc-key", "model": "vision-x", "has_image": True}, str(seen))
    r = await client.post(f"/api/admin/ocr-providers/{pid}", headers=player, data=json.dumps({"enabled": False}))
    check("API: игрок не может выключить", r.status == 403)
    r = await client.delete(f"/api/admin/ocr-providers/{pid}", headers=root)
    check("API: root удаляет", r.status == 200 and len(op.list_providers()) == 1)
    r = await client.get("/api/bootstrap", headers=root)
    check("bootstrap: is_root для кнопки админки", (await r.json())["user"]["is_root"] is True)

    await client.close()
    await fake_srv.close()


asyncio.run(run_api())
print("\nGATE 14:", "OK" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
