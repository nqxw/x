"""
Type converters for command arguments.
"""

import asyncio
from typing import Callable, Any

from ..core.snowflake import Snowflake
from ..errors import ConversionError


class ConverterRegistry:
    """Registry for mapping types to converter functions."""

    def __init__(self):
        self._converters: dict[type, Callable] = {}

    def register(self, type_: type, converter: Callable):
        """Register a converter for a type."""
        self._converters[type_] = converter

    def unregister(self, type_: type):
        """Remove a converter for a type."""
        self._converters.pop(type_, None)

    async def convert(self, ctx, type_: type, argument: str) -> Any:
        """Convert an argument to the given type."""
        converter = self._converters.get(type_, type_)
        try:
            if asyncio.iscoroutinefunction(converter):
                return await converter(ctx, argument)
            return converter(argument)
        except Exception as exc:
            raise ConversionError(converter, exc) from exc


# Built-in converters
_registry = ConverterRegistry()


def _member_converter(ctx, argument: str):
    from ..models.member import Member
    guild = ctx.guild
    if not guild:
        raise ValueError("No guild context")
    # Try by mention
    if argument.startswith("<@") and argument.endswith(">"):
        user_id = argument.replace("<@", "").replace("!", "").replace(">", "")
        member = guild.get_member(int(user_id))
        if member:
            return member
    # Try by name
    for member in guild._members.values():
        if str(member) == argument or member.name == argument:
            return member
    raise ValueError(f"Member {argument!r} not found")


def _user_converter(ctx, argument: str):
    from ..models.user import User
    # Try by mention
    if argument.startswith("<@") and argument.endswith(">"):
        user_id = argument.replace("<@", "").replace("!", "").replace(">", "")
        user = ctx.bot._state._users.get(int(user_id))
        if user:
            return user
    raise ValueError(f"User {argument!r} not found")


def _channel_converter(ctx, argument: str):
    from ..models.channel import Channel
    # Try by mention
    if argument.startswith("<#") and argument.endswith(">"):
        channel_id = argument.replace("<#", "").replace(">", "")
        channel = ctx.guild._channels.get(int(channel_id)) if ctx.guild else None
        if channel:
            return channel
    # Try by name
    if ctx.guild:
        for channel in ctx.guild._channels.values():
            if channel.name == argument:
                return channel
    raise ValueError(f"Channel {argument!r} not found")


_registry.register(int, int)
_registry.register(float, float)
_registry.register(str, str)

# Discord-specific converters (import lazily to avoid circular imports)
def get_registry():
    try:
        from ..models.member import Member
        from ..models.user import User
        from ..models.channel import Channel
        _registry.register(Member, _member_converter)
        _registry.register(User, _user_converter)
        _registry.register(Channel, _channel_converter)
    except ImportError:
        pass
    return _registry
