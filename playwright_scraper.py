"""Capture Pocket Option or Quotex sessions for the signal bot.

Cloudscraper is tried first for CSRF-protected form login. Browser automation
then captures the broker's websocket/session data, or provides a headed manual
login fallback when the HTTP login path is unavailable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

load_dotenv()

BROKERS = ("pocket_option", "quotex")
BROKER_CONFIG = {
    "pocket_option": {
        "prefix": "POCKETOPTION",
        "url": "https://pocketoption.com/en/cabinet/",
        "profile": "data/pocketoption-playwright",
    },
    "quotex": {
        "prefix": "QUOTEX",
        "url": "https://www.quotex.com/en/sign-in/",
        "profile": "data/quotex-playwright",
    },
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _quotex_trade_url(is_demo: bool) -> str:
    page = "demo-trade" if is_demo else "trade"
    return f"https://www.quotex.com/en/{page}/"


def _quotex_profile_path() -> Path:
    configured_path = Path(
        os.getenv("QUOTEX_PLAYWRIGHT_PROFILE", "data/quotex-playwright")
    ).expanduser()
    if not configured_path.is_absolute():
        configured_path = Path(__file__).resolve().parent / configured_path
    return configured_path


def _is_quotex_trade_page(url: str, is_demo: bool) -> bool:
    path = urlparse(url).path.rstrip("/")
    expected_path = "/en/demo-trade" if is_demo else "/en/trade"
    return path == expected_path


def _quotex_socket_event(payload: str | bytes) -> tuple[str, Any] | None:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    payload = payload.strip()
    if not payload.startswith("42"):
        return None
    try:
        message = json.loads(payload[2:])
    except json.JSONDecodeError:
        return None
    if not isinstance(message, list) or not message or not isinstance(message[0], str):
        return None
    return message[0], message[1] if len(message) > 1 else None


def _quotex_authorization_ssid(payload: str | bytes, is_demo: bool) -> str | None:
    event = _quotex_socket_event(payload)
    if not event or event[0] != "authorization":
        return None
    details = event[1]
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            return None
    if not isinstance(details, dict) or not details.get("session"):
        return None
    try:
        frame_is_demo = bool(int(details.get("isDemo")))
    except (TypeError, ValueError):
        return None
    if frame_is_demo != is_demo:
        return None
    return f'42["authorization",{json.dumps(details, separators=(",", ":"))}]'


def _has_quotex_browser_auth(session: dict[str, Any]) -> bool:
    return session.get("browser_authenticated") is True


def _listen_for_quotex_authorization(
    page: Any,
    is_demo: bool,
    get_future: Callable[[], asyncio.Future[str] | None],
    get_generation: Callable[[], int] = lambda: 0,
) -> None:
    def watch_socket(websocket: Any) -> None:
        pending_ssid: str | None = None
        socket_generation = get_generation()

        def on_frame_sent(payload: Any) -> None:
            nonlocal pending_ssid
            frame = _quotex_authorization_ssid(payload, is_demo)
            if frame:
                pending_ssid = frame

        def on_frame_received(payload: Any) -> None:
            future = get_future()
            if (
                not pending_ssid
                or future is None
                or future.done()
                or socket_generation != get_generation()
            ):
                return
            event = _quotex_socket_event(payload)
            if event and event[0] == "s_authorization":
                future.set_result(pending_ssid)

        websocket.on("framesent", on_frame_sent)
        websocket.on("framereceived", on_frame_received)

    page.on("websocket", watch_socket)


class QuotexBrowserSession:
    """Keep a persistent Quotex browser profile available for fresh auth frames."""

    def __init__(
        self,
        is_demo: bool,
        *,
        headless: bool = False,
        timeout_seconds: int = 120,
        email: str | None = None,
        password: str | None = None,
    ):
        self.is_demo = is_demo
        self.headless = headless
        self.timeout_seconds = timeout_seconds
        self.email = email
        self.password = password
        self._playwright: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._auth_future: asyncio.Future[str] | None = None
        self._refresh_lock = asyncio.Lock()
        self._auth_generation = 0

    @property
    def is_started(self) -> bool:
        return self._context is not None and self._page is not None

    async def start(self) -> str:
        if self.is_started:
            return await self.refresh_authorization()
        if not (self.email and self.password):
            _confirm_manual_browser_login("quotex")
            self.headless = False

        try:
            self._playwright = await async_playwright().start()
            profile_path = _quotex_profile_path()
            profile_path.mkdir(parents=True, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(profile_path),
                executable_path=resolve_edge_binary(),
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 768},
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            self._new_auth_future()
            _listen_for_quotex_authorization(
                self._page,
                self.is_demo,
                lambda: self._auth_future,
                lambda: self._auth_generation,
            )
            await self._page.goto(
                _quotex_trade_url(self.is_demo),
                wait_until="commit",
                timeout=45_000,
            )
            try:
                return await asyncio.wait_for(
                    asyncio.shield(self._auth_future),
                    timeout=min(self.timeout_seconds, 10),
                )
            except asyncio.TimeoutError:
                pass

            if self.email and self.password:
                await self._sign_in_with_credentials()
            return await self._wait_for_authorization()
        except BaseException:
            if not self.is_started:
                await self.close()
            raise

    async def _sign_in_with_credentials(self) -> None:
        page = self._page
        email, password = self.email, self.password
        if page is None or not email or not password:
            raise RuntimeError("Quotex browser page is not available")

        await page.goto(BROKER_CONFIG["quotex"]["url"], wait_until="commit", timeout=45_000)
        login_form = page.locator('form[action*="sign-in" i]')
        login_fields = login_form.locator(
            'input[name="email"], input[name="username"], input[name="login"]'
        ).first
        await login_fields.wait_for(state="visible", timeout=self.timeout_seconds * 1000)
        await login_fields.fill(email)
        await login_form.locator('input[name="password"]').first.fill(password)
        submit = login_form.locator(
            'button[type="submit"], input[type="submit"], button:has-text("Sign in")'
        ).first
        await submit.click()
        try:
            await page.wait_for_url(
                lambda url: "/sign-in" not in str(url),
                timeout=min(self.timeout_seconds, 45) * 1000,
            )
        except PlaywrightTimeoutError as exc:
            if await login_form.locator('input[name="password"]').count():
                raise RuntimeError(
                    "Quotex sign-in did not complete; the account page was not reached."
                ) from exc

        self._new_auth_future()
        await page.goto(
            _quotex_trade_url(self.is_demo),
            wait_until="commit",
            timeout=45_000,
        )

    async def refresh_authorization(self) -> str:
        async with self._refresh_lock:
            if not self.is_started:
                return await self.start()

            self._new_auth_future()
            await self._page.goto(
                _quotex_trade_url(self.is_demo),
                wait_until="commit",
                timeout=45_000,
            )
            if not _is_quotex_trade_page(self._page.url, self.is_demo):
                raise RuntimeError(
                    f"Quotex browser left the requested {'DEMO' if self.is_demo else 'LIVE'} page; "
                    "complete sign-in in the visible persistent browser, then retry the broker connection."
                )
            return await self._wait_for_authorization()

    def _new_auth_future(self) -> None:
        self._auth_generation += 1
        self._auth_future = asyncio.get_running_loop().create_future()

    async def _wait_for_authorization(self) -> str:
        try:
            frame = await asyncio.wait_for(
                asyncio.shield(self._auth_future),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"No successful Quotex {'DEMO' if self.is_demo else 'LIVE'} browser authorization was observed. "
                "The persistent browser remains open. Complete sign-in/CAPTCHA there, then retry "
                "the broker connection."
            ) from exc
        if not _is_quotex_trade_page(self._page.url, self.is_demo):
            raise RuntimeError(
                f"Quotex redirected away from the requested {'DEMO' if self.is_demo else 'LIVE'} page."
            )
        return frame

    async def close(self) -> None:
        context, playwright = self._context, self._playwright
        self._context = None
        self._playwright = None
        self._page = None
        self._auth_future = None
        try:
            if context is not None:
                await context.close()
        finally:
            if playwright is not None:
                await playwright.stop()


def _set_env_values(values: dict[str, str]) -> None:
    """Atomically update selected .env keys without disturbing other values."""
    env_path = Path(__file__).with_name(".env")
    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True) if env_path.exists() else []
    for key, value in values.items():
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        replacement = f"{key}='{escaped}'\n"
        for index, line in enumerate(lines):
            if line.lstrip().startswith(f"{key}="):
                lines[index] = replacement
                break
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(replacement)

    temporary = env_path.with_suffix(".env.tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(env_path)
    os.environ.update(values)


def _pocket_auth_frame(payload: str | bytes) -> tuple[str, str, str] | None:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    payload = payload.strip()
    if not payload.startswith("42"):
        return None
    try:
        message = json.loads(payload[2:])
    except json.JSONDecodeError:
        return None
    if not isinstance(message, list) or len(message) < 2 or message[0] != "auth":
        return None
    details: Any = message[1]
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            return None
    if not isinstance(details, dict):
        return None
    if not all(key in details for key in ("session", "isDemo", "uid")):
        return None
    return str(details["session"]), str(details["isDemo"]), str(details["uid"])


def _make_pocket_ssid(auth: tuple[str, str, str]) -> str:
    session, is_demo, uid = auth
    frame = {
        "session": session,
        "isDemo": int(is_demo),
        "uid": int(uid),
        "platform": 2,
        "isFastHistory": True,
        "isOptimized": True,
    }
    return f'42["auth",{json.dumps(frame, separators=(",", ":"))}]'


def _extract_csrf_form(html: str, base_url: str) -> tuple[str, dict[str, str]] | None:
    """Find a sign-in form and its CSRF token, tolerating broker markup changes."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    email_or_username = (
        'input[type="email"], input[name*="email" i], '
        'input[name*="user" i], input[type="text"]'
    )
    credential_forms = [
        candidate
        for candidate in forms
        if candidate.select_one('input[type="password"]')
        and candidate.select_one(email_or_username)
    ]
    form = next(
        (
            candidate
            for candidate in credential_forms
            if re.search(
                r"sign[-_]?in|login",
                urlparse(urljoin(base_url, str(candidate.get("action") or base_url))).path,
                re.I,
            )
        ),
        None,
    )
    if form is None and len(credential_forms) == 1:
        form = credential_forms[0]
    if form is None:
        return None

    fields: dict[str, str] = {}
    for element in form.select("input[name]"):
        name = str(element.get("name"))
        value = str(element.get("value") or "")
        if element.get("type", "").lower() == "hidden":
            fields[name] = value

    csrf = next(
        (value for name, value in fields.items() if "csrf" in name.lower() or name.lower() == "_token"),
        None,
    )
    if not csrf:
        meta = soup.select_one('meta[name="csrf-token"], meta[name="csrf"]')
        csrf = str(meta.get("content")) if meta and meta.get("content") else None
        if csrf:
            fields["_token"] = csrf
    if not csrf:
        settings = re.search(r"window\.settings\s*=\s*(\{.*?\})", html, re.S)
        if settings:
            try:
                csrf = json.loads(settings.group(1)).get("csrf")
            except (json.JSONDecodeError, TypeError):
                csrf = None
            if csrf:
                fields["_token"] = str(csrf)
    if not csrf:
        return None

    email_input = form.select_one(email_or_username)
    password_input = form.select_one('input[type="password"]')
    if not email_input or not password_input:
        return None
    fields["__email_field"] = str(email_input.get("name"))
    fields["__password_field"] = str(password_input.get("name"))
    fields.setdefault("remember", "1")
    action = urljoin(base_url, str(form.get("action") or base_url))
    return action, fields


