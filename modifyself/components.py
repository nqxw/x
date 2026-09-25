"""
Discord components (buttons, selects, modals).
"""

from typing import Optional, List, Dict, Any, Union, Callable, Awaitable
from enum import IntEnum
import json

class ComponentType(IntEnum):
    ACTION_ROW = 1
    BUTTON = 2
    SELECT_MENU = 3
    TEXT_INPUT = 4
    CHANNEL_SELECT = 5
    ROLE_SELECT = 6
    MENTIONABLE_SELECT = 7
    USER_SELECT = 8

class ButtonStyle(IntEnum):
    PRIMARY = 1
    SECONDARY = 2
    SUCCESS = 3
    DANGER = 4
    LINK = 5

class TextInputStyle(IntEnum):
    SHORT = 1
    PARAGRAPH = 2

class Component:
    def __init__(self, data: dict):
        self.type = data.get("type")
        self._data = data

    def to_dict(self) -> dict:
        return self._data

class Button(Component):
    def __init__(
        self,
        style: Union[ButtonStyle, int] = ButtonStyle.PRIMARY,
        label: Optional[str] = None,
        custom_id: Optional[str] = None,
        url: Optional[str] = None,
        emoji: Optional[Dict[str, Any]] = None,
        disabled: bool = False,
    ):
        data = {
            "type": ComponentType.BUTTON,
            "style": int(style),
            "disabled": disabled,
        }
        if label:
            data["label"] = label
        if custom_id:
            data["custom_id"] = custom_id
        if url:
            data["url"] = url
        if emoji:
            data["emoji"] = emoji
        super().__init__(data)
        self.style = style
        self.label = label
        self.custom_id = custom_id
        self.url = url
        self.emoji = emoji
        self.disabled = disabled

    @classmethod
    def primary(cls, label: str, custom_id: str, emoji: Optional[Dict[str, Any]] = None) -> "Button":
        return cls(style=ButtonStyle.PRIMARY, label=label, custom_id=custom_id, emoji=emoji)

    @classmethod
    def secondary(cls, label: str, custom_id: str, emoji: Optional[Dict[str, Any]] = None) -> "Button":
        return cls(style=ButtonStyle.SECONDARY, label=label, custom_id=custom_id, emoji=emoji)

    @classmethod
    def success(cls, label: str, custom_id: str, emoji: Optional[Dict[str, Any]] = None) -> "Button":
        return cls(style=ButtonStyle.SUCCESS, label=label, custom_id=custom_id, emoji=emoji)

    @classmethod
    def danger(cls, label: str, custom_id: str, emoji: Optional[Dict[str, Any]] = None) -> "Button":
        return cls(style=ButtonStyle.DANGER, label=label, custom_id=custom_id, emoji=emoji)

    @classmethod
    def link(cls, label: str, url: str, emoji: Optional[Dict[str, Any]] = None) -> "Button":
        return cls(style=ButtonStyle.LINK, label=label, url=url, emoji=emoji)

class SelectOption:
    def __init__(
        self,
        label: str,
        value: str,
        description: Optional[str] = None,
        emoji: Optional[Dict[str, Any]] = None,
        default: bool = False,
    ):
        self.label = label
        self.value = value
        self.description = description
        self.emoji = emoji
        self.default = default

    def to_dict(self) -> dict:
        data = {"label": self.label, "value": self.value}
        if self.description:
            data["description"] = self.description
        if self.emoji:
            data["emoji"] = self.emoji
        if self.default:
            data["default"] = True
        return data

class SelectMenu(Component):
    def __init__(
        self,
        custom_id: str,
        options: List[SelectOption],
        placeholder: Optional[str] = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ):
        data = {
            "type": ComponentType.SELECT_MENU,
            "custom_id": custom_id,
            "options": [opt.to_dict() for opt in options],
            "min_values": min_values,
            "max_values": max_values,
            "disabled": disabled,
        }
        if placeholder:
            data["placeholder"] = placeholder
        super().__init__(data)
        self.custom_id = custom_id
        self.options = options
        self.placeholder = placeholder
        self.min_values = min_values
        self.max_values = max_values
        self.disabled = disabled

class ChannelSelect(Component):
    def __init__(
        self,
        custom_id: str,
        placeholder: Optional[str] = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
        channel_types: Optional[List[int]] = None,
    ):
        data = {
            "type": ComponentType.CHANNEL_SELECT,
            "custom_id": custom_id,
            "min_values": min_values,
            "max_values": max_values,
            "disabled": disabled,
        }
        if placeholder:
            data["placeholder"] = placeholder
        if channel_types:
            data["channel_types"] = channel_types
        super().__init__(data)

class RoleSelect(Component):
    def __init__(
        self,
        custom_id: str,
        placeholder: Optional[str] = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ):
        data = {
            "type": ComponentType.ROLE_SELECT,
            "custom_id": custom_id,
            "min_values": min_values,
            "max_values": max_values,
            "disabled": disabled,
        }
        if placeholder:
            data["placeholder"] = placeholder
        super().__init__(data)

class MentionableSelect(Component):
    def __init__(
        self,
        custom_id: str,
        placeholder: Optional[str] = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ):
        data = {
            "type": ComponentType.MENTIONABLE_SELECT,
            "custom_id": custom_id,
            "min_values": min_values,
            "max_values": max_values,
            "disabled": disabled,
        }
        if placeholder:
            data["placeholder"] = placeholder
        super().__init__(data)

class UserSelect(Component):
    def __init__(
        self,
        custom_id: str,
        placeholder: Optional[str] = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ):
        data = {
            "type": ComponentType.USER_SELECT,
            "custom_id": custom_id,
            "min_values": min_values,
            "max_values": max_values,
            "disabled": disabled,
        }
        if placeholder:
            data["placeholder"] = placeholder
        super().__init__(data)

class TextInput(Component):
    def __init__(
        self,
        custom_id: str,
        label: str,
        style: Union[TextInputStyle, int] = TextInputStyle.SHORT,
        placeholder: Optional[str] = None,
        value: Optional[str] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        required: bool = True,
    ):
        data = {
            "type": ComponentType.TEXT_INPUT,
            "custom_id": custom_id,
            "label": label,
            "style": int(style),
            "required": required,
        }
        if placeholder:
            data["placeholder"] = placeholder
        if value:
            data["value"] = value
        if min_length:
            data["min_length"] = min_length
        if max_length:
            data["max_length"] = max_length
        super().__init__(data)

class ActionRow:
    def __init__(self, *components: Component):
        self.components = list(components)

    def add_component(self, component: Component):
        self.components.append(component)

    def to_dict(self) -> dict:
        return {
            "type": ComponentType.ACTION_ROW,
            "components": [c.to_dict() for c in self.components],
        }

class Modal:
    def __init__(
        self,
        custom_id: str,
        title: str,
        components: Optional[List[Component]] = None,
    ):
        self.custom_id = custom_id
        self.title = title
        self.components = components or []

    def add_component(self, component: Component):
        self.components.append(component)

    def to_dict(self) -> dict:
        return {
            "custom_id": self.custom_id,
            "title": self.title,
            "components": [ActionRow(c).to_dict() for c in self.components],
        }