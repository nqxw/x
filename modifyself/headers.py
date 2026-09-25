import base64
import json
import os
import re
import time
import uuid
import random
import hashlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List, Any, Union

import urllib.request as _urllib_req

from wreq import Emulation

_GREASE_BRANDS = [
    ('"Not A(Brand"', "99"),
    ('"Not)A;Brand"', "99"),
    ('"Not/A)Brand"', "99"),
    ('"Not=A?Brand"', "99"),
    ('"Not_A Brand"', "99"),
]

LOCATIONS = [
    {"timezone": "America/New_York", "locale": "en-US"},
    {"timezone": "America/Chicago", "locale": "en-US"},
    {"timezone": "America/Denver", "locale": "en-US"},
    {"timezone": "America/Los_Angeles", "locale": "en-US"},
    {"timezone": "Europe/London", "locale": "en-GB"},
    {"timezone": "Europe/Berlin", "locale": "de-DE"},
    {"timezone": "Europe/Paris", "locale": "fr-FR"},
    {"timezone": "Asia/Tokyo", "locale": "ja-JP"},
    {"timezone": "Australia/Sydney", "locale": "en-AU"},
    {"timezone": "America/Toronto", "locale": "en-CA"},
    {"timezone": "America/Sao_Paulo", "locale": "pt-BR"},
]

CONTEXT_LOCATIONS = {
    "chat": "chat_input",
    "guild": "guild_header",
    "profile": "user_profile",
    "dm": "dm_channel",
    "search": "search",
    "context_menu": "context_menu",
    "add_friend": "add_friend_navbar",
    "join_guild": "join_guild",
    "settings": "user_settings",
}

EMULATION = Emulation.Chrome149


@dataclass
class BrowserProfile:
    user_agent: str
    chrome_major: str
    browser_version: str
    locale: str
    timezone: str