def _build_quotex_session(cookies: dict[str, str], token: str | None, is_demo: bool) -> dict[str, Any] | None:
    cookie_session = next(
        (value for name, value in cookies.items() if name.lower() in {"session", "ssid", "qx_session"} and value),
        None,
    )
    source = cookie_session or token
    if not source:
        return None
    ssid = "42[\"authorization\"," + json.dumps(
        {"session": source, "isDemo": 1 if is_demo else 0, "tournamentId": 0},
        separators=(",", ":"),
    ) + "]"
    return {
        "ssid": ssid,
        "token": token,
        "cookies": "; ".join(f"{key}={value}" for key, value in cookies.items()),
        "is_demo": is_demo,
    }


def _settings_token(html: str) -> str | None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        text = (script.get_text() or "").strip()
        if "window.settings" not in text:
            continue
        candidate = re.sub(r"^window\.settings\s*=\s*", "", text.removesuffix(";"))
        try:
            token = json.loads(candidate).get("token")
        except (json.JSONDecodeError, AttributeError):
            continue
        if token:
            return str(token)
    match = re.search(r"window\.settings\s*=\s*(\{.*?\})", html, re.S)
    if match:
        try:
            token = json.loads(match.group(1)).get("token")
            return str(token) if token else None
        except (json.JSONDecodeError, AttributeError):
            return None
    return None


