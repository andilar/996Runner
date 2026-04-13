#!/usr/bin/env python3
"""
Porsche 996 Kleinanzeigen Scanner
Scannt täglich neue Angebote und sendet eine HTML-Zusammenfassung per Gmail.
"""

import os
import json
import re
import smtplib
import urllib.request
import urllib.parse
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html.parser import HTMLParser

# ─── Konfiguration ───────────────────────────────────────────────────────────

SEARCH_URL = (
    "https://www.kleinanzeigen.de/s-autos/996/k0c216+autos.shift_s:manuell"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

STATE_FILE = "scanner/last_seen_ids.json"

GMAIL_USER   = os.environ.get("GMAIL_USER", "")
GMAIL_PASS   = os.environ.get("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", GMAIL_USER)

# ─── Keywords die ein Angebot besonders interessant machen ───────────────────
# Jede Gruppe hat ein Label und eine Farbe für das Badge in der Mail

HIGHLIGHT_GROUPS = [
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
    """Gibt alle zutreffenden Highlight-Gruppen für einen Text zurück."""
    text_lower = text.lower()
    matches = []
    for group in HIGHLIGHT_GROUPS:
        if any(kw in text_lower for kw in group["keywords"]):
            matches.append(group)
    return matches

# ─── HTML Parser ─────────────────────────────────────────────────────────────

class ListingParser(HTMLParser):
    """Extrahiert Angebots-IDs, Titel, Preise und Links aus der Kleinanzeigen-Seite."""

    def __init__(self):
        super().__init__()
        self.listings = []
        self._current = {}
        self._in_article = False
        self._in_title = False
        self._in_price = False
        self._in_location = False
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        # Anzeigen-Artikel
        if tag == "article" and "data-adid" in attrs:
            self._current = {
                "id": attrs.get("data-adid", ""),
                "title": "",
                "price": "",
                "location": "",
                "url": "",
                "date": "",
                "km": "",
                "year": "",
            }
            self._in_article = True

        if not self._in_article:
            return

        if tag == "a" and "class" in attrs and "ellipsis" in attrs.get("class", ""):
            href = attrs.get("href", "")
            if href.startswith("/s-anzeige/"):
                self._current["url"] = "https://www.kleinanzeigen.de" + href
            self._in_title = True

        if tag == "p" and "class" in attrs:
            cls = attrs.get("class", "")
            if "aditem-main--middle--price" in cls:
                self._in_price = True
            if "aditem-main--top--left" in cls or "icon-pin" in cls:
                self._in_location = True

    def handle_endtag(self, tag):
        if tag == "article" and self._in_article:
            if self._current.get("id"):
                self.listings.append(self._current.copy())
            self._current = {}
            self._in_article = False
        if tag == "a":
            self._in_title = False
        if tag == "p":
            self._in_price = False
            self._in_location = False

    def handle_data(self, data):
        data = data.strip()
        if not data or not self._in_article:
            return
        if self._in_title and not self._current["title"]:
            self._current["title"] = data
        if self._in_price and not self._current["price"]:
            self._current["price"] = data
        if self._in_location and not self._current["location"]:
            self._current["location"] = data


def extract_listings_regex(html: str) -> list[dict]:
    """Fallback: extrahiert Anzeigen per Regex aus dem rohen HTML."""
    listings = []

    # Finde alle adid-Blöcke
    adid_blocks = re.finditer(r'data-adid="(\d+)"', html)
    for match in adid_blocks:
        adid = match.group(1)
        # Suche nach URL
        url_match = re.search(
            rf'href="(/s-anzeige/[^"]*/{adid}-\d+-\d+)"', html
        )
        url = ("https://www.kleinanzeigen.de" + url_match.group(1)) if url_match else ""

        # Titel aus URL ableiten
        title = ""
        if url_match:
            slug = url_match.group(1).split("/")[2] if url_match else ""
            title = slug.replace("-", " ").title()

        # Preis
        price_match = re.search(
            rf'{adid}.{{0,500}}?(\d{{2,3}}\.\d{{3}}\s*€|\d{{3,3}}\s*€|\d{{4,6}}\s*€)',
            html, re.DOTALL
        )
        price = price_match.group(1).strip() if price_match else "k.A."

        listings.append({
            "id": adid,
            "title": title or f"Anzeige #{adid}",
            "price": price,
            "location": "",
            "url": url,
            "date": "",
            "km": "",
            "year": "",
        })

    return listings


def fetch_listings(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    parser = ListingParser()
    parser.feed(html)

    listings = parser.listings

    # Fallback wenn Parser nichts findet
    if not listings:
        print("HTMLParser lieferte 0 Ergebnisse, versuche Regex-Fallback...")
        listings = extract_listings_regex(html)

    # Kilometerstand und Baujahr aus Titel/Beschreibung extrahieren
    km_pattern   = re.compile(r"(\d{2,3}[\.\s]?\d{3})\s*km", re.I)
    year_pattern = re.compile(r"\b(199[8-9]|200[0-9]|201[0-5])\b")
    for lst in listings:
        text = f"{lst['title']} {lst.get('desc','')}"
        m = km_pattern.search(text)
        if m:
            lst["km"] = m.group(0)
        m = year_pattern.search(text)
        if m:
            lst["year"] = m.group(1)

    return listings


# ─── State / Neue Angebote ermitteln ─────────────────────────────────────────

def load_seen_ids() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_ids(ids: set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(ids), f)


def find_new(listings: list[dict], seen: set) -> list[dict]:
    return [l for l in listings if l["id"] not in seen]


# ─── E-Mail ───────────────────────────────────────────────────────────────────

def build_html(new_listings: list[dict], total: int) -> str:
    today = date.today().strftime("%d.%m.%Y")
    count_new = len(new_listings)

    # Angebote sortieren: highlights zuerst
    def sort_key(l):
        return 0 if detect_highlights(f"{l.get('title','')} {l.get('desc','')}") else 1

    sorted_listings = sorted(new_listings, key=sort_key)
    highlight_count = sum(1 for l in new_listings
                          if detect_highlights(f"{l.get('title','')} {l.get('desc','')}"))

    rows = ""
    for l in sorted_listings:
        title  = l["title"] or "–"
        price  = l["price"] or "k.A."
        loc    = l["location"] or "–"
        km     = l["km"] or "–"
        year   = l["year"] or "–"
        url    = l["url"] or "#"

        full_text   = f"{title} {l.get('desc', '')}"
        highlights  = detect_highlights(full_text)
        is_top      = bool(highlights)

        # Zeilenhintergrund für Top-Angebote
        row_bg = "background:#fffbf0;" if is_top else ""
        left_border = "border-left:3px solid #E6A817;" if is_top else "border-left:3px solid transparent;"

        # Badges bauen
        badges = ""
        for h in highlights:
            badges += (
                f'<span style="display:inline-block;font-size:10px;font-weight:600;'
                f'padding:2px 7px;border-radius:4px;margin-left:5px;'
                f'background:{h["bg"]};color:{h["color"]};">{h["label"]}</span>'
            )

        star = "⭐ " if is_top else ""

        rows += f"""
        <tr style="{row_bg}{left_border}">
          <td style="padding:10px 12px;border-bottom:1px solid #eee;">
            <a href="{url}" style="color:#185FA5;font-weight:600;text-decoration:none;">{star}{title}</a>
            {badges}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;white-space:nowrap;font-weight:600;color:#1D9E75;">{price}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#666;">{year}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#666;">{km}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#666;">{loc}</td>
        </tr>"""

    if not rows:
        rows = """<tr><td colspan="5" style="padding:20px;text-align:center;color:#888;">
            Heute keine neuen Angebote – alles bereits bekannt.
        </td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><title>996 Scanner</title></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f5f5f5;">
<div style="max-width:700px;margin:24px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">
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
      <div style="font-size:12px;color:#666;margin-top:2px;">Besonders interessant</div>
    </div>
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:14px;border:1px solid #eee;">
      <div style="font-size:28px;font-weight:700;color:#444;">{total}</div>
      <div style="font-size:12px;color:#666;margin-top:2px;">Angebote gesamt</div>
    </div>
  </div>

  <!-- Badge-Legende -->
  <div style="padding:12px 28px;background:#fafafa;border-bottom:1px solid #eee;font-size:12px;color:#666;">
    <span style="font-weight:600;margin-right:8px;">Markierungen:</span>
    <span style="display:inline-block;padding:2px 7px;border-radius:4px;background:#E6F1FB;color:#185FA5;margin-right:4px;">Motorrevision</span>
    <span style="display:inline-block;padding:2px 7px;border-radius:4px;background:#E1F5EE;color:#0F6E56;margin-right:4px;">Scheckheft</span>
    <span style="display:inline-block;padding:2px 7px;border-radius:4px;background:#FAEEDA;color:#633806;margin-right:4px;">Wenig KM</span>
    <span style="display:inline-block;padding:2px 7px;border-radius:4px;background:#FAECE7;color:#712B13;margin-right:4px;">1. Hand</span>
    <span style="display:inline-block;padding:2px 7px;border-radius:4px;background:#EEEDFE;color:#3C3489;">Facelift</span>
  </div>

  <div style="padding:20px 28px;">
    <h2 style="margin:0 0 14px;font-size:15px;color:#333;">Neue Angebote</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead>
        <tr style="background:#f0f0f0;">
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">Titel</th>
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">Preis</th>
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">EZ</th>
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">KM</th>
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">Ort</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <div style="padding:16px 28px;background:#fafafa;border-top:1px solid #eee;text-align:center;">
    <a href="{SEARCH_URL}" style="display:inline-block;background:#185FA5;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;">
      Alle Angebote ansehen →
    </a>
  </div>

  <p style="text-align:center;padding:12px;font-size:11px;color:#bbb;margin:0;">
    Automatisch generiert von 996-Scanner · GitHub Actions
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

    seen      = load_seen_ids()
    new       = find_new(listings, seen)
    print(f"  {len(new)} davon neu")

    # State aktualisieren
    all_ids = seen | {l["id"] for l in listings}
    save_seen_ids(all_ids)

    # E-Mail immer senden (auch bei 0 neuen — als Lebenszeichen)
    subject = (
        f"🏎 996 Scanner: {len(new)} neue Angebote – {date.today():%d.%m.%Y}"
        if new else
        f"🏎 996 Scanner: Keine neuen Angebote – {date.today():%d.%m.%Y}"
    )
    html = build_html(new, len(listings))

    if GMAIL_USER and GMAIL_PASS:
        send_email(subject, html)
    else:
        print("GMAIL_USER / GMAIL_APP_PASSWORD nicht gesetzt – E-Mail übersprungen")
        print("HTML-Vorschau gespeichert: scanner/preview.html")
        with open("scanner/preview.html", "w") as f:
            f.write(html)

    print("Fertig.")


if __name__ == "__main__":
    main()
            if "aditem-main--top--left" in cls or "icon-pin" in cls:

            
                self._in_location = True

    def handle_endtag(self, tag):
        if tag == "article" and self._in_article:
            if self._current.get("id"):
                self.listings.append(self._current.copy())
            self._current = {}
            self._in_article = False
        if tag == "a":
            self._in_title = False
        if tag == "p":
            self._in_price = False
            self._in_location = False

    def handle_data(self, data):
        data = data.strip()
        if not data or not self._in_article:
            return
        if self._in_title and not self._current["title"]:
            self._current["title"] = data
        if self._in_price and not self._current["price"]:
            self._current["price"] = data
        if self._in_location and not self._current["location"]:
            self._current["location"] = data


def extract_listings_regex(html: str) -> list[dict]:
    """Fallback: extrahiert Anzeigen per Regex aus dem rohen HTML."""
    listings = []

    # Finde alle adid-Blöcke
    adid_blocks = re.finditer(r'data-adid="(\d+)"', html)
    for match in adid_blocks:
        adid = match.group(1)
        # Suche nach URL
        url_match = re.search(
            rf'href="(/s-anzeige/[^"]*/{adid}-\d+-\d+)"', html
        )
        url = ("https://www.kleinanzeigen.de" + url_match.group(1)) if url_match else ""

        # Titel aus URL ableiten
        title = ""
        if url_match:
            slug = url_match.group(1).split("/")[2] if url_match else ""
            title = slug.replace("-", " ").title()

        # Preis
        price_match = re.search(
            rf'{adid}.{{0,500}}?(\d{{2,3}}\.\d{{3}}\s*€|\d{{3,3}}\s*€|\d{{4,6}}\s*€)',
            html, re.DOTALL
        )
        price = price_match.group(1).strip() if price_match else "k.A."

        listings.append({
            "id": adid,
            "title": title or f"Anzeige #{adid}",
            "price": price,
            "location": "",
            "url": url,
            "date": "",
            "km": "",
            "year": "",
        })

    return listings


def fetch_listings(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    parser = ListingParser()
    parser.feed(html)

    listings = parser.listings

    # Fallback wenn Parser nichts findet
    if not listings:
        print("HTMLParser lieferte 0 Ergebnisse, versuche Regex-Fallback...")
        listings = extract_listings_regex(html)

    # Kilometerstand und Baujahr aus Titel/Beschreibung extrahieren
    km_pattern   = re.compile(r"(\d{2,3}[\.\s]?\d{3})\s*km", re.I)
    year_pattern = re.compile(r"\b(199[8-9]|200[0-9]|201[0-5])\b")
    for lst in listings:
        text = f"{lst['title']} {lst.get('desc','')}"
        m = km_pattern.search(text)
        if m:
            lst["km"] = m.group(0)
        m = year_pattern.search(text)
        if m:
            lst["year"] = m.group(1)

    return listings


# ─── State / Neue Angebote ermitteln ─────────────────────────────────────────

def load_seen_ids() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_ids(ids: set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(ids), f)


def find_new(listings: list[dict], seen: set) -> list[dict]:
    return [l for l in listings if l["id"] not in seen]


# ─── E-Mail ───────────────────────────────────────────────────────────────────

def build_html(new_listings: list[dict], total: int) -> str:
    today = date.today().strftime("%d.%m.%Y")
    count_new = len(new_listings)

    rows = ""
    for l in new_listings:
        title = l["title"] or "–"
        price = l["price"] or "k.A."
        loc   = l["location"] or "–"
        km    = l["km"] or "–"
        year  = l["year"] or "–"
        url   = l["url"] or "#"
        rows += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;">
            <a href="{url}" style="color:#185FA5;font-weight:600;text-decoration:none;">{title}</a>
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;white-space:nowrap;font-weight:600;color:#1D9E75;">{price}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#666;">{year}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#666;">{km}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#666;">{loc}</td>
        </tr>"""

    if not rows:
        rows = """<tr><td colspan="5" style="padding:20px;text-align:center;color:#888;">
            Heute keine neuen Angebote – alles bereits bekannt.
        </td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"><title>996 Scanner</title></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f5f5f5;">
<div style="max-width:700px;margin:24px auto;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">
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
      <div style="font-size:28px;font-weight:700;color:#444;">{total}</div>
      <div style="font-size:12px;color:#666;margin-top:2px;">Angebote gesamt</div>
    </div>
    <div style="flex:1;text-align:center;background:#fff;border-radius:8px;padding:14px;border:1px solid #eee;">
      <div style="font-size:28px;font-weight:700;color:#1D9E75;">148</div>
      <div style="font-size:12px;color:#666;margin-top:2px;">Basis (13.04.26)</div>
    </div>
  </div>

  <div style="padding:20px 28px;">
    <h2 style="margin:0 0 14px;font-size:15px;color:#333;">Neue Angebote</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead>
        <tr style="background:#f0f0f0;">
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">Titel</th>
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">Preis</th>
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">EZ</th>
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">KM</th>
          <th style="padding:8px 12px;text-align:left;font-weight:600;color:#555;">Ort</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <div style="padding:16px 28px;background:#fafafa;border-top:1px solid #eee;text-align:center;">
    <a href="{SEARCH_URL}" style="display:inline-block;background:#185FA5;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600;">
      Alle Angebote ansehen →
    </a>
  </div>

  <p style="text-align:center;padding:12px;font-size:11px;color:#bbb;margin:0;">
    Automatisch generiert von 996-Scanner · GitHub Actions
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

    seen      = load_seen_ids()
    new       = find_new(listings, seen)
    print(f"  {len(new)} davon neu")

    # State aktualisieren
    all_ids = seen | {l["id"] for l in listings}
    save_seen_ids(all_ids)

    # E-Mail immer senden (auch bei 0 neuen — als Lebenszeichen)
    subject = (
        f"🏎 996 Scanner: {len(new)} neue Angebote – {date.today():%d.%m.%Y}"
        if new else
        f"🏎 996 Scanner: Keine neuen Angebote – {date.today():%d.%m.%Y}"
    )
    html = build_html(new, len(listings))

    if GMAIL_USER and GMAIL_PASS:
        send_email(subject, html)
    else:
        print("GMAIL_USER / GMAIL_APP_PASSWORD nicht gesetzt – E-Mail übersprungen")
        print("HTML-Vorschau gespeichert: scanner/preview.html")
        with open("scanner/preview.html", "w") as f:
            f.write(html)

    print("Fertig.")


if __name__ == "__main__":
    main()
