"""agency.prospect — Google Places (New), never scraping.

AGENCY.md 2.1: scraping search results breaks Google's terms, the layout
moves under you, and the IP gets blocked. The Places API is the supported
path and returns structured data, billed per request — which is why every
call here goes through a hard monthly cap (`AGENCY_PLACES_MONTHLY_CAP`,
default 500) tracked in `agency_spend`, and the budget check reports
`Health.UNKNOWN` at the cap rather than pretending the pipeline is idle.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from ...kernel.contracts import ActionResult, Health, Observation
from .._http import explain, post_json
from . import _store

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.websiteUri,places.rating,"
    "places.userRatingCount,places.location,places.primaryType,"
    "places.businessStatus"
)


@dataclass(frozen=True)
class Place:
    place_id: str
    name: str
    category: str
    address: str | None
    phone: str | None
    website: str | None
    rating: float | None
    reviews: int | None
    lat: float | None
    lng: float | None
    # OPERATIONAL | CLOSED_TEMPORARILY | CLOSED_PERMANENTLY, as Places says;
    # None when it said nothing. `verify.py` cross-checks it.
    business_status: str | None = None


def cap() -> int:
    """Read fresh every call, like `executor.watch_only` — flipping the env
    var is a restart, not a code change, and a cap frozen at import would
    need one anyway to take effect."""
    raw = os.getenv("AGENCY_PLACES_MONTHLY_CAP", "500")
    try:
        return int(raw)
    except ValueError:
        return 500


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


async def spend_this_month() -> int:
    return await _store.spend_this_month(_month())


async def charge(n: int = 1) -> None:
    await _store.charge(_month(), n)


_EARTH_RADIUS_KM = 6371.0


def _distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def _name_of(raw: dict) -> str:
    display = raw.get("displayName")
    if isinstance(display, dict):
        return str(display.get("text") or "")
    return str(display or "")


async def search_places(
    query: str, *, lat: float, lng: float, radius_m: int,
) -> tuple[list[Place], str | None]:
    """Text Search, New API. Never raises; a shape we did not expect comes
    back as `(..., reason)` rather than a Place with an invented number."""
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key:
        return [], "GOOGLE_PLACES_API_KEY not set"

    data, reason = await post_json(
        PLACES_SEARCH_URL,
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK},
        json_body={
            "textQuery": query,
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": radius_m,
                }
            },
            "maxResultCount": 20,
        },
    )
    if reason is not None:
        return [], reason
    if not isinstance(data, dict):
        return [], f"expected a Places object, got {type(data).__name__}"

    raw_places = data.get("places", [])
    if not isinstance(raw_places, list):
        return [], "Places response 'places' was not a list — the assumed shape is wrong"

    places: list[Place] = []
    for raw in raw_places:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue                       # no place_id, nothing to key a lead on
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        places.append(Place(
            place_id=raw["id"],
            name=_name_of(raw),
            category=raw.get("primaryType") or "unknown",
            address=raw.get("formattedAddress"),
            phone=raw.get("nationalPhoneNumber"),
            website=raw.get("websiteUri"),
            rating=raw.get("rating"),          # None stays None, never 0.0
            reviews=raw.get("userRatingCount"),
            lat=location.get("latitude"),
            lng=location.get("longitude"),
            business_status=raw.get("businessStatus") or None,
        ))
    return places, None


LOCATE_FIELD_MASK = "places.location,places.formattedAddress,places.displayName"


async def locate(
    area: str, *, near: tuple[float, float, int] | None = None,
) -> tuple[dict | None, str | None]:
    """Where a place the owner NAMED is, read from Places -- never a coordinate a
    model wrote. One Text Search for the area itself ("Astoria"), biased
    toward `near` (AGENCY_HOME_AREA) when there is one, so "Astoria" is the
    one near him. Charged against the same monthly cap as a prospect call.

    Returns `({"label", "lat", "lng", "read_at"}, None)` or `(None, why)`.
    A read, like a collector's: it stores nothing but the spend."""
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key:
        return None, "GOOGLE_PLACES_API_KEY not set"
    try:
        used = await spend_this_month()
    except Exception as exc:                            # noqa: BLE001
        return None, f"could not read this month's Places spend: {explain(exc)}"
    if used >= cap():
        return None, f"Places budget exhausted: {used}/{cap()} requests this month"
    body: dict = {"textQuery": area, "maxResultCount": 1}
    if near is not None:
        lat, lng, radius_m = near
        body["locationBias"] = {"circle": {
            "center": {"latitude": lat, "longitude": lng},
            "radius": float(min(max(radius_m, 1000), 50000))}}
    data, reason = await post_json(
        PLACES_SEARCH_URL,
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": LOCATE_FIELD_MASK},
        json_body=body)
    await charge(1)
    if reason is not None:
        return None, f"Places search failed: {reason}"
    places = data.get("places") if isinstance(data, dict) else None
    if not isinstance(places, list) or not places or not isinstance(places[0], dict):
        return None, f"Places found nothing called {area!r}"
    first = places[0]
    location = first.get("location") if isinstance(first.get("location"), dict) else {}
    lat, lng = location.get("latitude"), location.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return None, f"Places returned {area!r} without a location"
    return {"label": first.get("formattedAddress") or _name_of(first) or area,
            "lat": float(lat), "lng": float(lng),
            "read_at": datetime.now(UTC).isoformat()}, None