def _new_cloudscraper() -> Any:
    try:
        import cloudscraper
    except ImportError as exc:
        raise RuntimeError("Cloudscraper login requires `cloudscraper`; install project dependencies.") from exc
    return cloudscraper.create_scraper()


def _cloud_login(
    broker: str, email: str, password: str, is_demo: bool
) -> tuple[Any, dict[str, Any] | None]:
    """Try the broker's CSRF form login and return its session/captured data."""
    scraper = _new_cloudscraper()
    config = BROKER_CONFIG[broker]
    if broker == "quotex":
        origin = "https://quotex.com"
        login_url = f"{origin}/en/sign-in/"
        target_url = _quotex_trade_url(is_demo)
        attempts = [login_url, f"{origin}/en/login/", target_url]
    else:
        origin = "https://pocketoption.com"
        target_url = config["url"]
        attempts = [f"{origin}/en/sign-in/", f"{origin}/en/login/", target_url]

    form_data: tuple[str, dict[str, str]] | None = None
    login_page = ""
    for url in attempts:
        response = scraper.get(url, timeout=30, headers={"Referer": url})
        if response.status_code != 200:
            continue
        login_page = response.url
        form_data = _extract_csrf_form(response.text, response.url)
        if form_data:
            break
    if not form_data:
        raise RuntimeError(f"No CSRF-protected email/password form found for {broker}")

    post_url, payload = form_data
    email_field = payload.pop("__email_field")
    password_field = payload.pop("__password_field")
    payload[email_field] = email
    payload[password_field] = password
    response = scraper.post(
        post_url,
        data=payload,
        headers={"Referer": login_page, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
        allow_redirects=True,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{broker} login POST returned HTTP {response.status_code}")

    target = scraper.get(target_url, timeout=30, headers={"Referer": login_page})
    if target.status_code >= 400:
        raise RuntimeError(f"{broker} authenticated page returned HTTP {target.status_code}")
    if broker == "quotex" and not _is_quotex_trade_page(target.url, is_demo):
        raise RuntimeError(
            f"Quotex redirected away from the requested {'DEMO' if is_demo else 'LIVE'} trade page; refusing session capture."
        )
    cookie_values = scraper.cookies.get_dict()
    if broker == "quotex":
        token = _settings_token(target.text)
        session = _build_quotex_session(cookie_values, token, is_demo)
        if not session:
            raise RuntimeError("Quotex login returned no session cookie or authorization token")
        return scraper, session

    # Pocket's usable SSID is the `42["auth", ...]` websocket frame, not just
    # an HTTP cookie. Pass the successful Cloudscraper cookies to Playwright,
    # which captures that canonical frame from the live websocket.
    if not cookie_values:
        raise RuntimeError("Pocket Option login returned no cookies")
    return scraper, None


def _browser_cookies(scraper: Any, broker: str) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    for cookie in scraper.cookies:
        domain = cookie.domain or urlparse(BROKER_CONFIG[broker]["url"]).hostname
        item: dict[str, Any] = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": domain,
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "httpOnly": bool(getattr(cookie, "_rest", {}).get("HttpOnly", False)),
        }
        if cookie.expires and cookie.expires > 0:
            item["expires"] = float(cookie.expires)
        cookies.append(item)
    return cookies


def resolve_edge_binary() -> str:
    configured = os.getenv("EDGE_BINARY")
    candidates = [
        configured,
        shutil.which("microsoft-edge-stable"),
        shutil.which("microsoft-edge"),
        "/opt/microsoft/msedge/msedge",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            if candidate != configured:
                print(f"Using Edge at {candidate}")
            return str(candidate)
    raise RuntimeError("Microsoft Edge not found; set EDGE_BINARY to the browser executable.")


def _save_broker_session(broker: str, session: dict[str, Any]) -> None:
    prefix = BROKER_CONFIG[broker]["prefix"]
    values = {f"{prefix}_SSID": str(session["ssid"])}
    if broker == "pocket_option":
        values[f"{prefix}_UID"] = str(session["uid"])
    values[f"{prefix}_ACCOUNT_TYPE"] = "DEMO" if session["is_demo"] else "REAL"
    _set_env_values(values)


def _confirm_manual_browser_login(broker: str) -> None:
    prefix = BROKER_CONFIG[broker]["prefix"]
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"{prefix}_EMAIL and {prefix}_PASSWORD are unavailable; refusing to open a "
            "browser without confirmation. Configure both credentials or retry interactively."
        )
    answer = input(
        f"No complete {prefix}_EMAIL/{prefix}_PASSWORD credentials are configured. "
        f"Open the {broker.replace('_', ' ').title()} browser for manual sign-in? [y/N]: "
    ).strip().lower()
    if answer not in {"y", "yes"}:
        raise RuntimeError("Manual browser sign-in was not confirmed.")


async def _validate_quotex_session(session: dict[str, Any], is_demo: bool) -> bool:
    """Check a captured Quotex frame using the API's real websocket auth path."""
    from api_quotex.client import AsyncQuotexClient

    client = AsyncQuotexClient(ssid=str(session["ssid"]), is_demo=is_demo)
    try:
        return bool(await client.connect(persistent=False))
    finally:
        await client.disconnect()


async def _capture_pocket_frame(
    account_type: str,
    timeout_seconds: int,
    headless: bool,
    scraper: Any | None,
    email: str | None,
    password: str | None,
) -> dict[str, Any]:
    edge_binary = resolve_edge_binary()
    profile_path = Path(os.getenv("POCKETOPTION_PLAYWRIGHT_PROFILE", "data/pocketoption-playwright"))
    profile_path.mkdir(parents=True, exist_ok=True)
    found: asyncio.Future[dict[str, Any]] | None = None
    expected_demo = "1" if account_type == "DEMO" else "0"

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_path),
            executable_path=edge_binary,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport=None,
        )
        try:
            if scraper is not None:
                await context.add_cookies(_browser_cookies(scraper, "pocket_option"))
            page = context.pages[0] if context.pages else await context.new_page()
            loop = asyncio.get_running_loop()
            found = loop.create_future()

            def watch_socket(websocket: Any) -> None:
                def inspect(payload: Any) -> None:
                    auth = _pocket_auth_frame(payload)
                    if not auth or auth[1] != expected_demo or found is None or found.done():
                        return
                    found.set_result({
                        "ssid": _make_pocket_ssid(auth),
                        "uid": auth[2],
                        "is_demo": expected_demo == "1",
                    })

                websocket.on("framereceived", inspect)
                websocket.on("framesent", inspect)

            for existing_page in context.pages:
                existing_page.on("websocket", watch_socket)
            context.on("page", lambda new_page: new_page.on("websocket", watch_socket))
            await page.goto(BROKER_CONFIG["pocket_option"]["url"], wait_until="domcontentloaded")
            print("Pocket Option browser open; waiting for the matching account auth frame.")

            # If Cloudscraper did not establish the session, submit credentials
            # through the browser when the page exposes a conventional login form.
            if scraper is None and email and password:
                for selector in ('input[type="email"]', 'input[name*="email" i]', 'input[type="text"]'):
                    field = page.locator(selector).first
                    if await field.count():
                        await field.fill(email)
                        break
                password_field = page.locator('input[type="password"]').first
                if await password_field.count():
                    await password_field.fill(password)
                    submit = page.locator('button[type="submit"], input[type="submit"]').first
                    if await submit.count():
                        await submit.click()

            try:
                return await asyncio.wait_for(found, timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
                hint = "Use a headed run to complete any manual login/CAPTCHA." if headless else "Keep the authenticated cabinet page open."
                raise RuntimeError(f"No matching Pocket Option auth frame received. {hint}") from exc
        finally:
            await context.close()


async def _capture_quotex_browser(
    account_type: str,
    timeout_seconds: int,
    headless: bool,
    scraper: Any | None,
    email: str | None,
    password: str | None,
) -> dict[str, Any]:
    edge_binary = resolve_edge_binary()
    profile_path = _quotex_profile_path()
    profile_path.mkdir(parents=True, exist_ok=True)
    is_demo = account_type == "DEMO"
    auth_url = BROKER_CONFIG["quotex"]["url"]
    target_url = _quotex_trade_url(is_demo)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_path),
            executable_path=edge_binary,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 768},
        )
        try:
            if scraper is not None:
                await context.add_cookies(_browser_cookies(scraper, "quotex"))
            page = context.pages[0] if context.pages else await context.new_page()
            loop = asyncio.get_running_loop()
            authenticated: asyncio.Future[str] = loop.create_future()
            _listen_for_quotex_authorization(
                page,
                is_demo,
                lambda: authenticated,
            )

            async def wait_for_browser_auth(timeout: int) -> dict[str, Any] | None:
                try:
                    frame = await asyncio.wait_for(asyncio.shield(authenticated), timeout=timeout)
                    return {
                        "ssid": frame,
                        "is_demo": is_demo,
                        "browser_authenticated": True,
                    }
                except asyncio.TimeoutError:
                    return None

            await page.goto(target_url, wait_until="commit", timeout=45_000)
            session = await wait_for_browser_auth(min(timeout_seconds, 10))
            if session:
                return session

            if email and password:
                if "/sign-in" not in page.url:
                    await page.goto(auth_url, wait_until="commit", timeout=45_000)

                login_form = page.locator('form[action*="sign-in" i]')
                await login_form.locator('input[name="email"], input[name="username"], input[name="login"]').first.wait_for(
                    state="visible", timeout=timeout_seconds * 1000
                )
                await login_form.locator('input[name="email"], input[name="username"], input[name="login"]').first.fill(email)
                await login_form.locator('input[name="password"]').first.fill(password)
                submit = login_form.locator('button[type="submit"], input[type="submit"], button:has-text("Sign in")').first
                await submit.click()
                try:
                    await page.wait_for_url(
                        lambda url: "/sign-in" not in str(url),
                        timeout=min(timeout_seconds, 45) * 1000,
                    )
                except Exception as exc:
                    if await login_form.locator('input[name="password"]').count():
                        raise RuntimeError("Quotex sign-in did not complete; the account page was not reached.") from exc
                await page.goto(target_url, wait_until="commit", timeout=45_000)
                if not _is_quotex_trade_page(page.url, is_demo):
                    raise RuntimeError(
                        f"Quotex redirected away from the requested {'DEMO' if is_demo else 'LIVE'} trade page; refusing session capture."
                    )
                session = await wait_for_browser_auth(timeout_seconds)
                if session:
                    return session
                raise RuntimeError(
                    f"No successful Quotex {'DEMO' if is_demo else 'LIVE'} WebSocket authorization was observed after sign-in."
                )

            if headless:
                raise RuntimeError("Quotex WebSocket authorization was not observed; retry headed for manual login/CAPTCHA.")
            print("Quotex browser open; log in and leave the trading page open.")
            session = await wait_for_browser_auth(timeout_seconds)
            if not session:
                await page.goto(target_url, wait_until="commit", timeout=45_000)
                session = await wait_for_browser_auth(20)
            if not session:
                raise RuntimeError(
                    f"No successful Quotex {'DEMO' if is_demo else 'LIVE'} WebSocket authorization was observed."
                )
            return session
        finally:
            await context.close()


