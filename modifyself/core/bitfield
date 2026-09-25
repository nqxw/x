"""
Bitfield and flag implementations for Discord.
"""

from typing import Any


class FlagMeta(type):
    """Metaclass that auto-registers flag constants."""

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        cls._member_map_ = {}
        for key, value in namespace.items():
            if isinstance(value, int) and not key.startswith("_"):
                cls._member_map_[key] = value
        return cls


class Bitfield(metaclass=FlagMeta):
    """
    Base class for Discord bitfields.

    Supports |, &, ~, ^, and membership testing.
    """

    def __init__(self, value: int = 0):
        self.value = int(value)

    def __or__(self, other: "Bitfield") -> "Bitfield":
        return self.__class__(self.value | other.value)

    def __and__(self, other: "Bitfield") -> "Bitfield":
        return self.__class__(self.value & other.value)

    def __xor__(self, other: "Bitfield") -> "Bitfield":
        return self.__class__(self.value ^ other.value)

    def __invert__(self) -> "Bitfield":
        return self.__class__(~self.value)

    def __contains__(self, flag: int) -> bool:
        return (self.value & flag) == flag

    def __int__(self) -> int:
        return self.value

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Bitfield):
            return self.value == other.value
        if isinstance(other, int):
            return self.value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        flags = [
            name for name, val in self._member_map_.items()
            if self.value & val == val
        ]
        return f"{self.__class__.__name__}({' | '.join(flags) or '0'})"

    def __iter__(self):
        for name, value in self._member_map_.items():
            if value in self:
                yield name, value

    @classmethod
    def all(cls) -> "Bitfield":
        """Return a bitfield with all flags set."""
        return cls(sum(cls._member_map_.values()))

    @classmethod
    def none(cls) -> "Bitfield":
        """Return a bitfield with no flags set."""
        return cls(0)


class Permissions(Bitfield):
    CREATE_INSTANT_INVITE = 1 << 0
    KICK_MEMBERS = 1 << 1
    BAN_MEMBERS = 1 << 2
    ADMINISTRATOR = 1 << 3
    MANAGE_CHANNELS = 1 << 4
    MANAGE_GUILD = 1 << 5
    ADD_REACTIONS = 1 << 6
    VIEW_AUDIT_LOG = 1 << 7
    PRIORITY_SPEAKER = 1 << 8
    STREAM = 1 << 9
    VIEW_CHANNEL = 1 << 10
    SEND_MESSAGES = 1 << 11
    SEND_TTS_MESSAGES = 1 << 12
    MANAGE_MESSAGES = 1 << 13
    EMBED_LINKS = 1 << 14
    ATTACH_FILES = 1 << 15
    READ_MESSAGE_HISTORY = 1 << 16
    MENTION_EVERYONE = 1 << 17
    USE_EXTERNAL_EMOJIS = 1 << 18
    VIEW_GUILD_INSIGHTS = 1 << 19
    CONNECT = 1 << 20
    SPEAK = 1 << 21
    MUTE_MEMBERS = 1 << 22
    DEAFEN_MEMBERS = 1 << 23
    MOVE_MEMBERS = 1 << 24
    USE_VAD = 1 << 25
    CHANGE_NICKNAME = 1 << 26
    MANAGE_NICKNAMES = 1 << 27
    MANAGE_ROLES = 1 << 28
    MANAGE_WEBHOOKS = 1 << 29
    MANAGE_GUILD_EXPRESSIONS = 1 << 30
    USE_APPLICATION_COMMANDS = 1 << 31
    REQUEST_TO_SPEAK = 1 << 32
    MANAGE_EVENTS = 1 << 33
    MANAGE_THREADS = 1 << 34
    CREATE_PUBLIC_THREADS = 1 << 35
    CREATE_PRIVATE_THREADS = 1 << 36
    USE_EXTERNAL_STICKERS = 1 << 37
    SEND_MESSAGES_IN_THREADS = 1 << 38
    USE_EMBEDDED_ACTIVITIES = 1 << 39
    MODERATE_MEMBERS = 1 << 40
    VIEW_CREATOR_MONETIZATION_ANALYTICS = 1 << 41
    USE_SOUNDBOARD = 1 << 42
    CREATE_EXPRESSIONS = 1 << 43
    CREATE_EVENTS = 1 << 44
    USE_EXTERNAL_SOUNDS = 1 << 45
    SEND_VOICE_MESSAGES = 1 << 46
    SET_VOICE_CHANNEL_STATUS = 1 << 48
    SEND_POLLS = 1 << 49
    USE_EXTERNAL_APPS = 1 << 50
    PIN_MESSAGES = 1 << 51
    BYPASS_SLOWMODE = 1 << 52
    MANAGE_OFFICIAL_MESSAGES = 1 << 53

    @classmethod
    def all(cls) -> "Permissions":
        bits = sum(v for v in cls._member_map_.values())
        return cls(bits)


class UserFlags(Bitfield):
    STAFF = 1 << 0
    PARTNER = 1 << 1
    HYPESQUAD = 1 << 2
    BUG_HUNTER_LEVEL_1 = 1 << 3
    HYPESQUAD_ONLINE_HOUSE_1 = 1 << 6
    HYPESQUAD_ONLINE_HOUSE_2 = 1 << 7
    HYPESQUAD_ONLINE_HOUSE_3 = 1 << 8
    PREMIUM_EARLY_SUPPORTER = 1 << 9
    TEAM_PSEUDO_USER = 1 << 10
    BUG_HUNTER_LEVEL_2 = 1 << 14
    VERIFIED_BOT = 1 << 16
    VERIFIED_DEVELOPER = 1 << 17
    CERTIFIED_MODERATOR = 1 << 18
    BOT_HTTP_INTERACTIONS = 1 << 19
    SPAMMER = 1 << 20
    ACTIVE_DEVELOPER = 1 << 22
