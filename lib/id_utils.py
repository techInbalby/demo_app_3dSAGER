"""Building-ID normalization helpers.

CityJSON 1.1 uses BAG-style identifiers (`NL.IMBAG.Pand.0518100000271783-0`),
CityJSON 2.0 uses `bag_<id>` prefixes, the pipeline writes raw numeric ids
(`0518100000271783`), and various intermediate files mix the three. Every
route that does "look up a building by id" has to be resilient to all of
them — this module is the single source of truth for the conversion.
"""
import re
from typing import List, Optional

# Match the 10+ digit BAG numeric — the one stable component every variant
# of a building id includes somewhere.
_NUMERIC_RE = re.compile(r'(\d{10,})')


def extract_numeric_id(building_id) -> Optional[str]:
    """Pull the BAG numeric out of any of the common id variants.

    Examples:
        'bag_0518100000271783'            -> '0518100000271783'
        'NL.IMBAG.Pand.0518100000271783-0' -> '0518100000271783'
        '0518100000271783'                 -> '0518100000271783'
        'no_digits_here'                   -> None
        ''                                 -> None
    """
    if building_id is None:
        return None
    match = _NUMERIC_RE.search(str(building_id))
    return match.group(1) if match else None


def id_variants(building_id) -> List[str]:
    """Return all common spellings of `building_id` for resilient lookups.

    Use when iterating over potential dict keys (`for v in id_variants(bid):
    if v in some_dict: ...`). Includes the raw string + the bag-prefixed
    form + the BAG-2.0 dot-prefixed form + the bare numeric. Order is
    most-specific first."""
    if building_id is None:
        return []
    raw = str(building_id)
    out: List[str] = [raw]
    numeric = extract_numeric_id(raw)
    if numeric and numeric != raw:
        out.append(numeric)
        out.append(f'bag_{numeric}')
        out.append(f'NL.IMBAG.Pand.{numeric}-0')
        out.append(f'NL.IMBAG.Pand.{numeric}')
    elif numeric == raw:
        out.append(f'bag_{numeric}')
        out.append(f'NL.IMBAG.Pand.{numeric}-0')
        out.append(f'NL.IMBAG.Pand.{numeric}')
    # Deduplicate while preserving order.
    seen, deduped = set(), []
    for v in out:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def numeric_ids_match(a, b) -> bool:
    """True if `a` and `b` refer to the same BAG building (regardless of
    which prefix variant each one uses)."""
    na, nb = extract_numeric_id(a), extract_numeric_id(b)
    return na is not None and na == nb
