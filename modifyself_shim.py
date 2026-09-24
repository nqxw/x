# modifyself_shim.py | discord-shaped namespace over modifyself.
# v5 — patches:
#   - Client.reconnect_gateway() with a class-level lock (no reconnect race)
#   - _upgrade() upgrades dict payloads to model objects before dispatch
#   - arity-aware event wrapper (0/1/2/3-arg handlers all work)
#   - _ChannelStub fallback for uncached channels
#   - _ch_send passes content positionally to match modifyself's HTTPClient
#   - _MSMessage.reply fallback through stub channels
import asyncio
import inspect
import json as _json
import logging
import os
from datetime import datetime, timezone as _tz

from modifyself import Client as _MSClient
from modifyself.activity import (
    Activity as _MSActivity,
    ActivityType as _MSActivityType,
)
from modifyself.components import (
    ActionRow as _MSActionRow,
    Button as _MSButton,
    ButtonStyle as _MSButtonStyle,
    Modal as _MSModal,
    TextInput as _MSTextInput,
    SelectMenu as _MSSelectMenu,
    SelectOption as _MSSelectOption,
    ComponentType as _MSComponentType,
    TextInputStyle as _MSTextInputStyle,
)
from modifyself.errors import (
    DiscordException,
    HTTPException as _MSHTTPException,
    GatewayException,
    ConnectionClosed as _MSConnectionClosed,
    CommandError,
    CheckFailure,
    ConversionError,
    CommandNotFound,
    MissingRequiredArgument,
    CaptchaRequired,
)

from modifyself.models.base import DiscordObject
from modifyself.models.user import User as _MSUser
from modifyself.models.member import Member as _MSMember
from modifyself.models.guild import Guild as _MSGuild
from modifyself.models.channel import (
    Channel as _MSChannel,
    TextChannel as _MSTextChannel,
    DMChannel as _MSDMChannel,
    GroupChannel as _MSGroupChannel,
    VoiceChannel as _MSVoiceChannel,
    CategoryChannel as _MSCategoryChannel,
    channel_factory as _channel_factory,
)
from modifyself.models.message import Message as _MSMessage
from modifyself.models.relationship import (
    Relationship as _MSRelationship,
    RelationshipType as _MSRelationshipType,
)
from modifyself.models.webhook import Webhook as _MSWebhook
from modifyself.gateway.websocket import GatewayWebSocket as _MSGateway

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  NAMESPACE
# ═══════════════════════════════════════════════════════════════════

class Status:
    online    = "online"
    idle      = "idle"
    dnd       = "dnd"
    invisible = "invisible"
    offline   = "offline"


class ActivityType:
    playing    = 0
    streaming  = 1
    listening  = 2
    watching   = 3
    custom     = 4
    competing  = 5


class Game:
    def __init__(self, name: str = "", **_):
        self.name = name
    def to_dict(self):
        return {"type": 0, "name": self.name[:128]}
    def to_activity(self):
        return _MSActivity(type=_MSActivityType.PLAYING, name=self.name)


class Activity:
    def __init__(self, *, type=0, name: str = "", url: str = None, **_):
        self.type = type
        self.name = name
        self.url = url
    def to_dict(self):
        d = {"type": int(self.type), "name": self.name[:128]}
        if self.url: d["url"] = self.url
        return d
    def to_activity(self):
        return _MSActivity(type=_MSActivityType(self.type), name=self.name, url=self.url)


class CustomActivity:
    def __init__(self, *, name: str = "", emoji=None, **_):
        self.name = name
        self.emoji = emoji
    def to_dict(self):
        return {"type": 4, "name": self.name[:128], "state": self.name[:128]}
    def to_activity(self):
        return _MSActivity(type=_MSActivityType.CUSTOM, name=self.name)


class Color:
    def __init__(self, value: int = 0):
        self.value = int(value)
    def __int__(self): return self.value
    def __eq__(self, o): return isinstance(o, Color) and o.value == self.value
    @classmethod
    def default(cls): return cls(0)
    @classmethod
    def random(cls):
        import random as _r; return cls(_r.randint(0, 0xFFFFFF))


class Permissions:
    def __init__(self, value: int = 0):
        self.value = int(value)
    def __int__(self): return self.value
    def __or__(self, o): return Permissions(self.value | int(o))
    def __and__(self, o): return Permissions(self.value & int(o))


class Object:
    def __init__(self, id: int = 0): self.id = int(id)
    def __repr__(self): return f"<Object id={self.id}>"


