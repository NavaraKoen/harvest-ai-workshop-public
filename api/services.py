"""
Music recommendation agent service endpoints, backed by the Audius API.

Audius (https://audius.co) is an open, decentralized music catalog. Its read-only
endpoints require no OAuth — only an `app_name` query parameter — so search,
trending/recommendations and real streamable track links all work out of the box.

Swapping these handlers for a different provider only touches this file; the
Temporal layer and tools stay unchanged.
"""
import os

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/services", tags=["services"])

APP_NAME = os.getenv("AUDIUS_APP_NAME", "TuneBot")

# Audius is decentralised: pick a discovery node from the registry, fall back to a
# well-known host if the registry is unreachable. Cached for the process lifetime.
_HOST_REGISTRY = "https://api.audius.co"
_FALLBACK_HOST = "https://discoveryprovider.audius.co"
_cached_host: str | None = None


def _get_host() -> str:
    global _cached_host
    if _cached_host:
        return _cached_host
    host = _FALLBACK_HOST
    try:
        r = httpx.get(_HOST_REGISTRY, timeout=10.0)
        r.raise_for_status()
        hosts = r.json().get("data", [])
        if hosts:
            host = hosts[0]
    except (httpx.HTTPError, ValueError, KeyError):
        host = _FALLBACK_HOST
    _cached_host = host
    return host


# ---------------------------------------------------------------------------
# Genre / mood mapping — friendly user terms -> Audius' controlled vocabulary
# ---------------------------------------------------------------------------

_GENRE_MAP: dict[str, str] = {
    "pop": "Pop",
    "rock": "Rock",
    "metal": "Metal",
    "alternative": "Alternative",
    "hip-hop": "Hip-Hop/Rap",
    "hip hop": "Hip-Hop/Rap",
    "rap": "Hip-Hop/Rap",
    "electronic": "Electronic",
    "edm": "Electronic",
    "house": "Electronic",
    "techno": "Electronic",
    "chill": "Lo-Fi",
    "lofi": "Lo-Fi",
    "lo-fi": "Lo-Fi",
    "jazz": "Jazz",
    "classical": "Classical",
    "country": "Country",
    "folk": "Folk",
    "r&b": "R&B/Soul",
    "soul": "R&B/Soul",
    "reggae": "Reggae",
    "latin": "Latin",
    "ambient": "Ambient",
}

_MOOD_MAP: dict[str, str] = {
    "happy": "Upbeat",
    "upbeat": "Upbeat",
    "energetic": "Energizing",
    "party": "Excited",
    "excited": "Excited",
    "chill": "Peaceful",
    "relaxed": "Peaceful",
    "peaceful": "Peaceful",
    "focus": "Cool",
    "cool": "Cool",
    "sad": "Melancholy",
    "melancholic": "Melancholy",
    "melancholy": "Melancholy",
    "romantic": "Romantic",
}


def _map_genre(genre: str) -> str:
    return _GENRE_MAP.get(genre.lower().strip(), "")


def _map_mood(mood: str) -> str:
    return _MOOD_MAP.get(mood.lower().strip(), "")


def _format_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Shared track model + mapping from an Audius track object
# ---------------------------------------------------------------------------

class Track(BaseModel):
    track_id: str
    title: str
    artist: str
    genre: str = ""
    mood: str = ""
    duration: str
    plays: int = 0
    url: str = ""


def _map_track(t: dict) -> Track:
    permalink = t.get("permalink") or ""
    return Track(
        track_id=str(t.get("id", "")),
        title=t.get("title", ""),
        artist=(t.get("user") or {}).get("name", ""),
        genre=t.get("genre") or "",
        mood=t.get("mood") or "",
        duration=_format_duration(t.get("duration", 0)),
        plays=t.get("play_count", 0) or 0,
        url=f"https://audius.co{permalink}" if permalink else "",
    )


def _audius_get(path: str, params: dict) -> list[dict]:
    host = _get_host()
    r = httpx.get(f"{host}/v1{path}", params={**params, "app_name": APP_NAME}, timeout=15.0)
    r.raise_for_status()
    return r.json().get("data", []) or []


# ---------------------------------------------------------------------------
# /services/search-tracks
# ---------------------------------------------------------------------------

class SearchTracksRequest(BaseModel):
    query: str


class SearchTracksResponse(BaseModel):
    tracks: list[Track]


@router.post("/search-tracks", response_model=SearchTracksResponse)
def search_tracks(req: SearchTracksRequest) -> SearchTracksResponse:
    data = _audius_get("/tracks/search", {"query": req.query})
    tracks = [_map_track(t) for t in data][:8]
    return SearchTracksResponse(tracks=tracks)


# ---------------------------------------------------------------------------
# /services/get-recommendations  (Audius trending, filtered by genre/mood)
# ---------------------------------------------------------------------------

class RecommendationsRequest(BaseModel):
    genre: str = ""
    mood: str = ""
    limit: int = Field(default=5, ge=1, le=10)


class RecommendationsResponse(BaseModel):
    seed_genre: str
    mood: str
    tracks: list[Track]


@router.post("/get-recommendations", response_model=RecommendationsResponse)
def get_recommendations(req: RecommendationsRequest) -> RecommendationsResponse:
    params: dict = {}
    genre = _map_genre(req.genre)
    if genre:
        params["genre"] = genre
    data = _audius_get("/tracks/trending", params)

    mood = _map_mood(req.mood)
    if mood:
        filtered = [t for t in data if (t.get("mood") or "").lower() == mood.lower()]
        if filtered:
            data = filtered

    tracks = [_map_track(t) for t in data][: req.limit]
    return RecommendationsResponse(
        seed_genre=req.genre or "trending",
        mood=req.mood or "any",
        tracks=tracks,
    )


# ---------------------------------------------------------------------------
# /services/create-playlist
#
# Creating a hosted Audius playlist requires user OAuth (write scope). Without it
# we build a shareable, curated list of REAL Audius track links by resolving each
# requested title against the catalog — so every link actually works.
# ---------------------------------------------------------------------------

class CreatePlaylistRequest(BaseModel):
    playlist_name: str
    tracks: list[str] = Field(default_factory=list)
    user_name: str = ""


class PlaylistTrack(BaseModel):
    title: str
    artist: str
    url: str


class CreatePlaylistResponse(BaseModel):
    name: str
    owner: str
    track_count: int
    tracks: list[PlaylistTrack]
    status: str
    message: str


@router.post("/create-playlist", response_model=CreatePlaylistResponse)
def create_playlist(req: CreatePlaylistRequest) -> CreatePlaylistResponse:
    resolved: list[PlaylistTrack] = []
    for title in req.tracks:
        try:
            hits = _audius_get("/tracks/search", {"query": title})
        except (httpx.HTTPError, ValueError, KeyError):
            hits = []
        if hits:
            t = _map_track(hits[0])
            resolved.append(PlaylistTrack(title=t.title, artist=t.artist, url=t.url))

    owner = req.user_name or "you"
    msg = (
        f"🎧 Curated '{req.playlist_name}' with {len(resolved)} track(s) for {owner}. "
        "Each link opens the track on Audius."
    )
    return CreatePlaylistResponse(
        name=req.playlist_name,
        owner=owner,
        track_count=len(resolved),
        tracks=resolved,
        status="CREATED",
        message=msg,
    )
