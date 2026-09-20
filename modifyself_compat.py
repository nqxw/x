# modifyself_compat.py | drop-in adapter — makes modifyself look like discord.py-self
# Chunk 1 of 4 — boot layer. Client wrapper, event rename map, top-level shims.
#
# Usage in selfbot.py:
#     import modifyself
#     import modifyself_compat as discord
#
# Every `discord.X` in selfbot.py resolves through this file.
# Chunk 2: message / channel / user wrappers.
# Chunk 3: guild / member / role / webhook wrappers.
# Chunk 4: File / Activity / ui.View / change_presence / wait_for.

import asyncio
import inspect
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional

import modifyself as _ms
import aiohttp

logger = logging.getLogger("modifyself_compat")


# ─────────────────────────────────────────────────────────────
# ENUMS — re-export modifyself's + add missing discord.py-self names
# ─────────────────────────────────────────────────────────────

ActivityType = _ms.ActivityType
ChannelType = _ms.ChannelType
MessageType = _ms.MessageType
Permissions = _ms.Permissions
ButtonStyle = _ms.ButtonStyle
TextInputStyle = _ms.TextInputStyle
ComponentType = _ms.ComponentType


class Status:
    online = "online"
    idle = "idle"
    dnd = "dnd"
    invisible = "invisible"
    offline = "offline"


class Intents:
    """Placeholder — modifyself doesn't use intents. selfbot.py passes nothing."""
    def __init__(self, **kwargs):
        self._kwargs = kwargs


# ─────────────────────────────────────────────────────────────
# EXCEPTIONS
# ─────────────────────────────────────────────────────────────

class LoginFailure(Exception):
    pass


class HTTPException(Exception):
    def __init__(self, response=None, message: str = ""):
        self.response = response
        self.status = getattr(response, "status", 0) if response else 0
        super().__init__(message)


class NotFound(Exception):
    pass


# ─────────────────────────────────────────────────────────────
# ACTIVITY SHIMS — chunk 4 will extend, minimal here so imports work
# ─────────────────────────────────────────────────────────────

class Game:
    def __init__(self, name: str):
        self.name = name
        self.type = 0


class Activity:
    def __init__(self, *, type=0, name=None, url=None, **kw):
        self.type = type
        self.name = name
        self.url = url
        self.details = kw.get("details")
        self.state = kw.get("state")
        self.timestamps = kw.get("timestamps")
        self.assets = kw.get("assets")
        self.application_id = kw.get("application_id")
        self.instance = kw.get("instance", True)
        self.flags = kw.get("flags", 0)
        self.buttons = kw.get("buttons")


class CustomActivity:
    def __init__(self, name=None, emoji=None):
        self.name = name
        self.emoji = emoji
        self.type = 4


# ─────────────────────────────────────────────────────────────
# FILE SHIM — chunk 4 will wire real uploads. Stub for imports.
# ─────────────────────────────────────────────────────────────

class File:
    def __init__(self, fp, filename=None, spoiler=False):
        self.fp = fp
        self.filename = filename or getattr(fp, "name", "file")
        if spoiler and not self.filename.startswith("SPOILER_"):
            self.filename = "SPOILER_" + self.filename

    def read(self):
        if isinstance(self.fp, io.BytesIO):
            return self.fp.getvalue()
        return self.fp.read()


# ─────────────────────────────────────────────────────────────
# COLOR / PERMISSIONS SHIMS
# ─────────────────────────────────────────────────────────────

class Color:
    def __init__(self, value=0):
        self.value = int(value)

    @classmethod
    def from_rgb(cls, r, g, b):
        return cls((r << 16) + (g << 8) + b)

    def __int__(self):
        return self.value

    def __eq__(self, other):
        return isinstance(other, Color) and self.value == other.value


# ─────────────────────────────────────────────────────────────
# UTILS SHIM
# ─────────────────────────────────────────────────────────────

class _Utils:
    @staticmethod
    def utcnow():
        return datetime.now(timezone.utc)

    @staticmethod
    def get(iterable, **attrs):
        for item in iterable:
            if all(getattr(item, k, None) == v for k, v in attrs.items()):
                return item
        return None

    @staticmethod
    def find(predicate, iterable):
        for item in iterable:
            if predicate(item):
                return item
        return None


utils = _Utils()


