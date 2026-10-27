"""Trade tags applied to every bid from its title and category.

Cladding is the only tag. Bare "panel" is not enough: road, electrical,
and solar panel work stay untagged. "Vinyl siding" matches because of siding.
"""

from __future__ import annotations

import re

_CLADDING = re.compile(
    r"(?i)(?:"
    r"\bcurtain\s+walls?\b|"
    r"\b(?:insulated\s+)?metal\s+panels?\b|"
    r"\bcomposite\s+panels?\b|"
    r"\bacm(?:\s+panels?)?\b|"
    r"\bfib(?:er|re)\s+cement\b|"
    r"\bbrick\s+veneers?\b|"
    r"\bmasonry\s+veneers?\b|"
    r"\bexterior\s+masonry\b|"
    r"\b(?:exterior|building)\s+envelope\b|"
    r"\bwindow\s+replacements?\b|"
    r"\breplacement\s+windows?\b|"
    r"\breplace(?:ment\s+of)?\s+windows?\b|"
    r"\brainscreens?\b|"
    r"\bstorefronts?\b|"
    r"\bcladding\b|"
    r"\bsiding\b|"
    r"\beifs\b|"
    r"\bstucco\b|"
    r"\bfa(?:c|ç)ades?\b"
    r")"
)


def cladding_trade(*parts: str | None) -> str | None:
    text = " ".join(part or "" for part in parts)
    if _CLADDING.search(text):
        return "cladding"
    return None
