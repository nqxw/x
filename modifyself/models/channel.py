"""
Discord Channel models.
"""

from typing import TYPE_CHECKING, Optional, List

from .base import DiscordObject
from .user import User
from ..core.enums import ChannelType

if TYPE_CHECKING:
    from ..state import ConnectionState


class Channel(DiscordObject):
    """Base class for all channel types."""

    __slots__ = ("type", "guild_id", "position", "name")

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)
        self._update(data)

    def _update(self, data: dict):
        try:
            self.type = ChannelType(data.get("type", 0))
        except ValueError:
            self.type = data.get("type", 0)
        self.guild_id = int(data["guild_id"]) if "guild_id" in data else None
        self.position = data.get("position", 0)
        self.name = data.get("name")

    def __repr__(self) -> str:
        return f"<Channel id={self.id} name={self.name!r} type={self.type.name}>"

    def __str__(self) -> str:
        return self.name or ""

    async def send(self, content: str | None = None, **kwargs):
        """Send a message to this channel."""
        data = await self._state.http.send_message(self.id, content, **kwargs)
        return self._state._store_message(data)

    async def typing(self):
        """Trigger a typing indicator in this channel."""
        await self._state.http.trigger_typing(self.id)

    @property
    def guild(self):
        if self.guild_id:
            return self._state._guilds.get(self.guild_id)
        return None

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"


class TextChannel(Channel):
    """Represents a guild text channel."""

    __slots__ = ("topic", "nsfw", "last_message_id", "parent_id", "slowmode_delay")

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)

    def _update(self, data: dict):
        super()._update(data)
        self.topic = data.get("topic")
        self.nsfw = data.get("nsfw", False)
        self.last_message_id = (
            int(data["last_message_id"]) if data.get("last_message_id") else None
        )
        self.parent_id = int(data["parent_id"]) if data.get("parent_id") else None
        self.slowmode_delay = data.get("rate_limit_per_user", 0)

    def __repr__(self) -> str:
        return f"<TextChannel id={self.id} name={self.name!r}>"


class DMChannel(Channel):
    """Represents a DM channel."""

    __slots__ = ("recipients",)

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)

    def _update(self, data: dict):
        super()._update(data)
        self.recipients = [
            User(state=self._state, data=u) for u in data.get("recipients", [])
        ]

    @property
    def recipient(self):
        """The other user in this DM, if any."""
        if self.recipients:
            return self.recipients[0]
        return None

    def __repr__(self) -> str:
        return f"<DMChannel id={self.id} recipient={self.recipient}>"


class GroupChannel(Channel):
    """Represents a group DM channel."""

    __slots__ = ("name", "icon", "recipients", "owner_id", "application_id", "managed")

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)

    def _update(self, data: dict):
        super()._update(data)
        self.name = data.get("name")
        self.icon = data.get("icon")
        self.recipients = [
            User(state=self._state, data=u) for u in data.get("recipients", [])
        ]
        self.owner_id = int(data["owner_id"]) if data.get("owner_id") else None
        self.application_id = int(data["application_id"]) if data.get("application_id") else None
        self.managed = data.get("managed", False)

    @property
    def icon_url(self) -> Optional[str]:
        if self.icon:
            return f"https://cdn.discordapp.com/channel-icons/{self.id}/{self.icon}.png"
        return None

    def __repr__(self) -> str:
        return f"<GroupChannel id={self.id} name={self.name!r}>"


class VoiceChannel(Channel):
    """Represents a guild voice channel."""

    __slots__ = ("bitrate", "user_limit", "parent_id", "rtc_region")

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)

    def _update(self, data: dict):
        super()._update(data)
        self.bitrate = data.get("bitrate", 64000)
        self.user_limit = data.get("user_limit", 0)
        self.parent_id = int(data["parent_id"]) if data.get("parent_id") else None
        self.rtc_region = data.get("rtc_region")

    def __repr__(self) -> str:
        return f"<VoiceChannel id={self.id} name={self.name!r}>"


class CategoryChannel(Channel):
    """Represents a guild category channel."""

    __slots__ = ("nsfw",)

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)

    def _update(self, data: dict):
        super()._update(data)
        self.nsfw = data.get("nsfw", False)

    def __repr__(self) -> str:
        return f"<CategoryChannel id={self.id} name={self.name!r}>"


def channel_factory(state: "ConnectionState", data: dict) -> Channel:
    """Factory function to create the correct channel type."""
    channel_type = data.get("type", 0)
    if channel_type == ChannelType.GUILD_TEXT:
        return TextChannel(state=state, data=data)
    elif channel_type == ChannelType.DM:
        return DMChannel(state=state, data=data)
    elif channel_type == ChannelType.GROUP_DM:
        return GroupChannel(state=state, data=data)
    elif channel_type in (ChannelType.GUILD_VOICE, ChannelType.GUILD_STAGE_VOICE):
        return VoiceChannel(state=state, data=data)
    elif channel_type == ChannelType.GUILD_CATEGORY:
        return CategoryChannel(state=state, data=data)
    elif channel_type in (
        ChannelType.GUILD_ANNOUNCEMENT,
        ChannelType.GUILD_FORUM,
        ChannelType.GUILD_MEDIA,
    ):
        return TextChannel(state=state, data=data)
    elif channel_type in (
        ChannelType.ANNOUNCEMENT_THREAD,
        ChannelType.PUBLIC_THREAD,
        ChannelType.PRIVATE_THREAD,
    ):
        return TextChannel(state=state, data=data)
    else:
        return Channel(state=state, data=data)