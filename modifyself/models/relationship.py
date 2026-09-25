"""
Relationship models (friends, blocks, etc.)
"""

from enum import IntEnum
from typing import Optional, TYPE_CHECKING

from .base import DiscordObject
from .user import User
from ..utils import parse_time

if TYPE_CHECKING:
    from ..state import ConnectionState


class RelationshipType(IntEnum):
    NONE = 0
    FRIEND = 1
    BLOCKED = 2
    PENDING_INCOMING = 3
    PENDING_OUTGOING = 4
    IMPLICIT = 5
    SUGGESTION = 6


class Relationship(DiscordObject):
    """Represents a relationship (friend, block, pending request, etc.)."""

    __slots__ = (
        "type",
        "nickname",
        "since",
        "user",
    )

    def __init__(self, *, state: "ConnectionState", data: dict):
        super().__init__(state=state, data=data)
        self._update(data)

    def _update(self, data: dict):
        try:
            self.type = RelationshipType(data.get("type", 0))
        except ValueError:
            self.type = data.get("type", 0)
        self.nickname = data.get("nickname")
        self.since = parse_time(data["since"]) if data.get("since") else None
        user_data = data.get("user")
        if user_data:
            self.user = self._state._add_user(user_data)
        else:
            self.user = self._state._users.get(self.id)

    @property
    def is_friend(self) -> bool:
        return self.type == RelationshipType.FRIEND

    @property
    def is_blocked(self) -> bool:
        return self.type == RelationshipType.BLOCKED

    @property
    def is_incoming_request(self) -> bool:
        return self.type == RelationshipType.PENDING_INCOMING

    @property
    def is_outgoing_request(self) -> bool:
        return self.type == RelationshipType.PENDING_OUTGOING

    @property
    def is_implicit(self) -> bool:
        return self.type == RelationshipType.IMPLICIT

    @property
    def is_suggestion(self) -> bool:
        return self.type == RelationshipType.SUGGESTION

    def __repr__(self) -> str:
        return f"<Relationship id={self.id} type={self.type.name if isinstance(self.type, RelationshipType) else self.type}>"
