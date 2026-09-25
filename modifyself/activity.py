"""
Rich presence and activity types.
"""

import time
from typing import Optional, List, Dict, Any, Union
from enum import IntEnum

class ActivityType(IntEnum):
    PLAYING = 0
    STREAMING = 1
    LISTENING = 2
    WATCHING = 3
    CUSTOM = 4
    COMPETING = 5
    HANG = 6

class ActivityFlags(IntEnum):
    INSTANCE = 1 << 0
    JOIN = 1 << 1
    SPECTATE = 1 << 2  # deprecated
    JOIN_REQUEST = 1 << 3
    SYNC = 1 << 4
    PLAY = 1 << 5
    PARTY_PRIVACY_FRIENDS = 1 << 6
    PARTY_PRIVACY_VOICE_CHANNEL = 1 << 7
    EMBEDDED = 1 << 8
    CONTEXTLESS = 1 << 9

class Activity:
    def __init__(
        self,
        type: Union[ActivityType, int] = ActivityType.PLAYING,
        name: str = "",
        url: Optional[str] = None,
        details: Optional[str] = None,
        state: Optional[str] = None,
        timestamps: Optional[Dict[str, int]] = None,
        assets: Optional[Dict[str, str]] = None,
        party: Optional[Dict[str, Any]] = None,
        secrets: Optional[Dict[str, str]] = None,
        instance: bool = False,
        flags: int = 0,
        buttons: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        sync_id: Optional[str] = None,
        application_id: Optional[str] = None,
        platform: Optional[str] = None,
    ):
        self.type = type if isinstance(type, ActivityType) else ActivityType(type)
        self.name = name
        self.url = url
        self.details = details
        self.state = state
        self.timestamps = timestamps
        self.assets = assets
        self.party = party
        self.secrets = secrets
        self.instance = instance
        self.flags = flags
        self.buttons = buttons
        self.metadata = metadata
        self.session_id = session_id
        self.sync_id = sync_id
        self.application_id = application_id
        self.platform = platform

    def to_dict(self) -> dict:
        data = {
            "type": int(self.type),
            "name": self.name[:128],
        }
        if self.url:
            data["url"] = self.url
        if self.details:
            data["details"] = self.details[:128]
        if self.state:
            data["state"] = self.state[:128]
        if self.timestamps:
            data["timestamps"] = self.timestamps
        if self.assets:
            data["assets"] = self.assets
        if self.party:
            data["party"] = self.party
        if self.secrets:
            data["secrets"] = self.secrets
        if self.instance:
            data["instance"] = self.instance
        if self.flags:
            data["flags"] = self.flags
        if self.buttons:
            data["buttons"] = self.buttons[:2]
        if self.metadata:
            data["metadata"] = self.metadata
        if self.session_id:
            data["session_id"] = self.session_id
        if self.sync_id:
            data["sync_id"] = self.sync_id
        if self.application_id:
            data["application_id"] = self.application_id
        if self.platform:
            data["platform"] = self.platform
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Activity":
        return cls(
            type=data.get("type", 0),
            name=data.get("name", ""),
            url=data.get("url"),
            details=data.get("details"),
            state=data.get("state"),
            timestamps=data.get("timestamps"),
            assets=data.get("assets"),
            party=data.get("party"),
            secrets=data.get("secrets"),
            instance=data.get("instance", False),
            flags=data.get("flags", 0),
            buttons=data.get("buttons"),
            metadata=data.get("metadata"),
            session_id=data.get("session_id"),
            sync_id=data.get("sync_id"),
            application_id=data.get("application_id"),
            platform=data.get("platform"),
        )

    def __repr__(self) -> str:
        return f"<Activity type={self.type} name={self.name!r}>"


def spotify_activity(
    song: str,
    artist: str,
    album: str = "",
    duration_minutes: float = 3.5,
    current_position: float = 0,
    image_url: Optional[str] = None,
    track_id: str = None,
    album_id: str = None,
    artist_id: str = None,
) -> Activity:
    total_ms = int(duration_minutes * 60 * 1000)
    start_time = int(time.time() * 1000)
    end_time = start_time + total_ms
    
    track_id = track_id or "0VjIjW4GlUZAMYd2vXMi3b"
    album_id = album_id or "4yP0hdKOZPNshxUOjY0cZj"
    artist_id = artist_id or "1Xyo4u8uXC1ZmMpatF05PJ"
    
    activity = Activity(
        type=ActivityType.LISTENING,
        name="Spotify",
        details=song[:128],
        state=artist[:128],
        timestamps={"start": start_time, "end": end_time},
        application_id="3201606009684",
        sync_id=track_id,
        session_id=f"spotify:{track_id}",
        party={"id": f"spotify:{track_id}", "size": [1, 1]},
        secrets={"join": track_id, "spectate": track_id, "match": track_id},
        instance=True,
        flags=48,
        metadata={
            "context_uri": f"spotify:album:{album_id}",
            "album_id": album_id,
            "artist_ids": [artist_id],
            "track_id": track_id,
        },
    )
    
    if image_url:
        activity.assets = {
            "large_image": image_url,
            "large_text": f"{album} on Spotify" if album else "Spotify",
            "small_image": image_url,
            "small_text": album or "Spotify",
        }
    else:
        activity.assets = {"large_image": "spotify", "large_text": "Spotify"}
    
    return activity


