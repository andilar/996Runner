import unittest

from scanner.scanner import ListingParser, _validate_listings


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


if __name__ == "__main__":
    unittest.main()