# ─────────────────────────────────────────────────────────────
# EVENT NAME RENAME MAP
# ─────────────────────────────────────────────────────────────
#
# selfbot.py decorates with discord.py-self names. modifyself fires
# UPPERCASE gateway names. Some don't match after .replace("on_","").upper():
#
#   on_message_edit       → "MESSAGE_EDIT"       modifyself fires MESSAGE_UPDATE
#   on_message_delete     → "MESSAGE_DELETE"     ✅ matches
#   on_reaction_add       → "REACTION_ADD"       modifyself fires MESSAGE_REACTION_ADD
#   on_member_join        → "MEMBER_JOIN"        modifyself fires GUILD_MEMBER_ADD
#   on_member_remove      → "MEMBER_REMOVE"      modifyself fires GUILD_MEMBER_REMOVE
#   on_member_update      → "MEMBER_UPDATE"      modifyself fires GUILD_MEMBER_UPDATE
#   on_group_channel_create → "GROUP_CHANNEL_CREATE"  modifyself fires CHANNEL_CREATE (GROUP_DM type)
#   on_voice_state_update → "VOICE_STATE_UPDATE" ✅ matches
#   on_disconnect         → "DISCONNECT"         modifyself doesn't fire — synthesized
#   on_resumed            → "RESUMED"            ✅ matches
#   on_ready              → "READY"              ✅ matches
#   on_message            → "MESSAGE_CREATE"     ✅ matches
#   on_interaction        → "INTERACTION_CREATE" ✅ matches (registered internally)

_EVENT_RENAME = {
    "MESSAGE_EDIT":          "MESSAGE_UPDATE",
    "REACTION_ADD":          "MESSAGE_REACTION_ADD",
    "REACTION_REMOVE":       "MESSAGE_REACTION_REMOVE",
    "REACTION_CLEAR":        "MESSAGE_REACTION_REMOVE_ALL",
    "REACTION_CLEAR_EMOJI":  "MESSAGE_REACTION_REMOVE_EMOJI",
    "MEMBER_JOIN":           "GUILD_MEMBER_ADD",
    "MEMBER_REMOVE":         "GUILD_MEMBER_REMOVE",
    "MEMBER_UPDATE":         "GUILD_MEMBER_UPDATE",
    "MEMBER_BAN":            "GUILD_BAN_ADD",
    "MEMBER_UNBAN":          "GUILD_BAN_REMOVE",
    "GUILD_JOIN":            "GUILD_CREATE",
    "GUILD_REMOVE":          "GUILD_DELETE",
    "GUILD_AVAILABLE":       "GUILD_CREATE",
    "GUILD_CHANNEL_CREATE":  "CHANNEL_CREATE",
    "GUILD_CHANNEL_UPDATE":  "CHANNEL_UPDATE",
    "GUILD_CHANNEL_DELETE":  "CHANNEL_DELETE",
    "PRIVATE_CHANNEL_CREATE": "CHANNEL_CREATE",
    "PRIVATE_CHANNEL_DELETE": "CHANNEL_DELETE",
    "TYPING":                "TYPING_START",
    "GROUP_JOIN":            "CHANNEL_CREATE",
    "RAW_REACTION_ADD":      "MESSAGE_REACTION_ADD",
    "RAW_REACTION_REMOVE":   "MESSAGE_REACTION_REMOVE",
}


# ─────────────────────────────────────────────────────────────
# EVENT ARG WRAPPERS
# ─────────────────────────────────────────────────────────────
#
# modifyself's dispatcher passes parsed model objects (Message, User,
# Guild, Member, Channel) to handlers. discord.py-self passes different
# arg shapes. Chunk 1 provides identity wrappers — Chunk 2/3 replace
# with real wrappers once the model shims land.

def _wrap_message(data):
    """Chunk 2 will replace with real Message wrapper. Chunk 1: passthrough."""
    return data


def _wrap_message_pair(before, after):
    """For MESSAGE_UPDATE, modifyself passes one Message. We need before/after."""
    # modifyself's parse_message_update returns the updated Message.
    # selfbot.py expects (before, after). Chunk 1 sends the same object twice
    # so the handler runs. Chunk 2 will cache the previous version.
    return before, before


def _wrap_member(data):
    return data


def _wrap_channel(data):
    return data


# ─────────────────────────────────────────────────────────────
# CLIENT WRAPPER
# ─────────────────────────────────────────────────────────────

