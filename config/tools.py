import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, Optional

import httpx

_SERVICES_BASE_URL = os.getenv("SERVICES_BASE_URL", "http://localhost:8000")


@dataclass
class ToolDefinition:
    name: str
    description: str
    # JSON-Schema-style parameter descriptions
    parameters: Dict[str, Dict[str, str]]
    require_confirmation: bool = True
    # Async callable — calls the services API
    handler: Optional[Callable[..., Coroutine]] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Async HTTP handlers — each delegates to the /services/* API endpoints
# ---------------------------------------------------------------------------

async def _search_tracks(query: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{_SERVICES_BASE_URL}/services/search-tracks",
            json={"query": query},
        )
        r.raise_for_status()
        return json.dumps(r.json())


async def _get_recommendations(genre: str = "", mood: str = "", limit: int = 5) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{_SERVICES_BASE_URL}/services/get-recommendations",
            json={"genre": genre, "mood": mood, "limit": limit},
        )
        r.raise_for_status()
        return json.dumps(r.json())


async def _create_playlist(
    playlist_name: str,
    tracks: Optional[list] = None,
    user_name: str = "",
) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{_SERVICES_BASE_URL}/services/create-playlist",
            json={
                "playlist_name": playlist_name,
                "tracks": tracks or [],
                "user_name": user_name,
            },
        )
        r.raise_for_status()
        return json.dumps(r.json())


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS: Dict[str, ToolDefinition] = {
    "final_message": ToolDefinition(
        name="final_message",
        description="Show a final message to the user and end the chat. Put the message in the response message field.",
        parameters={},
        require_confirmation=False,
    ),
    "search_tracks": ToolDefinition(
        name="search_tracks",
        description="Search the Audius music catalog for tracks by song title or artist name. Returns real, playable Audius track links.",
        parameters={
            "query": {"type": "string", "description": "Song title or artist to search for"},
        },
        require_confirmation=False,
        handler=_search_tracks,
    ),
    "get_recommendations": ToolDefinition(
        name="get_recommendations",
        description="Get trending track recommendations from Audius for a given genre and/or mood. Example genres: pop, rock, hip-hop, electronic, lo-fi, jazz, classical, country, r&b, latin. Example moods: happy, energetic, party, chill, relaxed, focus, sad, romantic.",
        parameters={
            "genre": {"type": "string", "description": "Music genre to seed recommendations (optional)"},
            "mood":  {"type": "string", "description": "Desired mood/vibe (optional)"},
            "limit": {"type": "integer", "description": "Number of tracks to return (1-10, default 5)"},
        },
        require_confirmation=False,
        handler=_get_recommendations,
    ),
    "create_playlist": ToolDefinition(
        name="create_playlist",
        description="Build a shareable, curated list of real Audius track links from a list of selected track titles.",
        parameters={
            "playlist_name": {"type": "string", "description": "Name for the new playlist"},
            "tracks":        {"type": "array",  "description": "List of track titles to add to the playlist"},
            "user_name":     {"type": "string", "description": "Name of the user who owns the playlist (optional, use empty string if unknown)"},
        },
        require_confirmation=True,
        handler=_create_playlist,
    ),
}