async def check_prospect_budget() -> Observation:
    try:
        used = await spend_this_month()
    except Exception as exc:                            # noqa: BLE001
        # Unread is unknown, never a zero that reads as "budget untouched".
        return Observation(
            "prospect_budget", Health.UNKNOWN, None, "requests",
            f"could not read this month's Places spend: {explain(exc)}")
    limit = cap()
    if used >= limit:
        return Observation(
            "prospect_budget", Health.UNKNOWN, float(used), "requests",
            f"Places budget exhausted: {used}/{limit} requests this month — "
            "no more prospecting until next month or the cap is raised")
    return Observation(
        "prospect_budget", Health.OK, float(used), "requests",
        f"{used}/{limit} Places requests used this month")


# --------------------------------------------------------------- the action

async def prospect_action(query: str, lat: float, lng: float, radius_m: int) -> ActionResult:
    used = await spend_this_month()
    limit = cap()
    if used >= limit:
        return ActionResult(
            False, f"Places budget exhausted: {used}/{limit} requests this month")

    if not os.getenv("GOOGLE_PLACES_API_KEY"):
        # Nothing reached Google, so nothing should count against the
        # monthly budget — charging here would spend the cap on a config
        # error instead of a request.
        return ActionResult(False, "GOOGLE_PLACES_API_KEY not set")

    places, reason = await search_places(query, lat=lat, lng=lng, radius_m=radius_m)
    await charge(1)
    if reason is not None:
        return ActionResult(False, f"Places search failed: {reason}")

    lead_ids: list[int] = []
    for place in places:
        distance_km = (_distance_km(lat, lng, place.lat, place.lng)
                       if place.lat is not None and place.lng is not None
                       else None)
        lead_id = await _store.insert_lead(place, distance_km=distance_km)
        if lead_id is not None:
            lead_ids.append(lead_id)

    return ActionResult(
        True,
        f"{len(lead_ids)} new lead(s) from {len(places)} result(s) for {query!r}",
        evidence={"lead_ids": lead_ids, "query": query},
        rollback_token=",".join(str(i) for i in lead_ids),
    )


async def prospect_dry(query: str, lat: float, lng: float, radius_m: int) -> ActionResult:
    used = await spend_this_month()
    limit = cap()
    remaining = max(0, limit - used)
    if remaining <= 0:
        return ActionResult(
            False,
            f"would query Places for {query!r}, but the monthly budget is "
            f"exhausted ({used}/{limit})")
    return ActionResult(
        True,
        f"would query Places for {query!r} near ({lat}, {lng}) within "
        f"{radius_m}m; {remaining} request(s) left this month")


async def prospect_rollback(token: str) -> ActionResult:
    ids = [int(x) for x in token.split(",") if x]
    if not ids:
        return ActionResult(True, "nothing to roll back")
    await _store.mark_dropped(ids)
    return ActionResult(True, f"marked {len(ids)} lead(s) dropped: {ids}")
