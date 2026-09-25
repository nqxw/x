"""
Gateway WebSocket connection manager with automatic reconnection,
resume support, proper state management, and health checks.
NO INTENTS - user accounts don't use intents!
"""

import asyncio
import json
import logging
import zlib
import time
import random
import math
from typing import Optional, Dict, Any, Callable, Set, List, TYPE_CHECKING
from enum import Enum

import websockets
from websockets import WebSocketClientProtocol

from ..errors import GatewayException, ConnectionClosed
from .heartbeat import Heartbeat

if TYPE_CHECKING:
    from .dispatcher import EventDispatcher
    from ..headers import HeaderSpoofer

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RESUMING = "resuming"
    CLOSED = "closed"
    RECONNECTING = "reconnecting"


class GatewayWebSocket:
    
    GATEWAY = "wss://gateway.discord.gg/?encoding=json&v=9&compress=zlib-stream"
    ZLIB_SUFFIX = b"\x00\x00\xff\xff"
    
    RESUME_CODES = {4000, 4001, 4002, 4003, 4005, 4006, 4007, 4008, 4009}
    FATAL_CODES = {4004, 4010, 4011, 4012, 4013, 4014, 4015, 4016}

    __slots__ = (
        "_dispatcher",
        "_token",
        "_headers",
        "_ws",
        "_session_id",
        "_sequence",
        "_heartbeat",
        "_buffer",
        "_inflator",
        "_state",
        "_resume_gateway_url",
        "_reconnect_task",
        "_health_task",
        "_closed_event",
        "_connected_event",
        "_reconnect_attempts",
        "_max_reconnect_attempts",
        "_base_delay",
        "_last_error",
        "_subscriptions",
        "_processing_lock",
        "_heartbeat_interval",
        "_identify_sent",
        "_connect_timestamp",
    )

    def __init__(
        self,
        dispatcher: "EventDispatcher",
        token: str,
        headers: "HeaderSpoofer",
        max_reconnect_attempts: int = 10,
        base_delay: float = 1.0,
    ):
        self._dispatcher = dispatcher
        self._token = token
        self._headers = headers
        self._ws: Optional[WebSocketClientProtocol] = None
        self._session_id: Optional[str] = None
        self._sequence: Optional[int] = None
        self._heartbeat: Optional[Heartbeat] = None
        self._buffer = bytearray()
        self._inflator = zlib.decompressobj()
        self._state = ConnectionState.DISCONNECTED
        self._resume_gateway_url: Optional[str] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None
        self._closed_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = max_reconnect_attempts
        self._base_delay = base_delay
        self._last_error: Optional[Exception] = None
        self._subscriptions: Set[int] = set()
        self._processing_lock = asyncio.Lock()
        self._heartbeat_interval: Optional[float] = None
        self._identify_sent = False
        self._connect_timestamp: int = 0

    @property
    def latency(self) -> float:
        if self._heartbeat:
            return getattr(self._heartbeat, "_latency", 0.0)
        return 0.0

    @property
    def is_connected(self) -> bool:
        return self._state in (ConnectionState.CONNECTED, ConnectionState.RESUMING)

    @property
    def is_closed(self) -> bool:
        return self._state == ConnectionState.CLOSED

    async def connect(self) -> None:
        if self._closed_event.is_set():
            self._closed_event.clear()
        
        self._state = ConnectionState.CONNECTING
        self._reconnect_attempts = 0
        self._last_error = None
        
        try:
            await self._connect_with_retry()
        except Exception as e:
            self._state = ConnectionState.CLOSED
            self._last_error = e
            raise GatewayException(f"Failed to connect after retries: {e}") from e

    async def _connect_with_retry(self) -> None:
        while self._reconnect_attempts < self._max_reconnect_attempts:
            try:
                await self._do_connect()
                self._reconnect_attempts = 0
                return
            except Exception as e:
                self._last_error = e
                self._reconnect_attempts += 1
                
                if isinstance(e, ConnectionClosed) and e.code in self.FATAL_CODES:
                    logger.critical(f"Fatal close code {e.code}, not retrying")
                    raise
                
                delay = self._base_delay * (2 ** self._reconnect_attempts)
                jitter = random.uniform(0, delay * 0.1)
                wait = delay + jitter
                
                logger.warning(
                    f"Connection attempt {self._reconnect_attempts} failed: {e}. "
                    f"Retrying in {wait:.2f}s"
                )
                
                await asyncio.sleep(wait)
        
        raise GatewayException(f"Max reconnect attempts ({self._max_reconnect_attempts}) exceeded")

    async def _do_connect(self) -> None:
        url = self._resume_gateway_url or self.GATEWAY
        
        self._buffer = bytearray()
        self._inflator = zlib.decompressobj()
        self._identify_sent = False
        self._connect_timestamp = math.floor(time.time() * 1000)
        
        try:
            self._ws = await websockets.connect(
                url,
                additional_headers=self._headers.get_websocket_headers(),
                max_size=2**26,
            )
            
            self._state = ConnectionState.CONNECTED
            self._connected_event.set()
            logger.info(f"WebSocket connected to {url}")
            
            self._health_task = asyncio.create_task(self._health_check())
            
            await self._message_loop()
            
        except websockets.exceptions.ConnectionClosed as exc:
            self._state = ConnectionState.DISCONNECTED
            raise ConnectionClosed(self._ws, code=exc.code) from exc
        except Exception as exc:
            self._state = ConnectionState.DISCONNECTED
            raise GatewayException(f"Gateway error: {exc}") from exc

    async def _message_loop(self) -> None:
        try:
            async for msg in self._ws:
                await self._handle_message(msg)
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning(f"Connection closed: {exc.code}")
            self._state = ConnectionState.DISCONNECTED
            
            if not self._closed_event.is_set():
                await self._handle_disconnect(exc.code)
            raise

    async def _handle_message(self, msg: str | bytes) -> None:
        if isinstance(msg, bytes):
            self._buffer.extend(msg)
            if len(msg) >= 4 and msg[-4:] == self.ZLIB_SUFFIX:
                try:
                    decompressed = self._inflator.decompress(self._buffer)
                    self._buffer = bytearray()
                    msg = decompressed.decode("utf-8")
                except zlib.error as e:
                    logger.error(f"Failed to decompress message: {e}")
                    self._buffer = bytearray()
                    self._inflator = zlib.decompressobj()
                    return
            else:
                return
        
        try:
            payload = json.loads(msg)
            await self._handle_payload(payload)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}")
        except Exception as e:
            logger.exception(f"Error handling payload: {e}")

    async def _handle_payload(self, payload: dict) -> None:
        op = payload.get("op")
        data = payload.get("d")
        seq = payload.get("s")
        event = payload.get("t")
        
        if seq is not None:
            self._sequence = seq
        
        if op == 10:
            self._heartbeat_interval = data["heartbeat_interval"]
            self._heartbeat = Heartbeat(
                self, self._heartbeat_interval, 
                sequence=lambda: self._sequence
            )
            self._heartbeat.start()
            
            if self._session_id and self._sequence is not None:
                await self._resume()
            else:
                await self._identify()
            
        elif op == 11:
            if self._heartbeat:
                self._heartbeat.ack()
                
        elif op == 9:
            if data is True:
                logger.info("Session invalid but resumable, resuming...")
                await self._resume()
            else:
                logger.warning("Session invalid, re-identifying...")
                self._session_id = None
                self._sequence = None
                await self._identify()
                
        elif op == 7:
            logger.info("Gateway requested reconnect")
            self._state = ConnectionState.RECONNECTING
            self._closed_event.set()
            await self._reconnect()
            
        elif op == 0:
            if event == "READY":
                self._session_id = data["session_id"]
                self._resume_gateway_url = data.get("resume_gateway_url")
                self._identify_sent = True
                logger.info(f"Gateway ready! Session: {self._session_id}")
                asyncio.create_task(self._post_ready(data))
                
            elif event == "RESUMED":
                self._state = ConnectionState.CONNECTED
                self._identify_sent = True
                logger.info("Session resumed successfully")
            
            await self._dispatcher.dispatch(event, data)
            
        elif op == 1:
            await self.send_json({"op": 1, "d": self._sequence})

        elif op == 40:
            # QoS/performance monitoring — acknowledge silently
            logger.debug(f"Gateway QoS update: seq={data.get('seq')} active={data.get('active')}")

        elif op == 41:
            # Client init sync — server echoes our session state back
            logger.debug(f"Gateway init sync: session={data.get('session_id')}")

    async def _identify(self) -> None:
        identify_payload = {
            "op": 2,
            "d": {
                "token": self._token,
                "capabilities": 1734653,
                "properties": {
                    "os": "Windows",
                    "browser": "Chrome",
                    "device": "",
                    "system_locale": self._headers.profile.locale,
                    "has_client_mods": False,
                    "browser_user_agent": self._headers.profile.user_agent,
                    "browser_version": self._headers.profile.browser_version,
                    "os_version": "10",
                    "referrer": "",
                    "referring_domain": "",
                    "referrer_current": "",
                    "referring_domain_current": "",
                    "release_channel": "stable",
                    "client_build_number": self._headers.build_number,
                    "client_event_source": None,
                    "client_launch_id": self._headers._launch_id,
                    "launch_signature": self._headers._launch_signature,
                    "client_app_state": self._headers._app_state,
                    "is_fast_connect": False,
                    "gateway_connect_reasons": "AppSkeleton",
                    "installation_id": self._headers._installation_id,
                },
                "presence": {
                    "status": "unknown",
                    "since": 0,
                    "activities": [],
                    "afk": False,
                },
                "compress": False,
                "client_state": {
                    "guild_versions": {},
                },
                "qos_token": "CgA=",
            },
        }

        await self.send_json(identify_payload)
        logger.info("Identify sent (user account - no intents)")

    async def _resume(self) -> None:
        if not self._session_id or self._sequence is None:
            logger.warning("Cannot resume: no session_id or sequence")
            await self._identify()
            return
            
        resume_payload = {
            "op": 6,
            "d": {
                "token": self._token,
                "session_id": self._session_id,
                "seq": self._sequence,
            },
        }
        
        self._state = ConnectionState.RESUMING
        await self.send_json(resume_payload)
        logger.info(f"Resume sent (session: {self._session_id}, seq: {self._sequence})")

    async def _reconnect(self) -> None:
        await self.close()
        self._closed_event.clear()
        self._connected_event.clear()
        self._state = ConnectionState.RECONNECTING
        await self._connect_with_retry()

    async def _handle_disconnect(self, code: int) -> None:
        if code in self.FATAL_CODES:
            logger.critical(f"Fatal disconnect code {code}, closing")
            self._state = ConnectionState.CLOSED
            self._closed_event.set()
            return
            
        if code in self.RESUME_CODES:
            logger.info(f"Resumable disconnect code {code}, attempting resume")
            await self._reconnect()
            return
            
        logger.warning(f"Unknown close code {code}, reconnecting...")
        await self._reconnect()

    async def _health_check(self) -> None:
        while self.is_connected:
            await asyncio.sleep(30)
            
            if not self.is_connected:
                break
                
            if self._ws:
                try:
                    await self._ws.ping()
                except Exception:
                    logger.warning("Health check ping failed, reconnecting...")
                    asyncio.create_task(self._reconnect())
                    break

    async def _post_ready(self, ready_data: dict) -> None:
        await self._subscribe_to_guilds(ready_data.get("guilds", []))

    async def _subscribe_to_guilds(self, guilds: list) -> None:
        if not guilds:
            return
            
        chunk_size = 50
        total_guilds = len(guilds)
        guild_ids = [str(g["id"]) for g in guilds]
        
        logger.info(f"Subscribing to {total_guilds} guilds in chunks of {chunk_size}")
        
        for i in range(0, total_guilds, chunk_size):
            chunk = guild_ids[i:i + chunk_size]

            subscriptions = {
                guild_id: {
                    "typing": True,
                    "activities": True,
                    "threads": True,
                }
                for guild_id in chunk
            }

            await self.send_json({
                "op": 37,
                "d": {
                    "subscriptions": subscriptions,
                },
            })

            self._subscriptions.update(int(gid) for gid in chunk)
            await asyncio.sleep(0.5)

        logger.info(f"Subscribed to {len(self._subscriptions)} guilds")

    async def send_voice_state(
        self,
        guild_id: Optional[int],
        channel_id: Optional[int],
        self_mute: bool = False,
        self_deaf: bool = False,
        self_video: bool = False,
        flags: int = 0,
    ) -> None:
        await self.send_json({
            "op": 4,
            "d": {
                "guild_id": str(guild_id) if guild_id is not None else None,
                "channel_id": str(channel_id) if channel_id is not None else None,
                "self_mute": self_mute,
                "self_deaf": self_deaf,
                "self_video": self_video,
                "flags": flags,
            },
        })

    async def request_call_connect(self, channel_id: int) -> None:
        """Request pre-existing call data for a private channel (op 13)."""
        await self.send_json({"op": 13, "d": {"channel_id": str(channel_id)}})

    async def send_json(self, data: dict) -> None:
        if not self._ws:
            logger.warning("Cannot send: WebSocket is None")
            return
            
        try:
            await self._ws.send(json.dumps(data))
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Cannot send: WebSocket is closed")
        except Exception as e:
            logger.error(f"Failed to send JSON: {e}")
            raise

    async def close(self) -> None:
        if self._state == ConnectionState.CLOSED:
            return
            
        self._state = ConnectionState.CLOSED
        self._closed_event.set()
        
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            
        if self._heartbeat:
            self._heartbeat.stop()
            self._heartbeat = None
            
        if self._ws:
            try:
                await self._ws.close(1000, "Client closed connection")
            except Exception:
                pass
            self._ws = None
            
        self._connected_event.clear()
        logger.info("WebSocket closed")

    async def wait_until_connected(self, timeout: float = 30) -> None:
        await asyncio.wait_for(self._connected_event.wait(), timeout)

    def get_state(self) -> dict:
        return {
            "state": self._state.value,
            "session_id": self._session_id,
            "sequence": self._sequence,
            "reconnect_attempts": self._reconnect_attempts,
            "is_connected": self.is_connected,
            "is_closed": self.is_closed,
            "latency": self.latency,
            "subscriptions": len(self._subscriptions),
            "heartbeat_interval": self._heartbeat_interval,
            "identify_sent": self._identify_sent,
        }