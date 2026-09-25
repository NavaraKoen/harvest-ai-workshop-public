"""
Travel agent service endpoints.

These contain the actual (fake-but-realistic) business logic that was previously
inlined in config/tools.py. Splitting them here means they can be replaced with
real third-party API calls without touching the Temporal layer.
"""
import json
import random
import string

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/services", tags=["services"])

# ---------------------------------------------------------------------------
# Shared price helpers (same logic as before, lives here now)
# ---------------------------------------------------------------------------

_ROUTE_PRICES: dict[str, int] = {
    "amsterdam-barcelona": 120, "amsterdam-rome": 140, "amsterdam-paris": 80,
    "amsterdam-tokyo": 680,     "amsterdam-new york": 420, "amsterdam-bangkok": 560,
    "london-barcelona": 90,     "london-rome": 110,  "london-paris": 60,
    "london-tokyo": 700,        "london-new york": 380, "london-bangkok": 540,
    "berlin-barcelona": 100,    "berlin-rome": 95,   "berlin-paris": 75,
}

_MONTH_FACTOR: dict[str, float] = {
    "january": 0.8, "february": 0.8, "march": 1.0, "april": 1.1,
    "may": 1.2, "june": 1.4, "july": 1.5, "august": 1.5,
    "september": 1.2, "october": 1.0, "november": 0.85, "december": 1.3,
}


def _base_price(origin: str, destination: str, month: str) -> int:
    key = f"{origin.lower()}-{destination.lower()}"
    reverse = f"{destination.lower()}-{origin.lower()}"
    base = _ROUTE_PRICES.get(key) or _ROUTE_PRICES.get(reverse) or random.randint(150, 900)
    return int(base * _MONTH_FACTOR.get(month.lower(), 1.0))


# ---------------------------------------------------------------------------
# /services/search-flights
# ---------------------------------------------------------------------------

class SearchFlightsRequest(BaseModel):
    origin: str
    destination: str
    month: str


class FlightOption(BaseModel):
    flight_id: str
    airline: str
    origin: str
    destination: str
    month: str
    price_eur: int
    duration_hours: float
    stops: int


class SearchFlightsResponse(BaseModel):
    flights: list[FlightOption]


_AIRLINES = [
    ("KLM",        "KL",  0,   1.00),
    ("Lufthansa",  "LH",  50,  1.05),
    ("Ryanair",    "FR", -60,  0.75),
    ("EasyJet",    "U2", -40,  0.80),
    ("Air France", "AF",  30,  0.98),
]


@router.post("/search-flights", response_model=SearchFlightsResponse)
def search_flights(req: SearchFlightsRequest) -> SearchFlightsResponse:
    base = _base_price(req.origin, req.destination, req.month)
    flights = []
    for airline, code, offset, factor in _AIRLINES:
        price = max(30, int((base + offset) * factor) + random.randint(-20, 20))
        flights.append(FlightOption(
            flight_id=f"{code}{random.randint(100, 9999)}",
            airline=airline,
            origin=req.origin,
            destination=req.destination,
            month=req.month,
            price_eur=price,
            duration_hours=round(random.uniform(1.5, 13.0), 1),
            stops=0 if price > base * 0.85 else 1,
        ))
    flights.sort(key=lambda f: f.price_eur)
    return SearchFlightsResponse(flights=flights)


# ---------------------------------------------------------------------------
# /services/select-hotel
# ---------------------------------------------------------------------------

class SelectHotelRequest(BaseModel):
    city: str
    month: str
    max_budget_per_night_eur: int = Field(default=200, ge=0)


class HotelOption(BaseModel):
    hotel_id: str
    name: str
    city: str
    stars: int
    price_per_night_eur: int
    rating: float
    within_budget: bool


class SelectHotelResponse(BaseModel):
    hotels: list[HotelOption]


@router.post("/select-hotel", response_model=SelectHotelResponse)
def select_hotel(req: SelectHotelRequest) -> SelectHotelResponse:
    factor = _MONTH_FACTOR.get(req.month.lower(), 1.0)
    tiers = [
        (f"{req.city} Grand Palace",   5, int(280 * factor)),
        (f"Hotel {req.city} Central",  4, int(160 * factor)),
        (f"{req.city} Boutique Stay",  4, int(130 * factor)),
        (f"City Inn {req.city}",       3, int(90  * factor)),
        (f"{req.city} Budget Hostel",  2, int(45  * factor)),
    ]
    hotels = []
    for name, stars, base_price in tiers:
        price = base_price + random.randint(-10, 10)
        hotels.append(HotelOption(
            hotel_id=f"H{''.join(random.choices(string.digits, k=4))}",
            name=name,
            city=req.city,
            stars=stars,
            price_per_night_eur=price,
            rating=round(random.uniform(6.5 + stars * 0.3, 6.5 + stars * 0.5), 1),
            within_budget=price <= req.max_budget_per_night_eur,
        ))
    return SelectHotelResponse(hotels=hotels)


# ---------------------------------------------------------------------------
# /services/book-flight
# ---------------------------------------------------------------------------

class BookFlightRequest(BaseModel):
    flight_id: str
    passenger_name: str
    origin: str
    destination: str
    month: str
    hotel_name: str = ""


class BookFlightResponse(BaseModel):
    booking_reference: str
    status: str
    flight_id: str
    passenger_name: str
    route: str
    month: str
    hotel: str = ""
    message: str


@router.post("/book-flight", response_model=BookFlightResponse)
def book_flight(req: BookFlightRequest) -> BookFlightResponse:
    ref = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    msg = f"✈ Flight booked! Your reference is {ref}. Have a great trip to {req.destination}!"
    if req.hotel_name:
        msg += f" We also reserved {req.hotel_name} for you."
    return BookFlightResponse(
        booking_reference=ref,
        status="CONFIRMED",
        flight_id=req.flight_id,
        passenger_name=req.passenger_name,
        route=f"{req.origin} → {req.destination}",
        month=req.month,
        hotel=req.hotel_name,
        message=msg,
    )
