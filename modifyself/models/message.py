"""
Discord Message model.
"""

from typing import TYPE_CHECKING, Optional, List, Dict, Any, Union
from datetime import datetime

from .base import DiscordObject
from .user import User
from .member import Member
from ..core.enums import MessageType
from ..utils import parse_time
from ..components import Component, ActionRow, ComponentType

if TYPE_CHECKING:
    from ..state import ConnectionState


class Message(DiscordObject):
    """Represents a Discord message."""

    __slots__ = (
        "channel_id",
        "author",
        "content",
        "timestamp",
        "edited_timestamp",
        "tts",
        "mention_everyone",
        "mentions",
        "mention_roles",
        "attachments",
        "embeds",
        "reactions",
        "pinned",
        "type",
        "guild_id",
        "member",
        "referenced_message",
        "flags",
        "thread",
        "application",
        "message_reference",
        "interaction",
        "sticker_items",
        "position",
        "role_subscription_data",
        "resolved",
        "poll",
        "call",
        "_components",
    )

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)
        self._update(data)

    def _update(self, data: dict):
        self.id = int(data["id"])
        self.channel_id = int(data["channel_id"])
        self.content = data.get("content", "")
        self.timestamp = parse_time(data["timestamp"])
        self.edited_timestamp = parse_time(data["edited_timestamp"]) if data.get("edited_timestamp") else None
        self.tts = data.get("tts", False)
        self.mention_everyone = data.get("mention_everyone", False)
        self.mentions = [User(state=self._state, data=u) for u in data.get("mentions", [])]
        self.mention_roles = data.get("mention_roles", [])
        self.attachments = data.get("attachments", [])
        self.embeds = data.get("embeds", [])
        self.reactions = data.get("reactions", [])
        self.pinned = data.get("pinned", False)
        try:
            self.type = MessageType(data.get("type", 0))
        except ValueError:
            self.type = data.get("type", 0)
        self.guild_id = int(data["guild_id"]) if data.get("guild_id") else None
        self.flags = data.get("flags", 0)
        self.sticker_items = data.get("sticker_items", [])
        self.position = data.get("position", 0)
        self.role_subscription_data = data.get("role_subscription_data")
        self.resolved = data.get("resolved")
        self.poll = data.get("poll")
        self.call = data.get("call")
        
        # Author
        self.author = User(state=self._state, data=data["author"])
        
        # Member (if in guild)
        self.member = None
        if data.get("member"):
            self.member = Member(state=self._state, data=data["member"], guild_id=self.guild_id)
        
        # Referenced message
        self.referenced_message = None
        if data.get("referenced_message"):
            self.referenced_message = Message(state=self._state, data=data["referenced_message"])
        
        # Thread
        self.thread = data.get("thread")
        
        # Application
        self.application = data.get("application")
        
        # Message reference
        self.message_reference = data.get("message_reference")
        
        # Interaction
        self.interaction = data.get("interaction")
        
        # Components
        self._components = []
        for comp_data in data.get("components", []):
            if comp_data.get("type") == ComponentType.ACTION_ROW:
                row = ActionRow()
                for child_data in comp_data.get("components", []):
                    row.components.append(Component(child_data))
                self._components.append(row)

    @property
    def jump_url(self) -> str:
        """The jump URL for this message."""
        return f"https://discord.com/channels/{self.guild_id or '@me'}/{self.channel_id}/{self.id}"

    @property
    def clean_content(self) -> str:
        """The content of the message with mentions cleaned."""
        content = self.content
        for user in self.mentions:
            content = content.replace(f"<@{user.id}>", f"@{user.name}")
            content = content.replace(f"<@!{user.id}>", f"@{user.name}")
        for role_id in self.mention_roles:
            content = content.replace(f"<@&{role_id}>", f"@<role>")
        if self.mention_everyone:
            content = content.replace("@everyone", "@everyone")
            content = content.replace("@here", "@here")
        return content

    @property
    def components(self) -> List[ActionRow]:
        """The components in this message."""
        return self._components

    async def edit(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        components: Optional[List[ActionRow]] = None,
        **kwargs,
    ) -> "Message":
        """Edit this message."""
        payload = {}
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if components is not None:
            payload["components"] = [c.to_dict() for c in components]
        payload.update(kwargs)
        
        data = await self._state.http.edit_message(
            self.channel_id,
            self.id,
            **payload,
        )
        self._update(data)
        return self

    async def delete(self) -> None:
        """Delete this message."""
        await self._state.http.delete_message(self.channel_id, self.id)

    async def reply(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        components: Optional[List[ActionRow]] = None,
        **kwargs,
    ) -> "Message":
        """Reply to this message."""
        payload = {
            "message_reference": {"message_id": self.id},
        }
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if components is not None:
            payload["components"] = [c.to_dict() for c in components]
        payload.update(kwargs)
        
        data = await self._state.http.send_message(
            self.channel_id,
            **payload,
        )
        return self._state._store_message(data)

    async def add_reaction(self, emoji: str) -> None:
        """Add a reaction to this message."""
        await self._state.http.add_reaction(self.channel_id, self.id, emoji)

    async def remove_reaction(self, emoji: str) -> None:
        """Remove a reaction from this message."""
        await self._state.http.remove_reaction(self.channel_id, self.id, emoji)

    async def clear_reactions(self) -> None:
        """Clear all reactions from this message."""
        await self._state.http.clear_reactions(self.channel_id, self.id)

    async def pin(self) -> None:
        """Pin this message."""
        await self._state.http.pin_message(self.channel_id, self.id)

    async def unpin(self) -> None:
        """Unpin this message."""
        await self._state.http.unpin_message(self.channel_id, self.id)

    def __repr__(self) -> str:
        return f"<Message id={self.id} content={self.content[:50]!r}>"