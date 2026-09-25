"""
Interaction handling (buttons, selects, modals).
"""

import json
from typing import Optional, Dict, Any, List, Union, Callable, Awaitable
from enum import IntEnum
from dataclasses import dataclass, field

from .models.message import Message
from .models.user import User
from .models.member import Member
from .components import ComponentType, ButtonStyle, TextInputStyle, ActionRow, Component
from .http.route import Route

class InteractionType(IntEnum):
    PING = 1
    APPLICATION_COMMAND = 2
    MESSAGE_COMPONENT = 3
    APPLICATION_COMMAND_AUTOCOMPLETE = 4
    MODAL_SUBMIT = 5

class InteractionCallbackType(IntEnum):
    PONG = 1
    CHANNEL_MESSAGE_WITH_SOURCE = 4
    DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5
    DEFERRED_UPDATE_MESSAGE = 6
    UPDATE_MESSAGE = 7
    APPLICATION_COMMAND_AUTOCOMPLETE_RESULT = 8
    MODAL = 9

class Interaction:
    """Represents a Discord interaction."""
    
    def __init__(self, state, data: dict):
        self._state = state
        # Discord has been evolving the interaction payloads and now emits
        # events on the INTERACTION_CREATE path that no longer carry the full
        # set of fields (e.g. lightweight ack/lifecycle payloads that lack
        # "type", "application_id", "token" or "version"). Parse defensively so
        # a shape we don't fully recognise degrades instead of raising KeyError.
        self._raw = data
        self.id = int(data["id"]) if data.get("id") is not None else None
        self.application_id = (
            int(data["application_id"]) if data.get("application_id") is not None else None
        )
        raw_type = data.get("type")
        try:
            self.type = InteractionType(raw_type) if raw_type is not None else None
        except ValueError:
            # unknown/new interaction type Discord introduced — keep the raw int
            self.type = raw_type
        self.token = data.get("token")
        self.version = data.get("version")
        self.guild_id = int(data["guild_id"]) if data.get("guild_id") else None
        self.channel_id = int(data["channel_id"]) if data.get("channel_id") else None
        
        # Message
        self.message = None
        if data.get("message"):
            self.message = Message(state=self._state, data=data["message"])
        
        # Member/User
        self.member = None
        self.user = None
        if data.get("member"):
            self.member = Member(state=self._state, data=data["member"], guild_id=self.guild_id)
            self.user = self.member.user
        elif data.get("user"):
            self.user = User(state=self._state, data=data["user"])
        
        # Data
        self.data = data.get("data", {})
        self.custom_id = self.data.get("custom_id")
        
        # Values (for selects)
        self.values = self.data.get("values")
        
        # Component type
        self.component_type = self.data.get("component_type")
    
    @property
    def author(self):
        """The user who triggered the interaction."""
        return self.user or (self.member.user if self.member else None)
    
    async def respond(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        components: Optional[List[ActionRow]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Respond to the interaction."""
        payload = {
            "type": InteractionCallbackType.CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {},
        }
        if content is not None:
            payload["data"]["content"] = content
        if embeds is not None:
            payload["data"]["embeds"] = embeds
        if components is not None:
            payload["data"]["components"] = [c.to_dict() for c in components]
        payload["data"].update(kwargs)
        
        await self._state.http.request(
            Route("POST", f"/interactions/{self.id}/{self.token}/callback"),
            json=payload,
        )
        return payload

    async def update(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        components: Optional[List[ActionRow]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Update the message that the interaction was triggered from."""
        payload = {
            "type": InteractionCallbackType.UPDATE_MESSAGE,
            "data": {},
        }
        if content is not None:
            payload["data"]["content"] = content
        if embeds is not None:
            payload["data"]["embeds"] = embeds
        if components is not None:
            payload["data"]["components"] = [c.to_dict() for c in components]
        payload["data"].update(kwargs)
        await self._state.http.request(
            Route("POST", f"/interactions/{self.id}/{self.token}/callback"),
            json=payload,
        )
        return payload

    async def defer(self, ephemeral: bool = False) -> None:
        """Defer the interaction response."""
        payload = {
            "type": InteractionCallbackType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"flags": 64 if ephemeral else 0},
        }
        await self._state.http.request(
            Route("POST", f"/interactions/{self.id}/{self.token}/callback"),
            json=payload,
        )

    async def defer_update(self) -> None:
        """Defer the interaction response for message updates."""
        payload = {
            "type": InteractionCallbackType.DEFERRED_UPDATE_MESSAGE,
            "data": {},
        }
        await self._state.http.request(
            Route("POST", f"/interactions/{self.id}/{self.token}/callback"),
            json=payload,
        )

    async def show_modal(self, modal) -> None:
        """Show a modal to the user."""
        payload = {
            "type": InteractionCallbackType.MODAL,
            "data": modal.to_dict(),
        }
        await self._state.http.request(
            Route("POST", f"/interactions/{self.id}/{self.token}/callback"),
            json=payload,
        )

    async def edit_original_response(
        self,
        content: Optional[str] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        components: Optional[List[ActionRow]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Edit the original response message."""
        payload = {}
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds
        if components is not None:
            payload["components"] = [c.to_dict() for c in components]
        payload.update(kwargs)
        return await self._state.http.request(
            Route("PATCH", f"/webhooks/{self.application_id}/{self.token}/messages/@original"),
            json=payload,
        )

    async def delete_original_response(self) -> None:
        """Delete the original response message."""
        await self._state.http.request(
            Route("DELETE", f"/webhooks/{self.application_id}/{self.token}/messages/@original"),
        )


class InteractionHandler:
    """Manages interaction callbacks."""
    
    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
    
    def on(self, custom_id: str):
        """Decorator to register a handler for a specific custom_id."""
        def decorator(func: Callable[[Interaction], Awaitable[None]]):
            self._handlers[custom_id] = func
            return func
        return decorator
    
    async def handle(self, interaction: Interaction) -> bool:
        """Handle an interaction."""
        if interaction.custom_id in self._handlers:
            await self._handlers[interaction.custom_id](interaction)
            return True
        return False


# Global interaction handler instance
interaction_handler = InteractionHandler()