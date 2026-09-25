"""
Cog (extension) system for organizing commands and listeners.
"""

import inspect
from typing import Callable


class CogMeta(type):
    """Metaclass that auto-collects commands and listeners from cog classes."""

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        cls._commands = []
        cls._listeners = []

        for attr_name, value in namespace.items():
            if hasattr(value, "_command"):
                cls._commands.append(value._command)
            if hasattr(value, "_listener"):
                cls._listeners.append((value._listener_event, value, attr_name))

        return cls


class Cog(metaclass=CogMeta):
    """
    Base class for cogs (extensions).

    Group related commands and event listeners in a class.
    Supports hot-loading and hot-unloading.
    """

    def __init__(self, bot):
        self.bot = bot

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def _inject(self, bot):
        """Register this cog's commands and listeners with the bot."""
        import logging
        logger = logging.getLogger(__name__)
        self.__bound_listeners__ = []
        for cmd in self._commands:
            cmd._cog = self
            bot.add_command(cmd)
            logger.debug("[cog] registered command: %s", cmd.name)
        for event, listener, attr_name in self._listeners:
            bound = getattr(self, attr_name)
            bot.add_listener(event, bound)
            self.__bound_listeners__.append((event, bound))
            logger.info("[cog] registered listener: %s -> %s", event, attr_name)
        return self

    def _eject(self, bot):
        """Unregister this cog's commands and listeners from the bot."""
        for cmd in self._commands:
            bot.remove_command(cmd.name)
            for alias in cmd.aliases:
                bot.remove_command(alias)
        for event, bound in getattr(self, "__bound_listeners__", []):
            bot.remove_listener(event, bound)
        return self

    def cog_load(self):
        """Called when the cog is loaded. Override to perform setup."""
        pass

    def cog_unload(self):
        """Called when the cog is unloaded. Override to perform cleanup."""
        pass


def listener(name: str | None = None):
    """Decorator to mark a method as an event listener."""
    def decorator(func: Callable):
        func._listener = True
        func._listener_event = name or func.__name__.replace("on_", "", 1).upper()
        return func
    return decorator