class Embed:
    def __init__(self, **kw):
        self._d = {"type": "rich", **{k: v for k, v in kw.items() if v is not None}}
    def add_field(self, name, value, inline=True):
        self._d.setdefault("fields", []).append({"name": name, "value": value, "inline": inline})
        return self
    def set_author(self, name=None, icon_url=None, url=None):
        d = {k: v for k, v in (("name", name), ("icon_url", icon_url), ("url", url)) if v}
        if d: self._d["author"] = d
        return self
    def set_footer(self, text=None, icon_url=None):
        d = {k: v for k, v in (("text", text), ("icon_url", icon_url)) if v}
        if d: self._d["footer"] = d
        return self
    def set_image(self, url=None): self._d["image"] = {"url": url}; return self
    def set_thumbnail(self, url=None): self._d["thumbnail"] = {"url": url}; return self
    def to_dict(self): return self._d


class File:
    def __init__(self, fp, filename=None, spoiler: bool = False):
        self.fp = fp
        self.filename = filename or getattr(fp, "name", "file")
        self.spoiler = spoiler
    @property
    def payload(self):
        return {"fp": self.fp, "filename": self.filename}


class _UISelectOption:
    def __init__(self, *, label=None, value=None, description=None, emoji=None, default=False):
        self.label = label; self.value = value; self.description = description
        self.emoji = emoji; self.default = default
    def to_dict(self):
        d = {"label": self.label, "value": self.value}
        if self.description: d["description"] = self.description
        if self.emoji: d["emoji"] = self.emoji
        if self.default: d["default"] = True
        return d


class _UIButton:
    def __init__(self, *, style=None, label=None, custom_id=None, url=None,
                 disabled=False, emoji=None, row=None):
        self.style = style if style is not None else _MSButtonStyle.PRIMARY
        self.label = label; self.custom_id = custom_id; self.url = url
        self.disabled = disabled; self.emoji = emoji; self.row = row
    def to_dict(self):
        d = {"type": 2, "style": int(self.style), "disabled": bool(self.disabled)}
        if self.label: d["label"] = self.label
        if self.custom_id: d["custom_id"] = self.custom_id
        if self.url: d["url"] = self.url
        if self.emoji: d["emoji"] = self.emoji
        return d


class _UISelect:
    def __init__(self, *, custom_id=None, placeholder=None, options=None,
                 min_values=1, max_values=1, disabled=False):
        self.custom_id = custom_id; self.placeholder = placeholder
        self.options = options or []; self.min_values = min_values
        self.max_values = max_values; self.disabled = disabled
    def to_dict(self):
        d = {"type": 3, "custom_id": self.custom_id,
             "options": [o.to_dict() for o in self.options],
             "min_values": self.min_values, "max_values": self.max_values,
             "disabled": bool(self.disabled)}
        if self.placeholder: d["placeholder"] = self.placeholder
        return d


class _UIView:
    def __init__(self, *, timeout: float = 180.0):
        self.timeout = timeout; self._children = []
    def add_item(self, item): self._children.append(item)
    def to_components(self):
        rows = {}
        for c in self._children:
            r = getattr(c, "row", None)
            rows.setdefault(r, []).append(c.to_dict())
        return [{"type": 1, "components": v} for v in rows.values()]


class ui:
    Button = _UIButton
    Select = _UISelect
    View = _UIView


class ButtonStyle:
    primary   = _MSButtonStyle.PRIMARY
    secondary = _MSButtonStyle.SECONDARY
    success   = _MSButtonStyle.SUCCESS
    danger    = _MSButtonStyle.DANGER
    link      = _MSButtonStyle.LINK


class _Utils:
    @staticmethod
    def utcnow(): return datetime.now(_tz.utc)
    @staticmethod
    def get(iterable, **attrs):
        for it in iterable:
            if all(getattr(it, k, None) == v for k, v in attrs.items()):
                return it
        return None
    @staticmethod
    def find(pred, iterable):
        for it in iterable:
            if pred(it): return it
        return None


utils = _Utils

HTTPException    = _MSHTTPException
Forbidden        = _MSHTTPException
NotFound         = _MSHTTPException
DiscordException = DiscordException
LoginFailure     = DiscordException
InvalidToken     = DiscordException


# ═══════════════════════════════════════════════════════════════════
#  EVENT PAYLOAD UPGRADE
# ═══════════════════════════════════════════════════════════════════

