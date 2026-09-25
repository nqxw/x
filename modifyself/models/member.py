"""
Discord Member model (guild-specific user).
"""

from typing import TYPE_CHECKING

from .base import DiscordObject
from .user import User
from ..utils import parse_time

if TYPE_CHECKING:
    from ..state import ConnectionState
    from .guild import Guild


class Member(DiscordObject):
    """Represents a member of a guild."""

    __slots__ = (
        "guild_id",
        "nick",
        "roles",
        "joined_at",
        "premium_since",
        "pending",
        "avatar",
        "flags",
        "communication_disabled_until",
    )

    def __init__(self, *, state: "ConnectionState", data: dict, guild_id: int):
        user_data = data.get("user", {})
        # Member ID comes from the user object
        super().__init__(state=state, data=user_data or {"id": data.get("id", "0")})
        self.guild_id = guild_id
        self._update(data)

    def _update(self, data: dict):
        user_data = data.get("user")
        if user_data:
            user = self._state._users.get(self.id)
            if user is None:
                user = User(state=self._state, data=user_data)
                self._state._users[user.id] = user
            else:
                user._update(user_data)

        self.nick = data.get("nick")
        self.roles = [int(r) for r in data.get("roles", [])]
        self.joined_at = parse_time(data["joined_at"]) if "joined_at" in data else None
        self.premium_since = (
            parse_time(data["premium_since"]) if data.get("premium_since") else None
        )
        self.pending = data.get("pending", False)
        self.avatar = data.get("avatar")
        self.flags = data.get("flags", 0)
        self.communication_disabled_until = (
            parse_time(data["communication_disabled_until"])
            if data.get("communication_disabled_until")
            else None
        )

    @property
    def _user(self):
        return self._state._users.get(self.id)

    @property
    def name(self) -> str:
        user = self._user
        return self.nick or (user.name if user else "")

    @property
    def display_name(self) -> str:
        user = self._user
        return self.nick or (user.display_name if user else "")

    @property
    def mention(self) -> str:
        if self.nick:
            return f"<@!{self.id}>"
        return f"<@{self.id}>"

    @property
    def guild(self) -> "Guild | None":
        return self._state._guilds.get(self.guild_id)

    def __repr__(self) -> str:
        return f"<Member id={self.id} name={self.display_name!r} guild={self.guild_id}>"

    def __str__(self) -> str:
        return self.display_name
