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
DEBUG_HTML = "scanner/debug_last_fetch.html"   # wird bei DEBUG=1 geschrieben

GMAIL_USER   = os.environ.get("GMAIL_USER", "")
GMAIL_PASS   = os.environ.get("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", GMAIL_USER)
DEBUG        = os.environ.get("DEBUG", "0") == "1"

# ─── Keywords ────────────────────────────────────────────────────────────────

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
            "motoren überholt", "komplett überholt", "revidiert", "motor"
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


# ─── HTML Parser ─────────────────────────────────────────────────────────────

class ListingParser(HTMLParser):
    """
    Parst Kleinanzeigen-Suchergebnisse.

    Aktuelles DOM-Layout (Stand 2024/2025):
      <article data-adid="...">
        ...
        <a href="/s-anzeige/..." class="... ellipsis ...">TITEL</a>
        ...
        <p class="aditem-main--middle--price-shipping--price">PREIS</p>
        ...
        <span class="aditem-main--top--left">ORT · DATUM</span>
        ...
        <!-- KM-Stand und Baujahr stehen in eigenen <li>-Tags oder als
             freitext in der Beschreibung; wir sammeln den gesamten
             article-Text und extrahieren anschließend per Regex -->
      </article>
    """

    def __init__(self):
        super().__init__()
        self.listings: list[dict] = []

        # Zustand für das aktuelle article-Element
        self._cur: dict = {}
        self._in_article = False
        self._article_text = ""       # gesamter Rohtext innerhalb des article

        # Welches Feld füllen wir gerade?
        self._capture_title    = False
        self._capture_price    = False
        self._capture_location = False

    # ── Hilfsmethode ──────────────────────────────────────────────────────────
    def _cls(self, attrs: dict) -> str:
        return attrs.get("class", "") or ""

    # ── Tag öffnet sich ───────────────────────────────────────────────────────
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls   = self._cls(attrs)

        # ── Article-Container beginnt ────────────────────────────────────────
        if tag == "article" and "data-adid" in attrs:
            self._cur = {
                "id": attrs["data-adid"],
                "title": "",
                "price": "",
                "location": "",
                "url": "",
                "date": "",
                "km": "",
                "year": "",
            }
            self._in_article  = True
            self._article_text = ""
            return

        if not self._in_article:
            return

        # ── Titel-Link ───────────────────────────────────────────────────────
        # Kleinanzeigen nutzt verschiedene Klassen; wir matchen auf den href-
        # Pfad – das ist stabiler als Klassen-Namen.
        if tag == "a":
            href = attrs.get("href", "")
            if href.startswith("/s-anzeige/") and not self._cur["url"]:
                self._cur["url"] = "https://www.kleinanzeigen.de" + href
                self._capture_title = True
            return

        # ── Preis ─────────────────────────────────────────────────────────────
        # Mögliche Klassen (Kleinanzeigen baut das DOM gerne um):
        #   aditem-main--middle--price-shipping--price
        #   aditem-main--middle--price
        if tag == "p" and (
            "price" in cls.lower()
        ):
            self._capture_price = True
            return

        # ── Ort / Datum ───────────────────────────────────────────────────────
        # <span class="... aditem-main--top--left ...">ORT · DATUM</span>
        if tag in ("span", "p") and "aditem-main--top--left" in cls:
            self._capture_location = True
            return

    # ── Tag schließt sich ─────────────────────────────────────────────────────
    def handle_endtag(self, tag):
        if tag == "article" and self._in_article:
            # KM und Baujahr per Regex aus dem gesammelten article-Text ziehen
            self._extract_km_year()
            if self._cur.get("id"):
                self.listings.append(self._cur.copy())
            self._cur          = {}
            self._in_article   = False
            self._article_text = ""
            return

        if tag == "a":
            self._capture_title = False
        if tag == "p":
            self._capture_price    = False
            self._capture_location = False
        if tag == "span":
            self._capture_location = False

    # ── Textinhalt ────────────────────────────────────────────────────────────
    def handle_data(self, data):
        if not self._in_article:
            return
        stripped = data.strip()
        if not stripped:
            return

        # Rohtext aufsammeln (für KM/Jahr-Extraktion)
        self._article_text += " " + stripped

        if self._capture_title and not self._cur["title"]:
            self._cur["title"] = stripped
        if self._capture_price and not self._cur["price"]:
            # Preiszeilen können "VB" / "k.A." / "12.500 €" enthalten
            if re.search(r"[\d€]|VB|Preis|k\.A", stripped, re.I):
                self._cur["price"] = stripped
        if self._capture_location and not self._cur["location"]:
            self._cur["location"] = stripped

    # ── Extraktion KM / Baujahr aus article-Rohtext ───────────────────────────
    def _extract_km_year(self):
        text = self._article_text

        # KM-Stand: "123.456 km" oder "123456 km"
        km_m = re.search(r"\b(\d{1,3}[\.\s]?\d{3})\s*km\b", text, re.I)
        if km_m:
            self._cur["km"] = km_m.group(0).strip()

        # Baujahr: 4-stellige Zahl im Bereich 1997–2005
        year_m = re.search(r"\b(199[7-9]|200[0-5])\b", text)
        if year_m:
            self._cur["year"] = year_m.group(1)

        # Datum der Anzeige (z.B. "Heute" / "Gestern" / "15.04.2025")
        date_m = re.search(r"\b(Heute|Gestern|\d{2}\.\d{2}\.\d{4})\b", text, re.I)
        if date_m:
            self._cur["date"] = date_m.group(1)