def _upgrade(state, event_name, payload):
    """Upgrade a raw payload for the named event into the right model object."""
    if payload is None or not isinstance(payload, dict):
        return payload

    try:
        if event_name in ("MESSAGE_CREATE", "MESSAGE_UPDATE"):
            return state._store_message(payload)

        if event_name in ("MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE"):
            msg = state._messages.get(int(payload.get("message_id", 0)))
            if msg is None:
                ch = state._channels.get(int(payload.get("channel_id", 0)))
                if ch is None:
                    ch = _ChannelStub(payload.get("channel_id", 0), state,
                                       payload.get("guild_id"))
                class _StubMsg:
                    def __init__(self, mid, channel, data):
                        self.id = mid
                        self.channel = channel
                        self.channel_id = channel.id
                        self.guild_id = data.get("guild_id")
                        self._data = data
                        self.author = _MSUser(
                            state=state,
                            data=data.get("author", data.get("user", {"id": "0"})))
                    @property
                    def content(self): return self._data.get("content", "")
                    async def reply(self, content=None, **kw):
                        return await self.channel.send(content, **kw)
                msg = _StubMsg(int(payload.get("message_id", 0)), ch, payload)

            class _StubReaction:
                def __init__(self, emoji, message):
                    self.emoji = emoji
                    self.message = message
                    self.count = payload.get("count", 1)

            emoji_data = payload.get("emoji", {}) or {}
            emoji_str = (
                emoji_data.get("name") if not emoji_data.get("id")
                else f"<{'a' if emoji_data.get('animated') else ''}:"
                     f"{emoji_data.get('name')}:{emoji_data.get('id')}>"
            )
            return _StubReaction(emoji_str, msg)

        if event_name in ("GUILD_MEMBER_ADD", "GUILD_MEMBER_REMOVE",
                          "GUILD_MEMBER_UPDATE"):
            gid = int(payload.get("guild_id", 0))
            return _MSMember(state=state, data=payload, guild_id=gid)

        if event_name == "GUILD_CREATE":
            return state._add_guild(payload)

        if event_name in ("CHANNEL_CREATE", "CHANNEL_UPDATE"):
            return state._add_channel(payload)

        if event_name in ("VOICE_STATE_UPDATE", "TYPING_START", "INTERACTION_CREATE"):
            return payload
    except Exception as e:
        logger.debug(f"[shim] _upgrade {event_name} failed: {e}")
    return payload


# ═══════════════════════════════════════════════════════════════════
#  MODEL PATCHES
# ═══════════════════════════════════════════════════════════════════

def _http(state): return state.http


class _ChannelStub:
    def __init__(self, channel_id, state, guild_id=None):
        self.id = int(channel_id)
        self._state = state
        self.guild_id = guild_id
        self.name = None
        self.type = None
        self.position = 0

    @property
    def guild(self):
        if self.guild_id:
            return self._state._guilds.get(self.guild_id)
        return None

    @property
    def mention(self):
        return f"<#{self.id}>"

    async def send(self, content=None, **kw):
        payload = {}
        for k in ("embeds", "components", "tts", "allowed_mentions",
                  "message_reference", "files"):
            if k in kw and kw[k] is not None:
                payload[k] = kw[k]
        data = await _http(self._state).send_message(self.id, content, **payload)
        return self._state._store_message(data)

    async def delete(self):
        return await _http(self._state).request(
            method="DELETE", url=f"/channels/{self.id}"
        )

    async def edit(self, **kw):
        data = await _http(self._state).request(
            method="PATCH", url=f"/channels/{self.id}", json=kw
        )
        return self

    async def history(self, *, limit=100, before=None, after=None,
                       around=None, oldest_first=False):
        remaining = min(limit or 100, 2000)
        cursor = before
        while remaining > 0:
            n = min(remaining, 100)
            qs = f"limit={n}"
            if cursor: qs += f"&before={cursor}"
            if after:  qs += f"&after={after}"
            if around: qs += f"&around={around}"
            data = await _http(self._state).request(
                method="GET", url=f"/channels/{self.id}/messages?{qs}"
            )
            if not data: break
            batch = list(reversed(data)) if oldest_first else list(data)
            for md in batch:
                yield self._state._store_message(md)
            cursor = data[-1]["id"]
            remaining -= len(data)
            if len(data) < n: break

    async def fetch_message(self, message_id):
        data = await _http(self._state).request(
            method="GET", url=f"/channels/{self.id}/messages/{message_id}"
        )
        return self._state._store_message(data)

    async def typing(self):
        try:
            return await _http(self._state).trigger_typing(self.id)
        except Exception:
            return await _http(self._state).request(
                method="POST", url=f"/channels/{self.id}/typing"
            )

    def __repr__(self):
        return f"<ChannelStub id={self.id}>"


def _msg_channel(self):
    ch = self._state._channels.get(self.channel_id)
    if ch is not None:
        return ch
    return _ChannelStub(self.channel_id, self._state, self.guild_id)


def _msg_guild(self):
    if self.guild_id:
        return self._state._guilds.get(self.guild_id)
    return None


_MSMessage.channel = property(_msg_channel)
_MSMessage.guild   = property(_msg_guild)


async def _msg_reply(self, content=None, **kw):
    ch = _msg_channel(self)
    ref = {"message_id": str(self.id), "channel_id": str(self.channel_id)}
    if self.guild_id:
        ref["guild_id"] = str(self.guild_id)
    kw["message_reference"] = ref
    return await ch.send(content, **kw)


