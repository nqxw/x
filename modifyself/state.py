"""
Connection state: caches entities and parses gateway events.
"""

import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Optional, Dict, Any, List

from .core.snowflake import Snowflake
from .models.user import User
from .models.guild import Guild
from .models.channel import channel_factory, Channel, DMChannel, GroupChannel
from .models.message import Message
from .models.member import Member
from .models.relationship import Relationship

if TYPE_CHECKING:
    from .http.client import HTTPClient

logger = logging.getLogger(__name__)


class LRUCache(OrderedDict):
    __slots__ = ("maxsize",)
    
    def __init__(self, maxsize: int = 1000):
        super().__init__()
        self.maxsize = maxsize

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        if len(self) > self.maxsize:
            oldest = next(iter(self))
            del self[oldest]


class ConnectionState:
    __slots__ = (
        "http",
        "_users",
        "_guilds",
        "_channels",
        "_messages",
        "_relationships",
        "user",
        "session_id",
        "_ready",
        "_guild_versions",
        "_read_state_version",
        "_user_settings_version",
    )

    def __init__(self, http: "HTTPClient"):
        self.http = http
        self._users: Dict[Snowflake, User] = {}
        self._guilds: Dict[Snowflake, Guild] = {}
        self._channels: Dict[Snowflake, Channel] = {}
        self._messages: LRUCache = LRUCache(maxsize=5000)
        self._relationships: Dict[Snowflake, Relationship] = {}
        self.user: Optional[User] = None
        self.session_id: Optional[str] = None
        self._ready = False
        self._guild_versions: Dict[Snowflake, int] = {}
        self._read_state_version: int = 0
        self._user_settings_version: int = -1

    def _get_user(self, user_id: int) -> Optional[User]:
        return self._users.get(Snowflake(user_id))

    def _add_user(self, data: dict) -> User:
        user_id = Snowflake(data["id"])
        user = self._users.get(user_id)
        if user is None:
            user = User(state=self, data=data)
            self._users[user_id] = user
        else:
            user._update(data)
        return user

    def _remove_user(self, user_id: int) -> Optional[User]:
        return self._users.pop(Snowflake(user_id), None)

    def _add_guild(self, data: dict) -> Guild:
        guild_id = Snowflake(data["id"])
        guild = self._guilds.get(guild_id)
        if guild is None:
            guild = Guild(state=self, data=data)
            self._guilds[guild_id] = guild
        else:
            guild._update(data)
        return guild

    def _remove_guild(self, guild_id: int) -> Optional[Guild]:
        guild = self._guilds.pop(Snowflake(guild_id), None)
        if guild:
            for channel_id in list(guild._channels.keys()):
                self._channels.pop(channel_id, None)
        return guild

    def _add_channel(self, data: dict) -> Channel:
        channel_id = Snowflake(data["id"])
        channel = self._channels.get(channel_id)
        if channel is None:
            channel = channel_factory(self, data)
            self._channels[channel_id] = channel
        else:
            channel._update(data)
        return channel

    def _remove_channel(self, channel_id: int) -> Optional[Channel]:
        return self._channels.pop(Snowflake(channel_id), None)

    def _store_message(self, data: dict) -> Message:
        message_id = Snowflake(data["id"])
        message = self._messages.get(message_id)
        if message is None:
            message = Message(state=self, data=data)
            self._messages[message_id] = message
        else:
            message._update(data)
        return message

    def _remove_message(self, message_id: int) -> Optional[Message]:
        return self._messages.pop(Snowflake(message_id), None)

    def parse_ready(self, data: dict) -> User:
        self.session_id = data.get("session_id")
        
        client_state = data.get("client_state", {})
        self._guild_versions = client_state.get("guild_versions", {})
        self._read_state_version = client_state.get("read_state_version", 0)
        self._user_settings_version = client_state.get("user_settings_version", -1)
        
        self.user = User(state=self, data=data["user"])
        self._users[self.user.id] = self.user
        
        for guild_data in data.get("guilds", []):
            guild_id = Snowflake(guild_data["id"])
            if guild_id not in self._guilds:
                guild = Guild(state=self, data=guild_data)
                guild._unavailable = True
                self._guilds[guild_id] = guild
        
        for channel_data in data.get("private_channels", []):
            self._add_channel(channel_data)

        for rel_data in data.get("relationships", []):
            rel_id = Snowflake(rel_data["id"])
            rel = Relationship(state=self, data=rel_data)
            self._relationships[rel_id] = rel

        self._ready = True
        logger.info(f"Ready: {self.user} ({self.user.id})")
        return self.user

    def parse_resumed(self, data: dict) -> dict:
        logger.info(f"Resumed session {self.session_id}")
        self._ready = True
        return data

    def parse_user_update(self, data: dict) -> User:
        user = self._add_user(data)
        if self.user and user.id == self.user.id:
            self.user = user
        return user

    def parse_guild_create(self, data: dict) -> Guild:
        guild = self._add_guild(data)
        guild._unavailable = False
        self._guild_versions[guild.id] = data.get("guild_version", 0)
        return guild

    def parse_guild_update(self, data: dict) -> Guild:
        return self._add_guild(data)

    def parse_guild_delete(self, data: dict) -> Optional[Guild]:
        guild_id = Snowflake(data["id"])
        is_unavailable = data.get("unavailable", False)
        
        if is_unavailable:
            guild = self._guilds.get(guild_id)
            if guild:
                guild._unavailable = True
            return guild
        else:
            return self._remove_guild(guild_id)

    def parse_guild_member_add(self, data: dict) -> Optional[Member]:
        guild_id = int(data.get("guild_id", 0))
        member = Member(state=self, data=data, guild_id=guild_id)
        guild = self._guilds.get(guild_id)
        if guild:
            guild._members[member.id] = member
        return member

    def parse_guild_member_remove(self, data: dict) -> Optional[Member]:
        guild_id = int(data.get("guild_id", 0))
        user_data = data.get("user", {})
        user_id = Snowflake(user_data.get("id", 0))
        guild = self._guilds.get(guild_id)
        if guild:
            return guild._members.pop(user_id, None)
        return None

    def parse_guild_member_update(self, data: dict) -> Optional[Member]:
        guild_id = int(data.get("guild_id", 0))
        guild = self._guilds.get(guild_id)
        if guild:
            user_data = data.get("user", {})
            user_id = Snowflake(user_data.get("id", 0))
            member = guild._members.get(user_id)
            if member:
                member._update(data)
                return member
            else:
                member = Member(state=self, data=data, guild_id=guild_id)
                guild._members[member.id] = member
                return member
        return None

    def parse_channel_create(self, data: dict) -> Channel:
        channel = self._add_channel(data)
        guild_id = int(data.get("guild_id", 0))
        guild = self._guilds.get(guild_id)
        if guild:
            guild._channels[channel.id] = channel
        return channel

    def parse_channel_update(self, data: dict) -> Channel:
        channel_id = Snowflake(data["id"])
        channel = self._channels.get(channel_id)
        if channel:
            channel._update(data)
        else:
            channel = self._add_channel(data)
        return channel

    def parse_channel_delete(self, data: dict) -> Optional[Channel]:
        channel_id = Snowflake(data["id"])
        channel = self._remove_channel(channel_id)
        guild_id = int(data.get("guild_id", 0))
        guild = self._guilds.get(guild_id)
        if guild:
            guild._channels.pop(channel_id, None)
        return channel

    def parse_message_create(self, data: dict) -> Message:
        return self._store_message(data)

    def parse_message_update(self, data: dict) -> Message:
        message_id = Snowflake(data["id"])
        message = self._messages.get(message_id)
        if message:
            message._update(data)
        else:
            message = self._store_message(data)
        return message

    def parse_message_delete(self, data: dict) -> Optional[Message]:
        message_id = Snowflake(data["id"])
        return self._remove_message(message_id)

    def parse_message_delete_bulk(self, data: dict) -> List[Message]:
        ids = [Snowflake(i) for i in data.get("ids", [])]
        messages = []
        for mid in ids:
            msg = self._remove_message(mid)
            if msg:
                messages.append(msg)
        return messages

    def parse_typing_start(self, data: dict) -> dict:
        return data

    def parse_presence_update(self, data: dict) -> dict:
        user_data = data.get("user")
        if user_data and "id" in user_data:
            user_id = Snowflake(user_data["id"])
            user = self._users.get(user_id)
            if user and len(user_data) > 1:
                user._update({**{"id": user_data["id"], "username": user.name, "discriminator": user.discriminator, "avatar": user.avatar}, **user_data})
        return data

    def parse_relationship_add(self, data: dict) -> Relationship:
        rel_id = Snowflake(data["id"])
        rel = self._relationships.get(rel_id)
        if rel:
            rel._update(data)
        else:
            rel = Relationship(state=self, data=data)
            self._relationships[rel_id] = rel
        return rel

    def parse_relationship_remove(self, data: dict) -> Optional[Relationship]:
        rel_id = Snowflake(data["id"])
        return self._relationships.pop(rel_id, None)

    def parse_channel_recipient_add(self, data: dict) -> Optional[Channel]:
        channel_id = Snowflake(data.get("channel_id", 0))
        channel = self._channels.get(channel_id)
        if channel and isinstance(channel, GroupChannel):
            user_data = data.get("user")
            if user_data:
                user = self._add_user(user_data)
                if user not in channel.recipients:
                    channel.recipients.append(user)
        return channel

    def parse_channel_recipient_remove(self, data: dict) -> Optional[Channel]:
        channel_id = Snowflake(data.get("channel_id", 0))
        channel = self._channels.get(channel_id)
        if channel and isinstance(channel, GroupChannel):
            user_data = data.get("user")
            if user_data:
                user_id = int(user_data.get("id", 0))
                channel.recipients = [u for u in channel.recipients if u.id != user_id]
        return channel

    def parse_guild_members_chunk(self, data: dict) -> dict:
        guild_id = int(data.get("guild_id", 0))
        guild = self._guilds.get(guild_id)
        if guild:
            for member_data in data.get("members", []):
                user_data = member_data.get("user", {})
                member_id = Snowflake(user_data.get("id", 0))
                if member_id:
                    existing = guild._members.get(member_id)
                    if existing:
                        existing._update(member_data)
                    else:
                        member = Member(state=self, data=member_data, guild_id=guild_id)
                        guild._members[member.id] = member
                    if user_data:
                        self._add_user(user_data)
        return data

    def parse_message_reaction_add(self, data: dict) -> dict:
        return data

    def parse_message_reaction_remove(self, data: dict) -> dict:
        return data

    def parse_message_reaction_remove_all(self, data: dict) -> dict:
        return data

    def parse_message_reaction_remove_emoji(self, data: dict) -> dict:
        return data

    def parse_call_create(self, data: dict) -> dict:
        return data

    def parse_call_update(self, data: dict) -> dict:
        return data

    def parse_call_delete(self, data: dict) -> dict:
        return data

    def parse_voice_state_update(self, data: dict) -> dict:
        return data

    def parse_voice_server_update(self, data: dict) -> dict:
        return data

    def parse_thread_create(self, data: dict) -> Channel:
        return self._add_channel(data)

    def parse_thread_update(self, data: dict) -> Channel:
        return self.parse_channel_update(data)

    def parse_thread_delete(self, data: dict) -> Optional[Channel]:
        return self.parse_channel_delete(data)

    def parse_thread_list_sync(self, data: dict) -> dict:
        for thread_data in data.get("threads", []):
            self._add_channel(thread_data)
        return data

    @property
    def ready(self) -> bool:
        return self._ready

    def get_guild_versions(self) -> dict:
        return self._guild_versions

    def get_client_state(self) -> dict:
        return {
            "guild_versions": {str(k): v for k, v in self._guild_versions.items()},
            "highest_last_message_id": "0",
            "read_state_version": self._read_state_version,
            "user_guild_settings_version": -1,
            "user_settings_version": self._user_settings_version,
        }

    def clear(self) -> None:
        self._users.clear()
        self._guilds.clear()
        self._channels.clear()
        self._messages.clear()
        self._relationships.clear()
        self.user = None
        self.session_id = None
        self._ready = False
        self._guild_versions.clear()
        self._read_state_version = 0
        self._user_settings_version = -1
        logger.info("State cleared")

    def get_stats(self) -> dict:
        return {
            "users": len(self._users),
            "guilds": len(self._guilds),
            "channels": len(self._channels),
            "messages": len(self._messages),
            "ready": self._ready,
            "session_id": self.session_id,
            "guild_versions": len(self._guild_versions),
        }