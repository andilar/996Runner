import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from scanner.scanner import (
    ListingParser,
    _validate_listings,
    build_html,
    compute_score,
    save_seen_ids,
    update_all_time_favorites,
)
from scanner import scanner
from scanner.market_history import dashboard_data, generate_dashboard, record_market_snapshot


def make_listing(ad_id, title, price, date, desc=""):
    return {
        "id": ad_id,
        "title": title,
        "price": price,
        "location": "Testort",
        "url": f"https://example.test/{ad_id}",
        "date": date,
        "km": "100.000 km",
        "year": "2002",
        "image": "",
        "desc": desc,
        "plz": "",
    }


class ListingParserTest(unittest.TestCase):
    def test_parses_current_kleinanzeigen_markup(self):
        markup = """
        <article data-adid="3513853384"
                 data-href="/s-anzeige/porsche-996/3513853384-216-5197">
          <script type="application/ld+json">
            {"title":"Porsche 996 Carrera 4 Coup&eacute;",
             "description":"Motorrevision und l&uuml;ckenloses Scheckheft",
             "contentUrl":"https://img.example/car.jpg",
             "@type":"ImageObject"}
          </script>
          <a href="/s-anzeige/porsche-996/3513853384-216-5197">
            <img src="thumbnail.jpg"><div>22</div>
          </a>
          <div>
            <svg data-title="locationOutline"></svg><span>67551 Worms</span>
          </div>
          <h3><a href="/s-anzeige/porsche-996/3513853384-216-5197">
            Porsche 996 Carrera 4 Coup&eacute;
          </a></h3>
          <p class="text-onSurfaceSubdued">Kurzbeschreibung</p>
          <p class="my-xsmall text-title3 font-strong text-secondary">21.500 €</p>
          <span>99.000 km</span><span>EZ 06/2000</span><span>Gestern</span>
        </article>
        """

        parser = ListingParser()
        parser.feed(markup)

        self.assertEqual(len(parser.listings), 1)
        listing = parser.listings[0]
        self.assertEqual(listing["title"], "Porsche 996 Carrera 4 Coupé")
        self.assertEqual(listing["price"], "21.500 €")
        self.assertEqual(listing["location"], "67551 Worms")
        self.assertEqual(listing["plz"], "67551")
        self.assertEqual(listing["year"], "2000")
        self.assertEqual(listing["km"], "99.000 km")
        self.assertEqual(listing["date"], "Gestern")
        self.assertEqual(listing["desc"], "Motorrevision und lückenloses Scheckheft")
        self.assertEqual(listing["image"], "https://img.example/car.jpg")
        self.assertEqual(
            listing["url"],
            "https://www.kleinanzeigen.de/s-anzeige/porsche-996/3513853384-216-5197",
        )
        self.assertEqual(parser.next_href, "")

    def test_finds_next_results_page(self):
        parser = ListingParser()
        parser.feed('<a aria-label="Nächste Seite" href="/s-autos/seite:2/996/k0c216">Weiter</a>')
        self.assertEqual(parser.next_href, "/s-autos/seite:2/996/k0c216")

    def test_rejects_corrupt_results_before_they_are_marked_seen(self):
        corrupt = [
            {"title": "22", "price": "", "url": "https://example.test/1"},
            {"title": "17", "price": "", "url": "https://example.test/2"},
        ]

        with self.assertRaisesRegex(RuntimeError, "State wird nicht aktualisiert"):
            _validate_listings(corrupt)

    def test_email_always_shows_current_top_three_with_online_dates(self):
        findings = [
            make_listing("4", "Low score", "35.000 €", "04.09.2026"),
            make_listing("2", "Second score", "8.000 €", "02.09.2026"),
            make_listing("1", "Top score", "35.000 €", "01.09.2026", "Motorrevision"),
            make_listing("3", "Third score", "20.000 €", "03.09.2026"),
        ]

        email = build_html([], findings, findings)

        self.assertIn("🏆 All-Time-Favoriten", email)
        self.assertLess(email.index("Top score"), email.index("Second score"))
        self.assertLess(email.index("Second score"), email.index("Third score"))
        self.assertIn("Online seit: 01.09.2026", email)
        self.assertIn("Online seit: 02.09.2026", email)
        self.assertIn("Online seit: 03.09.2026", email)
        self.assertNotIn("Online seit: 04.09.2026", email)
        self.assertIn("Heute keine neuen Angebote", email)

    def test_email_places_new_top_three_above_all_time_favorites(self):
        new_findings = [
            make_listing("n1", "New first", "8.000 €", "Heute"),
            make_listing("n2", "New second", "20.000 €", "Heute"),
            make_listing("n3", "New third", "25.000 €", "Heute"),
            make_listing("n4", "New fourth", "35.000 €", "Heute"),
        ]
        favorites = [
            make_listing("f1", "Favorite first", "8.000 €", "01.08.2026", "Motorrevision"),
            make_listing("f2", "Favorite second", "20.000 €", "02.08.2026", "Motorrevision"),
            make_listing("f3", "Favorite third", "35.000 €", "03.08.2026", "Motorrevision"),
        ]

        email = build_html(new_findings, new_findings, favorites)

        new_heading = email.index("🆕 Top 3 der neuen Angebote")
        rest_heading = email.index("Weitere neue Angebote (1)")
        favorites_heading = email.index("🏆 All-Time-Favoriten")
        self.assertLess(new_heading, rest_heading)
        self.assertLess(rest_heading, favorites_heading)
        self.assertLess(email.index("New first"), email.index("New second"))
        self.assertLess(email.index("New second"), email.index("New third"))
        self.assertIn("New fourth", email[rest_heading:favorites_heading])

    def test_all_time_favorites_retain_older_high_scores(self):
        previous = [make_listing("old", "Old favorite", "8.000 €", "01.08.2026", "Motorrevision")]
        current = [make_listing("new", "New lower score", "35.000 €", "Heute")]

        favorites = update_all_time_favorites(previous, current)

        self.assertEqual(favorites[0]["id"], "old")
        self.assertEqual({item["id"] for item in favorites}, {"old", "new"})

    def test_email_escapes_listing_content(self):
        unsafe = make_listing("x", '<img src=x onerror="alert(1)">', "8.000 €", "Heute")
        email = build_html([unsafe], [unsafe], [])
        self.assertNotIn('<img src=x onerror="alert(1)">', email)
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", email)

    def test_seen_ids_are_saved_deterministically(self):
        with TemporaryDirectory() as directory:
            original = scanner.STATE_FILE
            scanner.STATE_FILE = Path(directory) / "seen.json"
            try:
                save_seen_ids({"3", "1", "2"})
                self.assertEqual(scanner.STATE_FILE.read_text(), '["1", "2", "3"]')
            finally:
                scanner.STATE_FILE = original

    def test_records_market_history_and_price_changes(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite"
            page = Path(directory) / "index.html"
            first = [
                make_listing("1", "Carrera", "20.000 €", "Heute"),
                make_listing("2", "Carrera 4", "30.000 €", "Heute"),
            ]
            second = [
                make_listing("1", "Carrera", "18.000 €", "Gestern"),
                make_listing("3", "Targa", "40.000 €", "Heute"),
            ]

            initial = record_market_snapshot(
                database, first, compute_score, datetime(2026, 9, 22, tzinfo=timezone.utc)
            )
            latest = record_market_snapshot(
                database, second, compute_score, datetime(2026, 9, 23, tzinfo=timezone.utc)
            )
            generate_dashboard(database, page)
            data = dashboard_data(database)

            self.assertEqual(initial["median_price_eur"], 25000)
            self.assertEqual(latest["new_count"], 1)
            self.assertEqual(latest["removed_count"], 1)
            self.assertEqual(latest["price_down_count"], 1)
            self.assertEqual(data["price_changes"][0]["current_price"], 18000)
            self.assertIn("Porsche 996", page.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