def youtube_activity(
    video_title: str,
    channel_name: str,
    elapsed_minutes: float = 0,
    duration_minutes: float = 10,
    image_url: Optional[str] = None,
) -> Activity:
    cur_ms = int(elapsed_minutes * 60 * 1000)
    tot_ms = int(duration_minutes * 60 * 1000)
    start_time = int(time.time() * 1000)
    
    activity = Activity(
        type=ActivityType.WATCHING,
        name="YouTube",
        details=video_title[:128],
        state=channel_name[:128],
        timestamps={"start": start_time - cur_ms, "end": start_time - cur_ms + tot_ms},
        application_id="111299001912",
    )
    
    if image_url:
        activity.assets = {"large_image": image_url}
    
    return activity


def xbox_activity(
    game_name: str,
    details: Optional[str] = None,
    state: Optional[str] = None,
    image_url: Optional[str] = None,
    party_cur: Optional[int] = None,
    party_max: Optional[int] = None,
) -> Activity:
    activity = Activity(
        type=ActivityType.PLAYING,
        name=game_name[:128],
        application_id="622174530214821906",
        platform="xbox",
        timestamps={"start": int(time.time() * 1000)},
    )
    
    if details:
        activity.details = details[:128]
    if state:
        activity.state = state[:128]
    if image_url:
        activity.assets = {"large_image": image_url}
    if party_cur and party_max:
        activity.party = {"id": "xbox-party", "size": [party_cur, party_max]}
    
    return activity


def playstation_activity(
    game_name: str,
    platform: str = "ps5",
    details: Optional[str] = None,
    state: Optional[str] = None,
    image_url: Optional[str] = None,
    party_cur: Optional[int] = None,
    party_max: Optional[int] = None,
) -> Activity:
    activity = Activity(
        type=ActivityType.PLAYING,
        name=game_name[:128],
        application_id="1470539864909943067",
        platform=platform,
        timestamps={"start": int(time.time() * 1000)},
    )
    
    if details:
        activity.details = details[:128]
    if state:
        activity.state = state[:128]
    if image_url:
        activity.assets = {"large_image": image_url}
    if party_cur and party_max:
        activity.party = {"id": "ps-party", "size": [party_cur, party_max]}
    
    return activity


def crunchyroll_activity(
    anime_title: str,
    episode_title: Optional[str] = None,
    elapsed_minutes: float = 0,
    total_minutes: float = 24,
    image_url: Optional[str] = None,
) -> Activity:
    activity = Activity(
        type=ActivityType.WATCHING,
        name="Crunchyroll",
        details=anime_title[:128],
        application_id="981509069309354054",
        timestamps={
            "start": int(time.time() * 1000) - int(elapsed_minutes * 60 * 1000),
            "end": int(time.time() * 1000) - int(elapsed_minutes * 60 * 1000) + int(total_minutes * 60 * 1000),
        },
    )
    
    if episode_title:
        activity.state = episode_title[:128]
    if image_url:
        activity.assets = {"large_image": image_url}
    
    return activity


def custom_activity(
    name: str,
    activity_type: Union[ActivityType, int] = ActivityType.PLAYING,
    details: Optional[str] = None,
    state: Optional[str] = None,
    url: Optional[str] = None,
    image_url: Optional[str] = None,
    application_id: Optional[str] = None,
    platform: Optional[str] = None,
) -> Activity:
    return Activity(
        type=activity_type,
        name=name[:128],
        details=details[:128] if details else None,
        state=state[:128] if state else None,
        url=url,
        assets={"large_image": image_url} if image_url else None,
        application_id=application_id or "1487942541696434337",
        platform=platform,
        timestamps={"start": int(time.time() * 1000)},
    )


def listening_activity(
    name: str,
    details: Optional[str] = None,
    state: Optional[str] = None,
    image_url: Optional[str] = None,
    total_minutes: Optional[float] = None,
    elapsed_minutes: float = 0,
) -> Activity:
    activity = Activity(
        type=ActivityType.LISTENING,
        name=name[:128],
        application_id="1487942541696434337",
    )
    
    if details:
        activity.details = details[:128]
    if state:
        activity.state = state[:128]
    if image_url:
        activity.assets = {"large_image": image_url}
    if total_minutes:
        activity.timestamps = {
            "start": int(time.time() * 1000) - int(elapsed_minutes * 60 * 1000),
            "end": int(time.time() * 1000) - int(elapsed_minutes * 60 * 1000) + int(total_minutes * 60 * 1000),
        }
    else:
        activity.timestamps = {"start": int(time.time() * 1000)}
    
    return activity


def streaming_activity(
    name: str,
    url: str = "https://twitch.tv/kaicenat",
    details: Optional[str] = None,
    state: Optional[str] = None,
    image_url: Optional[str] = None,
) -> Activity:
    activity = Activity(
        type=ActivityType.STREAMING,
        name=name[:128],
        url=url,
        application_id="1487942541696434337",
        timestamps={"start": int(time.time() * 1000)},
    )
    
    if details:
        activity.details = details[:128]
    if state:
        activity.state = state[:128]
    if image_url:
        activity.assets = {"large_image": image_url}
    
    return activity


def competing_activity(
    name: str,
    details: Optional[str] = None,
    state: Optional[str] = None,
    image_url: Optional[str] = None,
) -> Activity:
    activity = Activity(
        type=ActivityType.COMPETING,
        name=name[:128],
        application_id="1487942541696434337",
        timestamps={"start": int(time.time() * 1000)},
    )
    
    if details:
        activity.details = details[:128]
    if state:
        activity.state = state[:128]
    if image_url:
        activity.assets = {"large_image": image_url}
    
    return activity


LOGO_MAP = {
    "xbox": ("622174530214821906", "xbox"),
    "playstation": ("1470539864909943067", "ps5"),
    "ps5": ("1470539864909943067", "ps5"),
    "ps4": ("1470539864909943067", "ps4"),
    "crunchyroll": ("981509069309354054", None),
    "spotify": ("3201606009684", None),
    "youtube": ("111299001912", None),
}