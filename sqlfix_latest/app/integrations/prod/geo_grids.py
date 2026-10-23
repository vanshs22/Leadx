"""
Statewide / metro geo grids for high-volume ICPs.

Example: "New Jersey med spa" → every major NJ city + county seat + zip clusters
so continuous discovery can pull 10,000+ unique businesses across the state.
"""
from __future__ import annotations

import re
from typing import Optional

# Full-state grids (label strings used in Serper Maps / organic queries)
STATE_GRIDS: dict[str, list[str]] = {
    "new jersey": [
        # North NJ
        "Hoboken NJ", "Jersey City NJ", "Newark NJ", "Paterson NJ", "Elizabeth NJ",
        "Clifton NJ", "Passaic NJ", "Union City NJ", "Bayonne NJ", "East Orange NJ",
        "Hackensack NJ", "Paramus NJ", "Fort Lee NJ", "Englewood NJ", "Teaneck NJ",
        "Morristown NJ", "Wayne NJ", "Montclair NJ", "Bloomfield NJ", "West New York NJ",
        "Secaucus NJ", "Weehawken NJ", "North Bergen NJ", "Livingston NJ", "Summit NJ",
        "Short Hills NJ", "Millburn NJ", "Maplewood NJ", "South Orange NJ", "Westfield NJ",
        "Cranford NJ", "Rahway NJ", "Linden NJ", "Plainfield NJ", "Bound Brook NJ",
        "Bridgewater NJ", "Somerville NJ", "Flemington NJ",
        # Central NJ
        "New Brunswick NJ", "Edison NJ", "Woodbridge NJ", "Perth Amboy NJ",
        "Piscataway NJ", "East Brunswick NJ", "North Brunswick NJ", "Somerset NJ",
        "Princeton NJ", "Trenton NJ", "Hamilton NJ", "Lawrenceville NJ",
        "Freehold NJ", "Middletown NJ", "Red Bank NJ", "Long Branch NJ",
        "Asbury Park NJ", "Toms River NJ", "Brick NJ", "Lakewood NJ",
        "Old Bridge NJ", "Sayreville NJ", "South Amboy NJ",
        # South NJ
        "Camden NJ", "Cherry Hill NJ", "Voorhees NJ", "Marlton NJ", "Mount Laurel NJ",
        "Moorestown NJ", "Burlington NJ", "Willingboro NJ",
        "Atlantic City NJ", "Vineland NJ", "Millville NJ", "Bridgeton NJ",
        "Glassboro NJ", "Washington Township NJ", "Sewell NJ",
        "Cape May NJ", "Ocean City NJ", "Wildwood NJ",
        # County-level sweeps (catch rural / suburban)
        "Bergen County NJ", "Hudson County NJ", "Essex County NJ", "Passaic County NJ",
        "Morris County NJ", "Union County NJ", "Somerset County NJ", "Middlesex County NJ",
        "Monmouth County NJ", "Ocean County NJ", "Mercer County NJ", "Burlington County NJ",
        "Camden County NJ", "Gloucester County NJ", "Atlantic County NJ",
    ],
    "new york": [
        "Manhattan NY", "Brooklyn NY", "Queens NY", "Bronx NY", "Staten Island NY",
        "Upper East Side NY", "Upper West Side NY", "Midtown Manhattan", "SoHo NY",
        "Williamsburg Brooklyn", "Park Slope Brooklyn", "Astoria NY", "Flushing NY",
        "Long Island City NY", "Downtown Brooklyn", "Bay Ridge Brooklyn",
        "Yonkers NY", "White Plains NY", "New Rochelle NY", "Mount Vernon NY",
        "Hempstead NY", "Huntington NY", "Brookhaven NY", "Islip NY",
        "Buffalo NY", "Rochester NY", "Syracuse NY", "Albany NY",
    ],
    "california": [
        "Los Angeles CA", "San Francisco CA", "San Diego CA", "San Jose CA",
        "Sacramento CA", "Oakland CA", "Fresno CA", "Long Beach CA",
        "Santa Monica CA", "Beverly Hills CA", "Pasadena CA", "Irvine CA",
        "Anaheim CA", "Santa Ana CA", "Riverside CA", "San Bernardino CA",
        "Palo Alto CA", "Mountain View CA", "Sunnyvale CA", "Fremont CA",
        "Berkeley CA", "Daly City CA", "San Mateo CA", "Redwood City CA",
        "Malibu CA", "Venice CA", "Culver City CA", "Glendale CA", "Burbank CA",
        "Orange County CA", "Silicon Valley CA",
    ],
    "texas": [
        "Houston TX", "Dallas TX", "Austin TX", "San Antonio TX", "Fort Worth TX",
        "El Paso TX", "Arlington TX", "Plano TX", "Frisco TX", "McKinney TX",
        "Irving TX", "Garland TX", "Grand Prairie TX", "Amarillo TX", "Lubbock TX",
        "Corpus Christi TX", "Round Rock TX", "Cedar Park TX", "The Woodlands TX",
        "Sugar Land TX", "Pearland TX", "Katy TX", "Pasadena TX",
    ],
    "florida": [
        "Miami FL", "Miami Beach FL", "Fort Lauderdale FL", "West Palm Beach FL",
        "Boca Raton FL", "Orlando FL", "Tampa FL", "St Petersburg FL",
        "Jacksonville FL", "Naples FL", "Sarasota FL", "Clearwater FL",
        "Coral Gables FL", "Aventura FL", "Hollywood FL", "Brickell FL",
        "Winter Park FL", "Kissimmee FL",
    ],
}

