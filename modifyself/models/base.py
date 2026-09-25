"""
Base class for all Discord entity models.
"""

from typing import TYPE_CHECKING

from ..core.snowflake import Snowflake
from ..core.mixins import Hashable, EqualityById

if TYPE_CHECKING:
    from ..state import ConnectionState


class DiscordObject(Hashable, EqualityById):
    """
    Base class for all Discord objects.

    Provides:
    - __slots__ for memory efficiency
    - __eq__ and __hash__ based on snowflake ID
    - _update() hook for partial data updates
    - created_at from snowflake timestamp
    """

    __slots__ = ("_state", "id")

    def __init__(self, *, state: "ConnectionState", data: dict):
        self._state = state
        self.id = Snowflake(data["id"])

    def _update(self, data: dict):
        """Update attributes from a partial data payload."""
        raise NotImplementedError

    @property
    def created_at(self):
        """The datetime this object was created."""
        return self.id.created_at

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id}>"
