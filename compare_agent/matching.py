"""Decides whether a listing on a reference site is the same product as the supplier's.

EAN match wins outright; a different EAN rejects. Otherwise titles are compared
with fuzzy matching, but "key tokens" (capacities, model numbers, Roman-numeral
generations, colours) must agree — 990 PRO 1TB must not match 990 PRO 2TB, and
Major IV must not match Major V.
"""
from __future__ import annotations

import re
from typing import Optional

from rapidfuzz import fuzz

from .loader import normalize_ean
from .models import Offer, Product

ROMAN = {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}
# words that mark a different model line (990 PRO vs 990 EVO Plus): must agree between name and listing
VARIANT_WORDS = {"pro", "plus", "evo", "max", "mini", "ultra", "lite", "air", "neo", "fe", "edge", "slim",
                 "creator", "anc", "heatsink"}

# colour words (English / Bulgarian / Italian / German) -> canonical colour
COLOURS = {
    "black": "black", "черен": "black", "черна": "black", "черно": "black", "черни": "black",
    "nero": "black", "schwarz": "black",
    "white": "white", "бял": "white", "бяла": "white", "бяло": "white", "бели": "white",
    "bianco": "white", "weiss": "white", "weiß": "white",
    "blue": "blue", "син": "blue", "синя": "blue", "синьо": "blue", "сини": "blue", "blu": "blue", "blau": "blue",
    "brown": "brown", "кафяв": "brown", "кафява": "brown", "кафяво": "brown", "кафяви": "brown",
    "marrone": "brown", "braun": "brown",
    "cream": "cream", "кремав": "cream", "кремава": "cream", "кремаво": "cream", "кремави": "cream",
    "crema": "cream", "creme": "cream",
    "red": "red", "червен": "red", "червена": "red", "червено": "red", "rosso": "red", "rot": "red",
    "green": "green", "зелен": "green", "зелена": "green", "зелено": "green", "verde": "green", "grün": "green",
    "pink": "pink", "розов": "pink", "розова": "pink", "розово": "pink",
    "grey": "grey", "gray": "grey", "сив": "grey", "сива": "grey", "сиво": "grey", "grau": "grey",
    "silver": "silver", "сребрист": "silver", "сребриста": "silver", "сребристо": "silver",
    "navy": "blue", "beige": "cream", "ivory": "cream", "graphite": "grey", "purple": "purple",
    "violet": "purple", "yellow": "yellow", "orange": "orange", "gold": "gold", "golden": "gold",
}

# Bulgarian colour adjectives in all forms (masc. / fem. / neut. / plural): "тъмносин" = navy, "бежов" = beige
_BG_COLOUR_FORMS = {
    "black": ["черен", "черна", "черно", "черни"],
    "white": ["бял", "бяла", "бяло", "бели"],
    "blue": ["син", "синя", "синьо", "сини", "тъмносин", "тъмносиня", "тъмносиньо", "тъмносини",
             "светлосин", "светлосиня", "светлосиньо", "светлосини"],
    "cream": ["бежов", "бежова", "бежово", "бежови", "кремав", "кремава", "кремаво", "кремави"],
    "brown": ["кафяв", "кафява", "кафяво", "кафяви"],
    "grey": ["сив", "сива", "сиво", "сиви", "графит", "графитен", "графитена", "графитено", "графитени"],
    "red": ["червен", "червена", "червено", "червени", "бордо"],
    "green": ["зелен", "зелена", "зелено", "зелени"],
    "pink": ["розов", "розова", "розово", "розови"],
    "purple": ["лилав", "лилава", "лилаво", "лилави", "виолетов", "виолетова", "виолетово", "виолетови"],
    "yellow": ["жълт", "жълта", "жълто", "жълти"],
    "orange": ["оранжев", "оранжева", "оранжево", "оранжеви"],
    "gold": ["златист", "златиста", "златисто", "златисти"],
    "silver": ["сребристи"],
}
for _colour, _forms in _BG_COLOUR_FORMS.items():
    COLOURS.update({form: _colour for form in _forms})

