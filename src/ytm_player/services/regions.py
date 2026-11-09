"""YouTube Music chart region list.

YouTube's ``get_charts`` response includes a ``countries.options`` field
listing every region code the endpoint accepts (currently 62, including
``ZZ`` = Global). That's the authoritative source — region codes are
ISO 3166-1 alpha-2 (two letters), no locale suffixes ever. Locale-style
codes like ``ES-ES`` or ``en-GB`` cause YouTube to silently fall back
to Global; callers should normalise via ``normalise_region`` before
passing to ``get_charts``.

We also keep six entries (Hong Kong, Malaysia, Singapore, Taiwan,
Thailand, Vietnam) that don't appear in YouTube's advertised list but
have historically returned chart data when queried.

Whether a given code returns rich data, partial data, or falls back to
Global varies by account, time, and YouTube's editorial choices — the
UI surfaces "No chart data available for X" if the response is empty,
so users can self-discover which regions work for them.

Layout: ``ZZ`` (Global) first as the default, then alphabetical by
display name.
"""

from __future__ import annotations

CHART_REGIONS: tuple[tuple[str, str], ...] = (
    ("ZZ", "Global"),
    ("AR", "Argentina"),
    ("AU", "Australia"),
    ("AT", "Austria"),
    ("BE", "Belgium"),
    ("BO", "Bolivia"),
    ("BR", "Brazil"),
    ("CA", "Canada"),
    ("CL", "Chile"),
    ("CO", "Colombia"),
    ("CR", "Costa Rica"),
    ("CZ", "Czechia"),
    ("DK", "Denmark"),
    ("DO", "Dominican Republic"),
    ("EC", "Ecuador"),
    ("EG", "Egypt"),
    ("SV", "El Salvador"),
    ("EE", "Estonia"),
    ("FI", "Finland"),
    ("FR", "France"),
    ("DE", "Germany"),
    ("GT", "Guatemala"),
    ("HN", "Honduras"),
    ("HK", "Hong Kong"),
    ("HU", "Hungary"),
    ("IS", "Iceland"),
    ("IN", "India"),
    ("ID", "Indonesia"),
    ("IE", "Ireland"),
    ("IL", "Israel"),
    ("IT", "Italy"),
    ("JP", "Japan"),
    ("KE", "Kenya"),
    ("LU", "Luxembourg"),
    ("MY", "Malaysia"),
    ("MX", "Mexico"),
    ("NL", "Netherlands"),
    ("NZ", "New Zealand"),
    ("NI", "Nicaragua"),
    ("NG", "Nigeria"),
    ("NO", "Norway"),
    ("PA", "Panama"),
    ("PY", "Paraguay"),
    ("PE", "Peru"),
    ("PL", "Poland"),
    ("PT", "Portugal"),
    ("RO", "Romania"),
    ("RU", "Russia"),
    ("SA", "Saudi Arabia"),
    ("RS", "Serbia"),
    ("SG", "Singapore"),
    ("ZA", "South Africa"),
    ("KR", "South Korea"),
    ("ES", "Spain"),
    ("SE", "Sweden"),
    ("CH", "Switzerland"),
    ("TW", "Taiwan"),
    ("TZ", "Tanzania"),
    ("TH", "Thailand"),
    ("TR", "Turkey"),
    ("UG", "Uganda"),
    ("UA", "Ukraine"),
    ("AE", "United Arab Emirates"),
    ("GB", "United Kingdom"),
    ("US", "United States"),
    ("UY", "Uruguay"),
    ("VN", "Vietnam"),
    ("ZW", "Zimbabwe"),
)


def normalise_region(value: str) -> str:
    """Normalise a bare region code or a language-territory locale pair.

    YouTube's chart endpoint accepts only ISO 3166-1 alpha-2 codes.
    Locale-style values like ``ES-ES`` or ``en-GB`` fall through to
    Global silently — bad UX. Use the territory component, so ``en-GB``
    becomes ``GB`` and ``es_MX`` becomes ``MX``. Other inputs keep the
    previous uppercased-prefix behavior; this is not country validation.
    """
    if not value:
        return value
    head, separator, territory = value.strip().replace("_", "-").partition("-")
    if (
        separator
        and len(head) in (2, 3)
        and head.isascii()
        and head.isalpha()
        and len(territory) == 2
        and territory.isascii()
        and territory.isalpha()
    ):
        return territory.upper()
    return head.strip().upper()