_MSMessage.reply = _msg_reply


# ── Channel ──
async def _ch_history(self, *, limit=100, before=None, after=None,
                       around=None, oldest_first=False):
    remaining = min(limit or 100, 2000)
    cursor = before
    while remaining > 0:
        n = min(remaining, 100)
        qs = f"limit={n}"
        if cursor: qs += f"&before={cursor}"
        if after:  qs += f"&after={after}"
        if around: qs += f"&around={around}"
        data = await _http(self._state).request(
            method="GET", url=f"/channels/{self.id}/messages?{qs}"
        )
        if not data: break
        batch = list(reversed(data)) if oldest_first else list(data)
        for md in batch:
            yield self._state._store_message(md)
        cursor = data[-1]["id"]
        remaining -= len(data)
        if len(data) < n: break


async def _ch_send(self, content=None, *, embeds=None, components=None,
                    file=None, files=None, delete_after=None, tts=False,
                    allowed_mentions=None, reference=None, **kwargs):
    payload = {}
    if embeds:
        payload["embeds"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in embeds]
    if components:
        payload["components"] = [
            c.to_components() if hasattr(c, "to_components") else c.to_dict()
            for c in components
        ]
    if tts: payload["tts"] = tts
    if allowed_mentions: payload["allowed_mentions"] = allowed_mentions
    if reference: payload["message_reference"] = reference

    attach = []
    if file is not None:
        attach.append(file.payload if hasattr(file, "payload") else file)
    if files:
        for f in files:
            attach.append(f.payload if hasattr(f, "payload") else f)
    if attach: payload["files"] = attach
    payload.update(kwargs)

    msg_data = await _http(self._state).send_message(self.id, content, **payload)
    msg = self._state._store_message(msg_data)

    if delete_after is not None:
        async def _del():
            await asyncio.sleep(delete_after)
            try: await msg.delete()
            except Exception: pass
        asyncio.create_task(_del())
    return msg


async def _ch_delete(self):
    return await _http(self._state).request(method="DELETE", url=f"/channels/{self.id}")


async def _ch_edit(self, *, name=None, topic=None, position=None, nsfw=None,
                    slowmode_delay=None, bitrate=None, user_limit=None,
                    parent=None, **kwargs):
    payload = {}
    if name is not None: payload["name"] = name
    if topic is not None: payload["topic"] = topic
    if position is not None: payload["position"] = position
    if nsfw is not None: payload["nsfw"] = nsfw
    if slowmode_delay is not None: payload["rate_limit_per_user"] = slowmode_delay
    if bitrate is not None: payload["bitrate"] = bitrate
    if user_limit is not None: payload["user_limit"] = user_limit
    if parent is not None: payload["parent_id"] = str(getattr(parent, "id", parent))
    payload.update(kwargs)
    data = await _http(self._state).request(method="PATCH", url=f"/channels/{self.id}", json=payload)
    self._update(data)
    return self


async def _ch_set_perms(self, target, **kwargs):
    overwrite = {"id": str(getattr(target, "id", target)),
                  "type": 1 if isinstance(target, _MSMember) else 0}
    for k, v in kwargs.items():
        overwrite[k] = bool(v) if v is not None else None
    return await _http(self._state).request(
        method="PUT", url=f"/channels/{self.id}/permissions/{overwrite['id']}", json=overwrite
    )


async def _ch_webhooks(self):
    data = await _http(self._state).request(method="GET", url=f"/channels/{self.id}/webhooks")
    return [_MSWebhook(state=self._state, data=w) for w in (data or [])]


async def _ch_create_webhook(self, *, name, avatar=None):
    payload = {"name": name}
    if avatar: payload["avatar"] = avatar
    data = await _http(self._state).request(
        method="POST", url=f"/channels/{self.id}/webhooks", json=payload
    )
    return _MSWebhook(state=self._state, data=data)


async def _ch_fetch_message(self, message_id):
    data = await _http(self._state).request(
        method="GET", url=f"/channels/{self.id}/messages/{message_id}"
    )
    return self._state._store_message(data)


async def _ch_purge(self, *, limit=100, check=None, before=None, after=None):
    deleted = 0
    async for msg in self.history(limit=limit, before=before, after=after):
        if check and not check(msg): continue
        try:
            await msg.delete(); deleted += 1
        except Exception: pass
        await asyncio.sleep(0.3)
    return deleted


for _cls in (_MSChannel, _MSTextChannel, _MSDMChannel, _MSGroupChannel,
             _MSVoiceChannel, _MSCategoryChannel):
    _cls.history        = _ch_history
    _cls.send           = _ch_send
    _cls.delete         = _ch_delete
    _cls.edit           = _ch_edit
    _cls.set_permissions = _ch_set_perms
    _cls.webhooks       = _ch_webhooks
    _cls.create_webhook = _ch_create_webhook
    _cls.fetch_message  = _ch_fetch_message
    _cls.purge          = _ch_purge