# Google Shopping titles from other countries (French, Spanish, Dutch, Polish, Romanian, Greek)
_OTHER_COLOUR_FORMS = {
    "black": ["noir", "noire", "negro", "negra", "zwart", "czarny", "czarne", "negru", "neagra", "μαύρο", "μαύρα"],
    "white": ["blanc", "blanche", "blanco", "blanca", "wit", "witte", "biały", "białe", "alb", "alba", "λευκό"],
    "blue": ["bleu", "azul", "blauw", "niebieski", "niebieskie", "albastru", "μπλε"],
    "brown": ["brun", "marron", "marrón", "bruin", "brązowy", "brązowe", "maro", "καφέ"],
    "cream": ["crème", "crema", "kremowy", "kremowe", "κρεμ"],
    "red": ["rouge", "rojo", "roja", "rood", "czerwony", "roșu", "rosu", "κόκκινο"],
    "green": ["vert", "verte", "groen", "zielony", "verde"],
    "grey": ["gris", "grijs", "szary", "gri", "γκρι"],
    "pink": ["rose", "rosa", "roze", "różowy", "roz", "ροζ"],
    "silver": ["argent", "plata", "zilver", "srebrny", "argintiu", "ασημί"],
}
for _colour, _forms in _OTHER_COLOUR_FORMS.items():
    COLOURS.update({form: _colour for form in _forms if form not in COLOURS})

NBSP = "\N{NO-BREAK SPACE}"
INCH_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(\"|''|\N{RIGHT DOUBLE QUOTATION MARK}|\N{DOUBLE PRIME}|-?\s?inch\b)", re.I)
UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(tb|gb|mb|to|go|mo|mm|mp|w|mah|hz|ghz|k|g(?=\s|$))\b", re.I)
# French téraoctet / gigaoctet / mégaoctet; "128G" on memory cards
UNIT_ALIASES = {"to": "tb", "go": "gb", "mo": "mb", "g": "gb"}
# a microSD card "+ SD adapter" is the normal retail pack, not a bundle
ADAPTER_RE = re.compile(r"(\+|с|with|и)\s*(sd\s*)?(адаптер|adapter)", re.I)
SPEC_UNIT_RE = re.compile(r"^\d+(\.\d+)?(tb|gb|mm|mp|mah|inch)$")
KIT_RE = re.compile(r"(\+|\bkit\b|комплект|\bbundle\b|\d+-\d+\s*mm)", re.I)
# Listings for accessories that name the product they fit ("mount for GoPro Max", "pads for Marshall Major V")
ACCESSORY_RE = re.compile(
    r"\b(аксесоар|accessor|lens mod|housing|gehäuse|schutzgehäuse|корпус|защитн|mount|holder|стойк|държач|калъф|case\b|cover\b|кейс|кабел|cable|подложк|батери|battery|зарядн|charger|"
    r"протектор|protector|стъкло|screen glass|strap|каишк|coussinet|ear ?pads|наушник|накрайни|tripod|статив|"
    r"сенник|lens hood|филтър|filter\b|дистанционно|remote\b|чанта|bag\b|sticker|стикер|резервн|replacement)",
    re.I)
# Second-hand offers (marketplaces, Google Shopping) are not the market price for a new product
USED_RE = re.compile(
    r"\b(used|refurbished|renewed|pre-?owned|open box|second hand|2nd hand|b-ware|gebraucht|zustand|"
    r"generalüberholt|reconditionn[ée]e?s?|d'occasion|occasion|état neuf|comme neuf|usato|ricondizionat[oa]|"
    r"reacondicionad[oa]|segunda mano|como nuevo|używan[ya]|odnowion[ya]|refurbish|recondiționat|"
    r"second-hand|употребяван[аои]?|рециклиран|μεταχειρισμένο)", re.I)
FITS_RE =r"\b(for|за|für|pour|per|compatible|kompatibel|съвместим\w*)\b"
UNIT_WORDS = r"tb|gb|mb|mm|mp|w|mah|hz|ghz|k|inch|бр|pcs|pack|x"   # "MAX 2" vs "2 TB" / "pack of 2"
MOQ_RE = re.compile(r"\(?\bmoq\b[^)]*\)?", re.I)
GEN_RE = re.compile(r"\b(?:gen\.?\s?|g\.\s?)(\d)\b", re.I)   # Gen.2 / Gen 2 / Gen2 / G.2
PLUS_RE = re.compile(r"(\w)\+(?=\s|$|[,.;:)])")   # "Canvas Select+" (not "R10+RF-S", which is a kit)
# connector / form-factor specs that shops often leave out of the title
SOFT_SPECS = {"3.5mm", "2.5mm", "6.3mm", "2.5inch", "3.5inch"}
# title score used when brand + every model number are confirmed (penalties still apply after it)
MODEL_CONFIRMED_FLOOR = 0.85