class HeaderSpoofer:
    _build_cache: Optional[int] = None
    _build_cache_time: float = 0.0
    _chrome_cache: Optional[Dict] = None
    _chrome_cache_time: float = 0.0
    BUILD_CACHE_TTL = 3600

    def __init__(self, token: str, emulation: Emulation = EMULATION):
        self.token = token
        self._app_state = "focused"
        self._seed = int(hashlib.md5(token.encode()).hexdigest(), 16)
        self._rng = random.Random(self._seed)
        self._emulation = emulation
        self.profile = self._make_profile()
        self._async_client: Optional[Any] = None
        self.build_number = self._get_build_number()
        self._launch_id = str(uuid.uuid4())
        self._launch_signature = str(uuid.uuid4())
        self._heartbeat_session_id = str(uuid.uuid4())
        self._installation_id = str(uuid.uuid4())
        self._science_token = self._make_science_token()
        self._grease_brand = self._rng.choice(_GREASE_BRANDS)

    def set_app_state(self, state: str) -> None:
        if state in ["focused", "unfocused"]:
            self._app_state = state

    def get_async_client(self) -> Any:
        from wreq import Client
        if self._async_client is None:
            self._async_client = Client(emulation=self._emulation)
        return self._async_client

    async def close(self) -> None:
        if self._async_client is not None:
            await self._async_client.close()
            self._async_client = None

    def _get_latest_chrome(self) -> str:
        now = time.time()
        if HeaderSpoofer._chrome_cache and now - HeaderSpoofer._chrome_cache_time < 86400:
            return HeaderSpoofer._chrome_cache["major"]

        cache_file = os.path.join(os.path.dirname(__file__), ".chrome_version.json")
        try:
            _req = _urllib_req.Request(
                "https://versionhistory.googleapis.com/v1/chrome/platforms/win/channels/stable/versions/all/releases?filter=endtime=none",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with _urllib_req.urlopen(_req, timeout=5) as _resp:
                data = json.loads(_resp.read())
            latest = max(data["releases"], key=lambda x: [int(p) for p in x["version"].split(".")])
            major = latest["version"].split(".")[0]
            HeaderSpoofer._chrome_cache = {"major": major}
            HeaderSpoofer._chrome_cache_time = now
            try:
                with open(cache_file, "w") as f:
                    json.dump(HeaderSpoofer._chrome_cache, f)
            except Exception:
                pass
            return major
        except Exception:
            try:
                if os.path.exists(cache_file):
                    with open(cache_file) as f:
                        cached = json.load(f)
                    if "major" in cached:
                        HeaderSpoofer._chrome_cache = cached
                        HeaderSpoofer._chrome_cache_time = now
                        return cached["major"]
            except Exception:
                pass
            return "150"

    def _make_profile(self) -> BrowserProfile:
        rng = self._rng
        loc = rng.choice(LOCATIONS)
        major = self._get_latest_chrome()
        ua = (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36"
        )
        return BrowserProfile(
            user_agent=ua,
            chrome_major=major,
            browser_version=f"{major}.0.0.0",
            locale=loc["locale"],
            timezone=loc["timezone"],
        )

    def _make_science_token(self) -> str:
        parts = self.token.split(".")
        return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else self.token

    def _get_build_number(self) -> int:
        now = time.time()
        if HeaderSpoofer._build_cache and now - HeaderSpoofer._build_cache_time < self.BUILD_CACHE_TTL:
            return HeaderSpoofer._build_cache

        build_cache_file = os.path.join(os.path.dirname(__file__), ".build_cache.json")

        try:
            _req1 = _urllib_req.Request(
                "https://discord.com/login",
                headers={"User-Agent": self.profile.user_agent},
            )
            with _urllib_req.urlopen(_req1, timeout=30) as _resp1:
                _html = _resp1.read().decode("utf-8", errors="replace")
            scripts = re.findall(r'<script[^>]+src="(/assets/[^"]+\.js)"', _html)
            for path in reversed(scripts):
                _req2 = _urllib_req.Request(
                    f"https://discord.com{path}",
                    headers={"User-Agent": self.profile.user_agent},
                )
                with _urllib_req.urlopen(_req2, timeout=30) as _resp2:
                    _js_text = _resp2.read().decode("utf-8", errors="replace")
                for pat in [
                    r'buildNumber\s*[=:]\s*"?(\d{5,7})"?',
                    r'"buildNumber","(\d{5,7})"',
                    r'CLIENT_BUILD_NUMBER\s*[=:]\s*(\d{5,7})',
                    r'build_number["\s:=]+(\d{5,7})',
                    r'"(\d{6,7})"\s*,\s*"stable"',
                ]:
                    m = re.search(pat, _js_text)
                    if m:
                        build = int(m.group(1))
                        HeaderSpoofer._build_cache = build
                        HeaderSpoofer._build_cache_time = now
                        try:
                            with open(build_cache_file, "w") as f:
                                json.dump({"build": build}, f)
                        except Exception:
                            pass
                        return build
        except Exception:
            pass

        try:
            if os.path.exists(build_cache_file):
                with open(build_cache_file) as f:
                    cached = json.load(f)
                if "build" in cached:
                    HeaderSpoofer._build_cache = cached["build"]
                    HeaderSpoofer._build_cache_time = now
                    return cached["build"]
        except Exception:
            pass

        fallback = 611316
        HeaderSpoofer._build_cache = fallback
        HeaderSpoofer._build_cache_time = now
        return fallback

    def refresh_build_number(self) -> int:
        HeaderSpoofer._build_cache_time = 0.0
        self.build_number = self._get_build_number()
        return self.build_number

    def _xsp(self, referrer_current: str = "", referring_domain_current: str = "") -> str:
        # Mirrors getSuperProperties() in Discord's web client.
        # is_fast_connect, gateway_connect_reasons, installation_id are
        # added to the gateway identify payload separately — not part of base super props.
        props = {
            "os": "Windows",
            "browser": "Chrome",
            "device": "",
            "system_locale": self.profile.locale,
            "has_client_mods": False,
            "browser_user_agent": self.profile.user_agent,
            "browser_version": self.profile.browser_version,
            "os_version": "10",
            "referrer": "",
            "referring_domain": "",
            "referrer_current": referrer_current,
            "referring_domain_current": referring_domain_current,
            "release_channel": "stable",
            "client_build_number": self.build_number,
            "client_event_source": None,
            "client_launch_id": self._launch_id,
            "launch_signature": self._launch_signature,
            "client_app_state": self._app_state,
            "client_heartbeat_session_id": self._heartbeat_session_id,
        }
        return base64.b64encode(json.dumps(props, separators=(",", ":")).encode()).decode()

    def _sec_ch_ua(self) -> str:
        v = self.profile.chrome_major
        brand, gv = self._grease_brand
        entries = [
            f'{brand};v="{gv}"',
            f'"Chromium";v="{v}"',
            f'"Google Chrome";v="{v}"',
        ]
        rotation = self._rng.choice([
            [entries[0], entries[1], entries[2]],
            [entries[1], entries[2], entries[0]],
            [entries[1], entries[0], entries[2]],
        ])
        return ", ".join(rotation)

    def _context_props(self, location: str) -> str:
        return base64.b64encode(json.dumps({"location": location}, separators=(",", ":")).encode()).decode()

    def _accept_language(self) -> str:
        locale = self.profile.locale
        lang = locale.split("-")[0]
        # Build like navigator.languages → ["en-US","en"] → "en-US,en;q=0.9"
        if lang == locale:
            return locale
        if lang == "en":
            return f"{locale},en;q=0.9"
        return f"{locale},{lang};q=0.9,en;q=0.8,en-US;q=0.7"

    def _shared(self, referer: str) -> Dict:
        return {
            "User-Agent": self.profile.user_agent,
            "Accept": "*/*",
            "Accept-Language": self._accept_language(),
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Origin": "https://discord.com",
            "Referer": referer,
            "Sec-Ch-Ua": self._sec_ch_ua(),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Debug-Options": "bugReporterEnabled",
            "X-Discord-Locale": self.profile.locale,
            "X-Discord-Timezone": self.profile.timezone,
            "X-Super-Properties": self._xsp(),
            "X-Installation-ID": self._installation_id,
            "Priority": "u=1, i",
        }

    def set_focused(self) -> None:
        self._app_state = "focused"

    def set_unfocused(self) -> None:
        self._app_state = "unfocused"

    def rotate_session(self) -> None:
        self._heartbeat_session_id = str(uuid.uuid4())
        self._launch_id = str(uuid.uuid4())
        self._launch_signature = str(uuid.uuid4())

    def get_headers(
        self,
        referer: str = "https://discord.com/channels/@me",
        context_location: str = "chat_input",
        extra: Optional[Dict] = None,
        skip_context_props: bool = False,
    ) -> Dict:
        h = {
            "Authorization": self.token,
            "Content-Type": "application/json",
            **self._shared(referer),
        }
        if not skip_context_props:
            h["X-Context-Properties"] = self._context_props(context_location)
        if extra:
            h.update(extra)
        return h

    def get_science_headers(self, referer: str = "https://discord.com/channels/@me") -> Dict:
        return {
            "Authorization": self.token,
            "Content-Type": "application/json",
            **self._shared(referer),
        }

    def get_multipart_headers(self, content_type: str, referer: str = "https://discord.com/channels/@me") -> Dict:
        return {
            "Authorization": self.token,
            "Content-Type": content_type,
            **self._shared(referer),
        }

    def get_websocket_headers(self) -> Dict:
        return {
            "User-Agent": self.profile.user_agent,
            "Accept-Language": f"{self.profile.locale},en;q=0.9",
            "Origin": "https://discord.com",
        }