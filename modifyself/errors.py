"""
Exception hierarchy for modifyself.
"""


class DiscordException(Exception):
    """Base exception for all modifyself errors."""
    pass


class HTTPException(DiscordException):
    """Raised when an HTTP request fails."""

    def __init__(self, response, message: dict | str):
        self.response = response
        self.status: int = getattr(response, "status_code", getattr(response, "status", 0))
        if isinstance(message, dict):
            self.code: int = message.get("code", 0)
            self.text: str = message.get("message", "")
        else:
            self.code = 0
            self.text = str(message)
        super().__init__(f"{self.status} (error code: {self.code}): {self.text}")


class GatewayException(DiscordException):
    """Raised when a Gateway/WebSocket error occurs."""
    pass


class ConnectionClosed(GatewayException):
    """Raised when the gateway connection is closed unexpectedly."""

    def __init__(self, socket, *, code: int | None = None):
        self.code = code or getattr(socket, "close_code", None)
        super().__init__(f"WebSocket closed with code {self.code}")


class CommandError(DiscordException):
    """Base exception for command-related errors."""
    pass


class CheckFailure(CommandError):
    """Raised when a command check fails."""
    pass


class ConversionError(CommandError):
    """Raised when a type converter fails."""

    def __init__(self, converter, original: Exception):
        self.converter = converter
        self.original = original
        super().__init__(f"Failed to convert using {converter}: {original}")


class CommandNotFound(CommandError):
    """Raised when a command is not found."""
    pass


class MissingRequiredArgument(CommandError):
    """Raised when a required command argument is missing."""

    def __init__(self, param):
        self.param = param
        super().__init__(f"Missing required argument: {param.name}")


class CaptchaRequired(DiscordException):
    """Raised when Discord requires a captcha."""

    def __init__(self, sitekey: str, rqdata: str | None = None):
        self.sitekey = sitekey
        self.rqdata = rqdata
        super().__init__("Captcha required but no solver configured.")
