#!/usr/bin/env python3
"""
Porsche 996 Kleinanzeigen Scanner
Scannt täglich neue Angebote und sendet eine HTML-Zusammenfassung per Gmail.
"""

import os
import json
import re
import math
import smtplib
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin
from pathlib import Path
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape, unescape
from html.parser import HTMLParser

try:
    from .market_history import generate_dashboard, record_market_snapshot
except ImportError:  # Direct execution: python scanner/scanner.py
    from market_history import generate_dashboard, record_market_snapshot

# ─── Konfiguration ───────────────────────────────────────────────────────────

SEARCH_URL = (
    "https://www.kleinanzeigen.de/s-autos/996/k0c216+autos.shift_s:manuell"
)
BASE_URL = "https://www.kleinanzeigen.de"
MAX_RESULT_PAGES = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SCANNER_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCANNER_DIR.parent
STATE_FILE = SCANNER_DIR / "last_seen_ids.json"
FAVORITES_FILE = SCANNER_DIR / "all_time_favorites.json"
DATABASE_FILE = SCANNER_DIR / "market_history.sqlite"
DASHBOARD_FILE = PROJECT_DIR / "docs" / "index.html"
DEBUG_HTML = SCANNER_DIR / "debug_last_fetch.html"
MARKET_DASHBOARD_URL = os.environ.get(
    "MARKET_DASHBOARD_URL", "https://andilar.github.io/996Runner/"
)

GMAIL_USER   = os.environ.get("GMAIL_USER", "")
GMAIL_PASS   = os.environ.get("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", GMAIL_USER)
DEBUG        = os.environ.get("DEBUG", "0") == "1"

# Heimatposition für Entfernungs-Ranking (Vordorf, Niedersachsen)
HOME_POSTAL_CODE = "38533"
HOME_COORDINATES = (52.42, 10.60)

SCORING_RULES = {
    "primary": {
        "Motorrevision": 100,
        "Vesuvio": 100,
        "Stahlbuchsen": 100,
    },
    "price": {
        "maximum_bonus": 50,
        "full_bonus_until_eur": 8_000,
        "zero_bonus_from_eur": 35_000,
        "minimum_plausible_eur": 8_000,
    },
    "distance": {
        "maximum_bonus": 30,
        "full_bonus_until_km": 50,
        "zero_bonus_from_km": 600,
    },
    "other": {
        "Scheckheft": 15,
        "1. Hand": 10,
        "Facelift": 8,
        "Wenig KM": 5,
    },
}

# ─── Keywords ────────────────────────────────────────────────────────────────

HIGHLIGHT_GROUPS = [
    {
        "label": "Stahlbuchsen",
        "color": "#7A271A",
        "bg": "#FCE8E6",
        "keywords": [
            "stahlbuchse", "stahlbuchsen", "stahl zylinder", "stahlzylinder",
            "steel liner", "steel liners", "closed deck stahl",
        ],
    },
    {
        "label": "Vesuvio",
        "color": "#4A4D52",
        "bg": "#ECEDEF",
        "keywords": [
            "vesuvio", "vesuvio metallic", "vesuvio-metallic",
            "vesuviometallic", "vesuvgrau", "l40x",
        ],
    },
    {
        "label": "Motorrevision",
        "color": "#185FA5",
        "bg": "#E6F1FB",
        "keywords": [
            "motorrevision", "motor revision", "motorüberholung", "motor überholt",
            "motor erneuert", "austauschmotor", "rumpfmotor", "kurzmotor",
            "ims getauscht", "ims ersetzt", "ims upgrade", "ims lager",
            "zwischenwelle", "rms getauscht", "rms erneuert",
            "motoren überholt", "komplett überholt",
        ],
    },
    {
        "label": "Scheckheft",
        "color": "#0F6E56",
        "bg": "#E1F5EE",
        "keywords": [
            "scheckheft", "scheckheftgepflegt", "sh gepflegt", "lückenlos",
            "porsche zentrum", "pz gepflegt", "serviceheft",
        ],
    },
    {
        "label": "Wenig KM",
        "color": "#633806",
        "bg": "#FAEEDA",
        "keywords": [
            "wenig km", "niedrige laufleistung", "garage", "garagenfahrzeug",
            "sommerfahr", "sommerfahrzeug", "selten gefahren",
        ],
    },
    {
        "label": "1. Hand",
        "color": "#712B13",
        "bg": "#FAECE7",
        "keywords": [
            "1. hand", "1.hand", "erste hand", "erstbesitz", "1 vorbesitzer",
            "ein vorbesitzer", "ein halter",
        ],
    },
    {
        "label": "Facelift",
        "color": "#3C3489",
        "bg": "#EEEDFE",
        "keywords": [
            "facelift", "996.2", "996 2", "mj2002", "mj 2002",
            "mj2003", "mj2004", "mj2005",
        ],
    },
]


def detect_highlights(text: str) -> list[dict]:
    text_lower = text.lower()
    return [g for g in HIGHLIGHT_GROUPS if any(kw in text_lower for kw in g["keywords"])]


