"""
Command invocation context.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.message import Message
    from ..models.user import User
    from ..models.member import Member
    from ..models.channel import Channel
    from ..models.guild import Guild
    from .core import Command


class Context:
    """
    The context of a command invocation.

    Bridges the command framework with Discord. Provides shortcuts
    for common actions like sending messages and accessing the
    current guild/channel/author.
    """

    __slots__ = ("message", "command", "args", "kwargs", "bot", "_cs_guild", "_cs_channel")

    def __init__(self, *, message: "Message", command: "Command", args: list, kwargs: dict, bot):
        self.message = message
        self.command = command
        self.args = args
        self.kwargs = kwargs
        self.bot = bot
        self._cs_guild = None
        self._cs_channel = None

    def __repr__(self) -> str:
        return f"<Context command={self.command.name} message={self.message.id}>"

    @property
    def author(self) -> "User | Member":
        return self.message.author

    @property
    def channel(self) -> "Channel | None":
        if self._cs_channel is None:
            # Try to get channel from bot's state using channel_id
            ch = self.bot._state._channels.get(self.message.channel_id)
            if ch is None:
                # Try to fetch it
                import asyncio
                try:
                    # Create a new event loop if needed
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # We're already in an async context, fetch directly
                        data = asyncio.create_task(self.bot._http.fetch_channel(self.message.channel_id))
                        # This won't work directly, we need to handle it differently
                except:
                    pass
            self._cs_channel = ch
        return self._cs_channel

    @property
    def guild(self) -> "Guild | None":
        if self._cs_guild is None:
            self._cs_guild = self.message.guild
        return self._cs_guild

    @property
    def me(self) -> "Member | User | None":
        if self.guild:
            return self.guild.me
        return self.bot.user

    @property
    def prefix(self) -> str:
        return getattr(self.bot, "command_prefix", "!")

    @property
    def invoked_with(self) -> str:
        return self.command.name

    @property
    def clean_content(self) -> str:
        return self.message.clean_content

    async def send(self, content: str | None = None, **kwargs):
        """Send a message to the current channel."""
        ch = self.channel
        if ch is None:
            # Fallback: use message.channel_id directly
            try:
                return await self.bot._http.send_message(self.message.channel_id, content, **kwargs)
            except Exception as e:
                raise RuntimeError(f"Cannot send: {e}")
        return await ch.send(content, **kwargs)

    async def reply(self, content: str | None = None, **kwargs):
        """Reply to the message that invoked this command."""
        return await self.message.reply(content, **kwargs)

    async def trigger_typing(self):
        """Trigger typing in the current channel."""
        ch = self.channel
        if ch:
            await ch.typing()

    async def fetch_message(self, message_id: int):
        """Fetch a message from the current channel."""
        ch = self.channel
        if ch:
            data = await self.bot._state.http.get_messages(
                ch.id, limit=1, before=message_id + 1
            )
            if data:
                from ..models.message import Message
                return Message(state=self.bot._state, data=data[0])
        return None