# ── Guild ──
async def _g_build_cache(self):
    if getattr(self, "_cache_full", False): return
    try:
        data = await _http(self._state).request(
            method="GET", url=f"/guilds/{self.id}?with_counts=true"
        )
        self._update(data)
        self._cache_full = True
    except Exception as e:
        logger.debug(f"[shim] guild cache: {e}")


async def _g_create_text_channel(self, name, **kw):
    payload = {"name": name, "type": 0}
    if "category" in kw:
        payload["parent_id"] = str(getattr(kw.pop("category"), "id", kw.pop("category", None)))
    if "topic" in kw: payload["topic"] = kw["topic"]
    if "slowmode_delay" in kw: payload["rate_limit_per_user"] = kw["slowmode_delay"]
    data = await _http(self._state).request(method="POST", url=f"/guilds/{self.id}/channels", json=payload)
    return self._state._add_channel(data)


async def _g_create_voice_channel(self, name, **kw):
    payload = {"name": name, "type": 2}
    if "category" in kw:
        payload["parent_id"] = str(getattr(kw.pop("category"), "id", kw.pop("category", None)))
    if "bitrate" in kw: payload["bitrate"] = kw["bitrate"]
    if "user_limit" in kw: payload["user_limit"] = kw["user_limit"]
    data = await _http(self._state).request(method="POST", url=f"/guilds/{self.id}/channels", json=payload)
    return self._state._add_channel(data)


async def _g_create_category(self, name, **kw):
    data = await _http(self._state).request(
        method="POST", url=f"/guilds/{self.id}/channels",
        json={"name": name, "type": 4, **kw},
    )
    return self._state._add_channel(data)


async def _g_create_role(self, *, name=None, permissions=None, color=None,
                          hoist=False, mentionable=False, **kw):
    payload = {}
    if name: payload["name"] = name
    if permissions is not None: payload["permissions"] = str(int(permissions))
    if color is not None: payload["color"] = int(color)
    if hoist: payload["hoist"] = True
    if mentionable: payload["mentionable"] = True
    payload.update(kw)
    data = await _http(self._state).request(method="POST", url=f"/guilds/{self.id}/roles", json=payload)
    return Role(data=data)


async def _g_create_custom_emoji(self, *, name, image):
    import base64 as _b64
    if isinstance(image, (bytes, bytearray)):
        image = _b64.b64encode(image).decode()
    payload_img = image if "," in image else f"data:image/png;base64,{image}"
    data = await _http(self._state).request(
        method="POST", url=f"/guilds/{self.id}/emojis",
        json={"name": name, "image": payload_img},
    )
    return Emoji(data=data)


async def _g_edit(self, *, name=None, icon=None, banner=None, **kw):
    import base64 as _b64
    payload = {}
    if name is not None: payload["name"] = name
    if icon is not None:
        if isinstance(icon, (bytes, bytearray)): icon = _b64.b64encode(icon).decode()
        payload["icon"] = icon if "," in str(icon) else f"data:image/png;base64,{icon}"
    if banner is not None:
        if isinstance(banner, (bytes, bytearray)): banner = _b64.b64encode(banner).decode()
        payload["banner"] = banner if "," in str(banner) else f"data:image/png;base64,{banner}"
    payload.update(kw)
    data = await _http(self._state).request(method="PATCH", url=f"/guilds/{self.id}", json=payload)
    self._update(data); return self


async def _g_ban(self, user, *, reason=None, delete_message_seconds=0):
    uid = getattr(user, "id", user)
    return await _http(self._state).request(
        method="PUT", url=f"/guilds/{self.id}/bans/{uid}",
        json={"delete_message_seconds": delete_message_seconds},
    )


async def _g_unban(self, user):
    uid = getattr(user, "id", user)
    return await _http(self._state).request(method="DELETE", url=f"/guilds/{self.id}/bans/{uid}")


async def _g_kick(self, user, *, reason=None):
    uid = getattr(user, "id", user)
    return await _http(self._state).request(method="DELETE", url=f"/guilds/{self.id}/members/{uid}")


async def _g_invites(self):
    return (await _http(self._state).request(method="GET", url=f"/guilds/{self.id}/invites")) or []


async def _g_fetch_member(self, member_id):
    data = await _http(self._state).request(
        method="GET", url=f"/guilds/{self.id}/members/{member_id}"
    )
    return _MSMember(state=self._state, data=data, guild_id=self.id)


