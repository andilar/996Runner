"""Opt-in live contract check for the Kleinanzeigen search markup."""

import unittest
from pathlib import Path

from scanner.scanner import (
    ListingParser,
    SEARCH_URL,
    _fetch_html,
    _regex_fallback,
    _validate_listings,
    deduplicate_listings,
)


FAILURE_HTML = Path("scanner/live_check_failure.html")


class LiveScannerContractTest(unittest.TestCase):
    def test_live_search_page_still_matches_parser_contract(self):
        html = _fetch_html(SEARCH_URL)
        try:
            parser = ListingParser()
            parser.feed(html)
            parsed_listings = parser.listings or _regex_fallback(html)
            _validate_listings(parsed_listings)
            listings = deduplicate_listings(parsed_listings)
            duplicate_count = len(parsed_listings) - len(listings)

            coverage = {
                field: sum(bool(item.get(field)) for item in listings)
                for field in ("title", "price", "location", "url", "image", "desc")
            }
            print(
                f"Live-Contract: {len(parsed_listings)} geparst, "
                f"{len(listings)} eindeutige Angebote, {duplicate_count} Dubletten, "
                f"Feldabdeckung: {coverage}"
            )
            for field in ("location", "image", "desc"):
                self.assertGreater(
                    coverage[field], 0, f"Kein Live-Angebot enthält das Feld {field!r}"
                )
            self.assertEqual(
                len({item["id"] for item in listings}),
                len(listings),
                "Deduplizierung der Live-Ergebnisse ist fehlgeschlagen",
            )
        except Exception:
            FAILURE_HTML.write_text(html, encoding="utf-8")
            print(f"Fehlerhaftes Live-HTML gespeichert: {FAILURE_HTML}")
            raise


if __name__ == "__main__":
    unittest.main()
