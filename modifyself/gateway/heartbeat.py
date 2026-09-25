"""
Gateway heartbeat manager aligned with userdocs.
"""

import asyncio
import random
import logging
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class Heartbeat:
    """
    Manages the gateway heartbeat loop.
    """
    
    __slots__ = (
        "_ws",
        "_interval",
        "_ack_event",
        "_task",
        "_sequence",
        "_heartbeat_time",
        "_latency",
        "_stop_event",
        "_pending",
    )

    def __init__(
        self,
        ws,
        interval: float,
        sequence: Callable[[], Optional[int]]
    ):
        self._ws = ws
        self._interval = interval / 1000
        self._ack_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._sequence = sequence
        self._heartbeat_time = 0.0
        self._latency = 0.0
        self._stop_event = asyncio.Event()
        self._pending = False

    @property
    def latency(self) -> float:
        return self._latency

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Heartbeat started with interval {self._interval:.2f}s")

    def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        logger.info("Heartbeat stopped")

    def ack(self) -> None:
        self._ack_event.set()
        self._pending = False
        
        if self._heartbeat_time > 0:
            self._latency = time.monotonic() - self._heartbeat_time
            logger.debug(f"Heartbeat ACK received, latency: {self._latency:.3f}s")

    async def _send_heartbeat(self) -> None:
        seq = self._sequence()
        payload = {"op": 1, "d": seq}
        await self._ws.send_json(payload)
        
        self._heartbeat_time = time.monotonic()
        self._pending = True
        self._ack_event.clear()
        
        logger.debug(f"Heartbeat sent (seq: {seq})")

    async def _loop(self) -> None:
        jitter = random.random() * self._interval
        logger.debug(f"Starting heartbeat with jitter: {jitter:.3f}s")
        
        try:
            await asyncio.sleep(jitter)
        except asyncio.CancelledError:
            return
        
        while not self._stop_event.is_set():
            try:
                await self._send_heartbeat()
                
                # Create tasks for waiting
                ack_task = asyncio.create_task(self._ack_event.wait())
                timeout_task = asyncio.create_task(asyncio.sleep(self._interval))
                stop_task = asyncio.create_task(self._stop_event.wait())
                
                done, pending = await asyncio.wait(
                    [ack_task, timeout_task, stop_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                
                for task in pending:
                    task.cancel()
                
                if self._stop_event.is_set():
                    logger.info("Heartbeat loop stopped by event")
                    break
                
                if ack_task.done():
                    logger.debug(f"Heartbeat ACK received, latency: {self._latency:.3f}s")
                else:
                    raise ConnectionResetError(
                        f"Heartbeat ACK not received within {self._interval:.2f}s"
                    )
                
                elapsed = time.monotonic() - self._heartbeat_time
                remaining = max(0, self._interval - elapsed)
                
                if remaining > 0:
                    try:
                        await asyncio.sleep(remaining)
                    except asyncio.CancelledError:
                        if self._stop_event.is_set():
                            break
                        raise
                
            except asyncio.CancelledError:
                if self._stop_event.is_set():
                    break
                raise
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")
                raise

    async def wait_for_ack(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._ack_event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def is_alive(self) -> bool:
        return not self._pending

    def get_status(self) -> dict:
        return {
            "interval": self._interval,
            "latency": self._latency,
            "pending_ack": self._pending,
            "last_heartbeat": self._heartbeat_time,
            "is_alive": self.is_alive(),
            "running": self._task is not None and not self._task.done(),
        }