class _WebSocketProxy:
    """
    selfbot.py calls:
        client.ws.send(json_string)
        client.ws.send_as_json(dict)
        client.ws.close(code=4000)

    modifyself exposes:
        client._gateway.send_json(dict)
        client._gateway.close()
        client._gateway._ws.send(string)

    This proxy bridges them.
    """

    def __init__(self, gateway, client_ref):
        self._gateway = gateway
        self._client = client_ref

    async def send(self, data, *args, **kwargs):
        """Accept either a JSON string or a dict."""
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        if isinstance(data, str):
            try:
                payload = json.loads(data)
            except Exception:
                logger.warning("[ws proxy] could not parse string payload: %s", data[:80])
                return
        else:
            payload = data
        await self._gateway.send_json(payload)

    async def send_as_json(self, data):
        await self._gateway.send_json(data)

    async def close(self, code=None, *args, **kwargs):
        # modifyself has no code argument on close — the reconnect logic
        # in websocket.py decides based on the close code Discord returns.
        await self._gateway.close()

    async def send_voice_state(self, **kwargs):
        await self._gateway.send_voice_state(**kwargs)


class _HTTPProxy:
    """
    selfbot.py uses client.http.token for raw requests in the RPC cog's
    fallback path. modifyself's HTTPClient already has .token.
    """
    pass


