"""
User settings models.
"""

from typing import Optional, Dict, Any, List
from .base import DiscordObject

class GuildSettings(DiscordObject):
    """Settings for a specific guild."""
    
    __slots__ = (
        "guild_id",
        "channel_overwrites",
        "muted",
        "mute_config",
        "mobile_push",
        "message_notifications",
        "flags",
        "notify_highlights",
    )
    
    def __init__(self, *, state, data: dict):
        super().__init__(state=state, data=data)
        self._update(data)
    
    def _update(self, data: dict):
        self.guild_id = int(data.get("guild_id", 0))
        self.channel_overwrites = data.get("channel_overwrites", {})
        self.muted = data.get("muted", False)
        self.mute_config = data.get("mute_config", {})
        self.mobile_push = data.get("mobile_push", False)
        self.message_notifications = data.get("message_notifications", 0)
        self.flags = data.get("flags", 0)
        self.notify_highlights = data.get("notify_highlights", {})
    
    def __repr__(self) -> str:
        return f"<GuildSettings guild_id={self.guild_id}>"

class UserSettings:
    """Container for user settings (not a model)."""
    
    def __init__(self, data: dict):
        self.theme = data.get("theme", "dark")
        self.locale = data.get("locale", "en-US")
        self.status = data.get("status", "online")
        self.developer_mode = data.get("developer_mode", False)
        self.afk_timeout = data.get("afk_timeout", 600)
        self.animate_emoji = data.get("animate_emoji", True)
        self.animate_stickers = data.get("animate_stickers", True)
        self.enable_tts = data.get("enable_tts", False)
        self.guilds = data.get("guild_settings", {})
    
    def __repr__(self) -> str:
        return f"<UserSettings theme={self.theme} status={self.status}>"