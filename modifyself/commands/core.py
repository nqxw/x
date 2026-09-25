"""
Command definition and registration.
"""

import asyncio
import inspect
import logging
from typing import Callable, Any

from ..errors import CommandError, CheckFailure, MissingRequiredArgument

logger = logging.getLogger(__name__)


class Command:
    """
    Represents a bot command.

    Commands are created via the @command decorator and registered
    with a Client or Cog.
    """

    __slots__ = (
        "callback",
        "name",
        "aliases",
        "signature",
        "checks",
        "cooldown",
        "description",
        "hidden",
        "parent",
        "_cog",
    )

    def __init__(
        self,
        callback: Callable,
        *,
        name: str | None = None,
        aliases: list[str] | None = None,
        checks: list[Callable] | None = None,
        description: str | None = None,
        hidden: bool = False,
        parent: "Command | None" = None,
    ):
        self.callback = callback
        self.name = name or callback.__name__
        self.aliases = aliases or []
        self.signature = inspect.signature(callback)
        self.checks = checks or []
        self.cooldown = None
        self.description = description or callback.__doc__ or ""
        self.hidden = hidden
        self.parent = parent
        self._cog = None

    def __repr__(self) -> str:
        return f"<Command name={self.name}>"

    @property
    def qualified_name(self) -> str:
        if self.parent:
            return f"{self.parent.qualified_name} {self.name}"
        return self.name

    def add_check(self, func: Callable):
        """Add a check to this command."""
        self.checks.append(func)

    def remove_check(self, func: Callable):
        """Remove a check from this command."""
        if func in self.checks:
            self.checks.remove(func)

    async def _convert(self, ctx, param, raw):
        """Convert a raw string argument to the parameter's type."""
        converter = param.annotation if param.annotation != param.empty else str
        if converter == str:
            return raw
        if converter == int:
            return int(raw)
        if converter == float:
            return float(raw)
        if hasattr(converter, "convert"):
            return await converter.convert(ctx, raw)
        if asyncio.iscoroutinefunction(converter):
            return await converter(ctx, raw)
        return converter(raw)

    async def invoke(self, ctx):
        """Invoke the command with the given context."""
        # Run checks
        for check in self.checks:
            try:
                result = check(ctx)
                if asyncio.iscoroutinefunction(check):
                    result = await result
                if not result:
                    raise CheckFailure(
                        f"Check {check.__name__} failed for command {self.name}"
                    )
            except CheckFailure:
                raise
            except Exception as exc:
                raise CheckFailure(f"Check {check.__name__} raised {exc}") from exc

        # Build argument list
        args = []
        kwargs = {}
        params = list(self.signature.parameters.items())

        # Prepend cog instance if bound
        if self._cog is not None:
            args.append(self._cog)
            if params and params[0][0] == "self":
                params = params[1:]

        # Skip ctx/context parameter
        if params and params[0][0] in ("ctx", "context"):
            params = params[1:]

        args.append(ctx)
        arg_index = 0
        raw_args = ctx.args

        for param_name, param in params:
            if param.kind == param.VAR_POSITIONAL:
                args.extend(raw_args[arg_index:])
                break

            if param.kind == param.VAR_KEYWORD:
                kwargs.update(ctx.kwargs)
                continue

            if param.kind == param.KEYWORD_ONLY:
                if arg_index < len(raw_args):
                    raw = " ".join(raw_args[arg_index:])
                    converted = await self._convert(ctx, param, raw)
                    kwargs[param_name] = converted
                elif param.default != param.empty:
                    kwargs[param_name] = param.default
                else:
                    raise MissingRequiredArgument(param)
                break

            if arg_index < len(raw_args):
                raw = raw_args[arg_index]
                converted = await self._convert(ctx, param, raw)
                args.append(converted)
                arg_index += 1
            elif param.default != param.empty:
                args.append(param.default)
            else:
                raise MissingRequiredArgument(param)

        return await self.callback(*args, **kwargs)


def command(
    *,
    name: str | None = None,
    aliases: list[str] | None = None,
    checks: list[Callable] | None = None,
    description: str | None = None,
    hidden: bool = False,
):
    """Decorator to register a command."""
    def decorator(func: Callable):
        cmd = Command(
            func,
            name=name,
            aliases=aliases,
            checks=checks,
            description=description,
            hidden=hidden,
        )
        func._command = cmd
        return func
    return decorator


def group(
    *,
    name: str | None = None,
    aliases: list[str] | None = None,
    checks: list[Callable] | None = None,
    description: str | None = None,
    hidden: bool = False,
    invoke_without_command: bool = False,
):
    """Decorator to register a command group."""
    def decorator(func: Callable):
        cmd = Command(
            func,
            name=name,
            aliases=aliases,
            checks=checks,
            description=description,
            hidden=hidden,
        )
        cmd.invoke_without_command = invoke_without_command
        func._command = cmd
        return func
    return decorator