def _g_members_prop(self): return list(self._members.values())
def _g_member_count(self): return getattr(self, "max_members", 0) or len(self._members)
def _g_channels_prop(self): return list(self._channels.values())
def _g_roles_prop(self): return [Role(data=r) for r in (getattr(self, "_roles", None) or [])]
def _g_emojis_prop(self): return [Emoji(data=e) for e in (getattr(self, "_emojis", None) or [])]


_MSGuild.create_text_channel  = _g_create_text_channel
_MSGuild.create_voice_channel = _g_create_voice_channel
_MSGuild.create_category      = _g_create_category
_MSGuild.create_role          = _g_create_role
_MSGuild.create_custom_emoji  = _g_create_custom_emoji
_MSGuild.edit                 = _g_edit
_MSGuild.ban                  = _g_ban
_MSGuild.unban                = _g_unban
_MSGuild.kick                 = _g_kick
_MSGuild.invites              = _g_invites
_MSGuild.fetch_member         = _g_fetch_member
_MSGuild._build_cache         = _g_build_cache
_MSGuild.member_count         = property(_g_member_count)
_MSGuild.channels             = property(_g_channels_prop)
_MSGuild.roles                = property(_g_roles_prop)
_MSGuild.emojis               = property(_g_emojis_prop)
_MSGuild.members              = property(_g_members_prop)


# ── Member ──
async def _m_ban(self, *, reason=None, delete_message_seconds=0):
    g = self._state._guilds.get(self.guild_id)
    if g: return await _g_ban(g, self.id, reason=reason,
                               delete_message_seconds=delete_message_seconds)


async def _m_kick(self, *, reason=None):
    g = self._state._guilds.get(self.guild_id)
    if g: return await _g_kick(g, self.id, reason=reason)


async def _m_edit(self, *, nick=None, mute=None, deaf=None, roles=None, **kw):
    payload = {}
    if nick is not None: payload["nick"] = nick
    if mute is not None: payload["mute"] = mute
    if deaf is not None: payload["deaf"] = deaf
    if roles is not None: payload["roles"] = [str(getattr(r, "id", r)) for r in roles]
    payload.update(kw)
    return await _http(self._state).request(
        method="PATCH", url=f"/guilds/{self.guild_id}/members/{self.id}", json=payload
    )


async def _m_timeout(self, until):
    payload = {"communication_disabled_until":
                until.astimezone(_tz.utc).isoformat() if isinstance(until, datetime)
                else (until.isoformat() if until else None)}
    return await _http(self._state).request(
        method="PATCH", url=f"/guilds/{self.guild_id}/members/{self.id}", json=payload
    )


async def _m_add_roles(self, *roles):
    for r in roles:
        rid = getattr(r, "id", r)
        await _http(self._state).request(
            method="PUT", url=f"/guilds/{self.guild_id}/members/{self.id}/roles/{rid}"
        )


async def _m_remove_roles(self, *roles):
    for r in roles:
        rid = getattr(r, "id", r)
        await _http(self._state).request(
            method="DELETE", url=f"/guilds/{self.guild_id}/members/{self.id}/roles/{rid}"
        )


_MSMember.ban          = _m_ban
_MSMember.kick         = _m_kick
_MSMember.edit         = _m_edit
_MSMember.timeout      = _m_timeout
_MSMember.add_roles    = _m_add_roles
_MSMember.remove_roles = _m_remove_roles


# ── User ──
def _u_dm_channel(self):
    for ch in self._state._channels.values():
        if isinstance(ch, _MSDMChannel) and ch.recipients and ch.recipients[0].id == self.id:
            return ch
    return None


async def _u_create_dm(self):
    existing = _u_dm_channel(self)
    if existing: return existing
    data = await _http(self._state).request(
        method="POST", url="/users/@me/channels", json={"recipient_id": str(self.id)}
    )
    return self._state._add_channel(data)


async def _u_send(self, content=None, **kw):
    ch = _u_dm_channel(self) or await _u_create_dm(self)
    return await ch.send(content, **kw)


_MSUser.dm_channel = property(_u_dm_channel)
_MSUser.create_dm  = _u_create_dm
_MSUser.send       = _u_send


# ── Role / Emoji shims ──
class Role:
    def __init__(self, data: dict = None, **_):
        data = data or {}
        self.id = int(data.get("id", 0))
        self.name = data.get("name", "")
        self.position = data.get("position", 0)
        self.hoist = data.get("hoist", False)
        self.mentionable = data.get("mentionable", False)
        self.color = Color(data.get("color", 0))
        self.permissions = Permissions(int(data.get("permissions", 0)))
        self._data = data
    def is_default(self): return self.id == 0 or self.name == "@everyone"
    def __repr__(self): return f"<Role id={self.id} name={self.name!r}>"
    def __str__(self): return self.name