# ─── Fetch ────────────────────────────────────────────────────────────────────

def fetch_listings(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    if DEBUG:
        os.makedirs("scanner", exist_ok=True)
        with open(DEBUG_HTML, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [DEBUG] HTML gespeichert: {DEBUG_HTML}")

    parser = ListingParser()
    parser.feed(html)
    listings = parser.listings

    if not listings:
        print("  HTMLParser lieferte 0 Ergebnisse – versuche Regex-Fallback...")
        listings = _regex_fallback(html)

    print(f"  Parser: {len(listings)} Angebote, "
          f"davon Titel befüllt: {sum(1 for l in listings if l['title'])}, "
          f"Preis befüllt: {sum(1 for l in listings if l['price'])}, "
          f"Ort befüllt: {sum(1 for l in listings if l['location'])}")

    return listings


def _regex_fallback(html: str) -> list[dict]:
    """Notfall-Extraktion via Regex, wenn der HTML-Parser scheitert."""
    listings = []
    for m in re.finditer(r'data-adid="(\d+)"', html):
        adid = m.group(1)
        url_m = re.search(rf'href="(/s-anzeige/[^"]*{adid}[^"]*)"', html)
        url   = ("https://www.kleinanzeigen.de" + url_m.group(1)) if url_m else ""

        # Titel aus dem Link-Slug ableiten
        title = ""
        if url_m:
            parts = url_m.group(1).split("/")
            if len(parts) >= 3:
                title = parts[2].replace("-", " ").title()

        # Preis im umliegenden Kontext (bis 800 Zeichen nach adid)
        pos   = m.end()
        chunk = html[pos:pos+800]
        p_m   = re.search(r"(\d{1,3}(?:\.\d{3})*)\s*€", chunk)
        price = (p_m.group(0)) if p_m else "k.A."

        # KM
        km_m  = re.search(r"\b(\d{1,3}[\.\s]?\d{3})\s*km\b", chunk, re.I)
        km    = km_m.group(0).strip() if km_m else ""

        # Baujahr
        yr_m  = re.search(r"\b(199[7-9]|200[0-5])\b", chunk)
        year  = yr_m.group(1) if yr_m else ""

        listings.append({
            "id": adid, "title": title or f"Anzeige #{adid}",
            "price": price, "location": "", "url": url,
            "date": "", "km": km, "year": year,
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
        json.dump(list(ids), f)


def find_new(listings: list[dict], seen: set) -> list[dict]:
    return [l for l in listings if l["id"] not in seen]


# ─── E-Mail ───────────────────────────────────────────────────────────────────

def build_html(new_listings: list[dict], total: int) -> str:
    today         = date.today().strftime("%d.%m.%Y")
    count_new     = len(new_listings)

    def sort_key(l):
        return 0 if detect_highlights(f"{l.get('title','')} {l.get('desc','')}") else 1

    sorted_listings = sorted(new_listings, key=sort_key)
    highlight_count = sum(
        1 for l in new_listings
        if detect_highlights(f"{l.get('title','')} {l.get('desc','')}")
    )

    rows = ""
    for l in sorted_listings:
        title  = l["title"]    or "–"
        price  = l["price"]    or "k.A."
        loc    = l["location"] or "–"
        km     = l["km"]       or "–"
        year   = l["year"]     or "–"
        url    = l["url"]      or "#"

        full_text  = f"{title} {l.get('desc', '')}"
        highlights = detect_highlights(full_text)
        is_top     = bool(highlights)

        row_bg      = "background:#fffbf0;" if is_top else ""
        left_border = "border-left:3px solid #E6A817;" if is_top else "border-left:3px solid transparent;"

        badges = "".join(
            f'<span style="display:inline-block;font-size:10px;font-weight:600;'
            f'padding:2px 7px;border-radius:4px;margin-left:5px;'
            f'background:{h["bg"]};color:{h["color"]};">{h["label"]}</span>'
            for h in highlights
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

    seen = load_seen_ids()
    new  = find_new(listings, seen)
    print(f"  {len(new)} davon neu")

    all_ids = seen | {l["id"] for l in listings}
    save_seen_ids(all_ids)

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
        os.makedirs("scanner", exist_ok=True)
        with open("scanner/preview.html", "w") as f:
            f.write(html)
        print("HTML-Vorschau gespeichert: scanner/preview.html")

    print("Fertig.")


if __name__ == "__main__":
    main()
        
