"""Polite HTTP fetches: honest User-Agent, robots.txt, per-host delay."""

from __future__ import annotations

import http.cookiejar
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from scraper.robots import RobotsPolicy, parse_robots

DEFAULT_UA = (
    "bid-watch/1.0 (personal municipal bid tracker; "
    "+https://github.com/solopass/bid-watch)"
)
MIN_INTERVAL_SECONDS = 10
MAX_BYTES = 5_000_000
TIMEOUT = 45


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RobotsDisallowed(FetchError):
    pass


class PoliteFetcher:
    def __init__(self, user_agent: str = DEFAULT_UA, min_interval: int = MIN_INTERVAL_SECONDS):
        self.user_agent = user_agent
        # The floor is 10 seconds. A config file cannot lower it.
        self.min_interval = max(MIN_INTERVAL_SECONDS, int(min_interval))
        # One cookie jar so a public form post (COMMBUYS) can reuse its own session.
        self._opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotsPolicy] = {}
        self._delay: dict[str, int] = {}

    def get(self, url: str) -> str:
        _status, body, content_type, _final = self._read(url, data=None)
        return _decode(body, content_type)

    def get_page(self, url: str) -> tuple[str, str]:
        """Return the decoded body and the final URL after redirects."""
        _status, body, content_type, final = self._read(url, data=None)
        return _decode(body, content_type), final

    def get_bytes(self, url: str) -> tuple[bytes, str]:
        _status, body, _content_type, final = self._read(
            url,
            data=None,
            accept="application/pdf,*/*;q=0.8",
        )
        return body, final

    def post_form(self, url: str, fields: dict[str, str]) -> tuple[int, str, str]:
        status, text, content_type, _final = self.post_form_result(url, fields)
        return status, text, content_type

    def post_form_result(self, url: str, fields: dict[str, str]) -> tuple[int, str, str, str]:
        from urllib.parse import urlencode

        payload = urlencode(fields).encode("utf-8")
        status, body, content_type, final = self._read(
            url,
            data=payload,
            extra_headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return status, _decode(body, content_type), content_type, final

    def _read(
        self,
        url: str,
        data: bytes | None,
        extra_headers: dict | None = None,
        accept: str | None = None,
    ):
        self._ensure_robots(url)
        policy = self._robots[_host_key(url)]
        if not policy.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        self._wait(url)
        request = Request(url, data=data, method="POST" if data is not None else "GET")
        request.add_header("User-Agent", self.user_agent)
        request.add_header(
            "Accept",
            accept or "text/html,application/xhtml+xml,text/csv;q=0.9,*/*;q=0.8",
        )
        request.add_header("Accept-Language", "en-US,en;q=0.9")
        for key, value in (extra_headers or {}).items():
            request.add_header(key, value)
        print(f"{'POST' if data is not None else 'GET'} {url}", flush=True)
        try:
            with self._opener.open(request, timeout=TIMEOUT) as response:
                status = getattr(response, "status", 200)
                final_url = response.geturl()
                content_type = response.headers.get("Content-Type", "")
                body = response.read(MAX_BYTES + 1)
        except HTTPError as exc:
            self._last[_host_key(url)] = time.monotonic()
            detail = exc.read(500).decode("utf-8", "replace") if exc.fp else ""
            raise FetchError(f"HTTP {exc.code} for {url} {detail[:180]}".strip(), exc.code) from exc
        except URLError as exc:
            self._last[_host_key(url)] = time.monotonic()
            raise FetchError(f"could not fetch {url}: {exc.reason}") from exc
        self._last[_host_key(url)] = time.monotonic()
        if not policy.can_fetch(self.user_agent, final_url):
            raise RobotsDisallowed(f"robots.txt disallows redirect target {final_url}")
        if len(body) > MAX_BYTES:
            raise FetchError(f"response from {url} exceeded {MAX_BYTES} bytes")
        if status >= 400:
            raise FetchError(f"HTTP {status} for {url}", status)
        return status, body, content_type, final_url

    def _ensure_robots(self, url: str) -> None:
        parts = urlsplit(url)
        key = _host_key(url)
        if key in self._robots:
            return
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        policy = self._fetch_robots(robots_url, key)
        self._robots[key] = policy
        delay = policy.crawl_delay(self.user_agent) or 0
        self._delay[key] = max(self.min_interval, delay)

    def _fetch_robots(self, robots_url: str, key: str) -> RobotsPolicy:
        # robots.txt is the first contact with a host. It is not gated on
        # itself, and it starts the per-host clock.
        request = Request(robots_url, method="GET")
        request.add_header("User-Agent", self.user_agent)
        request.add_header("Accept", "text/plain,*/*;q=0.8")
        print(f"GET {robots_url}", flush=True)
        try:
            with self._opener.open(request, timeout=TIMEOUT) as response:
                status = getattr(response, "status", 200)
                final = urlsplit(response.geturl())
                body = response.read(200_000)
        except HTTPError as exc:
            self._last[key] = time.monotonic()
            if exc.code in (401, 403):
                return RobotsPolicy.deny_everything()
            if 400 <= exc.code < 500:
                return RobotsPolicy.allow_everything()
            raise FetchError(f"robots.txt returned HTTP {exc.code} for {robots_url}", exc.code) from exc
        except URLError as exc:
            self._last[key] = time.monotonic()
            raise FetchError(f"could not read robots.txt {robots_url}: {exc.reason}") from exc
        self._last[key] = time.monotonic()
        if _host_key_from_netloc(final.netloc) != key:
            # A redirect onto another site is not this host's robots file.
            print(f"robots.txt for {key} redirected off-host; public pages allowed", flush=True)
            return RobotsPolicy.allow_everything()
        if status >= 500:
            raise FetchError(f"robots.txt returned HTTP {status} for {robots_url}", status)
        text = body.decode("utf-8", "replace")
        return parse_robots(text)

    def _wait(self, url: str) -> None:
        key = _host_key(url)
        delay = self._delay.get(key, self.min_interval)
        last = self._last.get(key)
        if last is None:
            return
        remaining = delay - (time.monotonic() - last)
        if remaining > 0:
            print(f"waiting {remaining:.0f}s before the next request to {key}", flush=True)
            time.sleep(remaining)


def _decode(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    match = None
    lowered = content_type.lower()
    if "charset=" in lowered:
        match = lowered.split("charset=", 1)[1].split(";", 1)[0].strip()
    if match:
        charset = match
    try:
        return body.decode(charset, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def _host_key(url: str) -> str:
    return _host_key_from_netloc(urlsplit(url).netloc)


def _host_key_from_netloc(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host
