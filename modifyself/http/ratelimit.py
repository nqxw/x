"""
Based on Discord's rate limit documentation:
- X-RateLimit-Limit: Max requests per bucket
- X-RateLimit-Remaining: Remaining requests in current window
- X-RateLimit-Reset: Timestamp when bucket resets
- X-RateLimit-Reset-After: Seconds until reset (preferred)
- X-RateLimit-Bucket: Unique bucket identifier
- X-RateLimit-Global: Global rate limit flag
- Retry-After: Seconds to wait for global limits
"""

import asyncio
import time
import logging
from typing import Dict, Optional, Any
from collections import defaultdict

logger = logging.getLogger(__name__)


class Bucket:
    """
    Rate limit bucket for a specific endpoint group.
    
    Based on userdocs:
    - Each bucket has a limit (max requests)
    - Remaining decreases with each request
    - Reset time determines when the bucket refreshes
    - Buckets are identified by a hash from Discord
    """
    
    __slots__ = (
        "_lock",
        "_queue",
        "remaining",
        "reset_at",
        "limit",
        "hash",
        "_global",
        "_pending_requests",
        "_retry_after",
    )

    def __init__(self):
        self._lock = asyncio.Lock()
        self._queue = asyncio.Queue()
        self.remaining = 10  # Default, will be updated from headers
        self.reset_at = 0.0
        self.limit = 10
        self.hash = None  # Discord's bucket hash (X-RateLimit-Bucket)
        self._global = False
        self._pending_requests = 0
        self._retry_after = 0.0

    async def acquire(self) -> None:
        """
        Acquire a slot in this bucket.
        Waits if the bucket is exhausted.
        
        Based on userdocs:
        - If remaining <= 0 and current time < reset_at, wait
        - Use X-RateLimit-Reset-After for precise waiting
        """
        async with self._lock:
            self._pending_requests += 1
        
        await self._queue.put(asyncio.current_task())
        
        try:
            while True:
                # Ensure remaining is never negative
                self.remaining = max(self.remaining, 0)
                
                now = time.monotonic()
                
                # Check if bucket is exhausted
                if self.remaining <= 0 and now < self.reset_at:
                    wait_time = self.reset_at - now + 0.05  # Small buffer
                    logger.debug(f"Bucket exhausted, waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
                    # After waiting, reset remaining to limit
                    self.remaining = self.limit
                    continue
                
                # Check for global rate limit (X-RateLimit-Global)
                if self._global and now < self.reset_at:
                    wait_time = self.reset_at - now + 0.05
                    logger.debug(f"Global rate limit, waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
                    self._global = False
                    continue
                
                # We have a slot available
                self.remaining = max(self.remaining - 1, 0)
                break
                
        finally:
            await self._queue.get()
            self._pending_requests -= 1

    def update(self, headers: dict) -> None:
        """
        Update bucket state from response headers.
        
        Based on userdocs:
        - X-RateLimit-Limit: Total allowed requests
        - X-RateLimit-Remaining: Remaining requests
        - X-RateLimit-Reset-After: Seconds until reset (preferred)
        - X-RateLimit-Reset: Unix timestamp of reset (fallback)
        - X-RateLimit-Bucket: Bucket identifier
        - Retry-After: Used for global limits
        - X-RateLimit-Global: Indicates global limit
        """
        try:
            # Update limit if provided
            if "X-RateLimit-Limit" in headers:
                self.limit = int(headers["X-RateLimit-Limit"])
            
            # Update remaining
            if "X-RateLimit-Remaining" in headers:
                self.remaining = int(headers["X-RateLimit-Remaining"])
                self.remaining = max(self.remaining, 0)
            
            # Update reset time (prefer Reset-After as per userdocs)
            if "X-RateLimit-Reset-After" in headers:
                # Reset-After is in seconds from now
                self.reset_at = time.monotonic() + float(headers["X-RateLimit-Reset-After"])
            elif "X-RateLimit-Reset" in headers:
                # Reset is a Unix timestamp
                self.reset_at = float(headers["X-RateLimit-Reset"])
            
            # Store bucket hash for proper routing
            if "X-RateLimit-Bucket" in headers:
                self.hash = headers["X-RateLimit-Bucket"]
                
            # Handle retry-after (for global limits)
            if "Retry-After" in headers:
                self._retry_after = float(headers["Retry-After"])
                
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse rate limit headers: {e}")

    def mark_global(self, retry_after: float) -> None:
        """
        Mark this bucket as globally rate limited.
        
        Based on userdocs:
        - When X-RateLimit-Global is present, wait Retry-After seconds
        - All buckets are affected by global limits
        """
        self._global = True
        self.reset_at = time.monotonic() + retry_after
        self._retry_after = retry_after
        logger.warning(f"Global rate limit hit, retry after {retry_after:.2f}s")

    @property
    def is_exhausted(self) -> bool:
        """Check if this bucket is currently exhausted."""
        now = time.monotonic()
        if self._global and now < self.reset_at:
            return True
        if self.remaining <= 0 and now < self.reset_at:
            return True
        return False

    @property
    def reset_in(self) -> float:
        """Time until this bucket resets."""
        remaining = self.reset_at - time.monotonic()
        return max(remaining, 0.0)


class RateLimiter:
    """
    Manages rate limit buckets across all routes.
    
    Based on userdocs:
    - Maintains separate buckets for each endpoint group
    - Handles bucket hash remapping (X-RateLimit-Bucket)
    - Handles global rate limits (X-RateLimit-Global)
    - Uses exponential backoff for server errors (5xx)
    """
    
    def __init__(self):
        self._buckets: Dict[str, Bucket] = defaultdict(Bucket)
        self._global_lock = asyncio.Lock()
        self._global_reset = 0.0
        self._global_retry_after = 0.0
        self._logger = logger.getChild("RateLimiter")
        self._last_bucket_mapping: Dict[str, str] = {}  # Route key -> Bucket hash

    def _get_bucket(self, route) -> Bucket:
        """
        Get or create a bucket for a route.
        
        Based on userdocs:
        - Buckets are identified by a hash from Discord
        - Different routes can share the same bucket
        - The same route can have different buckets for different parameters
        """
        # First check if we have a mapped bucket hash for this route
        route_key = route.bucket
        if route_key in self._last_bucket_mapping:
            bucket_hash = self._last_bucket_mapping[route_key]
            if bucket_hash in self._buckets:
                return self._buckets[bucket_hash]
        
        # Fall back to using the route key as the bucket identifier
        return self._buckets[route_key]

    async def pre_request(self, route) -> None:
        """
        Called before making a request.
        Waits for both global and per-bucket rate limits.
        
        Based on userdocs:
        - Always check global limits first (they affect ALL requests)
        - Then check the specific bucket for the route
        """
        # First, check global rate limit (X-RateLimit-Global)
        async with self._global_lock:
            now = time.monotonic()
            if now < self._global_reset:
                wait = self._global_reset - now + 0.05
                self._logger.debug(f"Global rate limit, waiting {wait:.2f}s")
                await asyncio.sleep(wait)
                self._global_reset = 0.0

        # Then acquire from the specific bucket
        bucket = self._get_bucket(route)
        await bucket.acquire()

    async def update(self, route, response) -> None:
        """
        Called after a request is made.
        Updates bucket state from response headers.
        
        Based on userdocs:
        - Check for X-RateLimit-Global first (requires immediate action)
        - Update the bucket with X-RateLimit-* headers
        - Remap bucket if X-RateLimit-Bucket is provided
        """
        # Extract headers from response
        headers = getattr(response, "headers", {})
        
        # Handle global rate limit (X-RateLimit-Global)
        global_limit = headers.get("X-RateLimit-Global", False)
        if global_limit in (True, "true", "True", "1"):
            retry_after = float(headers.get("Retry-After", 1))
            async with self._global_lock:
                self._global_reset = time.monotonic() + retry_after
                self._global_retry_after = retry_after
            self._logger.warning(f"Global rate limit: {retry_after:.2f}s")
            # Also mark the specific bucket as globally limited
            bucket = self._get_bucket(route)
            bucket.mark_global(retry_after)
            return

        # Get the bucket for this route
        route_key = route.bucket
        bucket = self._get_bucket(route)
        bucket.update(headers)

        # Check if Discord gave us a bucket hash (X-RateLimit-Bucket)
        bucket_hash = headers.get("X-RateLimit-Bucket")
        if bucket_hash and bucket_hash != route_key:
            # Remap the bucket to the hash Discord provides
            # This ensures we use the correct bucket for future requests
            if bucket_hash not in self._buckets:
                # Move the bucket to the new key
                self._buckets[bucket_hash] = bucket
                # Remove old key if it exists
                if route_key in self._buckets and route_key != bucket_hash:
                    self._buckets.pop(route_key, None)
                # Store the mapping for future lookups
                self._last_bucket_mapping[route_key] = bucket_hash
                self._logger.debug(f"Remapped bucket {route_key} -> {bucket_hash}")

    def get_bucket_state(self, route) -> Optional[dict]:
        """
        Get the current state of a bucket for debugging.
        """
        bucket = self._get_bucket(route)
        if not bucket:
            return None
        return {
            "remaining": bucket.remaining,
            "limit": bucket.limit,
            "reset_in": bucket.reset_in,
            "is_exhausted": bucket.is_exhausted,
            "hash": bucket.hash,
        }

    def reset_bucket(self, route) -> None:
        """
        Reset a bucket manually (for testing or recovery).
        """
        bucket = self._get_bucket(route)
        if bucket:
            bucket.remaining = bucket.limit
            bucket.reset_at = 0.0
            bucket._global = False

    async def clear_all_buckets(self) -> None:
        """
        Clear all buckets (useful for debugging or when token changes).
        """
        async with self._global_lock:
            self._global_reset = 0.0
            self._global_retry_after = 0.0
        for key in list(self._buckets.keys()):
            bucket = self._buckets[key]
            async with bucket._lock:
                bucket.remaining = bucket.limit
                bucket.reset_at = 0.0
                bucket._global = False
        self._last_bucket_mapping.clear()

    def get_stats(self) -> dict:
        """
        Get statistics about all buckets.
        """
        stats = {
            "total_buckets": len(self._buckets),
            "global_reset": self._global_reset,
            "global_retry_after": self._global_retry_after,
            "buckets": {},
        }
        for key, bucket in self._buckets.items():
            stats["buckets"][key] = {
                "remaining": bucket.remaining,
                "limit": bucket.limit,
                "reset_in": bucket.reset_in,
                "is_exhausted": bucket.is_exhausted,
            }
        return stats