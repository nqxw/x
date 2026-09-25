"""
Pure utility functions. No Discord-specific state here.
"""

import re
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Optional



MARKDOWN_ESCAPE_RE = re.compile(r"([*_{\[\]()~`>\#+\-=|.!])")


def escape_markdown(text: str) -> str:
    """Escape Discord markdown characters."""
    return MARKDOWN_ESCAPE_RE.sub(r"\\\1", text)


def parse_time(timestamp: str) -> datetime:
    """Parse an ISO 8601 timestamp from Discord."""
    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1] + "+00:00"
    return datetime.fromisoformat(timestamp)


def snowflake_time(snowflake: int) -> datetime:
    """Extract the creation time from a snowflake ID."""
    from .core.snowflake import Snowflake
    return datetime.fromtimestamp(
        ((snowflake >> 22) + Snowflake.EPOCH) / 1000,
        tz=timezone.utc,
    )


def chunk_list(lst: list, size: int):
    """Yield successive chunks of a list."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def find(predicate, iterable):
    """Return the first item in iterable matching predicate."""
    for item in iterable:
        if predicate(item):
            return item
    return None


def get(iterable, **attrs):
    """Return the first item in iterable with matching attributes."""
    for item in iterable:
        if all(getattr(item, k, None) == v for k, v in attrs.items()):
            return item
    return None


def oauth_url(client_id: int, *, permissions: int = 0, guild_id: int | None = None):
    """Generate an OAuth2 authorization URL."""
    url = f"https://discord.com/oauth2/authorize?client_id={client_id}&scope=bot"
    if permissions:
        url += f"&permissions={permissions}"
    if guild_id:
        url += f"&guild_id={guild_id}"
    return url


def send_notification(
    title: str,
    message: str,
    image_url: Optional[str] = None,
    timeout: int = 5,
    app_name: str = "modifyself"
) -> bool:
    """Send a system desktop notification with an optional image icon."""
    try:
        from plyer import notification as _notify
    except ImportError:
        print("[modifyself] plyer not installed. Install with: pip install plyer pillow")
        return False

    try:
        icon_path = None

        if image_url:
            try:
                import urllib.request as _ureq
                with _ureq.urlopen(image_url, timeout=10) as _resp:
                    img_data = _resp.read()
                is_windows = sys.platform.startswith("win")
                suffix = ".ico" if is_windows else ".png"
                icon_temp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                icon_temp.write(img_data)
                icon_temp.close()
                icon_path = icon_temp.name
                if is_windows:
                    try:
                        from PIL import Image
                        with Image.open(icon_path) as img:
                            if img.format != "ICO":
                                ico_path = icon_path.replace(".ico", "_converted.ico")
                                img.save(ico_path, format="ICO", sizes=[(32, 32), (48, 48), (64, 64)])
                        try:
                            os.unlink(icon_path)
                        except Exception:
                            pass
                        icon_path = ico_path
                    except Exception as e:
                        print(f"[modifyself] ICO conversion warning: {e}")
            except Exception as e:
                print(f"[modifyself] Error downloading notification image: {e}")

        _notify.notify(
            title=title,
            message=message,
            app_name=app_name,
            app_icon=icon_path,
            timeout=timeout,
        )

        if icon_path and os.path.exists(icon_path):
            try:
                os.unlink(icon_path)
            except Exception:
                pass

        return True

    except Exception as e:
        print(f"[modifyself] Failed to send notification: {e}")
        return False

# Default notification images - using GitHub raw URL
START_IMAGE = None
ERROR_IMAGE = None