# Aliases → canonical state key
STATE_ALIASES = {
    "nj": "new jersey",
    "new jersey": "new jersey",
    "jersey": "new jersey",
    "ny": "new york",
    "new york": "new york",
    "new york state": "new york",
    "ca": "california",
    "california": "california",
    "tx": "texas",
    "texas": "texas",
    "fl": "florida",
    "florida": "florida",
}


def detect_state(location: str) -> Optional[str]:
    loc = (location or "").lower().strip()
    if loc in STATE_ALIASES:
        return STATE_ALIASES[loc]
    for alias, key in STATE_ALIASES.items():
        if alias in loc and len(alias) > 2:
            return key
    # "Newark NJ" → not a full-state request unless explicit
    return None


def is_state_level_icp(locations: list[str]) -> bool:
    """True when ICP is statewide (e.g. ['New Jersey'] or ['NJ'])."""
    if len(locations) == 1:
        return detect_state(locations[0]) is not None
    # all locations are state aliases
    return all(detect_state(l) for l in locations) and len(locations) <= 2


def expand_state_grid(locations: list[str]) -> list[str]:
    """
    If user passes 'New Jersey', return full NJ city/county grid.
    Otherwise expand known metros + keep originals.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        k = label.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(label)

    for loc in locations:
        state = detect_state(loc)
        if state and state in STATE_GRIDS:
            for cell in STATE_GRIDS[state]:
                add(cell)
            # also keep the state-level query itself for organic
            add(f"{state.title()}")
        else:
            add(loc)

    return out


def recommend_cells_for_target(target_new_leads: int, n_available: int) -> int:
    """How many geo cells to scan this run for a volume target."""
    if target_new_leads >= 8000:
        return min(n_available, 80)
    if target_new_leads >= 3000:
        return min(n_available, 50)
    if target_new_leads >= 1000:
        return min(n_available, 35)
    if target_new_leads >= 500:
        return min(n_available, 25)
    return min(n_available, 15)


def recommend_target(locations: list[str], explicit: int = 0) -> int:
    """Default target when ICP is statewide and user didn't set one."""
    if explicit and explicit > 0:
        return explicit
    if is_state_level_icp(locations):
        return 5000  # aggressive default for "New Jersey med spa"
    return 0  # use classic discovery
