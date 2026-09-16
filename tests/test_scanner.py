import unittest

from scanner.scanner import ListingParser, _validate_listings, build_html


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

    def test_rejects_corrupt_results_before_they_are_marked_seen(self):
        corrupt = [
            {"title": "22", "price": "", "url": "https://example.test/1"},
            {"title": "17", "price": "", "url": "https://example.test/2"},
        ]

        with self.assertRaisesRegex(RuntimeError, "State wird nicht aktualisiert"):
            _validate_listings(corrupt)

    def test_email_shows_top_three_by_score_with_online_dates(self):
        def listing(ad_id, title, price, date, desc=""):
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

        findings = [
            listing("4", "Low score", "35.000 €", "04.09.2026"),
            listing("2", "Second score", "8.000 €", "02.09.2026"),
            listing("1", "Top score", "35.000 €", "01.09.2026", "Motorrevision"),
            listing("3", "Third score", "20.000 €", "03.09.2026"),
        ]

        email = build_html(findings, total=4)

        self.assertIn("🏆 Top 3 nach Bewertung", email)
        self.assertLess(email.index("Top score"), email.index("Second score"))
        self.assertLess(email.index("Second score"), email.index("Third score"))
        self.assertIn("Online seit: 01.09.2026", email)
        self.assertIn("Online seit: 02.09.2026", email)
        self.assertIn("Online seit: 03.09.2026", email)
        self.assertNotIn("Online seit: 04.09.2026", email)


if __name__ == "__main__":
    unittest.main()
