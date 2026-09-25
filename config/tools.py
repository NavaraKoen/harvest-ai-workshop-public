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

async def _search_flights(origin: str, destination: str, month: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{_SERVICES_BASE_URL}/services/search-flights",
            json={"origin": origin, "destination": destination, "month": month},
        )
        r.raise_for_status()
        return json.dumps(r.json())


async def _select_hotel(city: str, month: str, max_budget_per_night_eur: int = 200) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{_SERVICES_BASE_URL}/services/select-hotel",
            json={"city": city, "month": month, "max_budget_per_night_eur": max_budget_per_night_eur},
        )
        r.raise_for_status()
        return json.dumps(r.json())


async def _book_flight(
    flight_id: str,
    passenger_name: str,
    origin: str,
    destination: str,
    month: str,
    hotel_name: str = "",
) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{_SERVICES_BASE_URL}/services/book-flight",
            json={
                "flight_id": flight_id,
                "passenger_name": passenger_name,
                "origin": origin,
                "destination": destination,
                "month": month,
                "hotel_name": hotel_name,
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
    "search_flights": ToolDefinition(
        name="search_flights",
        description="Search for available flights between two cities in a given month. Make sure to ask the user for both the departure and the destination city.",
        parameters={
            "origin":      {"type": "string", "description": "Departure city"},
            "destination": {"type": "string", "description": "Destination city"},
            "month":       {"type": "string", "description": "Travel month"},
        },
        require_confirmation=False,
        handler=_search_flights,
    ),
    "select_hotel": ToolDefinition(
        name="select_hotel",
        description="Find available hotels in the destination city filtered by budget.",
        parameters={
            "city":                    {"type": "string",  "description": "Destination city"},
            "month":                   {"type": "string",  "description": "Travel month"},
            "max_budget_per_night_eur": {"type": "integer", "description": "Maximum price per night in EUR"},
        },
        require_confirmation=False,
        handler=_select_hotel,
    ),
    "book_flight": ToolDefinition(
        name="book_flight",
        description="Confirm and book a selected flight (and optionally a hotel).",
        parameters={
            "flight_id":      {"type": "string", "description": "Flight ID from search_flights result"},
            "passenger_name": {"type": "string", "description": "Full name of the passenger"},
            "origin":         {"type": "string", "description": "Departure city"},
            "destination":    {"type": "string", "description": "Destination city"},
            "month":          {"type": "string", "description": "Travel month"},
            "hotel_name":     {"type": "string", "description": "Hotel name to include in booking (optional, use empty string if none)"},
        },
        require_confirmation=True,
        handler=_book_flight,
    ),
}
