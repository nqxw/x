"""
Discord voice support.
"""

import asyncio
import json
import logging
import socket
import struct
import threading
import time
import uuid
from typing import Optional, List, Dict, Any, Callable, Union
from dataclasses import dataclass

try:
    import nacl.secret
    import nacl.utils
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

try:
    import opuslib
    HAS_OPUS = True
except ImportError:
    HAS_OPUS = False

from .http.route import Route
from .errors import VoiceError

logger = logging.getLogger(__name__)

class VoiceState:
    """Voice connection state."""
    def __init__(self):
        self.session_id = None
        self.token = None
        self.endpoint = None
        self.guild_id = None
        self.user_id = None
        self.channel_id = None
        self.mute = False
        self.deaf = False
        self.self_mute = False
        self.self_deaf = False
        self.self_stream = False


class VoiceClient:
    """Represents a connection to a Discord voice channel."""
    
    def __init__(self, client, channel_id: int, guild_id: int, endpoint: str, token: str, session_id: str):
        self.client = client
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.endpoint = endpoint
        self.token = token
        self.session_id = session_id
        self._state = VoiceState()
        self._state.channel_id = channel_id
        self._state.guild_id = guild_id
        self._state.session_id = session_id
        self._state.token = token
        self._state.endpoint = endpoint
        
        self._ws = None
        self._connected = False
        self._closed = False
        self._sequence = 0
        self._timestamp = 0
        self._ssrc = None
        self._encryption = None
        self._secret_key = None
        self._voice_ip = None
        self._voice_port = None
        self._sock = None
        
        self._heartbeat_task = None
        self._receive_task = None
        self._audio_task = None
        self._audio_queue = asyncio.Queue()
        self._ready = asyncio.Event()
        
        self._handlers = {
            "on_ready": [],
            "on_speaking": [],
            "on_disconnect": [],
            "on_error": [],
        }
        
        logger.info(f"Voice client created for guild {guild_id}, channel {channel_id}")

    @property
    def is_connected(self) -> bool:
        return self._connected and not self._closed

    async def connect(self) -> bool:
        """Connect to the voice channel."""
        if self._connected:
            return True

        try:
            import websockets
            ws_url = f"wss://{self.endpoint}?v=8"
            self._ws = await websockets.connect(
                ws_url,
                additional_headers={"Origin": "https://discord.com"},
            )

            # Wait for HELLO (op 8) before identifying
            hello = await self._wait_for_op(8, timeout=10)
            if not hello:
                logger.error("Voice: did not receive HELLO")
                return False

            heartbeat_interval = hello["d"]["heartbeat_interval"]
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(heartbeat_interval)
            )

            # Send IDENTIFY (op 0)
            await self._send({
                "op": 0,
                "d": {
                    "server_id": str(self.guild_id),
                    "user_id": str(self.client.user.id),
                    "session_id": self.session_id,
                    "token": self.token,
                },
            })

            # Wait for READY (op 2)
            ready = await self._wait_for_op(2, timeout=10)
            if ready:
                d = ready["d"]
                self._ssrc = d.get("ssrc")
                self._voice_ip = d.get("ip")
                self._voice_port = d.get("port")
                self._connected = True
                self._ready.set()
                logger.info(f"Voice connection ready (ssrc={self._ssrc})")
                await self._on_ready()
                return True

            return False

        except Exception as e:
            logger.error(f"Voice connection failed: {e}")
            await self._on_error(e)
            return False

    async def _heartbeat_loop(self, interval_ms: float) -> None:
        interval = interval_ms / 1000
        while self._connected and not self._closed:
            await self._send({"op": 3, "d": int(asyncio.get_event_loop().time() * 1000)})
            await asyncio.sleep(interval)

    async def disconnect(self) -> None:
        """Disconnect from voice channel."""
        if self._closed:
            return
        
        self._closed = True
        self._connected = False
        self._ready.clear()
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        
        if self._receive_task:
            self._receive_task.cancel()
        
        if self._audio_task:
            self._audio_task.cancel()
        
        if self._sock:
            self._sock.close()
            self._sock = None
        
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        logger.info("Voice connection closed")

    async def send_audio(self, data: bytes) -> None:
        """Send audio data to the voice channel."""
        if not self._connected:
            raise VoiceError("Not connected to voice")
        await self._audio_queue.put(data)

    async def _send(self, data: dict) -> None:
        """Send a WebSocket message."""
        if self._ws:
            await self._ws.send(json.dumps(data))

    async def _wait_for_op(self, op: int, timeout: float = 10) -> Optional[dict]:
        """Wait for a specific voice gateway opcode."""
        start = time.time()
        while time.time() - start < timeout:
            if self._ws:
                try:
                    msg = await asyncio.wait_for(self._ws.recv(), timeout=1)
                    data = json.loads(msg)
                    if data.get("op") == op:
                        return data
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error waiting for voice op {op}: {e}")
                    break
        return None

    def on(self, event: str, callback: Callable):
        """Register an event handler."""
        if event not in self._handlers:
            self._handlers[event] = []
        self._handlers[event].append(callback)

    async def _on_ready(self):
        for callback in self._handlers.get("on_ready", []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(self)
                else:
                    callback(self)
            except Exception as e:
                logger.error(f"Error in on_ready handler: {e}")

    async def _on_error(self, error: Exception):
        for callback in self._handlers.get("on_error", []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(error)
                else:
                    callback(error)
            except Exception as e:
                logger.error(f"Error in on_error handler: {e}")

    def __repr__(self) -> str:
        return f"<VoiceClient guild={self.guild_id} channel={self.channel_id} connected={self._connected}>"


class VoiceManager:
    """Manages voice connections."""
    
    def __init__(self, client):
        self.client = client
        self._connections: Dict[int, VoiceClient] = {}
        self._state: Dict[int, VoiceState] = {}

    async def connect(
        self,
        channel_id: int,
        guild_id: int,
        endpoint: str,
        token: str,
        session_id: str,
    ) -> VoiceClient:
        """Create and connect a voice client."""
        if guild_id in self._connections:
            await self._connections[guild_id].disconnect()
        
        client = VoiceClient(
            self.client,
            channel_id,
            guild_id,
            endpoint,
            token,
            session_id,
        )
        
        await client.connect()
        self._connections[guild_id] = client
        return client

    def get_client(self, guild_id: int) -> Optional[VoiceClient]:
        """Get the voice client for a guild."""
        return self._connections.get(guild_id)

    async def disconnect(self, guild_id: int) -> None:
        """Disconnect a voice client."""
        if guild_id in self._connections:
            await self._connections[guild_id].disconnect()
            del self._connections[guild_id]

    async def disconnect_all(self) -> None:
        """Disconnect all voice clients."""
        for guild_id in list(self._connections.keys()):
            await self.disconnect(guild_id)

    def update_state(self, guild_id: int, state_data: dict) -> None:
        """Update voice state."""
        state = self._state.get(guild_id)
        if not state:
            state = VoiceState()
            self._state[guild_id] = state
        
        state.session_id = state_data.get("session_id")
        state.token = state_data.get("token")
        state.endpoint = state_data.get("endpoint")
        state.channel_id = state_data.get("channel_id")