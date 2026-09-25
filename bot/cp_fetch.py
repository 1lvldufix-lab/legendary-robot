"""Загрузка страницы турнира Challenge Place по ссылке (для /scan <url>).

Порядок: сначала обычный HTTPS-запрос (urllib) — если в ответе уже есть
window.__INITIAL_STATE__ с дашбордом, браузер не нужен. Иначе (Cloudflare
«Just a moment», SPA без SSR) — headless Chromium через Playwright (опционально,
bot/requirements-optional.txt). Cloudflare умеет ловить автоматизацию
(проверено 25.09) — тогда внятная ошибка: сохранить страницу и дать /scan файл.

SSRF: пускаем только https и только хосты Challenge Place (и поддомены),
редиректы проверяются тем же правилом. IP-адреса, порты, логин в URL — отказ.
Всё здесь блокирующее — из бота звать через asyncio.to_thread.
"""
import ipaddress
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("bot.cp_fetch")

# реальный сайт — challenge.place (план 07); challengeplace.com — на случай зеркала
ALLOWED_HOSTS = ("challenge.place", "challengeplace.com")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
MAX_BYTES = 8 * 1024 * 1024
INSTALL_HINT = ("venv/bin/pip install playwright && "
                "venv/bin/playwright install --with-deps chromium")


class CPFetchError(RuntimeError):
    """Понятная пользователю ошибка загрузки (текст уходит в чат)."""


def validate_url(url: str) -> str:
    """Ссылка на Challenge Place → нормализованный URL, иначе CPFetchError."""
    try:
        u = urllib.parse.urlsplit((url or "").strip())
        port = u.port
    except ValueError:
        raise CPFetchError("Кривая ссылка.")
    if u.scheme != "https":
        raise CPFetchError("Нужна ссылка https://challenge.place/…")
    host = (u.hostname or "").rstrip(".").lower()
    if not host or u.username or u.password or port not in (None, 443):
        raise CPFetchError("Ссылка без логина и портов: https://challenge.place/…")
    try:
        ipaddress.ip_address(host)
        raise CPFetchError("IP-адреса не принимаются — только challenge.place.")
    except ValueError:
        pass
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        raise CPFetchError("Скан только с challenge.place (и его поддоменов).")
    return urllib.parse.urlunsplit(("https", host, u.path or "/", u.query, ""))


def _check_resolved(host: str) -> None:
    """Защита от DNS-подмены: хост не должен резолвиться во внутреннюю сеть."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise CPFetchError(f"Не резолвится {host}: {e}")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise CPFetchError(f"{host} указывает во внутреннюю сеть — отказ.")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = validate_url(urllib.parse.urljoin(req.full_url, newurl))
        _check_resolved(urllib.parse.urlsplit(newurl).hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: float) -> tuple[int, str]:
    """Голый HTTP-транспорт: (статус, текст). URL уже проверен вызывающим."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    })
    opener = urllib.request.build_opener(_SafeRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read(MAX_BYTES + 1)
            status = r.status
            charset = r.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        raw = e.read(MAX_BYTES + 1) if e.fp else b""
        status = e.code
        charset = "utf-8"
    if len(raw) > MAX_BYTES:
        raise CPFetchError("Страница слишком большая.")
    return status, raw.decode(charset, errors="replace")


def has_dashboard(text: str) -> bool:
    """В HTML есть то, что нужно parser_cp (хотя бы один участник)."""
    import parser_cp
    try:
        return bool(parser_cp.parse_state(text)["competitors"])
    except Exception:
        return False


def fetch_plain(url: str, timeout: float = 20) -> str | None:
    """Обычный HTTPS-запрос. HTML с дашбордом или None (Cloudflare/нет стейта/сеть)."""
    url = validate_url(url)
    try:
        _check_resolved(urllib.parse.urlsplit(url).hostname)
        status, text = _open(url, timeout)
    except CPFetchError:
        raise
    except Exception as e:
        log.info("[cp] plain fetch не удался: %s", e)
        return None
    if status == 200 and has_dashboard(text):
        return text
    log.info("[cp] plain fetch: статус %s, дашборда нет — нужен браузер", status)
    return None


def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise CPFetchError(
            "Сайт не отдаёт данные без браузера, а playwright не установлен. "
            f"Поставь: {INSTALL_HINT}\n"
            "Или сохрани страницу турнира (Ctrl+S) и дай боту файл: /scan <путь.html>")
    return sync_playwright


_STATE_JS = ("() => { const s = window.__INITIAL_STATE__;"
             " return s ? JSON.stringify(s) : null; }")


def _render(url: str, timeout: float, check_url=None) -> str:
    """Headless Chromium. check_url(final_url) — проверка, куда в итоге привела навигация."""
    sync_playwright = _sync_playwright()
    ms = int(timeout * 1000)
    with sync_playwright() as p:
        browser, err = None, None
        for channel in (None, "chrome"):   # chromium от playwright, затем системный Chrome
            try:
                browser = p.chromium.launch(
                    headless=True, channel=channel,
                    args=["--disable-blink-features=AutomationControlled"])
                break
            except Exception as e:
                err = err or e
        if browser is None:
            raise CPFetchError(f"Chromium не запустился ({str(err).splitlines()[0]}). Поставь: {INSTALL_HINT}")
        try:
            ctx = browser.new_context(user_agent=USER_AGENT, locale="ru-RU",
                                      viewport={"width": 1366, "height": 900})
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=ms)
            except Exception as e:
                raise CPFetchError(f"Страница не открылась: {str(e).splitlines()[0]}")
            # ждём стейт дашборда (после челленджа Cloudflare), иначе — тишину в сети
            try:
                page.wait_for_function(
                    "() => !!(window.__INITIAL_STATE__ && window.__INITIAL_STATE__.rooms)", timeout=ms)
            except Exception:
                try:
                    page.wait_for_load_state("networkidle", timeout=min(ms, 15000))
                except Exception:
                    pass
            if check_url:
                check_url(page.url)
            html = page.content()
            state = page.evaluate(_STATE_JS)
        finally:
            browser.close()
    if state and not has_dashboard(html):
        # SPA могла удалить исходный <script> — возвращаем стейт в виде, понятном parser_cp
        html += ("<script>window.__INITIAL_STATE__=" + state.replace("</", "<\\/") + ";</script>")
    return html


def fetch_rendered(url: str, timeout: float = 60) -> str:
    """Headless Chromium → HTML отрисованной страницы турнира."""
    return _render(validate_url(url), timeout, check_url=validate_url)


def fetch_page(url: str, timeout: float = 60, progress=None) -> str:
    """/scan <url>: plain → браузер. progress(text) — колбэк статуса (из потока)."""
    url = validate_url(url)
    html = fetch_plain(url, timeout=min(timeout, 20))
    if html:
        return html
    if progress:
        progress("🌐 Без браузера сайт данные не отдал — открываю headless Chromium…")
    html = fetch_rendered(url, timeout)
    if not has_dashboard(html):
        raise CPFetchError(
            "Cloudflare не пропустил браузер-автомат (Just a moment). Сохрани страницу "
            "турнира в обычном браузере (Ctrl+S, «веб-страница целиком») и дай боту "
            "файл: /scan <путь.html>")
    return html
