import json
import sqlite3
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from scanner import scanner


LISTINGS = {
    "1001": ("Porsche 996 Carrera mit Motorrevision", "25.000 €", "Motorrevision"),
    "1002": ("Porsche 996 Carrera 4", "19.000 €", "Scheckheftgepflegt"),
    "1003": ("Porsche 996 Facelift", "18.000 €", "Facelift aus erster Hand"),
    "1004": ("Porsche 996 Coupé", "22.000 €", "Gepflegtes Fahrzeug"),
}


def listing_markup(base_url: str, ad_id: str) -> str:
    title, price, description = LISTINGS[ad_id]
    metadata = json.dumps(
        {
            "title": title,
            "description": description,
            "contentUrl": f"{base_url}/image/{ad_id}.jpg",
            "@type": "ImageObject",
        }
    )
    return f"""
    <article data-adid="{ad_id}" data-href="{base_url}/ad/{ad_id}">
      <script type="application/ld+json">{metadata}</script>
      <div><svg data-title="locationOutline"></svg><span>38533 Vordorf</span></div>
      <h3><a href="/s-anzeige/porsche-996/{ad_id}-216-1">{title}</a></h3>
      <p class="text-title3 font-strong text-secondary">{price}</p>
      <span>100.000 km</span><span>EZ 06/2002</span><span>Heute</span>
    </article>
    """


class ScannerHandler(BaseHTTPRequestHandler):
    requested_paths = []

    def do_GET(self):
        type(self).requested_paths.append(self.path)
        parsed = urlparse(self.path)
        base_url = f"http://127.0.0.1:{self.server.server_port}"

        if parsed.path == "/search":
            page = parse_qs(parsed.query).get("page", ["1"])[0]
            ids = ("1001", "1002") if page == "1" else ("1003", "1004")
            next_link = '<a rel="next" href="/search?page=2">Weiter</a>' if page == "1" else ""
            body = "<html><body>" + "".join(
                listing_markup(base_url, ad_id) for ad_id in ids
            ) + next_link + "</body></html>"
            self._send(200, body)
            return

        if parsed.path == "/ad/old-removed":
            self._send(410, "Diese Anzeige ist nicht mehr verfügbar")
            return

        if parsed.path.startswith("/ad/"):
            self._send(200, "<html><body><h1>Aktives Angebot</h1></body></html>")
            return

        self._send(404, "Nicht gefunden")

    def _send(self, status: int, body: str):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


class ScannerEndToEndTest(unittest.TestCase):
    def test_full_pipeline_from_http_to_mail_state_database_and_dashboard(self):
        ScannerHandler.requested_paths = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), ScannerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                state_file = root / "last_seen_ids.json"
                favorites_file = root / "all_time_favorites.json"
                database_file = root / "market_history.sqlite"
                dashboard_file = root / "docs" / "index.html"
                preview_file = root / "preview.html"
                base_url = f"http://127.0.0.1:{server.server_port}"
                state_file.write_text('["1001"]', encoding="utf-8")
                favorites_file.write_text(
                    json.dumps(
                        [
                            {
                                "id": "old-removed",
                                "title": "Alter Favorit mit Motorrevision",
                                "price": "8.000 €",
                                "location": "38533 Vordorf",
                                "url": f"{base_url}/ad/old-removed",
                                "date": "Gestern",
                                "km": "90.000 km",
                                "year": "2002",
                                "image": "",
                                "desc": "Motorrevision und Scheckheft",
                                "plz": "38533",
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                sent_messages = []

                with patch.multiple(
                    scanner,
                    SEARCH_URL=f"{base_url}/search?page=1",
                    STATE_FILE=state_file,
                    FAVORITES_FILE=favorites_file,
                    DATABASE_FILE=database_file,
                    DASHBOARD_FILE=dashboard_file,
                    SCANNER_DIR=root,
                    GMAIL_USER="runner@example.test",
                    GMAIL_PASS="app-password",
                    NOTIFY_EMAIL="owner@example.test",
                    DEBUG=False,
                ), patch.object(
                    scanner,
                    "send_email",
                    side_effect=lambda subject, html: sent_messages.append((subject, html)),
                ):
                    scanner.main()

                self.assertEqual(len(sent_messages), 1)
                subject, html = sent_messages[0]
                self.assertIn("3 neue Angebote", subject)
                self.assertIn("Porsche 996 Carrera 4", html)
                self.assertNotIn("Alter Favorit mit Motorrevision", html)
                self.assertFalse(preview_file.exists())

                self.assertEqual(
                    json.loads(state_file.read_text(encoding="utf-8")),
                    ["1001", "1002", "1003", "1004"],
                )
                favorites = json.loads(favorites_file.read_text(encoding="utf-8"))
                self.assertEqual(len(favorites), 3)
                self.assertNotIn("old-removed", {item["id"] for item in favorites})

                with closing(sqlite3.connect(database_file)) as connection:
                    scan = connection.execute(
                        "SELECT listing_count, new_count FROM scans"
                    ).fetchone()
                    observations = connection.execute(
                        "SELECT COUNT(*) FROM observations"
                    ).fetchone()[0]
                self.assertEqual(scan, (4, 4))
                self.assertEqual(observations, 4)
                dashboard = dashboard_file.read_text(encoding="utf-8")
                self.assertIn("Porsche 996", dashboard)
                self.assertIn("Top 3 Angebote", dashboard)
                self.assertIn('"top_rankings":', dashboard)

                self.assertIn("/search?page=1", ScannerHandler.requested_paths)
                self.assertIn("/search?page=2", ScannerHandler.requested_paths)
                self.assertIn("/ad/old-removed", ScannerHandler.requested_paths)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
