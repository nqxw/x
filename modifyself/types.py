"""
Type definitions for modifyself.
"""

from typing import Any, Dict, List, Optional, Union, Callable, Coroutine, TypeVar, Generic
from typing_extensions import Self

# ============================================
# Basic Types
# ============================================

Snowflake = int
Timestamp = str
Color = int

# ============================================
# Command Types
# ============================================

CommandCallback = Callable[..., Coroutine[Any, Any, Any]]
CommandName = str
CommandAliases = List[str]
CommandCooldown = int

# ============================================
# Event Types
# ============================================

EventHandler = Callable[..., Coroutine[Any, Any, Any]]
EventName = str

# ============================================
# HTTP Types
# ============================================

HTTPMethod = str
HTTPHeaders = Dict[str, str]
HTTPParams = Dict[str, Any]
HTTPData = Union[Dict[str, Any], str, None]

# ============================================
# Gateway Types
# ============================================

GatewayPayload = Dict[str, Any]
GatewayOPCode = int
GatewaySequence = int

# ============================================
# Model Types
# ============================================

T = TypeVar('T')

class CachedObject(Generic[T]):
    """Base class for cached objects."""
    def __init__(self, data: T, state: Any) -> None:
        self._data = data
        self._state = state
    
    def _update(self, data: T) -> None:
        """Update the object with new data."""
        self._data = data

# ============================================
# Permission Types
# ============================================

PermissionValue = int
PermissionOverwrite = Dict[str, Any]

# ============================================
# Channel Types (using Python 3.12 union syntax)
# ============================================

ChannelID = int
GuildID = int
UserID = int
MessageID = int
RoleID = int
EmojiID = int

# ============================================
# Context Types
# ============================================

class ContextBase:
    """Base context class."""
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

# ============================================
# Option Types (Python 3.12 union syntax)
# ============================================

OptionalStr = str | None
OptionalInt = int | None
OptionalList = list | None
OptionalDict = dict | None

# ============================================
# Function Types
# ============================================

AsyncFunc = Callable[..., Coroutine[Any, Any, Any]]
SyncFunc = Callable[..., Any]
Predicate = Callable[[Any], bool]

# ============================================
# State Types
# ============================================

StateCache = Dict[Snowflake, Any]
StateUpdate = Dict[str, Any]

# ============================================
# Error Types
# ============================================

ErrorCode = int
ErrorMessage = str

# ============================================
# Rate Limit Types
# ============================================

RateLimitBucket = str
RateLimitRemaining = int
RateLimitReset = float

# ============================================
# WebSocket Types
# ============================================

WebSocketURL = str
WebSocketEvent = Dict[str, Any]

# ============================================
# Notification Types (Python 3.12 union syntax)
# ============================================

NotificationTitle = str
NotificationMessage = str
NotificationImage = str | None  # Python 3.12 union syntax
NotificationTimeout = int

# ============================================
# Client Types
# ============================================

ClientToken = str
ClientPrefix = str | list[str] | Callable  # Python 3.12 union syntax
ClientID = int

# ============================================
# JSON Types
# ============================================

JSONValue = str | int | float | bool | None | dict | list
JSONObject = dict[str, JSONValue]
JSONArray = list[JSONValue]