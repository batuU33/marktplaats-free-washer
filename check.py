#!/usr/bin/env python3
"""
Watch Marktplaats for FREE washing machines within 10 km of
Canvas Living Brainpark (K.P. van der Mandelelaan 130, 3062 MB Rotterdam)
and push new finds to ntfy.
"""
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------- config ---
POSTCODE = os.getenv("POSTCODE", "3062MB")          # Canvas Brainpark
RADIUS_M = int(os.getenv("RADIUS_M", "10000"))       # 10 km
QUERIES = [q.strip() for q in os.getenv(
    "QUERIES", "wasmachine,wasmachines,washing machine,was-droogcombinatie"
).split(",") if q.strip()]

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC")                 # required
NTFY_TOKEN = os.getenv("NTFY_TOKEN")                 # optional (private topics)

SEEN_FILE = Path(os.getenv("SEEN_FILE", Path(__file__).with_name("seen.json")))
SEEN_MAX = 2000                                      # keep file small

# Titles containing these are wanted-ads, parts, or repair services
EXCLUDE = ["gezocht", "gevraagd", "zoek", "onderdeel", "onderdelen", "reparatie",
           "repareren", "pomp", "koolborstel", "manchet", "rubber",
           "afvoerslang", "wanted", "ophalen van uw", "wij halen", "opkoop",
           "inkoop", "gratis opgehaald"]
# At least one of these must appear in title or description
MUST_HAVE = ["wasmachine", "washing machine", "was-droog", "wasdroog", "washer"]

# --- condition filter: skip anything described as broken or noisy ----------
# Regexes, matched against title + full description (lowercased).
DEFECT_PATTERNS = [
    # broken / not working
    r"defect", r"kapot", r"(is|zijn|ging|gaat|raakte?|was)\s+stuk\b", r"\bstuk\s+gegaan", r"werkt\s+niet", r"werkt\s+niet\s+meer",
    r"doet\s+het\s+niet", r"doet\s+(het\s+)?niks", r"niet\s+(meer\s+)?werkend",
    r"voor\s+onderdelen", r"voor\s+de\s+onderdelen", r"opknapper",
    r"handige\s+klusser", r"te\s+repareren", r"moet\s+gerepareerd",
    r"\bstoring(en)?\b", r"foutcode", r"error\s*code", r"\berror\b",
    r"lekt", r"lekkage", r"lek\b", r"centrifugeert\s+niet", r"pompt\s+niet",
    r"slingert\s+niet", r"trommel\s+draait\s+niet", r"wordt\s+niet\s+warm",
    r"verwarmt\s+niet", r"gaat\s+niet\s+aan", r"start\s+niet",
    r"deur\s+(gaat\s+)?niet\s+(open|dicht)", r"blijft\s+(staan|hangen)",
    r"broken", r"not\s+working", r"doesn'?t\s+work", r"does\s+not\s+work",
    r"faulty", r"for\s+parts", r"\bleak", r"needs\s+repair",
    # noise
    r"lawaai", r"herrie", r"kabaal", r"rammel", r"ratel", r"bonk", r"klappert",
    r"piept", r"piepen", r"kraakt", r"krakend", r"gromt", r"brom",
    r"(veel|raar|rare|vreemd|vreemde|hard|harde|luid|luide)\s+geluid",
    r"maakt\s+(?!geen\b|weinig\b|nauwelijks\b|niet\b)(\w+\s+)?geluid", r"geluid\s+bij\s+(het\s+)?centrifuge",
    r"lagers?\s+(is\s+|zijn\s+)?(kapot|versleten|op|stuk)\b",
    r"trilt\s+(erg|veel|hevig)", r"loopt\s+weg\s+tijdens",
    r"noisy", r"\bloud\b", r"strange\s+noise", r"weird\s+noise", r"makes\s+(a\s+)?noise",
    r"rattl", r"squeak", r"bang(s|ing)\b",
    r"bearings?\s+(are\s+|is\s+)?(gone|worn|broken|bad|shot)",
]
# A match is ignored when one of these appears just before it
# ("geen rare geluiden", "niet defect", "no strange noises", "nooit gelekt")
NEGATIONS = {"geen", "niet", "nooit", "zonder", "nauwelijks", "weinig", "no",
             "not", "never", "without", "zero", "hardly"}
