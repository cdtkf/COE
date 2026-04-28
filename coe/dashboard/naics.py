"""
naics.py — NAICS 2-digit sector code → title mapping.

NAICS (North American Industry Classification System) codes are 6 digits.
The first 2 digits identify the sector. There are 20 sectors and they're
stable — the official list is published by the U.S. Census Bureau and
revised only every 5 years.

We use this mapping for the dashboard's sector-rollup view: instead of
showing 200+ individual NAICS codes, we group by sector so the user
gets a readable high-level picture.

Source: https://www.census.gov/naics/ (2022 revision, current through 2027).
Codes 31-33 all map to "Manufacturing" and 44-45 to "Retail Trade" and
48-49 to "Transportation and Warehousing" — those are intentionally
duplicated so SUBSTR(naics_code, 1, 2) always finds a match.
"""

NAICS_SECTORS: dict[str, str] = {
    "11": "Agriculture, Forestry, Fishing and Hunting",
    "21": "Mining, Quarrying, and Oil and Gas Extraction",
    "22": "Utilities",
    "23": "Construction",
    "31": "Manufacturing",
    "32": "Manufacturing",
    "33": "Manufacturing",
    "42": "Wholesale Trade",
    "44": "Retail Trade",
    "45": "Retail Trade",
    "48": "Transportation and Warehousing",
    "49": "Transportation and Warehousing",
    "51": "Information",
    "52": "Finance and Insurance",
    "53": "Real Estate and Rental and Leasing",
    "54": "Professional, Scientific, and Technical Services",
    "55": "Management of Companies and Enterprises",
    "56": "Administrative and Support and Waste Management Services",
    "61": "Educational Services",
    "62": "Health Care and Social Assistance",
    "71": "Arts, Entertainment, and Recreation",
    "72": "Accommodation and Food Services",
    "81": "Other Services (except Public Administration)",
    "92": "Public Administration",
}


def sector_title(naics_code: str | None) -> str:
    """
    Return the sector title for a NAICS code.

    Falls back to "(unknown sector)" if the code is missing, malformed,
    or its 2-digit prefix isn't in our mapping (which would indicate
    bad data, not a missing sector).
    """
    if not naics_code or len(naics_code) < 2:
        return "(unknown sector)"
    return NAICS_SECTORS.get(naics_code[:2], "(unknown sector)")


def sector_label(prefix: str) -> str:
    """
    Format a 2-digit prefix as a chart label, e.g. '54' → '54 — Professional...'.
    Used by the dashboard so the y-axis is human-readable.
    """
    title = NAICS_SECTORS.get(prefix, "(unknown)")
    return f"{prefix} — {title}"