class Emoji:
    def __init__(self, data: dict = None, **_):
        data = data or {}
        self.id = int(data.get("id", 0)) if data.get("id") else None
        self.name = data.get("name", "")
        self.animated = data.get("animated", False)
        self._data = data
    def __str__(self):
        if self.id:
            p = "a" if self.animated else ""
            return f"<{p}:{self.name}:{self.id}>"
        return self.name or ""


# ═══════════════════════════════════════════════════════════════════
#  CLIENT + GATEWAY ADAPTERS
# ═══════════════════════════════════════════════════════════════════

_EVENT_MAP = {
    "MESSAGE":             "MESSAGE_CREATE",
    "MESSAGE_DELETE":      "MESSAGE_DELETE",
    "MESSAGE_EDIT":        "MESSAGE_UPDATE",
    "MESSAGE_UPDATE":      "MESSAGE_UPDATE",
    "MESSAGE_DELETE_BULK": "MESSAGE_DELETE_BULK",
    "MEMBER_UPDATE":       "GUILD_MEMBER_UPDATE",
    "MEMBER_JOIN":         "GUILD_MEMBER_ADD",
    "MEMBER_REMOVE":       "GUILD_MEMBER_REMOVE",
    "REACTION_ADD":        "MESSAGE_REACTION_ADD",
    "REACTION_REMOVE":     "MESSAGE_REACTION_REMOVE",
    "TYPING":              "TYPING_START",
    "INTERACTION":         "INTERACTION_CREATE",
    "CONNECT":             "CONNECT",
    "DISCONNECT":          "DISCONNECT",
    "RESUMED":             "RESUMED",
    "READY":               "READY",
    "VOICE_STATE_UPDATE":  "VOICE_STATE_UPDATE",
    "USER_UPDATE":         "USER_UPDATE",
    "PRESENCE_UPDATE":     "PRESENCE_UPDATE",
    "GUILD_JOIN":          "GUILD_CREATE",
    "PRIVATE_CHANNEL_UPDATE": "CHANNEL_UPDATE",
    "GROUP_JOIN":          "CHANNEL_CREATE",
}


class _HTTPShim:
    def __init__(self, real_http, token):
        self._http = real_http
        self.token = token
    def __getattr__(self, name): return getattr(self._http, name)


class _WSShim:
    def __init__(self, client):
        self._client = client

    def _gw(self): return getattr(self._client, "_gateway", None)

    async def send(self, data, *a, **kw):
        gw = self._gw()
        if gw is None:
            raise RuntimeError("modifyself gateway not attached")
        if isinstance(data, str):
            data = _json.loads(data)
        return await gw.send_json(data)

    async def send_json(self, data):
        gw = self._gw()
        if gw is None: return
        return await gw.send_json(data)

    async def send_as_json(self, data):
        return await self.send_json(data)

    async def close(self, *, code: int = 1000):
        gw = self._gw()
        if gw is None: return
        return await gw.close()

    async def send_voice_state(self, *a, **kw):
        gw = self._gw()
        if gw is None: return
        return await gw.send_voice_state(*a, **kw)


