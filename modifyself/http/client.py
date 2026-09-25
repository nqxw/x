"""
Discord HTTP client aligned with userdocs.
"""

import asyncio
import json as _json
import logging
import random
from datetime import timedelta
from typing import Optional, Dict, Any, List, Union
from urllib.parse import urlencode

from wreq import Client as WreqClient, Method

from ..headers import HeaderSpoofer, EMULATION
from .route import Route
from .ratelimit import RateLimiter
from ..errors import HTTPException

logger = logging.getLogger(__name__)


def _status(response) -> int:
    return getattr(response, "status", getattr(response, "status_code", 0))


class HTTPClient:
    
    API_VERSION = 9
    BASE_URL = f"https://discord.com/api/v{API_VERSION}"
    
    DEFAULT_TIMEOUT = 30
    LONG_TIMEOUT = 60
    
    MAX_RETRIES = 5
    BASE_BACKOFF = 1.0
    MAX_BACKOFF = 60.0

    def __init__(
        self,
        token: str,
        *,
        headers: "HeaderSpoofer",
        proxy: Optional[str] = None,
        emulation=EMULATION,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ):
        self.token = token
        self.proxy = proxy
        self.emulation = emulation
        self.timeout = timeout
        self.max_retries = max_retries
        
        self._spoofer = headers
        self._session_id: Optional[str] = None
        self._rate_limiter = RateLimiter()
        self._client: Optional[WreqClient] = None
        self._closed = False

    def _get_client(self) -> WreqClient:
        if self._client is None:
            self._client = WreqClient(emulation=self.emulation)
        return self._client

    def _get_timeout(self, route: Route) -> int:
        if route.method in ["POST", "PUT", "PATCH"] and "/attachments" in route.path:
            return self.LONG_TIMEOUT
        if route.method == "POST" and "/messages" in route.path:
            return self.LONG_TIMEOUT
        return self.timeout

    async def _execute_request(
        self,
        route: Route,
        *,
        json: Optional[Dict] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Dict[str, str],
        timeout: int,
        body: Optional[Union[bytes, str]] = None,
    ) -> Any:
        client = self._get_client()
        url = route.url
        if params:
            url += "?" + urlencode(params, doseq=True)
        
        method_enum = getattr(Method, route.method)
        timeout_timedelta = timedelta(seconds=timeout)
        
        try:
            # wreq doesn't support 'data' parameter, use 'json' or 'body'
            # For raw bytes, we need to send as 'json' with the raw data
            if body is not None:
                # For multipart, wreq expects files parameter or we need to use the underlying session
                # Since wreq doesn't support raw data, we'll use a different approach
                # For now, use json with the body as string
                response = await client.request(
                    method=method_enum,
                    url=url,
                    headers=headers,
                    json=body if isinstance(body, dict) else {"_raw": body.decode() if isinstance(body, bytes) else body},
                    timeout=timeout_timedelta,
                )
            else:
                response = await client.request(
                    method=method_enum,
                    url=url,
                    headers=headers,
                    json=json,
                    timeout=timeout_timedelta,
                )
            return response
        except Exception as e:
            raise HTTPException(response=None, message=str(e))

    async def request_raw(
        self,
        method: str,
        url: str,
        *,
        json: Optional[Dict] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Dict[str, str],
        data: Optional[Union[bytes, str]] = None,
    ) -> Any:
        if self._closed:
            raise RuntimeError("HTTP client is closed")
        
        if not url.startswith("https://"):
            url = f"{self.BASE_URL}{url}"
        
        if params:
            url += "?" + urlencode(params, doseq=True)
        
        # wreq doesn't support raw data, so we need to adapt
        # For multipart uploads, we should use the 'json' parameter with the data
        # Since wreq doesn't support raw bytes, we'll use the json parameter
        # and handle the data as a string
        
        # Create a route-like object for rate limiting
        class RawRoute:
            def __init__(self, method, url):
                self.method = method
                self.url = url
                self.bucket = f"{method}/{url.split('?')[0]}"
                self.path = url.split('?')[0]
        
        route = RawRoute(method, url)
        await self._rate_limiter.pre_request(route)
        
        timeout_timedelta = timedelta(seconds=self.DEFAULT_TIMEOUT)
        client = self._get_client()
        method_enum = getattr(Method, method)
        
        try:
            # For multipart form data, we need to send it as json
            # but wreq only accepts json, so we convert the data
            if data is not None:
                # Try to parse as json first
                try:
                    if isinstance(data, bytes):
                        json_data = _json.loads(data.decode('utf-8'))
                    else:
                        json_data = _json.loads(data)
                    response = await client.request(
                        method=method_enum,
                        url=url,
                        headers=headers,
                        json=json_data,
                        timeout=timeout_timedelta,
                    )
                except:
                    # If not json, send as string
                    response = await client.request(
                        method=method_enum,
                        url=url,
                        headers=headers,
                        json={"_raw_data": data.decode('utf-8') if isinstance(data, bytes) else data},
                        timeout=timeout_timedelta,
                    )
            else:
                response = await client.request(
                    method=method_enum,
                    url=url,
                    headers=headers,
                    json=json,
                    timeout=timeout_timedelta,
                )
            
            status = _status(response)
            
            if status == 204:
                return None
            if 200 <= status < 300:
                try:
                    return await response.json()
                except:
                    return response.text
            if 400 <= status < 500:
                try:
                    err = await response.json()
                    msg = err.get("message", str(err))
                except:
                    msg = f"HTTP {status}"
                raise HTTPException(response, msg)
            if 500 <= status < 600:
                raise HTTPException(response, f"Server Error: {status}")
            try:
                return await response.json()
            except:
                return response.text
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(response=None, message=str(e))

    async def _handle_response(self, response, route: Route) -> Any:
        status = _status(response)
        headers = getattr(response, "headers", {})
        
        try:
            await self._rate_limiter.update(route, response)
        except Exception:
            pass
        
        if status == 429:
            retry_after = headers.get("Retry-After", 1)
            try:
                retry_after = float(retry_after)
            except (ValueError, TypeError):
                retry_after = 1
            logger.warning(f"Rate limited, retry after {retry_after}s")
            await asyncio.sleep(retry_after + 0.1)
            raise HTTPException(response, f"Rate limited: {retry_after}s")
        
        if status == 204:
            return None
        
        if 400 <= status < 500:
            try:
                err = await response.json()
                msg = self._format_error_message(err, status)
            except Exception:
                msg = f"HTTP {status} - {response.text}"
            raise HTTPException(response, msg)
        
        if 500 <= status < 600:
            try:
                err = await response.json()
                msg = self._format_error_message(err, status)
            except Exception:
                msg = f"Server Error: {status}"
            raise HTTPException(response, msg)
        
        try:
            return await response.json()
        except Exception:
            return response.text

    def _format_error_message(self, err: dict, status: int) -> str:
        code = err.get("code", 0)
        message = err.get("message", "Unknown error")
        
        if code == 50035 and "errors" in err:
            error_details = self._format_field_errors(err["errors"])
            return f"Invalid Form Body: {error_details}"
        
        return f"HTTP {status} (error code: {code}): {message}"

    def _format_field_errors(self, errors: dict, prefix: str = "") -> str:
        parts = []
        for key, value in errors.items():
            if key == "_errors":
                for error in value:
                    parts.append(f"{prefix}: {error.get('message', 'Unknown error')}")
            elif isinstance(value, dict):
                new_prefix = f"{prefix}.{key}" if prefix else key
                parts.append(self._format_field_errors(value, new_prefix))
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    new_prefix = f"{prefix}[{idx}]"
                    if isinstance(item, dict):
                        parts.append(self._format_field_errors(item, new_prefix))
        return "; ".join(parts)

    async def _retry_request(
        self,
        route: Route,
        *,
        json: Optional[Dict] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Dict[str, str],
        timeout: int,
        body: Optional[Union[bytes, str]] = None,
        attempt: int = 0,
    ) -> Any:
        try:
            response = await self._execute_request(
                route=route,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout,
                body=body,
            )
            return await self._handle_response(response, route)
        
        except HTTPException as e:
            if attempt >= self.max_retries:
                raise
            
            if 400 <= e.status < 500 and e.status != 429:
                raise
            
            if e.status == 429:
                retry_after = getattr(e.response, "headers", {}).get("Retry-After", 1)
                try:
                    retry_after = float(retry_after)
                except (ValueError, TypeError):
                    retry_after = 1
            else:
                backoff = min(self.BASE_BACKOFF * (2 ** attempt), self.MAX_BACKOFF)
                jitter = backoff * 0.1 * (2 * random.random() - 1)
                retry_after = max(0.5, backoff + jitter)
            
            logger.warning(
                f"Retrying request to {route.path} in {retry_after:.2f}s "
                f"(attempt {attempt + 1}/{self.max_retries})"
            )
            await asyncio.sleep(retry_after)
            
            return await self._retry_request(
                route=route,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout,
                body=body,
                attempt=attempt + 1,
            )

    async def request(
        self,
        route: Route,
        *,
        json: Optional[Dict] = None,
        reason: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        if self._closed:
            raise RuntimeError("HTTP client is closed")
        
        await self._rate_limiter.pre_request(route)
        
        headers = self._build_headers(
            referer=kwargs.get("referer", "https://discord.com/channels/@me"),
            context_location=kwargs.get("context_location", "chat_input"),
            reason=reason,
            extra=kwargs.get("extra_headers", {}),
        )
        
        timeout = self._get_timeout(route)
        
        try:
            return await self._retry_request(
                route=route,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout,
                attempt=0,
            )
        except HTTPException as e:
            raise HTTPException(
                getattr(e, "response", None),
                f"Request to {route.path} failed: {e}"
            ) from e
        except Exception as e:
            raise HTTPException(
                response=None,
                message=f"Request to {route.path} failed: {str(e)}"
            ) from e

    def _build_headers(
        self,
        *,
        referer: str = "https://discord.com/channels/@me",
        context_location: str = "chat_input",
        reason: Optional[str] = None,
        extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        headers = self._spoofer.get_headers(
            referer=referer,
            context_location=context_location,
            skip_context_props=False,
        )
        
        if self._session_id:
            headers["X-Session-Id"] = self._session_id
        
        if reason:
            headers["X-Audit-Log-Reason"] = reason
        
        if extra:
            headers.update(extra)
        
        return headers

    async def close(self) -> None:
        self._closed = True
        if self._client:
            await self._client.close()
            self._client = None
        logger.info("HTTP client closed")

    def set_session_id(self, session_id: str) -> None:
        self._session_id = session_id

    def get_rate_limit_stats(self) -> dict:
        return self._rate_limiter.get_stats()

    # Messages
    async def send_message(
        self,
        channel_id: int,
        content: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        payload = {}
        if content is not None:
            payload["content"] = content
        payload.update(kwargs)
        
        return await self.request(
            Route.send_message(channel_id),
            json=payload,
            referer=f"https://discord.com/channels/@me/{channel_id}",
            context_location="chat_input",
        )

    async def edit_message(
        self,
        channel_id: int,
        message_id: int,
        content: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        payload = {}
        if content is not None:
            payload["content"] = content
        payload.update(kwargs)
        
        return await self.request(
            Route.edit_message(channel_id, message_id),
            json=payload,
        )

    async def delete_message(self, channel_id: int, message_id: int) -> None:
        await self.request(
            Route.delete_message(channel_id, message_id)
        )

    async def bulk_delete_messages(self, channel_id: int, message_ids: List[int]) -> None:
        await self.request(
            Route.bulk_delete_messages(channel_id),
            json={"messages": [str(m) for m in message_ids]},
        )

    async def fetch_message(self, channel_id: int, message_id: int) -> Dict[str, Any]:
        return await self.request(
            Route.channel_message(channel_id, message_id),
        )

    async def fetch_messages(
        self,
        channel_id: int,
        *,
        limit: int = 50,
        before: Optional[int] = None,
        after: Optional[int] = None,
        around: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params = {"limit": min(limit, 100)}
        if before:
            params["before"] = str(before)
        if after:
            params["after"] = str(after)
        if around:
            params["around"] = str(around)
        
        return await self.request(
            Route.channel_messages(channel_id),
            params=params,
        )

    async def pin_message(self, channel_id: int, message_id: int) -> None:
        await self.request(
            Route.pin_message(channel_id, message_id)
        )

    async def unpin_message(self, channel_id: int, message_id: int) -> None:
        await self.request(
            Route.unpin_message(channel_id, message_id)
        )

    async def fetch_pins(self, channel_id: int) -> List[Dict[str, Any]]:
        return await self.request(
            Route.channel_pins(channel_id)
        )

    # Reactions
    async def add_reaction(self, channel_id: int, message_id: int, emoji: str) -> None:
        await self.request(
            Route.add_reaction(channel_id, message_id, emoji)
        )

    async def remove_reaction(self, channel_id: int, message_id: int, emoji: str) -> None:
        await self.request(
            Route.remove_reaction(channel_id, message_id, emoji)
        )

    async def clear_reactions(self, channel_id: int, message_id: int) -> None:
        await self.request(
            Route.clear_reactions(channel_id, message_id)
        )

    # Channels
    async def fetch_channel(self, channel_id: int) -> Dict[str, Any]:
        return await self.request(
            Route.channel(channel_id)
        )

    async def edit_channel(self, channel_id: int, **kwargs) -> Dict[str, Any]:
        return await self.request(
            Route.edit_channel(channel_id),
            json=kwargs,
        )

    async def delete_channel(self, channel_id: int) -> None:
        await self.request(
            Route.delete_channel(channel_id)
        )

    async def trigger_typing(self, channel_id: int) -> None:
        await self.request(
            Route.typing(channel_id)
        )

    # Users
    async def fetch_me(self) -> Dict[str, Any]:
        return await self.request(Route.me())

    async def fetch_me_guilds(self) -> List[Dict[str, Any]]:
        return await self.request(Route.me_guilds())

    async def fetch_user(self, user_id: int) -> Dict[str, Any]:
        return await self.request(Route.user(user_id))

    async def edit_profile(self, **kwargs) -> Dict[str, Any]:
        return await self.request(
            Route.edit_me(),
            json=kwargs,
        )

    # Guilds
    async def fetch_guild(self, guild_id: int) -> Dict[str, Any]:
        return await self.request(Route.guild(guild_id))

    async def fetch_guild_channels(self, guild_id: int) -> List[Dict[str, Any]]:
        return await self.request(Route.guild_channels(guild_id))

    async def create_guild(self, **kwargs) -> Dict[str, Any]:
        return await self.request(
            Route.create_guild(),
            json=kwargs,
        )

    async def leave_guild(self, guild_id: int) -> None:
        await self.request(Route.leave_guild(guild_id))

    async def edit_guild(self, guild_id: int, **kwargs) -> Dict[str, Any]:
        return await self.request(Route.edit_guild(guild_id), json=kwargs)

    async def fetch_guild_members(
        self,
        guild_id: int,
        *,
        limit: int = 1000,
        after: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": min(limit, 1000)}
        if after:
            params["after"] = str(after)
        return await self.request(Route.guild_members(guild_id), params=params)

    async def search_guild_members(
        self,
        guild_id: int,
        query: str,
        limit: int = 1,
    ) -> List[Dict[str, Any]]:
        return await self.request(
            Route.search_guild_members(guild_id),
            params={"query": query, "limit": min(limit, 1000)},
        )

    async def kick_member(self, guild_id: int, user_id: int, *, reason: Optional[str] = None) -> None:
        await self.request(Route.kick_member(guild_id, user_id), reason=reason)

    async def ban_member(
        self,
        guild_id: int,
        user_id: int,
        *,
        delete_message_seconds: int = 0,
        reason: Optional[str] = None,
    ) -> None:
        await self.request(
            Route.ban_member(guild_id, user_id),
            json={"delete_message_seconds": delete_message_seconds},
            reason=reason,
        )

    async def unban_member(self, guild_id: int, user_id: int, *, reason: Optional[str] = None) -> None:
        await self.request(Route.unban_member(guild_id, user_id), reason=reason)

    async def fetch_guild_bans(self, guild_id: int) -> List[Dict[str, Any]]:
        return await self.request(Route.guild_bans(guild_id))

    async def fetch_guild_roles(self, guild_id: int) -> List[Dict[str, Any]]:
        return await self.request(Route.guild_roles(guild_id))

    async def create_role(self, guild_id: int, **kwargs) -> Dict[str, Any]:
        return await self.request(Route.create_role(guild_id), json=kwargs)

    async def edit_role(self, guild_id: int, role_id: int, **kwargs) -> Dict[str, Any]:
        return await self.request(Route.edit_role(guild_id, role_id), json=kwargs)

    async def delete_role(self, guild_id: int, role_id: int) -> None:
        await self.request(Route.delete_role(guild_id, role_id))

    # Invites
    async def fetch_invite(self, invite_code: str) -> Dict[str, Any]:
        return await self.request(Route.invite(invite_code))

    async def delete_invite(self, invite_code: str, *, reason: Optional[str] = None) -> None:
        await self.request(Route.delete_invite(invite_code), reason=reason)

    # Threads
    async def create_thread_from_message(
        self,
        channel_id: int,
        message_id: int,
        name: str,
        *,
        auto_archive_duration: int = 1440,
        **kwargs,
    ) -> Dict[str, Any]:
        payload = {"name": name, "auto_archive_duration": auto_archive_duration}
        payload.update(kwargs)
        return await self.request(Route.create_thread(channel_id, message_id), json=payload)

    async def create_thread(
        self,
        channel_id: int,
        name: str,
        *,
        type: int = 11,
        auto_archive_duration: int = 1440,
        **kwargs,
    ) -> Dict[str, Any]:
        payload = {"name": name, "type": type, "auto_archive_duration": auto_archive_duration}
        payload.update(kwargs)
        return await self.request(Route.create_thread_in_channel(channel_id), json=payload)

    # DMs
    async def create_dm(self, recipient_id: int) -> Dict[str, Any]:
        return await self.request(
            Route.create_dm(),
            json={"recipient_id": str(recipient_id)},
        )

    async def create_group_dm(self, access_tokens: List[str], nicks: Dict[str, str]) -> Dict[str, Any]:
        return await self.request(
            Route.create_group_dm(),
            json={"access_tokens": access_tokens, "nicks": nicks},
        )

    # Relationships
    async def fetch_relationships(self) -> List[Dict[str, Any]]:
        return await self.request(Route.relationships())