async def capture_session(
    broker: str,
    account_type: str,
    *,
    email: str | None = None,
    password: str | None = None,
    headless: bool = False,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Capture and persist the selected broker's session; reusable by main.py."""
    broker = broker.strip().lower().replace("-", "_")
    if broker not in BROKERS:
        raise ValueError(f"Unsupported broker {broker!r}; choose from {', '.join(BROKERS)}")
    account_type = account_type.upper()
    if account_type not in {"DEMO", "REAL"}:
        raise ValueError("account_type must be DEMO or REAL")

    prefix = BROKER_CONFIG[broker]["prefix"]
    email = email or os.getenv(f"{prefix}_EMAIL")
    password = password or os.getenv(f"{prefix}_PASSWORD")
    if not (email and password):
        _confirm_manual_browser_login(broker)
        headless = False
    is_demo = account_type == "DEMO"
    scraper = None
    session: dict[str, Any] | None = None

    if email and password:
        try:
            scraper, http_session = await asyncio.to_thread(
                _cloud_login, broker, email, password, is_demo
            )
            if broker == "quotex":
                print(
                    "Cloudscraper login succeeded for quotex; transferring its cookies "
                    "into the persistent browser profile."
                )
            else:
                session = http_session
                print(f"Cloudscraper login succeeded for {broker}; extracting broker session.")
        except Exception as exc:
            print(f"Cloudscraper login unavailable for {broker}: {exc}", file=sys.stderr)
            scraper = None
            session = None
    else:
        print(f"No {prefix}_EMAIL/{prefix}_PASSWORD credentials found; using browser login/capture.")

    if broker == "pocket_option":
        session = await _capture_pocket_frame(
            account_type, timeout_seconds, headless, scraper, email, password
        )
    else:
        session = await _capture_quotex_browser(
            account_type, timeout_seconds, headless, scraper, email, password
        )
        if not await _validate_quotex_session(session, is_demo):
            if not _has_quotex_browser_auth(session):
                raise RuntimeError("Quotex rejected the browser-captured authorization session")
            print(
                "Quotex confirmed the browser session, but standalone validation is unavailable; "
                "the broker adapter will remain disconnected until its WebSocket endpoint resolves."
            )

    _save_broker_session(broker, session)
    print(f"Captured {account_type} {broker} session.")
    return session


def _prompt_value(prompt: str, current: str | None, *, secret: bool = False) -> str:
    if current:
        return current
    if not sys.stdin.isatty():
        raise RuntimeError(f"{prompt.rstrip(': ')} is missing and stdin is not interactive.")
    if secret:
        import getpass

        return getpass.getpass(prompt)
    return input(prompt).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a Pocket Option or Quotex session")
    parser.add_argument(
        "--broker",
        choices=("pocket_option", "pocket-option", "quotex"),
        help="Broker session to capture",
    )
    parser.add_argument("--account-type", choices=("DEMO", "REAL"), help="Account environment")
    parser.add_argument("--email", help="Login email; otherwise use broker .env value or prompt")
    parser.add_argument("--password", help="Login password; otherwise use broker .env value or prompt")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="Run browser fallback without a visible window")
    mode.add_argument("--headed", action="store_true", help="Show browser fallback window")
    parser.add_argument("--timeout", type=int, default=120, help="Seconds to wait for browser authentication")
    args = parser.parse_args()

    broker = args.broker
    if not broker:
        broker = _prompt_value("Broker (pocket_option/quotex): ", None).lower().replace("-", "_")
    else:
        broker = broker.strip().lower().replace("-", "_")
    if broker not in BROKERS:
        parser.error(f"unsupported broker {broker!r}; choose pocket_option or quotex")
    prefix = BROKER_CONFIG[broker]["prefix"]
    account_type = args.account_type or os.getenv(f"{prefix}_ACCOUNT_TYPE")
    if not account_type:
        account_type = _prompt_value("Account type (DEMO/REAL): ", None).upper()
    if account_type.upper() not in {"DEMO", "REAL"}:
        parser.error("account type must be DEMO or REAL")

    email = args.email or os.getenv(f"{prefix}_EMAIL")
    password = args.password or os.getenv(f"{prefix}_PASSWORD")
    email = _prompt_value("Email: ", email)
    password = _prompt_value("Password: ", password, secret=True)

    if args.headless:
        headless = True
    elif args.headed:
        headless = False
    else:
        headless = _env_bool(f"{prefix}_SCRAPER_HEADLESS", False)

    try:
        asyncio.run(capture_session(
            broker,
            account_type,
            email=email,
            password=password,
            headless=headless,
            timeout_seconds=args.timeout,
        ))
    except Exception as exc:
        parser.exit(1, f"Session capture failed: {exc}\n")


if __name__ == "__main__":
    main()