class Client:
    """
    Drop-in replacement for discord.Client backed by modifyself.Client.

    Handles:
      - constructor kwargs (chunk_guilds_at_startup, request_guilds) — accepted & ignored
      - @client.event decorator with rename map
      - client.ws proxy
      - change_presence (raw gateway)
      - wait_for (event ring buffer)
      - Loops — task registry
      - run() / close() / start()
    """

    def __init__(self, *args, **kwargs):
        # selfbot.py passes chunk_guilds_at_startup=False, request_guilds=True.
        # modifyself takes token=... only. Extract token from env, matching
        # what selfbot.py already reads at module scope.
        token = (
            os.environ.get("TOKEN", "").strip()
            or os.environ.get("DISCORD_TOKEN", "").strip()
        ).strip('"').strip("'")
        if not token:
            # selfbot.py will sys.exit(1) before reaching here if no token.
            # Fallback so constructor doesn't crash on import.
            token = "NO_TOKEN"

        self._kwargs = kwargs
        self._client = _ms.Client(
            token=token,
            command_prefix="\x00never\x00",   # selfbot.py handles its own prefix
            notifications=False,             # silence plyer desktop popups
        )

        # Wire up our event registration first
        self._user_event_handlers: Dict[str, List[Callable]] = {}
        self._wait_for_waiters: Dict[str, List] = {}
        self._registered_gateway_events = set()

        # Register INTERNAL catch-all listeners on modifyself's dispatcher
        # for every gateway event, so we can rebroadcast to user handlers
        # with the correct names.
        self._hook_all_gateway_events()

        # ws proxy — created lazily on first access, since gateway exists at init
        self._ws_proxy = _WebSocketProxy(self._client._gateway, self)

    # ── constructor passthroughs ─────────────────────────────

    @property
    def user(self):
        return self._client.user

    @property
    def guilds(self):
        return self._client.guilds

    @property
    def users(self):
        return self._client.users

    @property
    def latency(self):
        return self._client.latency

    @property
    def ws(self):
        return self._ws_proxy

    @property
    def http(self):
        return self._client._http

    @property
    def private_channels(self):
        """list[DMChannel | GroupChannel]"""
        out = []
        for ch in self._client._state._channels.values():
            t = getattr(ch, "type", None)
            # modifyself ChannelType.DM == 1, GROUP_DM == 3
            tval = getattr(t, "value", t)
            if tval in (1, 3):
                out.append(ch)
        return out

    # ── event registration ──────────────────────────────────

    def _hook_all_gateway_events(self):
        """
        Register an internal listener on modifyself's dispatcher for every
        gateway event. When it fires, we translate the arg shape and forward
        to any user handlers registered under the discord.py-self name.
        """
        gateway_events = [
            "READY", "RESUMED", "USER_UPDATE",
            "GUILD_CREATE", "GUILD_UPDATE", "GUILD_DELETE",
            "GUILD_MEMBER_ADD", "GUILD_MEMBER_REMOVE", "GUILD_MEMBER_UPDATE",
            "GUILD_MEMBERS_CHUNK", "GUILD_BAN_ADD", "GUILD_BAN_REMOVE",
            "CHANNEL_CREATE", "CHANNEL_UPDATE", "CHANNEL_DELETE",
            "CHANNEL_RECIPIENT_ADD", "CHANNEL_RECIPIENT_REMOVE",
            "THREAD_CREATE", "THREAD_UPDATE", "THREAD_DELETE", "THREAD_LIST_SYNC",
            "MESSAGE_CREATE", "MESSAGE_UPDATE", "MESSAGE_DELETE",
            "MESSAGE_DELETE_BULK",
            "MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE",
            "MESSAGE_REACTION_REMOVE_ALL", "MESSAGE_REACTION_REMOVE_EMOJI",
            "TYPING_START", "PRESENCE_UPDATE",
            "RELATIONSHIP_ADD", "RELATIONSHIP_REMOVE",
            "CALL_CREATE", "CALL_UPDATE", "CALL_DELETE",
            "VOICE_STATE_UPDATE", "VOICE_SERVER_UPDATE",
            "INTERACTION_CREATE",
        ]

        for evt in gateway_events:
            self._client._dispatcher.on(evt, self._make_forwarder(evt))

    def _make_forwarder(self, gateway_event: str):
        async def forwarder(payload):
            await self._on_gateway_event(gateway_event, payload)
        return forwarder

    async def _on_gateway_event(self, gateway_event: str, payload):
        """
        Rebroadcast a gateway event to user handlers, translating names
        and argument shapes.
        """
        # Find the discord.py-self name(s) that map to this gateway event
        pyself_names = set()
        # Direct name → discord.py-self event name
        direct_map = {
            "READY":              "ready",
            "RESUMED":            "resumed",
            "MESSAGE_CREATE":     "message",
            "MESSAGE_UPDATE":     "message_edit",
            "MESSAGE_DELETE":     "message_delete",
            "MESSAGE_DELETE_BULK":"message_delete_bulk",
            "GUILD_MEMBER_ADD":   "member_join",
            "GUILD_MEMBER_REMOVE":"member_remove",
            "GUILD_MEMBER_UPDATE":"member_update",
            "GUILD_MEMBER_CHUNK": "member_chunk",
            "MESSAGE_REACTION_ADD":    "reaction_add",
            "MESSAGE_REACTION_REMOVE": "reaction_remove",
            "MESSAGE_REACTION_REMOVE_ALL":  "reaction_clear",
            "MESSAGE_REACTION_REMOVE_EMOJI":"reaction_clear_emoji",
            "TYPING_START":       "typing",
            "GUILD_CREATE":       "guild_join",
            "GUILD_DELETE":       "guild_remove",
            "GUILD_UPDATE":       "guild_update",
            "GUILD_BAN_ADD":      "member_ban",
            "GUILD_BAN_REMOVE":   "member_unban",
            "CHANNEL_CREATE":     "guild_channel_create",
            "CHANNEL_UPDATE":     "guild_channel_update",
            "CHANNEL_DELETE":     "guild_channel_delete",
            "THREAD_CREATE":      "thread_create",
            "THREAD_UPDATE":      "thread_update",
            "THREAD_DELETE":      "thread_delete",
            "VOICE_STATE_UPDATE": "voice_state_update",
            "INTERACTION_CREATE": "interaction",
            "USER_UPDATE":        "user_update",
            "PRESENCE_UPDATE":    "presence_update",
        }
        pyself_name = direct_map.get(gateway_event)
        if pyself_name:
            pyself_names.add(pyself_name)

        # Synthesize group_channel_create from CHANNEL_CREATE of type GROUP_DM
        if gateway_event == "CHANNEL_CREATE":
            tval = getattr(getattr(payload, "type", None), "value", getattr(payload, "type", None))
            if tval == 3:
                pyself_names.add("group_channel_create")

        # Synthesize disconnect: not fired by modifyself gateway. Skip for now —
        # chunk 4 wires a task watcher on _gateway._state.

        # Build args based on target handler name
        for name in pyself_names:
            args = self._args_for(name, payload, gateway_event)
            if args is None:
                continue
            for handler in list(self._user_event_handlers.get(name, [])):
                try:
                    await handler(*args)
                except Exception as e:
                    logger.exception("[compat] handler %s raised: %s", name, e)

            # Also fire wait_for waiters
            self._dispatch_wait_for(name, args)

    def _args_for(self, name, payload, gateway_event):
        """Shape args to match what discord.py-self passes."""
        # READY / RESUMED / INTERACTION / TYPING etc. — payload is already right
        if name == "ready":
            return ()
        if name == "resumed":
            return ()
        if name == "message":
            return (payload,)  # Chunk 2 will wrap
        if name == "message_edit":
            # modifyself fires once with the updated Message. discord.py-self
            # fires (before, after). Chunk 2 caches history for real `before`.
            return (payload, payload)
        if name == "message_delete":
            return (payload,)
        if name in ("member_join", "member_remove", "member_update"):
            return (payload,)
        if name in ("reaction_add", "reaction_remove"):
            # modifyself fires raw dict. discord.py-self fires (reaction, user).
            # Chunk 2 wraps.
            return (payload, payload)
        if name == "guild_join":
            return (payload,)
        if name == "guild_remove":
            return (payload,)
        if name == "guild_update":
            return (payload, payload)
        if name in ("guild_channel_create", "guild_channel_delete"):
            return (payload,)
        if name == "guild_channel_update":
            return (payload, payload)
        if name == "group_channel_create":
            return (payload,)
        if name == "voice_state_update":
            # selfbot.py signature: (member, before, after). modifyself
            # fires raw dict. Chunk 3 provides real before/after.
            return (payload, payload, payload)
        if name == "interaction":
            return (payload,)
        if name == "typing":
            return (payload,)
        if name in ("member_ban", "member_unban"):
            return (payload,)
        if name in ("thread_create", "thread_update", "thread_delete"):
            return (payload,)
        if name in ("presence_update", "user_update"):
            return (payload,)
        return (payload,)

    def event(self, coro: Callable):
        """
        Register an event handler. discord.py-self syntax:
            @client.event
            async def on_message(message): ...
        """
        if not asyncio.iscoroutinefunction(coro):
            raise TypeError("Event handlers must be coroutines")
        raw = coro.__name__
        if raw.startswith("on_"):
            raw = raw[3:]
        self._user_event_handlers.setdefault(raw, []).append(coro)
        logger.debug("[compat] registered user handler: on_%s", raw)
        return coro

    def listen(self, name: Optional[str] = None):
        def decorator(coro):
            evt = name or coro.__name__
            if evt.startswith("on_"):
                evt = evt[3:]
            self._user_event_handlers.setdefault(evt, []).append(coro)
            return coro
        return decorator

    # ── wait_for — event ring buffer ────────────────────────

    def _dispatch_wait_for(self, event_name: str, args):
        waiters = self._wait_for_waiters.get(event_name, [])
        still = []
        for w in waiters:
            fut, check = w["future"], w["check"]
            if fut.done():
                continue
            try:
                matches = check(*args) if check else True
            except Exception:
                matches = False
            if matches:
                if not fut.done():
                    fut.set_result(args[0] if len(args) == 1 else args)
            else:
                still.append(w)
        if still:
            self._wait_for_waiters[event_name] = still
        else:
            self._wait_for_waiters.pop(event_name, None)

    async def wait_for(self, event: str, *, check=None, timeout=None):
        evt = event[3:] if event.startswith("on_") else event
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._wait_for_waiters.setdefault(evt, []).append({"future": fut, "check": check})
        try:
            if timeout:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        except asyncio.TimeoutError:
            raise
        finally:
            lst = self._wait_for_waiters.get(evt, [])
            self._wait_for_waiters[evt] = [w for w in lst if w["future"] is not fut]

    # ── presence ────────────────────────────────────────────

    async def change_presence(self, *, activity=None, status=None, afk=False):
        """
        Send a raw op 3 to the gateway. modifyself doesn't expose this.
        activity may be a Game, Activity, or CustomActivity shim.
        """
        payload_activity = None
        if activity is not None:
            if isinstance(activity, Game):
                payload_activity = {"type": 0, "name": activity.name}
            elif isinstance(activity, CustomActivity):
                payload_activity = {
                    "type": 4,
                    "name": activity.name or "Custom Status",
                    "state": activity.emoji or activity.name or "",
                }
            elif isinstance(activity, Activity):
                d = {"type": int(activity.type) if not isinstance(activity.type, int) else activity.type,
                     "name": activity.name}
                for k in ("url", "details", "state", "timestamps", "assets",
                          "application_id", "instance", "flags", "buttons"):
                    v = getattr(activity, k, None)
                    if v is not None:
                        d[k] = v
                payload_activity = d
            else:
                # generic object with .name
                payload_activity = {"type": getattr(activity, "type", 0) or 0,
                                    "name": getattr(activity, "name", "?")}

        status_str = status or "online"
        if hasattr(status_str, "value"):
            status_str = status_str.value

        await self._client._gateway.send_json({
            "op": 3,
            "d": {
                "since": 0,
                "activities": [payload_activity] if payload_activity else [],
                "status": status_str,
                "afk": afk,
            },
        })

    # ── state access ────────────────────────────────────────

    def get_channel(self, channel_id: int):
        return self._client.get_channel(channel_id)

    def get_guild(self, guild_id: int):
        return self._client.get_guild(guild_id)

    def get_user(self, user_id: int):
        return self._client.get_user(user_id)

    async def fetch_user(self, user_id: int):
        return await self._client.fetch_user(user_id)

    async def fetch_guild(self, guild_id: int):
        return await self._client.fetch_guild(guild_id)

    async def fetch_channel(self, channel_id: int):
        return await self._client.fetch_channel(channel_id)

    async def fetch_webhook(self, webhook_id: int):
        return await self._client.get_webhook(webhook_id)

    # ── run / close ─────────────────────────────────────────

    def run(self, token=None):
        """
        selfbot.py calls client.run(TOKEN). modifyself's Client.run() takes
        no token — it reads from constructor. Our constructor already
        picked up TOKEN from env. If a different token is passed, patch it.
        """
        if token and token != self._client.token:
            self._client.token = token
        try:
            self._client.run()
        except _ms.errors.ConnectionClosed as e:
            raise LoginFailure(str(e)) from e

    async def start(self, token=None):
        if token:
            self._client.token = token
        try:
            await self._client.start()
        except _ms.errors.ConnectionClosed as e:
            raise LoginFailure(str(e)) from e

    async def close(self):
        await self._client.close()


