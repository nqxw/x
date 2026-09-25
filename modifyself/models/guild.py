"""
Discord Guild model.
"""

from typing import TYPE_CHECKING

from .base import DiscordObject
from ..core.enums import (
    VerificationLevel,
    NotificationLevel,
    ExplicitContentFilterLevel,
    MFALevel,
    NSFWLevel,
)

if TYPE_CHECKING:
    from ..state import ConnectionState


class Guild(DiscordObject):
    """Represents a Discord guild (server)."""

    __slots__ = (
        "name",
        "icon",
        "splash",
        "owner_id",
        "region",
        "afk_channel_id",
        "afk_timeout",
        "verification_level",
        "default_notifications",
        "explicit_content_filter",
        "mfa_level",
        "system_channel_id",
        "system_channel_flags",
        "max_presences",
        "max_members",
        "vanity_url_code",
        "description",
        "banner",
        "premium_tier",
        "premium_subscription_count",
        "preferred_locale",
        "public_updates_channel_id",
        "max_video_channel_users",
        "nsfw_level",
        "_members",
        "_channels",
        "_unavailable",
    )

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)
        self._members = {}
        self._channels = {}
        self._unavailable = False
        self._update(data)

    def _update(self, data: dict):
        # Update guild properties
        self.name = data.get("name", "")
        self.icon = data.get("icon")
        self.splash = data.get("splash")
        self.owner_id = int(data.get("owner_id", 0))
        self.region = data.get("region", "")
        self.afk_channel_id = (
            int(data["afk_channel_id"]) if data.get("afk_channel_id") else None
        )
        self.afk_timeout = data.get("afk_timeout", 0)
        try:
            self.verification_level = VerificationLevel(data.get("verification_level", 0))
        except ValueError:
            self.verification_level = data.get("verification_level", 0)
        try:
            self.default_notifications = NotificationLevel(data.get("default_message_notifications", 0))
        except ValueError:
            self.default_notifications = data.get("default_message_notifications", 0)
        try:
            self.explicit_content_filter = ExplicitContentFilterLevel(data.get("explicit_content_filter", 0))
        except ValueError:
            self.explicit_content_filter = data.get("explicit_content_filter", 0)
        try:
            self.mfa_level = MFALevel(data.get("mfa_level", 0))
        except ValueError:
            self.mfa_level = data.get("mfa_level", 0)
        self.system_channel_id = (
            int(data["system_channel_id"]) if data.get("system_channel_id") else None
        )
        self.system_channel_flags = data.get("system_channel_flags", 0)
        self.max_presences = data.get("max_presences")
        self.max_members = data.get("max_members", 0)
        self.vanity_url_code = data.get("vanity_url_code")
        self.description = data.get("description")
        self.banner = data.get("banner")
        self.premium_tier = data.get("premium_tier", 0)
        self.premium_subscription_count = data.get("premium_subscription_count", 0)
        self.preferred_locale = data.get("preferred_locale", "en-US")
        self.public_updates_channel_id = (
            int(data["public_updates_channel_id"])
            if data.get("public_updates_channel_id")
            else None
        )
        self.max_video_channel_users = data.get("max_video_channel_users", 0)
        try:
            self.nsfw_level = NSFWLevel(data.get("nsfw_level", 0))
        except ValueError:
            self.nsfw_level = data.get("nsfw_level", 0)

        # Parse nested members if present
        from .member import Member
        from .channel import channel_factory

        if "members" in data and data["members"]:
            for member_data in data["members"]:
                member_id = int(member_data.get("user", {}).get("id", 0))
                if member_id:
                    # Check if member already exists
                    existing = self._members.get(member_id)
                    if existing:
                        existing._update(member_data)
                    else:
                        member = Member(state=self._state, data=member_data, guild_id=self.id)
                        self._members[member.id] = member
                        # Also cache the user globally
                        if member_data.get("user"):
                            self._state._add_user(member_data["user"])

        # Parse channels if present
        if "channels" in data and data["channels"]:
            for channel_data in data["channels"]:
                channel_id = int(channel_data.get("id", 0))
                if channel_id:
                    existing = self._channels.get(channel_id)
                    if existing:
                        existing._update(channel_data)
                    else:
                        channel = channel_factory(self._state, channel_data)
                        self._channels[channel.id] = channel
                        self._state._channels[channel.id] = channel

    def __repr__(self) -> str:
        return f"<Guild id={self.id} name={self.name!r}>"

    def __str__(self) -> str:
        return self.name

    @property
    def icon_url(self) -> str | None:
        if self.icon:
            ext = "gif" if self.icon.startswith("a_") else "png"
            return f"https://cdn.discordapp.com/icons/{self.id}/{self.icon}.{ext}?size=1024"
        return None

    @property
    def splash_url(self) -> str | None:
        if self.splash:
            return f"https://cdn.discordapp.com/splashes/{self.id}/{self.splash}.png?size=1024"
        return None

    @property
    def banner_url(self) -> str | None:
        if self.banner:
            ext = "gif" if self.banner.startswith("a_") else "png"
            return f"https://cdn.discordapp.com/banners/{self.id}/{self.banner}.{ext}?size=1024"
        return None

    @property
    def owner(self):
        return self._state._users.get(self.owner_id)

    @property
    def unavailable(self) -> bool:
        return self._unavailable

    def get_member(self, member_id: int):
        return self._members.get(member_id)

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    @property
    def me(self):
        """The client's member in this guild."""
        return self._members.get(self._state.user.id) if self._state.user else None