# ─── PLZ → Koordinaten (grob, für Ranking) ────────────────────────────────────
# Ungefähre Zentren der deutschen PLZ-Leitregionen (erste 2 Stellen).
# Reicht für "ist das in der Nähe?" vs "am anderen Ende der Republik".

PLZ_REGIONS = {
    "01": (51.05, 13.74), "02": (51.18, 14.43), "03": (51.76, 14.33),
    "04": (51.34, 12.38), "06": (51.48, 11.97), "07": (50.93, 11.59),
    "08": (50.72, 12.49), "09": (50.83, 12.92), "10": (52.52, 13.40),
    "12": (52.48, 13.45), "13": (52.56, 13.33), "14": (52.41, 12.54),
    "15": (52.34, 14.55), "16": (52.83, 13.24), "17": (53.56, 13.26),
    "18": (54.09, 12.13), "19": (53.63, 11.41), "20": (53.55, 10.00),
    "21": (53.25, 10.41), "22": (53.59, 9.95),  "23": (53.87, 10.69),
    "24": (54.32, 10.13), "25": (53.99, 9.77),  "26": (53.14, 7.89),
    "27": (53.18, 8.58),  "28": (53.08, 8.80),  "29": (52.94, 10.55),
    "30": (52.37, 9.73),  "31": (52.15, 9.96),  "32": (52.02, 8.53),
    "33": (51.72, 8.75),  "34": (51.31, 9.49),  "35": (50.80, 8.77),
    "36": (50.56, 9.68),  "37": (51.53, 9.93),  "38": (52.26, 10.52),
    "39": (52.13, 11.63), "40": (51.22, 6.78),  "41": (51.17, 6.44),
    "42": (51.25, 7.15),  "44": (51.51, 7.47),  "45": (51.45, 7.01),
    "46": (51.66, 6.63),  "47": (51.43, 6.76),  "48": (51.96, 7.63),
    "49": (52.27, 8.05),  "50": (50.94, 6.96),  "51": (51.00, 7.00),
    "52": (50.78, 6.08),  "53": (50.74, 7.10),  "54": (49.75, 6.64),
    "55": (49.99, 8.24),  "56": (50.36, 7.60),  "57": (50.87, 8.02),
    "58": (51.36, 7.47),  "59": (51.58, 7.97),  "60": (50.11, 8.68),
    "61": (50.32, 8.75),  "63": (50.00, 9.15),  "64": (49.87, 8.65),
    "65": (50.08, 8.24),  "66": (49.24, 7.00),  "67": (49.45, 8.07),
    "68": (49.49, 8.47),  "69": (49.41, 8.69),  "70": (48.78, 9.18),
    "71": (48.83, 9.23),  "72": (48.52, 9.06),  "73": (48.80, 9.79),
    "74": (49.14, 9.22),  "75": (48.89, 8.70),  "76": (49.01, 8.40),
    "77": (48.46, 8.00),  "78": (47.99, 8.53),  "79": (47.99, 7.85),
    "80": (48.14, 11.58), "81": (48.11, 11.60), "82": (48.04, 11.26),
    "83": (47.86, 12.40), "84": (48.55, 12.15), "85": (48.37, 11.50),
    "86": (48.37, 10.90), "87": (47.73, 10.32), "88": (47.78, 9.62),
    "89": (48.40, 9.99),  "90": (49.45, 11.08), "91": (49.58, 11.00),
    "92": (49.45, 12.13), "93": (49.02, 12.10), "94": (48.84, 12.96),
    "95": (50.08, 11.86), "96": (50.26, 10.96), "97": (49.79, 9.95),
    "98": (50.68, 10.91), "99": (50.98, 11.03),
}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Luftlinie in km zwischen zwei (lat, lon)-Paaren."""
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    x = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(x))


def distance_from_home(plz: str) -> float | None:
    if not plz or len(plz) < 2:
        return None
    coords = PLZ_REGIONS.get(plz[:2])
    if not coords:
        return None
    return haversine_km(HOME_COORDINATES, coords)


# ─── Preis-Parsing ────────────────────────────────────────────────────────────

def parse_price(price_str: str) -> int | None:
    """'12.500 €' oder '12.500 € VB' → 12500."""
    if not price_str:
        return None
    m = re.search(r"(\d{1,3}(?:\.\d{3})+|\d{3,6})", price_str)
    if not m:
        return None
    return int(m.group(1).replace(".", ""))


WANTED_TITLE_PATTERNS = (
    re.compile(r"^\s*(?:suche|gesucht|ankauf|kaufe|wir\s+suchen)\b", re.I),
    re.compile(r"\b(?:ankauf|(?:dringend\s+)?gesucht)\s*[!?.]*$", re.I),
)

DESCRIPTION_PRICE_RE = re.compile(
    r"\b(?:kaufpreis|preis)\s*(?:ist|liegt\s+bei|[:=-])?\s*"
    r"(\d{1,3}(?:\.\d{3})+|\d{4,6})\s*(?:€|eur|vb)?\b",
    re.I,
)

UNAVAILABLE_PAGE_PATTERNS = (
    re.compile(r"diese\s+anzeige\s+ist(?:\s+leider)?\s+nicht\s+mehr\s+verfügbar", re.I),
    re.compile(r"anzeige\s+wurde\s+(?:gelöscht|deaktiviert)", re.I),
    re.compile(r"<title[^>]*>[^<]*(?:nicht\s+mehr\s+verfügbar|gelöscht)", re.I),
)

AVAILABILITY_TIMEOUT_SECONDS = 10
AVAILABILITY_BODY_LIMIT = 512_000


def _description_price_eur(description: str) -> int | None:
    """Liest einen ausdrücklich benannten Kaufpreis aus der Beschreibung."""
    match = DESCRIPTION_PRICE_RE.search(description or "")
    return int(match.group(1).replace(".", "")) if match else None


def ranking_price_eur(listing: dict) -> int | None:
    """Liefert einen plausiblen Preis und korrigiert offensichtliche Platzhalter."""
    minimum = SCORING_RULES["price"]["minimum_plausible_eur"]
    advertised = parse_price(listing.get("price", ""))
    if advertised is None or advertised >= minimum:
        return advertised

    described = _description_price_eur(listing.get("desc", ""))
    return described if described is not None and described >= minimum else None


def is_rankable_listing(listing: dict) -> bool:
    """Lässt nur Verkaufsangebote ohne ungeklärten Platzhalterpreis ins Ranking."""
    if listing.get("_ranking_unavailable"):
        return False

    title = listing.get("title", "") or ""
    if any(pattern.search(title) for pattern in WANTED_TITLE_PATTERNS):
        return False

    advertised = parse_price(listing.get("price", ""))
    return advertised is None or ranking_price_eur(listing) is not None


def _ranking_price_display(listing: dict) -> str:
    """Kennzeichnet einen aus der Beschreibung korrigierten Rankingpreis."""
    original = listing.get("price", "") or "k.A."
    advertised = parse_price(original)
    ranked = ranking_price_eur(listing)
    if advertised is not None and ranked is not None and advertised != ranked:
        corrected = f"{ranked:,}".replace(",", ".")
        return f"{corrected} €*"
    return original


def check_listing_availability(
    listing: dict, opener=urllib.request.urlopen
) -> bool | None:
    """Prüft eine Detailseite; ``None`` bedeutet vorübergehend nicht prüfbar."""
    url = listing.get("url", "")
    if not re.match(r"^https?://", url, re.I):
        return False

    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with opener(request, timeout=AVAILABILITY_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None) or response.getcode()
            body = response.read(AVAILABILITY_BODY_LIMIT).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if status in (404, 410):
            return False
        return None
    except OSError:
        return None

    if status in (404, 410):
        return False
    if not body:
        return None
    if any(pattern.search(unescape(body)) for pattern in UNAVAILABLE_PAGE_PATTERNS):
        return False
    return True


def select_available_ranked_listings(
    listings: list[dict], limit: int = 3, availability_fn=check_listing_availability
) -> list[dict]:
    """Wählt Ranking-Kandidaten und überspringt sicher nicht mehr verfügbare Seiten."""
    ranked = sorted(
        (listing for listing in listings if is_rankable_listing(listing)),
        key=lambda listing: compute_score(listing)[0],
        reverse=True,
    )
    selected = []
    for listing in ranked:
        status = availability_fn(listing)
        if status is False:
            listing["_ranking_unavailable"] = True
            print(f"  Nicht mehr verfügbar, aus Top-Liste entfernt: {listing.get('title', '')}")
            continue
        if status is None:
            print(f"  Verfügbarkeit nicht prüfbar, Angebot bleibt erhalten: {listing.get('title', '')}")
        selected.append(listing)
        if len(selected) == limit:
            break
    return selected


# ─── Score-Berechnung ─────────────────────────────────────────────────────────

def _linear_bonus(
    value: float, full_until: float, zero_from: float, maximum: float
) -> float:
    if value <= full_until:
        return maximum
    if value >= zero_from:
        return 0
    return round(maximum * (zero_from - value) / (zero_from - full_until), 1)


def compute_score(listing: dict) -> tuple[float, dict]:
    """
    Gewichtetes Ranking:
      1. Primärmerkmale Motorrevision, Vesuvio und Stahlbuchsen: je +100
      2. Preis (mittel): niedriger Preis → Bonus bis +50
      3. Entfernung (niedriger): näher dran → Bonus bis +30
    Plus kleine Boni für andere Highlights.
    """
    text = f"{listing.get('title','')} {listing.get('desc','')}"
    labels = {h["label"] for h in detect_highlights(text)}

    bd = {"primary": 0, "price": 0, "distance": 0, "other": 0}

    for label, bonus in SCORING_RULES["primary"].items():
        if label in labels:
            bd["primary"] += bonus

    price = ranking_price_eur(listing)
    if price is not None:
        rules = SCORING_RULES["price"]
        bd["price"] = _linear_bonus(
            price,
            rules["full_bonus_until_eur"],
            rules["zero_bonus_from_eur"],
            rules["maximum_bonus"],
        )

    dist = distance_from_home(listing.get("plz", ""))
    if dist is not None:
        rules = SCORING_RULES["distance"]
        bd["distance"] = _linear_bonus(
            dist,
            rules["full_bonus_until_km"],
            rules["zero_bonus_from_km"],
            rules["maximum_bonus"],
        )
        listing["distance_km"] = round(dist)

    for label, bonus in SCORING_RULES["other"].items():
        if label in labels:
            bd["other"] += bonus

    return sum(bd.values()), bd


# ─── HTML Parser ─────────────────────────────────────────────────────────────

class ListingParser(HTMLParser):
    """Parst article-Blöcke der Kleinanzeigen-Suchergebnisse."""

    def __init__(self):
        super().__init__()
        self.listings: list[dict] = []
        self.next_href = ""
        self._cur: dict = {}
        self._in_article = False
        self._article_text = ""

        self._capture_title    = False
        self._capture_price    = False
        self._capture_location = False
        self._capture_desc     = False
        self._capture_json_ld  = False
        self._in_title_heading = False
        self._await_location_span = False

    @staticmethod
    def _cls(attrs: dict) -> str:
        return attrs.get("class", "") or ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls   = self._cls(attrs)

        rel = attrs.get("rel", "")
        label = f"{attrs.get('aria-label', '')} {attrs.get('title', '')}".lower()
        if (
            tag in ("a", "link")
            and attrs.get("href")
            and ("next" in rel.lower() or "nächste" in label or "naechste" in label)
        ):
            self.next_href = attrs["href"]

        if tag == "article" and "data-adid" in attrs:
            href = attrs.get("data-href", "")
            self._cur = {
                "id": attrs["data-adid"],
                "title": "", "price": "", "location": "",
                "url": urljoin(BASE_URL, href) if href else "",
                "date": "", "km": "", "year": "", "image": "",
                "desc": "", "plz": "",
            }
            self._in_article  = True
            self._article_text = ""
            return

        if not self._in_article:
            return

        # The current result page exposes reliable title/description/image data
        # as JSON-LD inside every article. Keep the CSS-based parsing below for
        # older markup, but prefer this semantic source when it is available.
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._capture_json_ld = True
            return

        if tag in ("h2", "h3"):
            self._in_title_heading = True

        if tag == "svg" and attrs.get("data-title") == "locationOutline":
            self._await_location_span = True

        if tag == "span" and self._await_location_span:
            self._capture_location = True
            self._await_location_span = False
            return

        # Erstes <img> im article → Vorschaubild
        if tag == "img" and not self._cur["image"]:
            srcset = attrs.get("srcset", "")
            if srcset:
                parts = [p.strip().split()[0] for p in srcset.split(",") if p.strip()]
                if parts:
                    self._cur["image"] = parts[-1]
            if not self._cur["image"]:
                src = attrs.get("src", "") or attrs.get("data-src", "")
                if src and not src.startswith("data:"):
                    self._cur["image"] = src

        # Titel-Link
        if tag == "a":
            href = attrs.get("href", "")
            if href.startswith("/s-anzeige/") and not self._cur["url"]:
                self._cur["url"] = urljoin(BASE_URL, href)
            if href.startswith("/s-anzeige/") and self._in_title_heading:
                self._capture_title = True
            return

        if tag == "p" and (
            "price" in cls.lower()
            or ("text-title3" in cls and "font-strong" in cls and "text-secondary" in cls)
        ):
            self._capture_price = True
            return

        if tag in ("span", "p") and "aditem-main--top--left" in cls:
            self._capture_location = True
            return

        if tag == "p" and "aditem-main--middle--description" in cls:
            self._capture_desc = True
            return

    def handle_endtag(self, tag):
        if tag == "article" and self._in_article:
            self._extract_km_year_plz()
            if self._cur.get("id"):
                self.listings.append(self._cur.copy())
            self._cur          = {}
            self._in_article   = False
            self._article_text = ""
            return

        if tag == "a":
            self._capture_title = False
        if tag in ("h2", "h3"):
            self._in_title_heading = False
        if tag == "script":
            self._capture_json_ld = False
        if tag == "p":
            self._capture_price    = False
            self._capture_location = False
            self._capture_desc     = False
        if tag == "span":
            self._capture_location = False

    def handle_data(self, data):
        if not self._in_article:
            return
        stripped = data.strip()
        if not stripped:
            return

        if self._capture_json_ld:
            try:
                metadata = json.loads(stripped)
            except (TypeError, ValueError):
                return
            if metadata.get("@type") == "ImageObject":
                self._cur["title"] = unescape(metadata.get("title", "")) or self._cur["title"]
                self._cur["desc"] = unescape(metadata.get("description", "")) or self._cur["desc"]
                self._cur["image"] = metadata.get("contentUrl", "") or self._cur["image"]
            return

        self._article_text += " " + stripped

        if self._capture_title and not self._cur["title"]:
            self._cur["title"] = stripped
        if self._capture_price and not self._cur["price"]:
            if re.search(r"[\d€]|VB|Preis|k\.A", stripped, re.I):
                self._cur["price"] = stripped
        if self._capture_location and not self._cur["location"]:
            self._cur["location"] = stripped
        if self._capture_desc:
            self._cur["desc"] = (self._cur["desc"] + " " + stripped).strip()

    def _extract_km_year_plz(self):
        text = self._article_text

        if not self._cur["price"]:
            price_m = re.search(r"\b\d{1,3}(?:\.\d{3})*\s*€(?:\s*VB)?", text, re.I)
            if price_m:
                self._cur["price"] = price_m.group(0).strip()

        km_m = re.search(r"\b(\d{1,3}[\.\s]?\d{3})\s*km\b", text, re.I)
        if km_m:
            self._cur["km"] = km_m.group(0).strip()

        year_m = re.search(r"\b(199[7-9]|200[0-5])\b", text)
        if year_m:
            self._cur["year"] = year_m.group(1)

        date_m = re.search(r"\b(Heute|Gestern|\d{2}\.\d{2}\.\d{4})\b", text, re.I)
        if date_m:
            self._cur["date"] = date_m.group(1)

        plz_m = re.search(r"\b(\d{5})\b", self._cur.get("location", "") + " " + text)
        if plz_m:
            self._cur["plz"] = plz_m.group(1)


# ─── Fetch ────────────────────────────────────────────────────────────────────

def deduplicate_listings(listings: list[dict]) -> list[dict]:
    """Dedupliziert Angebote anhand ihrer stabilen ID; der letzte Stand gewinnt."""
    listings_by_id: dict[str, dict] = {}
    for listing in listings:
        listing_id = str(listing.get("id", "")).strip()
        if not listing_id:
            raise RuntimeError("Angebot ohne stabile ID gefunden; Scan abgebrochen")
        listings_by_id[listing_id] = listing
    return list(listings_by_id.values())


def fetch_listings(url: str) -> list[dict]:
    parsed_listings: list[dict] = []
    visited_urls = set()
    next_url = url
    max_pages = MAX_RESULT_PAGES

    while next_url and next_url not in visited_urls and len(visited_urls) < max_pages:
        visited_urls.add(next_url)
        html = _fetch_html(next_url)

        if DEBUG:
            DEBUG_HTML.parent.mkdir(parents=True, exist_ok=True)
            page_number = len(visited_urls)
            debug_path = DEBUG_HTML.with_name(f"debug_fetch_page_{page_number}.html")
            debug_path.write_text(html, encoding="utf-8")
            print(f"  [DEBUG] HTML gespeichert: {debug_path}")

        parser = ListingParser()
        parser.feed(html)
        page_listings = parser.listings
        if not page_listings:
            print("  HTMLParser lieferte 0 Ergebnisse – Regex-Fallback...")
            page_listings = _regex_fallback(html)
        _validate_listings(page_listings)
        parsed_listings.extend(page_listings)
        next_url = urljoin(next_url, parser.next_href) if parser.next_href else ""

    if next_url and len(visited_urls) >= max_pages:
        raise RuntimeError(f"Mehr als {max_pages} Ergebnisseiten; Scan abgebrochen")

    listings = deduplicate_listings(parsed_listings)

    print(f"  Parser: {len(listings)} Angebote, "
          f"Titel: {sum(1 for l in listings if l['title'])}, "
          f"Preis: {sum(1 for l in listings if l['price'])}, "
          f"Ort: {sum(1 for l in listings if l['location'])}, "
          f"Bild: {sum(1 for l in listings if l['image'])}")

    return listings


def _fetch_html(url: str, attempts: int = 3) -> str:
    """Fetch one results page with short retries for transient failures."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except OSError as error:
            last_error = error
            if attempt < attempts:
                delay = attempt * 2
                print(f"  Abruf fehlgeschlagen ({attempt}/{attempts}), neuer Versuch in {delay}s")
                time.sleep(delay)
    raise RuntimeError(f"Abruf nach {attempts} Versuchen fehlgeschlagen: {last_error}")


