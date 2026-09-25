"""Свои OCR-провайдеры (OpenAI-совместимые: /chat/completions с картинкой), добавляются
root'ом из мини-аппа. Ключи лежат только в БД сервера, наружу отдаются замаскированными.
"""
import ipaddress
import re
from urllib.parse import urlparse

import db as appdb

POSITIONS = ("first", "last")  # first — раньше встроенных, last — после облачных встроенных


class ProviderError(ValueError):
    pass


def mask_key(key: str | None) -> str:
    key = key or ""
    if len(key) <= 6:
        return "•" * len(key)
    return "•" * 6 + key[-4:]


def normalize_base_url(url: str) -> str:
    """https://host/v1[/chat/completions] → https://host/v1. http — только localhost/LAN (Ollama и т.п.)."""
    url = (url or "").strip().rstrip("/")
    url = re.sub(r"/chat/completions$", "", url)
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ProviderError("Base URL должен начинаться с https://")
    if p.scheme == "http":
        host = p.hostname
        local = host in ("localhost",)
        try:
            local = local or ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if not local:
            raise ProviderError("http:// разрешён только для локальных адресов — для внешних нужен https://")
    return url


def _public(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"], "base_url": row["base_url"], "model": row["model"],
        "key_masked": mask_key(row["api_key"]), "has_key": bool(row["api_key"]),
        "enabled": bool(row["enabled"]), "position": row["position"],
        "last_test": row.get("last_test"), "last_test_at": row.get("last_test_at"),
    }


def list_providers(public: bool = True, enabled_only: bool = False) -> list[dict]:
    c = appdb.db()
    sql = "SELECT * FROM ocr_providers"
    if enabled_only:
        sql += " WHERE enabled=1"
    rows = [dict(r) for r in c.execute(sql + " ORDER BY sort_order, id").fetchall()]
    c.close()
    return [_public(r) for r in rows] if public else rows


def get_provider(pid: int) -> dict | None:
    c = appdb.db()
    row = c.execute("SELECT * FROM ocr_providers WHERE id=?", (pid,)).fetchone()
    c.close()
    return dict(row) if row else None


def create_provider(name: str, base_url: str, model: str, api_key: str, position: str = "first") -> dict:
    name, model, api_key = (name or "").strip()[:40], (model or "").strip()[:120], (api_key or "").strip()
    if not model:
        raise ProviderError("Укажи модель")
    base_url = normalize_base_url(base_url)
    if position not in POSITIONS:
        position = "first"
    c = appdb.db()
    order = c.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 n FROM ocr_providers").fetchone()["n"]
    pid = c.insert_returning_id(
        "INSERT INTO ocr_providers (name, base_url, model, api_key, position, enabled, sort_order) "
        "VALUES (?,?,?,?,?,1,?)", (name or model, base_url, model, api_key, position, order))
    c.commit()
    c.close()
    return _public(get_provider(pid))


def update_provider(pid: int, **fields) -> dict:
    row = get_provider(pid)
    if not row:
        raise ProviderError("Провайдер не найден")
    sets, args = [], []
    if "name" in fields and fields["name"] is not None:
        sets.append("name=?"); args.append(str(fields["name"]).strip()[:40] or row["model"])
    if fields.get("model"):
        sets.append("model=?"); args.append(str(fields["model"]).strip()[:120])
    if fields.get("base_url"):
        sets.append("base_url=?"); args.append(normalize_base_url(fields["base_url"]))
    if fields.get("api_key"):  # пусто — ключ не меняем
        sets.append("api_key=?"); args.append(str(fields["api_key"]).strip())
    if "enabled" in fields and fields["enabled"] is not None:
        sets.append("enabled=?"); args.append(1 if fields["enabled"] else 0)
    if fields.get("position") in POSITIONS:
        sets.append("position=?"); args.append(fields["position"])
    if sets:
        c = appdb.db()
        c.execute(f"UPDATE ocr_providers SET {', '.join(sets)} WHERE id=?", (*args, pid))
        c.commit()
        c.close()
    return _public(get_provider(pid))


def delete_provider(pid: int) -> None:
    c = appdb.db()
    c.execute("DELETE FROM ocr_providers WHERE id=?", (pid,))
    c.commit()
    c.close()


def save_test_result(pid: int, text: str) -> None:
    c = appdb.db()
    c.execute("UPDATE ocr_providers SET last_test=?, last_test_at=datetime('now') WHERE id=?", (text[:200], pid))
    c.commit()
    c.close()