_DEFECT_RE = [re.compile(p) for p in DEFECT_PATTERNS]

API = "https://www.marktplaats.nl/lrp/api/search"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "nl-NL,nl;q=0.9",
}


# ------------------------------------------------------------- marktplaats ---
def search(query: str) -> list[dict]:
    params = {
        "query": query,
        "postcode": POSTCODE,
        "distanceMeters": RADIUS_M,
        "limit": 100,
        "offset": 0,
        "sortBy": "SORT_INDEX",          # newest first
        "sortOrder": "DECREASING",
        "viewOptions": "list-view",
    }
    r = requests.get(API, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json().get("listings", [])


def is_free(item: dict) -> bool:
    price = item.get("priceInfo") or {}
    if price.get("priceType") == "FREE":
        return True
    # some sellers pick "fixed price €0" instead of "Gratis"
    if price.get("priceType") == "FIXED" and price.get("priceCents", 1) == 0:
        return True
    return False


def is_washer(item: dict) -> bool:
    text = f"{item.get('title', '')} {item.get('description', '')}".lower()
    title = item.get("title", "").lower()
    if any(w in title for w in EXCLUDE):
        return False
    return any(w in text for w in MUST_HAVE)


def _negated(text: str, start: int) -> bool:
    """True if a negation word appears within the 3 words before `start`."""
    before = re.findall(r"[\w']+", text[max(0, start - 40):start])[-3:]
    return any(w in NEGATIONS for w in before)


def defect_reason(text: str) -> str | None:
    """Return the matching phrase if the text describes a defect/noise, else None."""
    text = text.lower()
    for rx in _DEFECT_RE:
        for m in rx.finditer(text):
            # "werkt niet" style patterns carry their own "niet"; only look before them
            if not _negated(text, m.start()):
                return m.group(0)
    return None


def condition_attr(item: dict) -> str:
    """Marktplaats' own condition field, e.g. 'Gebruikt' or 'Niet werkend'."""
    vals = [str(a.get("value", "")) for a in item.get("attributes") or []
            if str(a.get("key", "")).lower() in ("condition", "conditie", "staat")]
    return " ".join(vals).lower()


def full_description(item: dict) -> str:
    """Search results only contain a short snippet, so fetch the listing page
    and pull the full description. Falls back to the snippet on any error."""
    snippet = item.get("description") or ""
    try:
        r = requests.get(listing_url(item), headers={**HEADERS, "Accept": "text/html"},
                         timeout=20)
        r.raise_for_status()
        page = r.text
        texts = []
        # JSON-LD product data
        for block in re.findall(
                r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S):
            try:
                data = json.loads(block)
                for d in data if isinstance(data, list) else [data]:
                    if isinstance(d, dict) and d.get("description"):
                        texts.append(d["description"])
            except json.JSONDecodeError:
                pass
        # Description container / meta fallback
        m = re.search(r'class="[^"]*Description-description[^"]*"[^>]*>(.*?)</div>',
                      page, re.S)
        if m:
            texts.append(re.sub(r"<[^>]+>", " ", m.group(1)))
        m = re.search(r'<meta[^>]+(?:property="og:description"|name="description")'
                      r'[^>]+content="([^"]*)"', page)
        if m:
            texts.append(m.group(1))
        full = html.unescape(" ".join(texts)).strip()
        return full if len(full) > len(snippet) else snippet
    except Exception as e:
        print(f"[warn] couldn't fetch full description for {item.get('itemId')}: {e}",
              file=sys.stderr)
        return snippet


def is_working(item: dict) -> tuple[bool, str | None]:
    if any(w in condition_attr(item) for w in ("niet werkend", "defect", "kapot")):
        return False, "condition: niet werkend"
    text = f"{item.get('title', '')} {item.get('_full_description') or item.get('description', '')}"
    reason = defect_reason(text)
    return reason is None, reason


def within_radius(item: dict) -> bool:
    dist = (item.get("location") or {}).get("distanceMeters")
    # Marktplaats returns -1/None when unknown; the API already filtered by radius
    return dist is None or dist < 0 or dist <= RADIUS_M


def listing_url(item: dict) -> str:
    vip = item.get("vipUrl", "")
    return vip if vip.startswith("http") else f"https://www.marktplaats.nl{vip}"


def picture(item: dict) -> str | None:
    pics = item.get("pictures") or []
    if pics:
        p = pics[0]
        return p.get("extraExtraLargeUrl") or p.get("largeUrl") or p.get("mediumUrl")
    return None


# ------------------------------------------------------------------- ntfy ---
def notify(item: dict) -> None:
    loc = item.get("location") or {}
    dist = loc.get("distanceMeters")
    dist_txt = f"{dist / 1000:.1f} km" if isinstance(dist, (int, float)) and dist >= 0 else "? km"
    city = loc.get("cityName", "")
    desc = (item.get("_full_description") or item.get("description") or "")
    desc = " ".join(desc.split())
    body = f"📍 {city} · {dist_txt}\n{desc[:250]}"

    url = listing_url(item)
    payload = {
        "topic": NTFY_TOPIC,
        "title": (t if (t := item.get("title", "wasmachine")).lower().startswith("gratis")
                  else f"Gratis: {t}")[:200],
        "message": body,
        "click": url,
        "tags": ["gift", "droplet"],
        "priority": 4,
        "actions": [{"action": "view", "label": "Open Marktplaats", "url": url}],
    }
    if pic := picture(item):
        payload["attach"] = pic if pic.startswith("http") else f"https:{pic}"
    headers = {"Authorization": f"Bearer {NTFY_TOKEN}"} if NTFY_TOKEN else {}

    # JSON publishing (POST to server root) handles emoji/accents safely
    r = requests.post(NTFY_SERVER, json=payload, headers=headers, timeout=15)
    r.raise_for_status()


# ------------------------------------------------------------------ state ---
def load_seen() -> list[str]:
    try:
        return json.loads(SEEN_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_seen(seen: list[str]) -> None:
    SEEN_FILE.write_text(json.dumps(seen[-SEEN_MAX:], indent=0) + "\n")


# ------------------------------------------------------------------- main ---
def main() -> int:
    if not NTFY_TOPIC:
        print("NTFY_TOPIC is not set", file=sys.stderr)
        return 1

    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    seen_set = set(seen)
    found: dict[str, dict] = {}

    for q in QUERIES:
        try:
            for item in search(q):
                iid = item.get("itemId")
                if iid and is_free(item) and is_washer(item) and within_radius(item):
                    found[iid] = item
        except Exception as e:  # keep going if one query fails
            print(f"[warn] query '{q}' failed: {e}", file=sys.stderr)
        time.sleep(2)

    new = [it for iid, it in found.items() if iid not in seen_set]
    print(f"{len(found)} free washers in range, {len(new)} new")

    if first_run:
        # Don't spam on the very first run: just remember what's already there
        print("First run: storing current listings without notifying.")
        if os.getenv("NOTIFY_ON_FIRST_RUN") == "1":
            first_run = False

    for item in new:
        if not first_run:
            # Check condition using the full description from the listing page
            item["_full_description"] = full_description(item)
            time.sleep(1)
            ok, reason = is_working(item)
            if not ok:
                print(f"  skipped (looks defective/noisy: '{reason}'): "
                      f"{item.get('title')}  {listing_url(item)}")
                seen.append(item["itemId"])
                continue
            try:
                notify(item)
                print(f"  notified: {item.get('title')}  {listing_url(item)}")
            except Exception as e:
                print(f"[warn] ntfy failed for {item.get('itemId')}: {e}", file=sys.stderr)
                continue  # retry next run
        seen.append(item["itemId"])

    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
