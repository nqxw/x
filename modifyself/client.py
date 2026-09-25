"""
Main client class for modifyself.
"""

import asyncio
import inspect
import logging
import signal
import sys
from typing import Callable, Any, Optional, List, Dict, Union

from .http.client import HTTPClient
from .http.route import Route
from .gateway.websocket import GatewayWebSocket
from .gateway.dispatcher import EventDispatcher
from .state import ConnectionState
from .commands.core import Command, command
from .commands.context import Context
from .commands.cog import Cog
from .errors import CommandError, ConversionError, CommandNotFound
from .models.message import Message
from .models.relationship import Relationship
from .models.billing import PaymentSource, Subscription
from .models.settings import GuildSettings, UserSettings
from .models.webhook import Webhook, WebhookMessage
from .headers import HeaderSpoofer, EMULATION
from .utils import send_notification, START_IMAGE, ERROR_IMAGE
from .interactions import Interaction, InteractionHandler, interaction_handler

logger = logging.getLogger(__name__)


class Client:
    """
    The main client for interacting with Discord.

    Usage:
        bot = Client(token="your_token")

        @bot.event
        async def on_ready():
            print(f"Logged in as {bot.user}")

        @bot.command()
        async def ping(ctx):
            await ctx.reply("Pong!")

        bot.run()
    """

    def __init__(
        self,
        *,
        token: str,
        command_prefix: Union[str, Callable[["Client", Message], str]] = "!",
        owner_ids: Optional[List[int]] = None,
        proxy: Optional[str] = None,
        notifications: bool = True,
    ):
        self.token = token
        self.command_prefix = command_prefix
        self.owner_ids = set(owner_ids) if owner_ids else set()
        self._notifications = notifications

        self._headers = HeaderSpoofer(token, EMULATION)
        self._http = HTTPClient(
            token,
            headers=self._headers,
            proxy=proxy,
        )
        self._state = ConnectionState(self._http)
        self._dispatcher = EventDispatcher(self._state)
        self._gateway = GatewayWebSocket(
            self._dispatcher,
            token,
            headers=self._headers,
        )

        self._event_handlers: Dict[str, List[Callable]] = {}
        self._once_handlers: Dict[str, List[Callable]] = {}

        self._commands: Dict[str, Command] = {}
        self._cogs: Dict[str, Cog] = {}

        self._ready = asyncio.Event()
        self._closed = False
        self._task: Optional[asyncio.Task] = None
        self._close_task: Optional[asyncio.Task] = None
        self._error_handled = False

        self._interaction_handlers: Dict[str, Callable] = {}

        self._dispatcher.on("READY", self._on_ready_internal)
        self._dispatcher.on("MESSAGE_CREATE", self._on_message_create_internal)
        self._dispatcher.on("INTERACTION_CREATE", self._on_interaction_create)

        if self._notifications:
            send_notification(
                title="🚀 modifyself Initialized",
                message="Client created successfully. Starting connection...",
                image_url=START_IMAGE,
                timeout=3
            )

    @property
    def user(self):
        return self._state.user

    @property
    def guilds(self):
        return list(self._state._guilds.values())

    @property
    def users(self):
        return list(self._state._users.values())

    @property
    def latency(self) -> float:
        return self._gateway.latency

    @property
    def listeners(self) -> Dict[str, List]:
        return dict(self._dispatcher._handlers)

    @property
    def commands(self) -> List[Command]:
        return list(self._commands.values())

    @property
    def cogs(self) -> List[Cog]:
        return list(self._cogs.values())

    def event(self, coro: Callable):
        if not asyncio.iscoroutinefunction(coro):
            raise TypeError("Event handlers must be coroutines")
        name = coro.__name__.replace("on_", "", 1).upper()
        self._dispatcher.on(name, coro)
        self._event_handlers.setdefault(name, []).append(coro)
        logger.debug("Registered event handler for %s", name)
        return coro

    def listen(self, name: Optional[str] = None):
        def decorator(coro: Callable):
            if not asyncio.iscoroutinefunction(coro):
                raise TypeError("Listeners must be coroutines")
            event_name = (name or coro.__name__).upper()
            self._dispatcher.on(event_name, coro)
            return coro
        return decorator

    def add_listener(self, event: str, handler: Callable):
        event = event.upper()
        self._dispatcher.on(event, handler)
        logger.info("[client] listener registered: %s -> %s", event, handler)

    def remove_listener(self, event: str, handler: Callable):
        self._dispatcher.off(event.upper(), handler)

    def command(self, *, name: Optional[str] = None, aliases: Optional[List[str]] = None, **kwargs):
        def decorator(func: Callable):
            cmd = Command(func, name=name, aliases=aliases, **kwargs)
            self.add_command(cmd)
            func._command = cmd
            return func
        return decorator

    def add_command(self, cmd: Command):
        if cmd.name in self._commands:
            raise ValueError(f"Command {cmd.name} is already registered")
        
        for alias in cmd.aliases:
            if alias in self._commands:
                raise ValueError(f"Alias {alias} is already registered")
        
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd
        logger.debug("Registered command: %s", cmd.name)

    def remove_command(self, name: str):
        cmd = self._commands.pop(name, None)
        if cmd and cmd.name == name:
            for alias in cmd.aliases:
                self._commands.pop(alias, None)

    def get_command(self, name: str) -> Optional[Command]:
        return self._commands.get(name)

    def add_cog(self, cog: Cog):
        if cog.name in self._cogs:
            raise ValueError(f"Cog {cog.name} is already loaded")
        cog._inject(self)
        self._cogs[cog.name] = cog
        cog.cog_load()
        logger.info("Loaded cog: %s", cog.name)

    def remove_cog(self, name: str):
        cog = self._cogs.pop(name, None)
        if cog:
            cog.cog_unload()
            cog._eject(self)
            logger.info("Unloaded cog: %s", name)

    def get_cog(self, name: str) -> Optional[Cog]:
        return self._cogs.get(name)

    def interaction_handler(self, custom_id: str):
        def decorator(func: Callable):
            self._interaction_handlers[custom_id] = func
            return func
        return decorator

    async def _on_ready_internal(self, user):
        self._ready.set()
        logger.debug("Ready event received internally")
        
        if self._notifications:
            username = user.name if user else "Unknown"
            send_notification(
                title="✅ Bot is Running",
                message=f"Logged in as {username}",
                image_url=START_IMAGE,
                timeout=5
            )

    async def _on_message_create_internal(self, message: Message):
        logger.debug(f"📩 MESSAGE_CREATE: {message.content[:50] if message.content else '(empty)'}")
        await self._process_commands(message)

    async def _on_interaction_create(self, data: dict):
        try:
            interaction = Interaction(self._state, data)
            
            if interaction.custom_id and interaction.custom_id in self._interaction_handlers:
                await self._interaction_handlers[interaction.custom_id](interaction)
            else:
                await interaction_handler.handle(interaction)
        except Exception as e:
            logger.exception(f"Error handling interaction: {e}")

    async def _process_commands(self, message: Message):
        if not self.user or message.author.id != self.user.id:
            return
        
        if message.author.bot:
            logger.debug("Message from bot, ignoring")
            return

        if callable(self.command_prefix):
            prefix = self.command_prefix(self, message)
        else:
            prefix = self.command_prefix

        if not prefix:
            return

        content = message.content
        if not content:
            return

        logger.debug(f"Checking command: content='{content}', prefix='{prefix}'")

        if not content.startswith(prefix):
            if self.user:
                mention = f"<@{self.user.id}>"
                mention_nick = f"<@!{self.user.id}>"
                if content.startswith(mention):
                    content = content[len(mention):].lstrip()
                    logger.debug(f"Stripped mention: '{content}'")
                elif content.startswith(mention_nick):
                    content = content[len(mention_nick):].lstrip()
                    logger.debug(f"Stripped mention_nick: '{content}'")
                else:
                    return
            else:
                return
        else:
            content = content[len(prefix):].strip()
            logger.debug(f"Stripped prefix: '{content}'")

        if not content:
            return

        parts = content.split()
        name = parts[0]
        args = parts[1:]

        logger.info(f"Command detected: {name} with args: {args}")

        command = self._commands.get(name)
        if not command:
            logger.debug(f"Command not found: {name}")
            for handler in self._event_handlers.get("COMMAND_NOT_FOUND", []):
                try:
                    await handler(message, name)
                except Exception:
                    logger.exception("Error in command not found handler")
            return

        ctx = Context(
            message=message,
            command=command,
            args=args,
            kwargs={},
            bot=self,
        )

        try:
            await command.invoke(ctx)
            logger.info(f"Command {name} executed successfully")
        except CommandError as exc:
            logger.warning("Command error in %s: %s", command.name, exc)
            self._handle_error(f"Command error in {command.name}: {exc}")
            for handler in self._event_handlers.get("COMMAND_ERROR", []):
                try:
                    await handler(ctx, exc)
                except Exception:
                    logger.exception("Error in command error handler")
        except Exception as exc:
            logger.exception("Unexpected error in command %s", command.name)
            self._handle_error(f"Unexpected error in {command.name}: {exc}")
            for handler in self._event_handlers.get("COMMAND_ERROR", []):
                try:
                    await handler(ctx, exc)
                except Exception:
                    pass

    def _handle_error(self, error_message: str):
        if self._notifications and not self._error_handled:
            self._error_handled = True
            send_notification(
                title="❌ Error Occurred",
                message=error_message[:100] + ("..." if len(error_message) > 100 else ""),
                image_url=ERROR_IMAGE,
                timeout=10
            )
            asyncio.get_event_loop().call_later(2, lambda: setattr(self, '_error_handled', False))

    async def start(self):
        self._closed = False
        self._error_handled = False
        try:
            await self._gateway.connect()
        except Exception as exc:
            logger.exception("Gateway connection error: %s", exc)
            self._handle_error(f"Failed to connect: {exc}")
            raise

    async def close(self):
        if self._closed:
            return
            
        self._closed = True
        self._ready.clear()
        
        await self._gateway.close()
        await self._http.close()
        
        logger.info("Client closed")

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        self._close_task = None

        def signal_handler(sig):
            logger.info("Received signal %s, shutting down...", sig)
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.close(), loop)

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
        except NotImplementedError:
            pass

        async def runner():
            try:
                await self.start()
            except Exception as exc:
                self._handle_error(f"Bot crashed: {exc}")
                raise
            finally:
                await self.close()

        try:
            loop.run_until_complete(runner())
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, shutting down...")
        except Exception as exc:
            self._handle_error(f"Bot crashed: {exc}")
            raise
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    async def wait_until_ready(self):
        await self._ready.wait()

    async def fetch_user(self, user_id: int):
        data = await self._http.request(Route.user(user_id))
        return self._state._add_user(data)

    async def fetch_guild(self, guild_id: int):
        data = await self._http.request(Route.guild(guild_id))
        return self._state._add_guild(data)

    async def fetch_channel(self, channel_id: int):
        data = await self._http.request(Route.channel(channel_id))
        return self._state._add_channel(data)

    async def fetch_message(self, channel_id: int, message_id: int):
        data = await self._http.request(Route.channel_message(channel_id, message_id))
        return self._state._store_message(data)

    def get_guild(self, guild_id: int):
        return self._state._guilds.get(guild_id)

    def get_channel(self, channel_id: int):
        return self._state._channels.get(channel_id)

    def get_user(self, user_id: int):
        return self._state._users.get(user_id)

    async def get_relationships(self) -> List[Relationship]:
        data = await self._http.request(Route("GET", "/users/@me/relationships"))
        return [Relationship(state=self._state, data=r) for r in data]

    async def add_friend(self, user_id: int, username: str = None) -> Dict[str, Any]:
        payload = {"username": username} if username else {}
        return await self._http.request(
            Route("POST", f"/users/@me/relationships/{user_id}"),
            json=payload
        )

    async def remove_friend(self, user_id: int) -> None:
        await self._http.request(
            Route("DELETE", f"/users/@me/relationships/{user_id}")
        )

    async def block_user(self, user_id: int) -> None:
        await self._http.request(
            Route("PUT", f"/users/@me/relationships/{user_id}"),
            json={"type": 2}
        )

    async def unblock_user(self, user_id: int) -> None:
        await self._http.request(
            Route("DELETE", f"/users/@me/relationships/{user_id}")
        )

    async def get_payment_sources(self) -> List[PaymentSource]:
        data = await self._http.request(Route("GET", "/users/@me/billing/payment-sources"))
        return [PaymentSource(state=self._state, data=s) for s in data]

    async def get_subscriptions(self) -> List[Subscription]:
        data = await self._http.request(Route("GET", "/users/@me/billing/subscriptions"))
        return [Subscription(state=self._state, data=s) for s in data]

    async def get_entitlements(self) -> List[Dict[str, Any]]:
        return await self._http.request(Route("GET", "/users/@me/entitlements"))

    async def get_skus(self) -> List[Dict[str, Any]]:
        return await self._http.request(Route("GET", "/users/@me/entitlements/skus"))

    async def get_user_settings(self) -> UserSettings:
        data = await self._http.request(Route("GET", "/users/@me/settings"))
        return UserSettings(data)

    async def update_user_settings(self, **kwargs) -> Dict[str, Any]:
        return await self._http.request(
            Route("PATCH", "/users/@me/settings"),
            json=kwargs
        )

    async def set_status(self, status: str) -> Dict[str, Any]:
        return await self.update_user_settings(status=status)

    async def set_theme(self, theme: str) -> Dict[str, Any]:
        return await self.update_user_settings(theme=theme)

    async def set_language(self, locale: str) -> Dict[str, Any]:
        return await self.update_user_settings(locale=locale)

    async def set_dev_mode(self, enabled: bool) -> Dict[str, Any]:
        return await self.update_user_settings(developer_mode=enabled)

    async def get_guild_settings(self, guild_id: int) -> GuildSettings:
        data = await self._http.request(
            Route("GET", f"/users/@me/guilds/{guild_id}/settings")
        )
        return GuildSettings(state=self._state, data=data)

    async def update_guild_settings(self, guild_id: int, **kwargs) -> Dict[str, Any]:
        return await self._http.request(
            Route("PATCH", f"/users/@me/guilds/{guild_id}/settings"),
            json=kwargs
        )

    async def get_profile(self, user_id: int = None) -> Dict[str, Any]:
        endpoint = f"/users/{user_id}/profile" if user_id else "/users/@me/profile"
        return await self._http.request(Route("GET", endpoint))

    async def update_avatar(self, avatar_data: str) -> Dict[str, Any]:
        return await self._http.request(
            Route("PATCH", "/users/@me"),
            json={"avatar": avatar_data}
        )

    async def update_banner(self, banner_data: str) -> Dict[str, Any]:
        return await self._http.request(
            Route("PATCH", "/users/@me"),
            json={"banner": banner_data}
        )

    async def update_bio(self, bio: str) -> Dict[str, Any]:
        return await self._http.request(
            Route("PATCH", "/users/@me"),
            json={"bio": bio}
        )

    async def update_display_name(self, name: str) -> Dict[str, Any]:
        return await self._http.request(
            Route("PATCH", "/users/@me"),
            json={"global_name": name}
        )

    async def get_connections(self) -> List[Dict[str, Any]]:
        return await self._http.request(Route("GET", "/users/@me/connections"))

    async def get_webhook(self, webhook_id: int) -> Webhook:
        data = await self._http.request(Route("GET", f"/webhooks/{webhook_id}"))
        return Webhook(state=self._state, data=data)

    async def get_webhook_with_token(self, webhook_id: int, token: str) -> Webhook:
        data = await self._http.request(
            method="GET",
            url=f"/webhooks/{webhook_id}/{token}",
        )
        return Webhook(state=self._state, data=data)

    async def get_channel_webhooks(self, channel_id: int) -> List[Webhook]:
        data = await self._http.request(Route("GET", f"/channels/{channel_id}/webhooks"))
        return [Webhook(state=self._state, data=w) for w in data]

    async def get_guild_webhooks(self, guild_id: int) -> List[Webhook]:
        data = await self._http.request(Route("GET", f"/guilds/{guild_id}/webhooks"))
        return [Webhook(state=self._state, data=w) for w in data]

    async def create_webhook(
        self,
        channel_id: int,
        name: str,
        avatar: Optional[str] = None,
    ) -> Webhook:
        payload = {"name": name}
        if avatar:
            payload["avatar"] = avatar
        data = await self._http.request(
            Route("POST", f"/channels/{channel_id}/webhooks"),
            json=payload,
        )
        return Webhook(state=self._state, data=data)