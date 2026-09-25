"""
Discord User model.
"""

from typing import TYPE_CHECKING

from .base import DiscordObject
from ..core.bitfield import UserFlags
from ..utils import parse_time

if TYPE_CHECKING:
    from ..state import ConnectionState


class User(DiscordObject):
    """Represents a Discord user."""

    __slots__ = (
        "name",
        "global_name",
        "discriminator",
        "avatar",
        "bot",
        "system",
        "public_flags",
        "_avatar_decoration",
    )

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)
        self._update(data)

    def _update(self, data: dict):
        self.name = data.get("username", "")
        self.global_name = data.get("global_name")
        self.discriminator = data.get("discriminator", "0")
        self.avatar = data.get("avatar")
        self.bot = data.get("bot", False)
        self.system = data.get("system", False)
        self.public_flags = UserFlags(data.get("public_flags", 0))
        self._avatar_decoration = data.get("avatar_decoration_data")

    def __repr__(self) -> str:
        return f"<User id={self.id} name={self.name!r}>"

    def __str__(self) -> str:
        if self.discriminator != "0":
            return f"{self.name}#{self.discriminator}"
        return self.name

    @property
    def display_name(self) -> str:
        """The user's display name (global_name or username)."""
        return self.global_name or self.name

    @property
    def mention(self) -> str:
        """The mention string for this user."""
        return f"<@{self.id}>"

    @property
    def avatar_url(self) -> str | None:
        """The URL of the user's avatar, or None if they have no avatar."""
        if self.avatar:
            ext = "gif" if self.avatar.startswith("a_") else "png"
            return f"https://cdn.discordapp.com/avatars/{self.id}/{self.avatar}.{ext}?size=1024"
        return None

    @property
    def default_avatar_url(self) -> str:
        """The URL of the user's default avatar."""
        index = (int(self.id) >> 22) % 6
        return f"https://cdn.discordapp.com/embed/avatars/{index}.png"

    @property
    def display_avatar_url(self) -> str:
        """The URL of the user's display avatar (custom or default)."""
        return self.avatar_url or self.default_avatar_url
