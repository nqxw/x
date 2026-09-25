"""
Discord Webhook models.
"""

from typing import TYPE_CHECKING, Optional, List, Dict, Any, Union
from .base import DiscordObject
from .user import User
from .channel import Channel
from ..utils import parse_time

if TYPE_CHECKING:
    from ..state import ConnectionState


class Webhook(DiscordObject):
    """Represents a Discord webhook."""

    __slots__ = (
        "type",
        "name",
        "avatar",
        "channel_id",
        "guild_id",
        "user",
        "token",
        "source_guild",
        "source_channel",
        "url",
    )

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)
        self._update(data)

    def _update(self, data: dict):
        self.id = int(data["id"])
        self.type = data.get("type", 1)  # 1=incoming, 2=channel follower
        self.name = data.get("name")
        self.avatar = data.get("avatar")
        self.channel_id = int(data["channel_id"])
        self.guild_id = int(data["guild_id"]) if data.get("guild_id") else None
        self.token = data.get("token")
        self.source_guild = data.get("source_guild")
        self.source_channel = data.get("source_channel")
        
        self.user = None
        if data.get("user"):
            self.user = User(state=self._state, data=data["user"])
        
        self.url = data.get("url")

    @property
    def avatar_url(self) -> Optional[str]:
        """The URL of the webhook's avatar."""
        if self.avatar:
            return f"https://cdn.discordapp.com/avatars/{self.id}/{self.avatar}.png"
        return None

    @property
    def display_name(self) -> str:
        """The display name of the webhook."""
        return self.name or "Unknown Webhook"

    async def send(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        tts: bool = False,
        components: Optional[List] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Send a message with this webhook."""
        payload = {}
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if username is not None:
            payload["username"] = username
        if avatar_url is not None:
            payload["avatar_url"] = avatar_url
        if tts:
            payload["tts"] = tts
        if components is not None:
            payload["components"] = [c.to_dict() for c in components]
        payload.update(kwargs)
        
        url = f"/webhooks/{self.id}/{self.token}"
        return await self._state.http.request(
            method="POST",
            url=url,
            json=payload,
        )

    async def edit(
        self,
        name: Optional[str] = None,
        avatar: Optional[str] = None,
        channel_id: Optional[int] = None,
        **kwargs,
    ) -> "Webhook":
        """Edit this webhook."""
        payload = {}
        if name is not None:
            payload["name"] = name
        if avatar is not None:
            payload["avatar"] = avatar
        if channel_id is not None:
            payload["channel_id"] = str(channel_id)
        payload.update(kwargs)
        
        data = await self._state.http.request(
            method="PATCH",
            url=f"/webhooks/{self.id}",
            json=payload,
        )
        self._update(data)
        return self

    async def delete(self) -> None:
        """Delete this webhook."""
        await self._state.http.request(
            method="DELETE",
            url=f"/webhooks/{self.id}",
        )

    def __repr__(self) -> str:
        return f"<Webhook id={self.id} name={self.name!r}>"


class WebhookMessage:
    """A message sent via webhook."""
    
    def __init__(self, data: dict):
        self.id = int(data["id"])
        self.channel_id = int(data["channel_id"])
        self.content = data.get("content", "")
        self.timestamp = parse_time(data["timestamp"])
        self.edited_timestamp = parse_time(data["edited_timestamp"]) if data.get("edited_timestamp") else None
        self.embeds = data.get("embeds", [])
        self.attachments = data.get("attachments", [])
        self.components = data.get("components", [])
        self.flags = data.get("flags", 0)
        self.webhook_id = int(data.get("webhook_id", 0))
    
    async def edit(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        components: Optional[List] = None,
        **kwargs,
    ) -> "WebhookMessage":
        """Edit this webhook message."""
        payload = {}
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if components is not None:
            payload["components"] = [c.to_dict() for c in components]
        payload.update(kwargs)
        return self
    
    async def delete(self) -> None:
        """Delete this webhook message."""
        pass