def _validate_listings(listings: list[dict]):
    """Abort before state is updated when a markup change corrupts parsing."""
    if not listings:
        raise RuntimeError("Keine Angebote gefunden; Parser oder Abruf ist vermutlich defekt")

    minimum = max(1, math.ceil(len(listings) / 2))
    valid_titles = sum(
        bool(item.get("title")) and not item["title"].strip().isdigit()
        for item in listings
    )
    prices = sum(bool(item.get("price")) for item in listings)
    urls = sum(bool(item.get("url")) for item in listings)

    if min(valid_titles, prices, urls) < minimum:
        raise RuntimeError(
            "Parser lieferte unvollständige Angebote "
            f"(gültige Titel: {valid_titles}, Preise: {prices}, URLs: {urls}, "
            f"gesamt: {len(listings)}); State wird nicht aktualisiert"
        )


def _regex_fallback(html: str) -> list[dict]:
    listings = []
    for m in re.finditer(r'data-adid="(\d+)"', html):
        adid = m.group(1)
        url_m = re.search(rf'href="(/s-anzeige/[^"]*{adid}[^"]*)"', html)
        url   = urljoin(BASE_URL, url_m.group(1)) if url_m else ""

        title = ""
        if url_m:
            parts = url_m.group(1).split("/")
            if len(parts) >= 3:
                title = parts[2].replace("-", " ").title()

        pos   = m.end()
        chunk = html[pos:pos+2000]
        p_m   = re.search(r"(\d{1,3}(?:\.\d{3})*)\s*€", chunk)
        price = p_m.group(0) if p_m else "k.A."

        km_m  = re.search(r"\b(\d{1,3}[\.\s]?\d{3})\s*km\b", chunk, re.I)
        km    = km_m.group(0).strip() if km_m else ""

        yr_m  = re.search(r"\b(199[7-9]|200[0-5])\b", chunk)
        year  = yr_m.group(1) if yr_m else ""

        plz_m = re.search(r"\b(\d{5})\b", chunk)
        plz   = plz_m.group(1) if plz_m else ""

        img_m = re.search(r'<img[^>]+src="(https?://[^"]+)"', chunk)
        image = img_m.group(1) if img_m else ""

        listings.append({
            "id": adid, "title": title or f"Anzeige #{adid}",
            "price": price, "location": "", "url": url,
            "date": "", "km": km, "year": year,
            "image": image, "desc": "", "plz": plz,
        })
    return listings