class Client(_MSClient):
    def __init__(self, *, token: str = None, **kwargs):
        for k in ("chunk_guilds_at_startup", "request_guilds", "intents",
                  "command_prefix", "status", "activity", "help_command",
                  "self_bot"):
            kwargs.pop(k, None)

        if not token:
            token = (os.environ.get("TOKEN", "").strip()
                     or os.environ.get("DISCORD_TOKEN", "").strip())
        if not token:
            raise RuntimeError("modifyself_shim.Client: no token")

        super().__init__(token=token, notifications=False, **kwargs)

        self.http = _HTTPShim(self._http, token)
        self.ws = _WSShim(self)
        self._presence_activity = None
        self._presence_status = "online"
        self._closed_flag = False

    # ── reconnect lock + driver ──

    _reconnect_lock = None

    def _get_reconnect_lock(self):
        if Client._reconnect_lock is None:
            Client._reconnect_lock = asyncio.Lock()
        return Client._reconnect_lock

    async def reconnect_gateway(self):
        """
        Force a fresh IDENTIFY on the gateway. Serializes on a class-level
        lock so modifyself's own reconnect task and this method never
        open the socket at the same time.
        """
        gw = getattr(self, "_gateway", None)
        if gw is None:
            logger.debug("[shim] reconnect_gateway: no gateway")
            return False

        lock = self._get_reconnect_lock()
        async with lock:
            # bail if already connected
            try:
                if gw.is_connected:
                    return True
            except Exception:
                pass

            # clear session so the next IDENTIFY is fresh, not RESUME
            try: gw._session_id = None
            except Exception: pass
            try: gw._sequence = None
            except Exception: pass

            # close cleanly if still up
            try:
                if gw._ws is not None and not getattr(gw, "is_closed", False):
                    await gw.close()
            except Exception as e:
                logger.debug(f"[shim] reconnect close err: {e}")

            # let modifyself's own _message_loop exit
            await asyncio.sleep(0.5)

            # drive the reconnect ourselves
            try:
                await gw.connect()
                logger.info("[shim] reconnect_gateway: connected")
                return True
            except Exception as e:
                logger.warning(f"[shim] reconnect_gateway failed: {e}")
                return False

    # ── events ──

    def event(self, coro):
        if not asyncio.iscoroutinefunction(coro):
            raise TypeError("Event handlers must be coroutines")

        raw = coro.__name__.replace("on_", "", 1).upper()
        name = _EVENT_MAP.get(raw, raw)

        sig = inspect.signature(coro)
        pos_params = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            and p.default is p.empty
        ]
        arity = len(pos_params)
        state = self._state

        async def _wrapped(payload, _coro=coro, _name=name, _arity=arity, _state=state):
            try:
                obj = _upgrade(_state, _name, payload)
                if _arity == 0:
                    await _coro()
                elif _arity == 1:
                    await _coro(obj)
                elif _arity == 2:
                    await _coro(obj, obj)
                elif _arity == 3:
                    await _coro(obj, obj, obj)
                else:
                    await _coro(*([obj] * _arity))
            except Exception as e:
                logger.exception(f"[shim] handler {_name} failed: {e}")

        _wrapped.__name__ = coro.__name__
        self._dispatcher.on(name, _wrapped)
        self._event_handlers.setdefault(name, []).append(_wrapped)
        return coro

    # ── presence ──

    async def change_presence(self, *, activity=None, status=None, **_):
        if activity is not None: self._presence_activity = activity
        if status is not None: self._presence_status = str(status)

        payload = {"op": 3, "d": {
            "since": 0, "activities": [],
            "status": self._presence_status, "afk": False,
        }}
        if activity is not None:
            if hasattr(activity, "to_dict"):
                payload["d"]["activities"] = [activity.to_dict()]
            else:
                payload["d"]["activities"] = [{"type": 0, "name": str(activity)}]

        try: await self.ws.send_json(payload)
        except Exception as e:
            logger.debug(f"[shim] change_presence forward failed: {e}")

    # ── lifecycle ──

    def is_closed(self) -> bool:
        return self._closed_flag or getattr(self, "_closed", False)

    async def close(self):
        self._closed_flag = True
        try: await super().close()
        except Exception: pass

    async def wait_for(self, event: str, *, check=None, timeout=None):
        ev = _EVENT_MAP.get(event.upper(), event.upper())
        fut = asyncio.get_event_loop().create_future()
        state = self._state

        async def _handler(payload):
            obj = _upgrade(state, ev, payload)
            try:
                ok = (check is None) or (callable(check) and check(obj))
            except Exception:
                ok = False
            if ok and not fut.done(): fut.set_result(obj)

        self._dispatcher.on(ev, _handler)
        try: return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            try: self._dispatcher.off(ev, _handler)
            except Exception: pass

    # ── channels ──

    @property
    def private_channels(self):
        try:
            return [c for c in self._state._channels.values()
                    if isinstance(c, (_MSDMChannel, _MSGroupChannel))]
        except Exception:
            return []

    # ── webhooks / invites ──

    async def fetch_webhook(self, webhook_id: int):
        data = await self._http.request(method="GET", url=f"/webhooks/{webhook_id}")
        return _MSWebhook(state=self._state, data=data)

    async def fetch_invite(self, url: str, *, with_counts: bool = True):
        code = url.rstrip("/").split("/")[-1]
        return await self._http.request(
            method="GET",
            url=f"/invites/{code}?with_counts={'true' if with_counts else 'false'}",
        )

    async def application_info(self):
        return await self._http.request(method="GET", url="/oauth2/applications/@me")


__all__ = [
    "Client", "Game", "Activity", "ActivityType", "CustomActivity", "Status",
    "Embed", "File", "Object", "Color", "Permissions", "Role", "Emoji",
    "Message", "User", "Member", "Guild", "Channel",
    "TextChannel", "DMChannel", "GroupChannel", "VoiceChannel", "CategoryChannel",
    "Relationship", "RelationshipType", "Webhook",
    "HTTPException", "Forbidden", "NotFound", "LoginFailure", "InvalidToken",
    "DiscordException", "ui", "ButtonStyle", "utils",
]

Message           = _MSMessage
User              = _MSUser
Member            = _MSMember
Guild             = _MSGuild
Channel           = _MSChannel
TextChannel       = _MSTextChannel
DMChannel         = _MSDMChannel
GroupChannel      = _MSGroupChannel
VoiceChannel      = _MSVoiceChannel
CategoryChannel   = _MSCategoryChannel
Relationship      = _MSRelationship
RelationshipType  = _MSRelationshipType
Webhook           = _MSWebhook
