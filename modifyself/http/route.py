"""
Immutable value objects representing Discord HTTP endpoints.
"""

from typing import Any


class Route:
    """
    Represents a Discord HTTP route.

    The bucket key is normalized (placeholders for IDs) so that
    different channels/guilds sharing a rate limit bucket are
    grouped correctly.
    """

    BASE = "https://discord.com/api/v9"

    def __init__(self, method: str, path: str, **parameters: Any):
        self.method = method.upper()
        normalized = {
            k: int(v) if isinstance(v, int) else v
            for k, v in parameters.items()
        }

        self.path = path.format(**normalized)
        self.bucket = f"{self.method}/{path}"

    @property
    def url(self) -> str:
        return f"{self.BASE}{self.path}"

    def __repr__(self) -> str:
        return f"<Route {self.method} {self.path}>"

    def __eq__(self, other) -> bool:
        if isinstance(other, Route):
            return self.method == other.method and self.path == other.path
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.method, self.path))

    # --- Users ---
    @classmethod
    def me(cls):
        return cls("GET", "/users/@me")

    @classmethod
    def me_guilds(cls, with_counts: bool = True):
        if with_counts:
            return cls("GET", "/users/@me/guilds?with_counts=true")
        return cls("GET", "/users/@me/guilds")

    @classmethod
    def user(cls, user_id: int):
        return cls("GET", "/users/{user_id}", user_id=user_id)

    @classmethod
    def edit_me(cls):
        return cls("PATCH", "/users/@me")

    @classmethod
    def profile(cls, user_id: int, guild_id: int = None):
        url = f"/users/{user_id}/profile"
        if guild_id:
            url += f"?guild_id={guild_id}"
        return cls("GET", url)

    @classmethod
    def settings(cls):
        return cls("GET", "/users/@me/settings-proto/1")

    @classmethod
    def patch_settings(cls):
        return cls("PATCH", "/users/@me/settings-proto/1")

    # --- Guilds ---
    @classmethod
    def guild(cls, guild_id: int):
        return cls("GET", "/guilds/{guild_id}", guild_id=guild_id)

    @classmethod
    def guild_channels(cls, guild_id: int):
        return cls("GET", "/guilds/{guild_id}/channels", guild_id=guild_id)

    @classmethod
    def create_guild(cls):
        return cls("POST", "/guilds")

    @classmethod
    def leave_guild(cls, guild_id: int):
        return cls("DELETE", "/users/@me/guilds/{guild_id}", guild_id=guild_id)

    @classmethod
    def guild_member(cls, guild_id: int, user_id: int):
        return cls("GET", "/guilds/{guild_id}/members/{user_id}", guild_id=guild_id, user_id=user_id)

    @classmethod
    def edit_nickname(cls, guild_id: int):
        return cls("PATCH", "/guilds/{guild_id}/members/@me", guild_id=guild_id)

    @classmethod
    def search_messages(cls, guild_id: int = None, channel_id: int = None):
        if guild_id:
            return cls("GET", f"/guilds/{guild_id}/messages/search")
        return cls("GET", f"/channels/{channel_id}/messages/search")

    @classmethod
    def join_guild(cls, invite_code: str):
        return cls("POST", f"/invites/{invite_code}")

    @classmethod
    def edit_guild(cls, guild_id: int):
        return cls("PATCH", "/guilds/{guild_id}", guild_id=guild_id)

    @classmethod
    def guild_members(cls, guild_id: int):
        return cls("GET", "/guilds/{guild_id}/members", guild_id=guild_id)

    @classmethod
    def search_guild_members(cls, guild_id: int):
        return cls("GET", "/guilds/{guild_id}/members/search", guild_id=guild_id)

    @classmethod
    def kick_member(cls, guild_id: int, user_id: int):
        return cls("DELETE", "/guilds/{guild_id}/members/{user_id}", guild_id=guild_id, user_id=user_id)

    @classmethod
    def ban_member(cls, guild_id: int, user_id: int):
        return cls("PUT", "/guilds/{guild_id}/bans/{user_id}", guild_id=guild_id, user_id=user_id)

    @classmethod
    def unban_member(cls, guild_id: int, user_id: int):
        return cls("DELETE", "/guilds/{guild_id}/bans/{user_id}", guild_id=guild_id, user_id=user_id)

    @classmethod
    def guild_bans(cls, guild_id: int):
        return cls("GET", "/guilds/{guild_id}/bans", guild_id=guild_id)

    @classmethod
    def guild_roles(cls, guild_id: int):
        return cls("GET", "/guilds/{guild_id}/roles", guild_id=guild_id)

    @classmethod
    def create_role(cls, guild_id: int):
        return cls("POST", "/guilds/{guild_id}/roles", guild_id=guild_id)

    @classmethod
    def edit_role(cls, guild_id: int, role_id: int):
        return cls("PATCH", "/guilds/{guild_id}/roles/{role_id}", guild_id=guild_id, role_id=role_id)

    @classmethod
    def delete_role(cls, guild_id: int, role_id: int):
        return cls("DELETE", "/guilds/{guild_id}/roles/{role_id}", guild_id=guild_id, role_id=role_id)

    @classmethod
    def invite(cls, invite_code: str):
        return cls("GET", f"/invites/{invite_code}")

    @classmethod
    def delete_invite(cls, invite_code: str):
        return cls("DELETE", f"/invites/{invite_code}")

    # --- Channels ---
    @classmethod
    def channel(cls, channel_id: int):
        return cls("GET", "/channels/{channel_id}", channel_id=channel_id)

    @classmethod
    def edit_channel(cls, channel_id: int):
        return cls("PATCH", "/channels/{channel_id}", channel_id=channel_id)

    @classmethod
    def delete_channel(cls, channel_id: int):
        return cls("DELETE", "/channels/{channel_id}", channel_id=channel_id)

    @classmethod
    def channel_messages(cls, channel_id: int):
        return cls("GET", "/channels/{channel_id}/messages", channel_id=channel_id)

    @classmethod
    def channel_message(cls, channel_id: int, message_id: int):
        return cls("GET", "/channels/{channel_id}/messages/{message_id}", channel_id=channel_id, message_id=message_id)

    @classmethod
    def send_message(cls, channel_id: int):
        return cls("POST", "/channels/{channel_id}/messages", channel_id=channel_id)

    @classmethod
    def edit_message(cls, channel_id: int, message_id: int):
        return cls(
            "PATCH",
            "/channels/{channel_id}/messages/{message_id}",
            channel_id=channel_id,
            message_id=message_id,
        )

    @classmethod
    def delete_message(cls, channel_id: int, message_id: int):
        return cls(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}",
            channel_id=channel_id,
            message_id=message_id,
        )

    @classmethod
    def bulk_delete_messages(cls, channel_id: int):
        return cls(
            "POST",
            "/channels/{channel_id}/messages/bulk-delete",
            channel_id=channel_id,
        )

    @classmethod
    def crosspost_message(cls, channel_id: int, message_id: int):
        return cls(
            "POST",
            "/channels/{channel_id}/messages/{message_id}/crosspost",
            channel_id=channel_id,
            message_id=message_id,
        )

    @classmethod
    def typing(cls, channel_id: int):
        return cls("POST", "/channels/{channel_id}/typing", channel_id=channel_id)

    @classmethod
    def pin_message(cls, channel_id: int, message_id: int):
        return cls(
            "PUT",
            "/channels/{channel_id}/pins/{message_id}",
            channel_id=channel_id,
            message_id=message_id,
        )

    @classmethod
    def unpin_message(cls, channel_id: int, message_id: int):
        return cls(
            "DELETE",
            "/channels/{channel_id}/pins/{message_id}",
            channel_id=channel_id,
            message_id=message_id,
        )

    @classmethod
    def channel_pins(cls, channel_id: int):
        return cls("GET", "/channels/{channel_id}/pins", channel_id=channel_id)

    @classmethod
    def create_thread(cls, channel_id: int, message_id: int):
        return cls(
            "POST",
            "/channels/{channel_id}/messages/{message_id}/threads",
            channel_id=channel_id,
            message_id=message_id,
        )

    @classmethod
    def create_thread_in_channel(cls, channel_id: int):
        return cls("POST", "/channels/{channel_id}/threads", channel_id=channel_id)

    @classmethod
    def channel_threads(cls, channel_id: int):
        return cls("GET", "/channels/{channel_id}/threads/active", channel_id=channel_id)

    # --- Reactions ---
    @classmethod
    def add_reaction(cls, channel_id: int, message_id: int, emoji: str):
        return cls(
            "PUT",
            "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me",
            channel_id=channel_id,
            message_id=message_id,
            emoji=emoji,
        )

    @classmethod
    def remove_reaction(cls, channel_id: int, message_id: int, emoji: str):
        return cls(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me",
            channel_id=channel_id,
            message_id=message_id,
            emoji=emoji,
        )

    @classmethod
    def clear_reactions(cls, channel_id: int, message_id: int):
        return cls(
            "DELETE",
            "/channels/{channel_id}/messages/{message_id}/reactions",
            channel_id=channel_id,
            message_id=message_id,
        )

    # --- Relationships ---
    @classmethod
    def relationships(cls):
        return cls("GET", "/users/@me/relationships")

    @classmethod
    def add_relationship(cls, user_id: int):
        return cls("PUT", "/users/@me/relationships/{user_id}", user_id=user_id)

    @classmethod
    def remove_relationship(cls, user_id: int):
        return cls("DELETE", "/users/@me/relationships/{user_id}", user_id=user_id)

    # --- DMs ---
    @classmethod
    def create_dm(cls):
        return cls("POST", "/users/@me/channels")

    @classmethod
    def create_group_dm(cls):
        return cls("POST", "/users/@me/channels")

    # --- Auth ---
    @classmethod
    def login(cls):
        return cls("POST", "/auth/login")

    @classmethod
    def logout(cls):
        return cls("POST", "/auth/logout")

    @classmethod
    def register(cls):
        return cls("POST", "/auth/register")