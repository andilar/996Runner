"""Opt-in live contract check for the Kleinanzeigen search markup."""

import unittest
from pathlib import Path

from scanner.scanner import (
    ListingParser,
    SEARCH_URL,
    _fetch_html,
    _regex_fallback,
    _validate_listings,
)


FAILURE_HTML = Path("scanner/live_check_failure.html")


class LiveScannerContractTest(unittest.TestCase):
    def test_live_search_page_still_matches_parser_contract(self):
        html = _fetch_html(SEARCH_URL)
        try:
            parser = ListingParser()
            parser.feed(html)
            listings = parser.listings or _regex_fallback(html)
            _validate_listings(listings)

            coverage = {
                field: sum(bool(item.get(field)) for item in listings)
                for field in ("title", "price", "location", "url", "image", "desc")
            }
            print(f"Live-Contract: {len(listings)} Angebote, Feldabdeckung: {coverage}")
            for field in ("location", "image", "desc"):
                self.assertGreater(
                    coverage[field], 0, f"Kein Live-Angebot enthält das Feld {field!r}"
                )
            self.assertEqual(
                len({item["id"] for item in listings}),
                len(listings),
                "Live-Ergebnisse enthalten doppelte Angebots-IDs",
            )
        except Exception:
            FAILURE_HTML.write_text(html, encoding="utf-8")
            print(f"Fehlerhaftes Live-HTML gespeichert: {FAILURE_HTML}")
            raise


if __name__ == "__main__":
    unittest.main()