# ─── State ───────────────────────────────────────────────────────────────────

def load_seen_ids() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_ids(ids: set):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(ids), f)


def find_new(listings: list[dict], seen: set) -> list[dict]:
    return [l for l in listings if l["id"] not in seen]


def load_all_time_favorites() -> list[dict]:
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def update_all_time_favorites(
    favorites: list[dict], current_listings: list[dict], availability_fn=None
) -> list[dict]:
    """Merge current findings into the persistent all-time top three."""
    by_id = {listing["id"]: listing for listing in favorites}
    by_id.update({listing["id"]: listing for listing in current_listings})
    candidates = [listing for listing in by_id.values() if is_rankable_listing(listing)]
    if availability_fn is not None:
        return select_available_ranked_listings(candidates, 3, availability_fn)
    ranked = sorted(candidates, key=lambda listing: compute_score(listing)[0], reverse=True)
    return ranked[:3]


def save_all_time_favorites(favorites: list[dict]):
    os.makedirs(os.path.dirname(FAVORITES_FILE), exist_ok=True)
    with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
        json.dump(favorites, f, ensure_ascii=False, indent=2)


# ─── E-Mail: HTML bauen ──────────────────────────────────────────────────────

def _top_card(listing: dict, score: float, rank: int) -> str:
    """Große Card für ein Top-Angebot mit Bild und Details."""
    raw_title = listing["title"] or "–"
    raw_desc = listing.get("desc", "")
    title = escape(raw_title)
    price = escape(_ranking_price_display(listing))
    loc   = escape(listing["location"] or "–")
    km    = escape(listing["km"] or "–")
    year  = escape(listing["year"] or "–")
    url   = escape(listing["url"] or "#", quote=True)
    img   = escape(listing["image"] or "", quote=True)
    desc  = escape(raw_desc)
    dist  = listing.get("distance_km")
    online_since = listing.get("date") or "–"

    if len(desc) > 220:
        desc = desc[:220].rsplit(" ", 1)[0] + "…"

    highlights = detect_highlights(f"{raw_title} {raw_desc}")
    badges = "".join(
        f'<span style="display:inline-block;font-size:11px;font-weight:600;'
        f'padding:3px 9px;border-radius:4px;margin:0 6px 4px 0;'
        f'background:{h["bg"]};color:{h["color"]};">{h["label"]}</span>'
        for h in highlights
    )

    img_html = (
        f'<img src="{img}" alt="" width="220" style="width:220px;height:auto;'
        f'display:block;border-radius:6px;">'
        if img else
        f'<div style="width:220px;height:150px;background:#eee;border-radius:6px;'
        f'display:flex;align-items:center;justify-content:center;color:#999;font-size:12px;">'
        f'kein Bild</div>'
    )

    dist_str = f"{dist} km" if dist is not None else "–"
    rank_colors = {1: "#D4AF37", 2: "#A8A8A8", 3: "#B87333"}
    rank_color  = rank_colors.get(rank, "#E6A817")

    return f"""
    <table style="width:100%;border-collapse:collapse;margin-bottom:16px;
                  border:1px solid #e8e8e8;border-radius:10px;overflow:hidden;
                  background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.04);">
      <tr>
        <td style="padding:14px;vertical-align:top;width:240px;">
          <a href="{url}">{img_html}</a>
        </td>
        <td style="padding:14px 16px 14px 4px;vertical-align:top;">
          <div style="margin-bottom:6px;">
            <span style="display:inline-block;background:{rank_color};color:#fff;
                         font-size:11px;font-weight:700;padding:3px 8px;
                         border-radius:12px;">#{rank} · Score {score:.0f}</span>
          </div>
          <a href="{url}" style="color:#185FA5;font-size:16px;font-weight:700;
                                 text-decoration:none;line-height:1.3;
                                 display:block;margin-bottom:8px;">{title}</a>
          <div style="margin-bottom:8px;">{badges}</div>
          <div style="font-size:13px;color:#333;margin-bottom:8px;">
            <span style="font-weight:700;color:#1D9E75;font-size:15px;">{price}</span>
            <span style="color:#aaa;margin:0 8px;">·</span>
            <span>{year}</span>
            <span style="color:#aaa;margin:0 8px;">·</span>
            <span>{km}</span>
          </div>
          <div style="font-size:12px;color:#666;margin-bottom:10px;">
            📍 {loc} <span style="color:#888;">({dist_str} Luftlinie)</span>
            <span style="color:#aaa;margin:0 6px;">·</span>
            📅 Online seit: {online_since}
          </div>
          <div style="font-size:12px;color:#555;line-height:1.5;">
            {desc or '<span style="color:#aaa;">(keine Kurzbeschreibung verfügbar)</span>'}
          </div>
        </td>
      </tr>
    </table>
    """


