"""Which organizations belong to the covered towns.

Town names and regions come from ``sources.json``. A COMMBUYS organization
matches a covered town, that town's school district, housing authority, or
water district, or a regional school that serves one of the towns. A similar
name such as Framingham State University does not match. Light plants and
statewide agencies do not match on the town name appearing in a description.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Regional districts are not town bid pages. They are included when the
# organization name contains one of these phrases. Region is MetroWest core
# when a core town is a member, and Next ring otherwise.
REGIONAL = (
    ("assabet valley", "Assabet Valley", "MetroWest core"),
    ("keefe technical", "Keefe Tech", "MetroWest core"),
    ("keefe tech", "Keefe Tech", "MetroWest core"),
    ("south middlesex regional vocational", "Keefe Tech", "MetroWest core"),
    ("lincoln sudbury", "Lincoln-Sudbury", "MetroWest core"),
    ("algonquin regional", "Algonquin Regional", "MetroWest core"),
    ("northborough southborough", "Algonquin Regional", "MetroWest core"),
    ("nashoba regional", "Nashoba Regional", "Next ring"),
    ("tri county regional", "Tri-County", "Next ring"),
    ("blackstone valley", "Blackstone Valley Tech", "MetroWest core"),
    ("minuteman regional", "Minuteman", "Next ring"),
    ("dover sherborn", "Dover-Sherborn", "Next ring"),
    ("acton boxborough", "Acton-Boxborough", "Next ring"),
    ("mendon upton", "Mendon-Upton", "Next ring"),
)

_RELATED_SUFFIXES = (
    "public schools",
    "public school district",
    "public school department",
    "school district",
    "school department",
    "housing authority",
    "water district",
    "community development authority",
)


class Place:
    def __init__(self, town: str, region: str, kind: str, sector: str = "town"):
        self.town = town
        self.region = region
        self.kind = kind
        self.sector = sector

    def __repr__(self) -> str:
        return f"Place({self.town!r}, {self.region!r}, {self.kind!r}, {self.sector!r})"


def town_regions_from_config(config: dict | None = None) -> dict[str, str]:
    if config is None:
        config = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    regions: dict[str, str] = {}
    for source in config["sources"]:
        regions.setdefault(source["town"], source["region"])
    return regions


def match_organization(org: str, town_regions: dict[str, str]) -> Place | None:
    cleaned = _clean(org)
    if not cleaned:
        return None
    towns = sorted(town_regions, key=len, reverse=True)
    for town in towns:
        name = _clean(town)
        region = town_regions[town]
        if cleaned in {name, f"town of {name}", f"city of {name}"}:
            return Place(town, region, "municipality", "town")
        if _related(cleaned, name):
            return Place(town, region, "related", _sector_from_name(cleaned))
    for phrase, town, region in REGIONAL:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", cleaned):
            return Place(town, region, "regional", "school")
    return None


def match_location(text: str, town_regions: dict[str, str]) -> Place | None:
    """Match a location field only when the whole value is one covered town.

    A project description that mentions a town is not a location.
    """
    cleaned = _clean(text)
    if not cleaned:
        return None
    towns = sorted(town_regions, key=len, reverse=True)
    for town in towns:
        name = _clean(town)
        if cleaned in {name, f"town of {name}", f"city of {name}"}:
            return Place(town, town_regions[town], "location")
    return None


def match_row(row: dict, town_regions: dict[str, str]) -> Place | None:
    place = match_organization(row.get("Organization Name") or "", town_regions)
    if place:
        return place
    for key, value in row.items():
        if "location" in key.casefold() and value:
            place = match_location(str(value), town_regions)
            if place:
                return place
    return None


def _related(cleaned: str, name: str) -> bool:
    bases = (name, f"town of {name}", f"city of {name}")
    for base in bases:
        for suffix in _RELATED_SUFFIXES:
            exact = f"{base} {suffix}"
            if cleaned == exact or cleaned.startswith(exact + " "):
                return True
    extras = (
        f"public schools of {name}",
        f"housing authority of {name}",
        f"housing authority of the town of {name}",
        f"housing authority of the city of {name}",
        f"water district of {name}",
    )
    return cleaned in extras or any(cleaned.startswith(item + " ") for item in extras)


def _sector_from_name(cleaned: str) -> str:
    if "housing authority" in cleaned or "community development authority" in cleaned:
        return "housing"
    if "school" in cleaned:
        return "school"
    if "water" in cleaned:
        return "utility"
    return "town"


def _clean(text: str) -> str:
    cleaned = text.replace("&", " and ")
    cleaned = cleaned.replace("-", " ")
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned.casefold())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned.startswith("the "):
        cleaned = cleaned[4:].strip()
    cleaned = re.sub(r"\s+(ma|massachusetts)$", "", cleaned).strip()
    return cleaned
