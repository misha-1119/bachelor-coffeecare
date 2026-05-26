"""Fuzzy matcher for user-entered coffee machine model strings.

Uses stdlib difflib so no extra dependency. Returns top-N suggestions from a
known-brand corpus (e.g. ['philips', 'delonghi', 'jura', ...]) combined with
common model templates.
"""

import re
from difflib import get_close_matches


_COMMON_MODELS = {
    "philips": ["Philips EP2231", "Philips EP3243", "Philips EP5447", "Philips Series 5400", "Philips LatteGo 5400"],
    "delonghi": ["DeLonghi Magnifica S", "DeLonghi Magnifica Evo", "DeLonghi Dinamica", "DeLonghi Eletta"],
    "jura": ["Jura E8", "Jura ENA 8", "Jura S8", "Jura Z10"],
    "saeco": ["Saeco PicoBaristo", "Saeco Xelsis", "Saeco Lirika"],
    "krups": ["Krups EA8108", "Krups EA9010", "Krups Evidence"],
    "siemens": ["Siemens EQ.6", "Siemens EQ.500", "Siemens EQ.9"],
    "bosch": ["Bosch VeroAroma", "Bosch VeroCup"],
    "melitta": ["Melitta Caffeo Solo", "Melitta Barista TS"],
}


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", "_", text)
    return text


def detect_brand(user_text: str, known_brands: list[str] | None = None) -> str | None:
    """Return matched brand key (lowercase) or None.

    Tries substring match first, then fuzzy on the first token.
    """
    t = user_text.strip().lower()
    if not t:
        return None
    brands = list(known_brands) if known_brands else list(_COMMON_MODELS.keys())
    brands = [b.lower() for b in brands]
    for b in sorted(brands, key=len, reverse=True):
        if b in t:
            return b
    first_token = re.split(r"[\s_]+", t)[0]
    matches = get_close_matches(first_token, brands, n=1, cutoff=0.75)
    return matches[0] if matches else None


def suggest_models(
    user_text: str,
    n: int = 3,
    known_brands: list[str] | None = None,
) -> list[str]:
    """Return up to N display-formatted model suggestions for user_text.

    Strategy:
    1. detect brand from input
    2. if brand known, fuzzy-match the rest against that brand's catalog
    3. otherwise, fuzzy against the flat list of all known models
    """
    text = user_text.strip()
    if not text:
        return []

    brand = detect_brand(text, known_brands=known_brands)
    pool: list[str]
    if brand and brand in _COMMON_MODELS:
        pool = _COMMON_MODELS[brand]
    else:
        pool = [m for models in _COMMON_MODELS.values() for m in models]

    needle = text.lower()
    scored: list[tuple[float, str]] = []

    for m in pool:
        m_l = m.lower()
        if needle in m_l:
            scored.append((1.0, m))
            continue
        ratio_matches = get_close_matches(needle, [m_l], n=1, cutoff=0.0)
        if ratio_matches:
            from difflib import SequenceMatcher
            score = SequenceMatcher(None, needle, m_l).ratio()
            if score >= 0.35:
                scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    out: list[str] = []
    for _, m in scored:
        if m not in seen:
            seen.add(m)
            out.append(m)
        if len(out) >= n:
            break
    return out


def normalize_model_slug(text: str) -> str:
    """Slug for persistence: 'Philips EP5447' -> 'philips_ep5447'."""
    s = _slugify(text)
    return s[:60] or "universal"