def _compact_row(listing: dict) -> str:
    """Kompakte Tabellenzeile für den Rest."""
    raw_title = listing["title"] or "–"
    raw_desc = listing.get("desc", "")
    title = escape(raw_title)
    price = escape(_ranking_price_display(listing))
    loc   = escape(listing["location"] or "–")
    km    = escape(listing["km"] or "–")
    year  = escape(listing["year"] or "–")
    url   = escape(listing["url"] or "#", quote=True)
    dist  = listing.get("distance_km")

    highlights = detect_highlights(f"{raw_title} {raw_desc}")
    badges = "".join(
        f'<span style="display:inline-block;font-size:9px;font-weight:600;'
        f'padding:1px 5px;border-radius:3px;margin-left:4px;'
        f'background:{h["bg"]};color:{h["color"]};">{h["label"]}</span>'
        for h in highlights
    )

    dist_str = f" · {dist}km" if dist is not None else ""
    loc_disp = f"{loc}{dist_str}"

    return f"""
    <tr>
      <td style="padding:8px 10px;border-bottom:1px solid #eee;font-size:12px;">
        <a href="{url}" style="color:#185FA5;font-weight:600;text-decoration:none;">{title}</a>
        {badges}
      </td>
      <td style="padding:8px 10px;border-bottom:1px solid #eee;white-space:nowrap;
                 font-weight:600;color:#1D9E75;font-size:12px;">{price}</td>
      <td style="padding:8px 10px;border-bottom:1px solid #eee;color:#666;font-size:12px;">{year}</td>
      <td style="padding:8px 10px;border-bottom:1px solid #eee;color:#666;font-size:12px;">{km}</td>
      <td style="padding:8px 10px;border-bottom:1px solid #eee;color:#666;font-size:12px;">{loc_disp}</td>
    </tr>"""


