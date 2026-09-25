"""
Discord snowflake ID implementation.
"""

from datetime import datetime, timezone


class Snowflake(int):
    """
    A 64-bit Discord snowflake ID.

    Subclasses int for free JSON serialization, arithmetic,
    and comparison. Adds creation-time extraction.
    """

    EPOCH = 1420070400000  # Discord epoch: Jan 1, 2015 UTC

    def __new__(cls, value):
        if isinstance(value, Snowflake):
            return value
        return super().__new__(cls, int(value))

    @property
    def created_at(self) -> datetime:
        """The datetime this snowflake was created."""
        return datetime.fromtimestamp(
            ((self >> 22) + self.EPOCH) / 1000,
            tz=timezone.utc,
        )

    @property
    def worker_id(self) -> int:
        """The internal worker ID that generated this snowflake."""
        return (self >> 17) & 0x1F

    @property
    def process_id(self) -> int:
        """The internal process ID that generated this snowflake."""
        return (self >> 12) & 0x1F

    @property
    def increment(self) -> int:
        """The increment counter for this snowflake."""
        return self & 0xFFF

    def __str__(self) -> str:
        return str(int(self))

    def __format__(self, spec: str) -> str:
        return format(int(self), spec)
    
    def __repr__(self) -> str:
        return f"Snowflake({int(self)})"

    def __hash__(self) -> int:
        # Hash by timestamp bucket for better dict performance
        return int(self) >> 22

    def __eq__(self, other) -> bool:
        if isinstance(other, Snowflake):
            return int(self) == int(other)
        if isinstance(other, int):
            return int(self) == other
        return NotImplemented