# Subtracted from the title score when the listing has a different EAN (settings: scoring.ean_mismatch_penalty).
# 1.0 makes EAN mismatches a hard reject.
EAN_MISMATCH_PENALTY = 0.15

# words that only describe/pad a title and should not affect matching
NOISE = {"the", "and", "with", "for", "s", "з", "с", "за", "и", "на", "нов", "new", "body", "тяло",
         "wireless", "безжични", "слушалки", "headphones", "camera", "камера", "фотоапарат",
         "action", "екшън", "digital", "цифров"}


def normalize(text: str) -> str:
    text = text.lower().replace(NBSP, " ")
    text = GEN_RE.sub(r" gen\1 ", text)  # -> "gen2"
    text = INCH_RE.sub(lambda m: " " + m.group(1).replace(",", ".") + "inch ", text)
    text = UNIT_RE.sub(lambda m: m.group(1).replace(",", ".")
                       + UNIT_ALIASES.get(m.group(2).lower(), m.group(2).lower()), text)
    text = re.sub(r"[^\w\s/+.-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return [t.strip(".") for t in re.split(r"[\s/+(),\-]+", normalize(text)) if t and t not in NOISE]


def _transitions(chunk: str) -> int:
    chars = [c for c in chunk if c.isalnum()]
    return sum(1 for a, b in zip(chars, chars[1:]) if a.isdigit() != b.isdigit())


def _is_part_number(chunk: str) -> bool:
    chunk = chunk.strip("()[],;").lower()
    return len(chunk) >= 5 and _transitions(chunk) >= 2 and not UNIT_RE.fullmatch(chunk)


def part_numbers(text: str) -> set[str]:
    """Manufacturer part numbers like SDCS3/128GB, SSDPR-CX400-01T-G2, 6FW06A (shops often omit them)."""
    return {re.sub(r"\W", "", c.lower()) for c in text.split() if _is_part_number(c)}


def key_tokens(text: str, skip_part_numbers: bool = False) -> set[str]:
    """Tokens that identify a specific variant: anything with a digit, or a Roman numeral."""
    if skip_part_numbers:
        text = " ".join(c for c in text.split() if not _is_part_number(c))
    return {t for t in tokens(text) if any(c.isdigit() for c in t) or t in ROMAN}


def _for_ratio(text: str) -> str:
    """Normalized text with colour words translated ("черни" == "black") and "45mm" written as "45"."""
    return " ".join(COLOURS.get(t, re.sub(r"^(\d+)mm$", r"\1", t)) for t in tokens(text))


def colours(text: str) -> set[str]:
    return {COLOURS[t] for t in tokens(text) if t in COLOURS}


def _compact(text: str) -> str:
    return re.sub(r"\W", "", normalize(text))


def _present(token: str, title_tokens: set[str], compact_title: str) -> bool:
    if token in title_tokens:
        return True
    if token in ROMAN:
        return False  # Roman numerals must match as whole words (V is not IV)
    if token.endswith("mm") and token[:-2] in title_tokens:
        return True   # lens ranges are often written "18-45" without the unit
    # "hero12" vs "HERO 12", "4l" vs "F4L" — but "r6" must not match "r60"
    return re.search(rf"(?<!\d){re.escape(_compact(token))}(?!\d)", compact_title) is not None


SHORT_NAME_FILLER = {"wireless", "headphones", "on-ear", "over-ear", "action", "camera", "body", "usa", "e",
                     "getta", "foto", "ssd", "nvme", "wlan", "mesh", "router", "jack", "pitch", "edition"}


def broad_query(product: Product) -> str:
    """Brand + model code only ("jbl t110", "goodram cx400", "kodak charmera"): a last try for shops whose
    search needs every word to match. The matcher then picks the right variant from the results."""
    words = tokens(short_name(product, 8))
    if not words:
        return ""
    brand, rest = words[0], words[1:]
    alnum = [i for i, w in enumerate(rest) if re.fullmatch(r"[a-z]+\d+[a-z\d]*", w)]   # T110, CX400, R6
    numbers = [i for i, w in enumerate(rest) if re.fullmatch(r"\d{3,}", w)]          # 990, 2000
    line = lambda upto: [w for w in rest[:upto] if w.isalpha() and len(w) >= 3  # noqa: E731
                         and w not in COLOURS and w not in VARIANT_WORDS][:1]
    variants = [w for w in rest if w in VARIANT_WORDS][:1]
    if alnum or numbers:
        i = (alnum or numbers)[0]
        return " ".join([brand] + line(i) + [rest[i]] + variants)
    return " ".join([brand] + line(len(rest)) + variants)


def short_name(product: Product, max_words: int = 5) -> str:
    """Brand + model words only, e.g. 'marshall major v' — a broad search query and the Semrush keyword."""
    name = MOQ_RE.sub(" ", product.name).split(" + ")[0]   # kits: the main item
    name = re.sub(r"\bSATA\s+III\b", " ", name, flags=re.I)
    words = []
    for w in name.split():
        lw = w.lower().strip(",()")
        if not lw or '"' in lw or _is_part_number(lw) or lw in COLOURS or lw in SHORT_NAME_FILLER:
            continue
        words.append(lw)
    return " ".join(words[:max_words])


def _ean_eq(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b) and a.lstrip("0") == b.lstrip("0")


def score_match(product: Product, offer: Offer) -> tuple[float, str]:
    """Return (score 0..1, reason)."""
    offer_ean = normalize_ean(offer.ean)
    if product.ean and offer_ean:
        if _ean_eq(product.ean, offer_ean):
            return 1.0, "EAN match"
        # Some brands (e.g. Canon) use region-specific EANs for the same item, so a different EAN
        # costs a penalty instead of rejecting outright; colour/capacity/generation checks still apply.
        title_score, reason = _title_score(product, offer)
        if title_score == 0.0:
            return 0.0, f"different EAN ({offer_ean}), {reason}"
        return round(max(0.0, title_score - EAN_MISMATCH_PENALTY), 3), f"different EAN ({offer_ean}), {reason}"
    return _title_score(product, offer)


def _title_score(product: Product, offer: Offer) -> tuple[float, str]:
    name = PLUS_RE.sub(r"\1 Plus", MOQ_RE.sub(" ", product.name))  # drop "(MOQ 528)"; "Select+" -> "Select Plus"
    title = PLUS_RE.sub(r"\1 Plus", offer.title)
    title_tokens = set(tokens(title))
    compact_title = _compact(title)

    # the full manufacturer part number in the listing (SSDPR-CX400-01T-G2) identifies the item almost as
    # surely as the EAN, even when the title leaves words out ("Gen.2"); colours must still agree
    long_pns = [pn for pn in part_numbers(name) if len(pn) >= 8]
    if long_pns and any(pn in compact_title for pn in long_pns):
        p_col, o_col = colours(name), colours(title)
        if not (p_col and o_col and not (p_col & o_col)):
            return 0.95, "part number match"

    required = key_tokens(name, skip_part_numbers=True) - SOFT_SPECS
    # aliases sharing a number ("Tune 110 T110"): any one of the group is enough
    groups: dict[str, set[str]] = {}
    for k in required:
        core = re.sub(r"\D", "", k) if re.fullmatch(r"[a-z]*\d+", k) else k
        groups.setdefault(core, set()).add(k)
    missing = {k for g in groups.values() if not any(_present(k, title_tokens, compact_title) for k in g)
               for k in g}
    if missing:
        return 0.0, f"missing {', '.join(sorted(missing))}"
    # a confirmed model number + brand: the title is right even if worded very differently
    # real model codes only: letters+digits (T110, R7, CX400) or 3+ digit numbers (990, 5690) - not "27" exposures
    model_confirmed = any(re.fullmatch(r"[a-z]+\d+[a-z\d]*|\d{3,}", k) and not SPEC_UNIT_RE.match(k)
                          for k in required)

    # Roman numerals in the listing that the product lacks; "V-NAND" (memory type) is not a generation
    roman_in_title = set(re.findall(r"\b(ii|iii|iv|v|vi|vii|viii|ix|x)\b(?!-)", normalize(title)))
    extra_roman = roman_in_title - set(tokens(name))
    if extra_roman:
        return 0.0, f"different generation ({', '.join(sorted(extra_roman))})"
    # "GoPro MAX 2" is not "GoPro MAX": a small number right after the last model word = next generation
    words = [w for w in tokens(short_name(product)) if w.isalpha()]
    if words:
        nxt = re.search(rf"\b{re.escape(words[-1])}\s+(\d{{1,2}})\b(?![.,]?\d|\s*(?:{UNIT_WORDS}))",
                        normalize(title))
        if nxt and nxt.group(1) not in set(tokens(name)):
            return 0.0, f"different generation ({words[-1]} {nxt.group(1)})"

    name_tokens = set(tokens(name))
    p_var, o_var = name_tokens & VARIANT_WORDS, title_tokens & VARIANT_WORDS
    if p_var - o_var:
        return 0.0, f"missing {', '.join(sorted(p_var - o_var))}"

    p_col, o_col = colours(name), colours(title)
    if p_col and o_col and not (p_col & o_col):
        return 0.0, f"colour {'/'.join(sorted(o_col))} vs {'/'.join(sorted(p_col))}"

    # full name, or just brand + model when the supplier name carries extra words ("usa e getta ... foto")
    ratio = max(fuzz.token_set_ratio(_for_ratio(name), _for_ratio(title)),
                fuzz.token_set_ratio(_for_ratio(short_name(product)), _for_ratio(title))) / 100
    score, notes = ratio, [f"title {ratio:.0%}"]
    brand_in_title = (product.brand or name.split()[0]).lower() in normalize(title)
    if model_confirmed and brand_in_title and score < MODEL_CONFIRMED_FLOOR:
        score = MODEL_CONFIRMED_FLOOR
        notes.append("model no. + brand confirmed")

    if product.brand and product.brand.lower() not in normalize(title):
        score -= 0.15
        notes.append("brand not in title")

    # every model word of the product ("major" in Marshall Major IV) should appear in the listing
    brand = (product.brand or name.split()[0]).lower()
    model_words = [w for w in tokens(short_name(product)) if w.isalpha() and len(w) >= 3
                   and w != brand and w not in COLOURS]
    missing_words = [w for w in model_words if not _present(w, title_tokens, compact_title)]
    if missing_words:
        has_keys = bool(key_tokens(name, skip_part_numbers=True))
        score -= (0.1 if has_keys else 0.2) * len(missing_words)
        notes.append(f"no {', '.join(missing_words)}")

    used = USED_RE.search(title)
    if used and not USED_RE.search(name):
        return 0.0, f"used / refurbished ({used.group(0).strip()})"

    accessory = ACCESSORY_RE.search(title)
    fits = re.search(FITS_RE + r"\s+(\w+\s+){0,3}" + re.escape(brand), title, re.I)
    if (accessory and not ACCESSORY_RE.search(name)) or fits:
        score -= 0.5
        notes.append(f"accessory? ({(accessory or fits).group(0).strip()})")
    else:
        # "HERO13 Black/HERO12 Black/HERO11": several generations of the model named = a compatibility list
        for k in required:
            fam = re.fullmatch(r"([a-z]{2,})\d+", k)
            if fam:
                family = set(re.findall(rf"\b{fam.group(1)}\s?(\d+)\b", normalize(title)))
                if len(family) >= 2:
                    score -= 0.5
                    notes.append(f"accessory? (fits {fam.group(1)} {'/'.join(sorted(family))})")
                    break

    plain_title = ADAPTER_RE.sub(" ", title)
    if KIT_RE.search(plain_title) and not KIT_RE.search(name):
        score -= 0.3
        notes.append("bundle/kit")
    elif plain_title.count("+") > name.count("+"):  # "R10 + 18-150mm + RF 55-210": more items than ours
        score -= 0.3
        notes.append("bundle/kit (extra item)")
    if re.search(r"\d+set\b|\bset of\b|\bпълен комплект\b", title, re.I) and not re.search(r"\bset\b", name, re.I):
        score -= 0.3
        notes.append("set")
    if o_var - p_var:
        score -= 0.3
        notes.append(f"other variant ({', '.join(sorted(o_var - p_var))})")
    extra_units = {t for t in title_tokens if SPEC_UNIT_RE.match(t)} - set(tokens(name))
    if extra_units:
        score -= min(0.3, 0.1 * len(extra_units))
        notes.append(f"extra {', '.join(sorted(extra_units))}")
    if any(m in compact_title for m in part_numbers(name)):
        score += 0.1
        notes.append("part number match")
    return round(max(0.0, min(1.0, score)), 3), ", ".join(notes)


def match_offers(product: Product, offers: list[Offer], threshold: float) -> list[Offer]:
    """Score every offer and keep the ones at or above the threshold."""
    kept = []
    for offer in offers:
        offer.match_score, offer.match_reason = score_match(product, offer)
        if offer.match_score >= threshold:
            kept.append(offer)
    return kept