def build_html(
    new_listings: list[dict], all_listings: list[dict], all_time_favorites: list[dict]
) -> str:
    today     = date.today().strftime("%d.%m.%Y")
    count_new = len(new_listings)
    total     = len(all_listings)

    scored_new = [(compute_score(l)[0], l) for l in new_listings]
    scored_new.sort(key=lambda t: t[0], reverse=True)
    top3_new = [(s, l) for s, l in scored_new if is_rankable_listing(l)][:3]
    top3_new_ids = {l["id"] for _, l in top3_new}
    rest = [(s, l) for s, l in scored_new if l["id"] not in top3_new_ids]

    scored_favorites = [(compute_score(l)[0], l) for l in all_time_favorites]
    scored_favorites.sort(key=lambda t: t[0], reverse=True)

    highlight_count = sum(
        1 for _, l in scored_new if is_rankable_listing(l)
        if detect_highlights(f"{l.get('title','')} {l.get('desc','')}")
    )

    if top3_new:
        new_top_cards = "".join(
            _top_card(l, s, rank=i+1) for i, (s, l) in enumerate(top3_new)
        )
        new_top_section = f"""
        <div style="padding:20px 28px 8px;">
          <h2 style="margin:0 0 14px;font-size:15px;color:#333;">
            🆕 Top 3 der neuen Angebote
            <span style="font-size:11px;color:#999;font-weight:400;margin-left:8px;">
              Primärmerkmale · Preis · Entfernung zu {HOME_POSTAL_CODE}
            </span>
          </h2>
          {new_top_cards}
        </div>"""
    else:
        empty_message = (
            "Heute keine neuen Angebote."
            if not new_listings else
            "Keine neuen rankbaren Verkaufsangebote."
        )
        new_top_section = f"""
        <div style="padding:30px 28px;text-align:center;color:#888;">
          {empty_message}
        </div>"""

    if rest:
        rows = "".join(_compact_row(l) for _, l in rest)
        rest_section = f"""
        <div style="padding:8px 28px 20px;">
          <h2 style="margin:12px 0 10px;font-size:14px;color:#555;">
            Weitere neue Angebote ({len(rest)})
          </h2>
          <table style="width:100%;border-collapse:collapse;">
            <thead>
              <tr style="background:#f0f0f0;">
                <th style="padding:7px 10px;text-align:left;font-weight:600;color:#555;font-size:11px;">Titel</th>
                <th style="padding:7px 10px;text-align:left;font-weight:600;color:#555;font-size:11px;">Preis</th>
                <th style="padding:7px 10px;text-align:left;font-weight:600;color:#555;font-size:11px;">EZ</th>
                <th style="padding:7px 10px;text-align:left;font-weight:600;color:#555;font-size:11px;">KM</th>
                <th style="padding:7px 10px;text-align:left;font-weight:600;color:#555;font-size:11px;">Ort</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""
    else:
        rest_section = ""

    if scored_favorites:
        favorite_cards = "".join(
            _top_card(l, s, rank=i+1) for i, (s, l) in enumerate(scored_favorites[:3])
        )
        favorites_section = f"""
        <div style="padding:20px 28px 8px;border-top:1px solid #eee;">
          <h2 style="margin:0 0 14px;font-size:15px;color:#333;">
            🏆 All-Time-Favoriten
            <span style="font-size:11px;color:#999;font-weight:400;margin-left:8px;">
              Die 3 besten jemals gefundenen Angebote
            </span>
          </h2>
          {favorite_cards}
        </div>"""
    else:
        favorites_section = ""

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><title>996 Scanner</title></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f5f5f5;">
<div style="max-width:720px;margin:24px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">

  <div style="background:#1a1a1a;padding:24px 28px;">
    <h1 style="margin:0;color:#fff;font-size:20px;">🏎 Porsche 996 Scanner</h1>
    <p style="margin:6px 0 0;color:#aaa;font-size:13px;">{today} · Schaltgetriebe · Kleinanzeigen.de</p>
  </div>

  <div style="display:flex;padding:20px 28px;gap:12px;background:#fafafa;border-bottom:1px solid #eee;">
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:14px;border:1px solid #eee;">
      <div style="font-size:28px;font-weight:700;color:#185FA5;">{count_new}</div>
      <div style="font-size:12px;color:#666;margin-top:2px;">Neu seit gestern</div>
    </div>
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:14px;border:1px solid #eee;">
      <div style="font-size:28px;font-weight:700;color:#E6A817;">⭐ {highlight_count}</div>
      <div style="font-size:12px;color:#666;margin-top:2px;">Mit Highlight</div>
    </div>
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:14px;border:1px solid #eee;">
      <div style="font-size:28px;font-weight:700;color:#444;">{total}</div>
      <div style="font-size:12px;color:#666;margin-top:2px;">Angebote gesamt</div>
    </div>
  </div>

  {new_top_section}
  {rest_section}
  {favorites_section}

  <div style="padding:16px 28px;background:#fafafa;border-top:1px solid #eee;text-align:center;">
    <a href="{SEARCH_URL}" style="display:inline-block;background:#185FA5;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;">
      Alle Angebote ansehen →
    </a>
    <a href="{MARKET_DASHBOARD_URL}" style="display:inline-block;color:#185FA5;padding:10px 18px;text-decoration:none;font-size:13px;font-weight:600;">
      Marktverlauf →
    </a>
  </div>

  <p style="text-align:center;padding:12px;font-size:11px;color:#bbb;margin:0;">
    Automatisch generiert von 996-Scanner · GitHub Actions<br>
    * Rankingpreis aus der Beschreibung bei unplausiblem Anzeigenpreis
  </p>
</div>
</body>
</html>"""


