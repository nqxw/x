"""
modifyself ? a clean, pythonic Discord self-bot library.
"""

__version__ = "0.4.1"

from .client import Client
from .commands.core import command
from .commands.cog import Cog
from .commands.context import Context

from .models.relationship import Relationship, RelationshipType
from .models.billing import PaymentSource, Subscription
from .models.settings import GuildSettings, UserSettings
from .models.webhook import Webhook, WebhookMessage

from .activity import Activity, ActivityType, ActivityFlags, spotify_activity, youtube_activity, xbox_activity, playstation_activity, crunchyroll_activity, custom_activity, listening_activity, streaming_activity, competing_activity, LOGO_MAP

from .components import ComponentType, ButtonStyle, TextInputStyle, Button, SelectOption, SelectMenu, ChannelSelect, RoleSelect, MentionableSelect, UserSelect, TextInput, ActionRow, Modal

from .interactions import Interaction, InteractionType, InteractionCallbackType, InteractionHandler, interaction_handler

from .core.enums import (
    ChannelType, MessageType, VerificationLevel, NotificationLevel,
    ExplicitContentFilterLevel, MFALevel, NSFWLevel, PremiumType,
    ActivityType as EnumActivityType, Permissions, GatewayCloseCode,
)

__all__ = [
    "Client", "command", "Cog", "Context",
    "Relationship", "RelationshipType", "PaymentSource", "Subscription",
    "GuildSettings", "UserSettings", "Webhook", "WebhookMessage",
    "Activity", "ActivityType", "ActivityFlags",
    "spotify_activity", "youtube_activity", "xbox_activity",
    "playstation_activity", "crunchyroll_activity", "custom_activity",
    "listening_activity", "streaming_activity", "competing_activity", "LOGO_MAP",
    "ComponentType", "ButtonStyle", "TextInputStyle", "Button", "SelectOption",
    "SelectMenu", "ChannelSelect", "RoleSelect", "MentionableSelect",
    "UserSelect", "TextInput", "ActionRow", "Modal",
    "Interaction", "InteractionType", "InteractionCallbackType",
    "InteractionHandler", "interaction_handler",
    "ChannelType", "MessageType", "VerificationLevel", "NotificationLevel",
    "ExplicitContentFilterLevel", "MFALevel", "NSFWLevel", "PremiumType",
    "Permissions", "GatewayCloseCode",
]