# ─────────────────────────────────────────────────────────────
# MODULE-LEVEL PASS-THROUGHS
# ─────────────────────────────────────────────────────────────

# Enums that selfbot.py accesses as discord.GroupChannel / DMChannel / TextChannel
# These are CLASSES in discord.py-self, used for isinstance() checks.
# Chunk 2 will provide real wrappers. Chunk 1 uses modifyself's classes
# so isinstance checks at least resolve without NameError.

GroupChannel = _ms.models.channel.GroupChannel if hasattr(_ms, "models") else None
DMChannel = _ms.models.channel.DMChannel if hasattr(_ms, "models") else None
TextChannel = _ms.models.channel.TextChannel if hasattr(_ms, "models") else None
VoiceChannel = _ms.models.channel.VoiceChannel if hasattr(_ms, "models") else None
CategoryChannel = _ms.models.channel.CategoryChannel if hasattr(_ms, "models") else None
Channel = _ms.models.channel.Channel if hasattr(_ms, "models") else None

# ui placeholder — Chunk 4 provides the real View/Button bridge
class _UI:
    class View:
        def __init__(self, *args, **kwargs):
            self.children = []
        def add_item(self, item):
            self.children.append(item)
    class Button:
        def __init__(self, *, label=None, custom_id=None, style=None, emoji=None, disabled=False):
            self.label = label
            self.custom_id = custom_id
            self.style = style
            self.emoji = emoji
            self.disabled = disabled

ui = _UI()


# ─────────────────────────────────────────────────────────────
# REDIRECT submodule lookups
# ─────────────────────────────────────────────────────────────
#
# selfbot.py accesses `discord.abc`, `discord.utils`, `discord.ui` etc.
# In this file, those names are plain attributes. This block makes
# `import modifyself_compat as discord; discord.X` resolve.

abc = sys.modules[__name__]
gateway = sys.modules[__name__]
ext = sys.modules[__name__]


# ─────────────────────────────────────────────────────────────
# RE-EXPORTS from modifyself
# ─────────────────────────────────────────────────────────────

# Components — Chunk 4 bridges View but ActionRow/Button are usable now
ActionRow = _ms.ActionRow
Button = _ms.Button
SelectMenu = _ms.SelectMenu
SelectOption = _ms.SelectOption
Modal = _ms.Modal
TextInput = _ms.TextInput

# Relationship enums
RelationshipType = _ms.RelationshipType

print("[compat] modifyself_compat Chunk 1 loaded — client wrapper active")