def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = NOTIFY_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
    print(f"E-Mail gesendet an {NOTIFY_EMAIL}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Starte Scan...")

    listings = fetch_listings(SEARCH_URL)
    print(f"  {len(listings)} Angebote gefunden")

    seen = load_seen_ids()
    new  = find_new(listings, seen)
    print(f"  {len(new)} davon neu")

    availability_cache: dict[str, bool | None] = {}

    def cached_availability(listing: dict) -> bool | None:
        listing_id = str(listing.get("id", ""))
        if listing_id not in availability_cache:
            availability_cache[listing_id] = check_listing_availability(listing)
        return availability_cache[listing_id]

    # Neue Top-Kandidaten sowie die persistenten All-Time-Favoriten prüfen.
    # Sicher entfernte Treffer werden markiert und von build_html übersprungen.
    select_available_ranked_listings(new, 3, cached_availability)
    favorites = update_all_time_favorites(
        load_all_time_favorites(), listings, cached_availability
    )

    subject = (
        f"🏎 996 Scanner: {len(new)} neue Angebote – {date.today():%d.%m.%Y}"
        if new else
        f"🏎 996 Scanner: Keine neuen Angebote – {date.today():%d.%m.%Y}"
    )
    html = build_html(new, listings, favorites)

    if GMAIL_USER and GMAIL_PASS and NOTIFY_EMAIL:
        send_email(subject, html)
        all_ids = seen | {l["id"] for l in listings}
        save_seen_ids(all_ids)
        save_all_time_favorites(favorites)
        metrics = record_market_snapshot(DATABASE_FILE, listings, compute_score)
        generate_dashboard(DATABASE_FILE, DASHBOARD_FILE)
        print(
            "Marktdaten gespeichert: "
            f"{metrics['listing_count']} Angebote, "
            f"Median {metrics['median_price_eur'] or 0:.0f} €"
        )
    else:
        print("E-Mail-Konfiguration fehlt – Versand und State-Update übersprungen")
        os.makedirs("scanner", exist_ok=True)
        with open(SCANNER_DIR / "preview.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"HTML-Vorschau gespeichert: {SCANNER_DIR / 'preview.html'}")

    print("Fertig.")


if __name__ == "__main__":
    main()
