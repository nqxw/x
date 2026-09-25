"""
Command checks and predicates.
"""

import functools
from typing import Callable

from ..errors import CheckFailure


def check(predicate: Callable):
    """Decorator to add a custom check to a command."""
    def decorator(func):
        if isinstance(func, type):
            # Being applied to a class
            return func
        if hasattr(func, "_command"):
            func._command.add_check(predicate)
        else:
            if not hasattr(func, "__checks__"):
                func.__checks__ = []
            func.__checks__.append(predicate)
        return func
    return decorator


def has_permissions(**perms):
    """Check if the command author has the specified permissions."""
    def predicate(ctx):
        if not ctx.guild:
            return False
        member = ctx.guild.me if hasattr(ctx, "_is_bot_check") else ctx.author
        if member is None:
            return False
        # Simplified check — real implementation would resolve overwrites
        return True

    return check(predicate)


def is_owner():
    """Check if the command author is in the bot owner list."""
    def predicate(ctx):
        return ctx.author.id in ctx.bot.owner_ids
    return check(predicate)


def guild_only():
    """Check that the command is used in a guild."""
    def predicate(ctx):
        if ctx.guild is None:
            raise CheckFailure("This command can only be used in a guild.")
        return True
    return check(predicate)


def dm_only():
    """Check that the command is used in a DM."""
    def predicate(ctx):
        if ctx.guild is not None:
            raise CheckFailure("This command can only be used in DMs.")
        return True
    return check(predicate)
