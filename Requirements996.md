# Requirements: konfigurierbarer Kleinanzeigen-Markt-Scanner

## 1. Ziel

Zu entwickeln ist eine kleine, automatisiert ausführbare Anwendung, die regelmäßig eine konfigurierte Suche auf einem Online-Kleinanzeigenmarkt abruft, Angebote strukturiert erfasst, neue Angebote erkennt und bewertet. Sie versendet eine HTML-Zusammenfassung per E-Mail, speichert die Marktentwicklung historisch und erzeugt daraus ein statisches, öffentlich auslieferbares Dashboard.

Die Anwendung soll ohne dauerhaft laufenden Server auskommen. Ein geplanter GitHub-Actions-Workflow übernimmt Tests, Scan, Persistierung und Deployment.

Diese Spezifikation ist bewusst wiederverwendbar. Werte in spitzen Klammern müssen für ein ähnliches Projekt ersetzt werden. Abschnitt 14 enthält die Konfiguration des aktuellen Referenzprojekts.

## 2. Nutzer und zentrale Anwendungsfälle

Primärer Nutzer ist eine Person, die einen bestimmten Gebrauchtmarkt kontinuierlich beobachten möchte.

Die Anwendung muss folgende Anwendungsfälle abdecken:

1. Eine gespeicherte Suche täglich und bei Bedarf manuell abrufen.
2. Alle Ergebnisse über mehrere Ergebnisseiten hinweg erfassen.
3. Neue Angebote gegenüber früheren Läufen erkennen.
4. Angebote anhand konfigurierbarer Kriterien priorisieren.
5. Eine übersichtliche E-Mail mit neuen Top-Angeboten und dauerhaften Favoriten senden.
6. Preis- und Bestandsentwicklung in einer lokalen Datenbank dokumentieren.
7. Ein statisches Markt-Dashboard generieren und über GitHub Pages veröffentlichen.
8. Bei Parser- oder Abruffehlern abbrechen, ohne den bisherigen Zustand fälschlich fortzuschreiben.

## 3. Systemgrenzen

### Im Umfang

- Abruf öffentlich sichtbarer Suchergebnisse einer fest konfigurierten URL
- Extraktion und Normalisierung von Angebotsdaten
- Erkennung neuer, entfernter und preislich veränderter Angebote
- regelbasiertes Scoring und Keyword-Highlights
- HTML-E-Mail via SMTP
- persistenter JSON-Zustand und SQLite-Markthistorie
- statisches HTML-Dashboard ohne Backend
- tägliche Automatisierung, Tests und GitHub-Pages-Deployment

### Nicht im Umfang

- Benutzerkonten oder interaktive Webanwendung
- Kaufabwicklung oder Kontaktaufnahme mit Verkäufern
- Ermittlung tatsächlicher Verkaufspreise
- präzise Routenplanung; Entfernungen sind nur grobe Luftlinienwerte
- Machine Learning oder automatische semantische Bewertung
- parallele Überwachung beliebig vieler Suchprofile in einer einzelnen Ausführung

## 4. Konfiguration

Mindestens folgende Werte müssen zentral und leicht änderbar sein:

| Wert | Bedeutung |
|---|---|
| `SEARCH_URL` | Vollständige URL der gespeicherten Suche inklusive Filter |
| `BASE_URL` | Domain der Datenquelle für relative Angebots-URLs |
| `HOME_POSTAL_CODE` | Referenz-Postleitzahl für das Entfernungsranking |
| `HOME_COORDINATES` | Breiten- und Längengrad des Referenzorts |
| `HIGHLIGHT_GROUPS` | Labels, Farben und zugehörige Keywords |
| `SCORING_RULES` | Gewichte und Grenzwerte der Bewertung |
| `MARKET_DASHBOARD_URL` | Öffentliche URL des Markt-Dashboards |
| `SCAN_SCHEDULE` | täglicher Ausführungszeitpunkt in UTC |
| Branding | Projektname, Titel, Modell-/Suchbezeichnung und Texte |

Zugangsdaten dürfen nicht im Repository stehen. Sie werden ausschließlich über folgende Umgebungsvariablen beziehungsweise GitHub Secrets bereitgestellt:

- `GMAIL_USER`: SMTP-Absender und Gmail-Benutzer
- `GMAIL_APP_PASSWORD`: Gmail-App-Passwort
- `NOTIFY_EMAIL`: Empfänger; fällt lokal optional auf `GMAIL_USER` zurück
- `DEBUG`: aktiviert bei Wert `1` HTML-Dumps der abgerufenen Ergebnisseiten
- `MARKET_DASHBOARD_URL`: überschreibt optional die Standard-Dashboard-URL

## 5. Datenabruf und Parsing

### FR-01: HTTP-Abruf

- Die Suchseite muss mit realistischen HTTP-Headern und deutschem Sprachwunsch abgerufen werden.
- Pro Seite gelten ein Timeout von 20 Sekunden und bis zu drei Versuche.
- Zwischen Wiederholungen ist ein kurzer, ansteigender Abstand einzuhalten.
- Nach endgültigem Fehlschlag muss der Lauf mit verständlicher Fehlermeldung fehlschlagen.

### FR-02: Seitennavigation

- Der Scanner muss einem als „nächste Seite“ gekennzeichneten Link folgen.
- Bereits besuchte URLs dürfen nicht erneut abgerufen werden.
- Angebots-IDs müssen seitenübergreifend dedupliziert werden.
- Zum Schutz vor Endlosschleifen gilt ein konfigurierbares Seitenlimit; Referenzwert: 20 Seiten.

### FR-03: Angebotsmodell

Jedes Angebot muss möglichst vollständig in dieser Struktur vorliegen:

| Feld | Typ | Beschreibung |
|---|---|---|
| `id` | String | stabile ID der Anzeige; Pflichtfeld |
| `title` | String | Titel |
| `price` | String | originale Preisdarstellung |
| `location` | String | Ortsangabe |
| `postal_code` | String | fünfstellige PLZ, sofern ermittelbar |
| `url` | String | absolute Detail-URL |
| `online_since` | String | „Heute“, „Gestern“ oder Datum |
| `mileage` | String | originale Kilometerangabe |
| `model_year` | String/Integer | Baujahr beziehungsweise Erstzulassungsjahr |
| `image_url` | String | Vorschaubild, sofern verfügbar |
| `description` | String | Kurzbeschreibung |
| `distance_km` | Integer/null | berechnete ungefähre Luftlinie |

### FR-04: Parser-Strategie

- Primär wird semantisches HTML beziehungsweise JSON-LD innerhalb eines Angebots verwendet.
- CSS-/Strukturmerkmale dienen als kompatibler zweiter Extraktionsweg.
- Ein begrenzter Regex-Fallback soll bei leicht verändertem Markup wenigstens ID, URL, abgeleiteten Titel, Preis, Kilometerstand, Jahr, PLZ und Bild erfassen.
- HTML-Entities müssen dekodiert werden.
- Relative Detail-URLs müssen in absolute URLs umgewandelt werden.

### FR-05: Plausibilitätsprüfung

Vor jeder Zustandsänderung muss die Ergebnisqualität geprüft werden:

- Null Ergebnisse gelten als Fehler, nicht als leerer Markt.
- Mindestens die Hälfte der Ergebnisse muss jeweils einen nicht rein numerischen Titel, einen Preis und eine URL besitzen.
- Bei Unterschreitung muss der Lauf abbrechen und ausdrücklich melden, dass der Zustand nicht aktualisiert wurde.

## 6. Highlight-Erkennung und Bewertung

### FR-06: Highlights

- Titel und Kurzbeschreibung werden ohne Beachtung der Groß-/Kleinschreibung nach konfigurierten Keywords durchsucht.
- Pro Treffergruppe wird höchstens ein Highlight vergeben.
- Highlights werden in E-Mail-Inhalten als farbige Badges dargestellt.

Die Referenzgruppen sind:

- Stahlbuchsen
- Motorrevision
- Scheckheft
- Wenig Kilometer
- Erste Hand
- Facelift

### FR-07: Ranking

Die Bewertungsfunktion muss einen Gesamtscore sowie eine Aufschlüsselung nach `primary`, `price`, `distance` und `other` liefern. Höhere Werte sind besser.

Referenzlogik:

- wichtigstes Keyword-Highlight: `+100`
- Preis: linear `0..50`; voller Bonus bis 8.000 EUR, kein Bonus ab 35.000 EUR
- Entfernung: linear `0..30`; voller Bonus bis 50 km, kein Bonus ab 600 km
- Scheckheft: `+15`
- erste Hand: `+10`
- Facelift: `+8`
- wenig Kilometer: `+5`
- Farbe Vesuvio: `+100`
- Stahlbuchsen: `+100`

Fehlende Preise oder Postleitzahlen dürfen den Lauf nicht verhindern; der jeweilige Teilscore beträgt dann null. Die Entfernung wird per Haversine-Formel aus der Referenzposition und groben Koordinaten der ersten zwei PLZ-Ziffern berechnet.

Für die Qualität der Top-Listen gelten zusätzlich folgende Regeln:

- Such- und Ankaufanzeigen werden nicht als Verkaufsofferten gerankt.
- Anzeigenpreise unter 8.000 EUR gelten als unplausible Platzhalterpreise.
- Nennt die Beschreibung ausdrücklich einen plausiblen Kaufpreis, wird dieser für das Ranking verwendet und in der Ausgabe gekennzeichnet.
- Ein unplausibler Anzeigenpreis ohne korrigierbaren Beschreibungspreis schließt das Angebot aus den Top-Listen aus; in der vollständigen Liste bleibt es sichtbar.
- Vor der Auswahl der Top 3 wird die jeweilige Detailseite auf Verfügbarkeit geprüft. Eindeutig entfernte Anzeigen werden übersprungen, bis bis zu drei verfügbare Kandidaten gefunden sind.
- Nicht eindeutige Netzwerk-, TLS-, Server- oder Rate-Limit-Fehler dürfen ein Angebot nicht dauerhaft aus den Favoriten entfernen.

## 7. Zustandsverwaltung

### FR-08: Bereits gesehene Angebote

- Bereits gemeldete IDs werden dauerhaft in `scanner/last_seen_ids.json` gespeichert.
- Ein Angebot ist neu, wenn seine ID in diesem Bestand nicht vorkommt.
- Beim Speichern werden alle bisher gesehenen und alle aktuell gefundenen IDs vereinigt; alte IDs bleiben erhalten.
- Die JSON-Liste muss deterministisch sortiert gespeichert werden.

### FR-09: All-Time-Favoriten

- Die drei bestbewerteten jemals beobachteten Angebote werden in `scanner/all_time_favorites.json` gespeichert.
- Vorherige Favoriten werden mit allen aktuellen Angeboten anhand der ID zusammengeführt und neu bewertet.
- Aktuelle Daten ersetzen ältere Daten derselben Anzeige.
- Ein nicht mehr aktiver, aber weiterhin hoch bewerteter Favorit bleibt erhalten.

### FR-10: Sicheres Aktualisierungsverhalten

- Bei Abruf-, Parser-, Validierungs- oder E-Mail-Fehlern dürfen neue IDs nicht als erfolgreich gemeldet gespeichert werden.
- Im lokalen Vorschau-Modus dürfen weder Seen-State, Favoriten noch Markthistorie verändert werden.
- Persistente Dateien werden erst im erfolgreich konfigurierten Versandlauf aktualisiert.

## 8. E-Mail-Benachrichtigung

### FR-11: Versand

- Versand als UTF-8-HTML-Mail über Gmail SMTP mit SSL auf Port 465.
- Betreff enthält Projektname, Anzahl neuer Angebote beziehungsweise „Keine neuen Angebote“ und das Tagesdatum.
- Fehlen SMTP-Zugangsdaten, wird keine Mail gesendet und stattdessen `scanner/preview.html` erzeugt.

### FR-12: Inhalt und Reihenfolge

Die E-Mail muss enthalten:

1. Kopfbereich mit Projekt-/Suchbezeichnung und Datum
2. Kennzahlen: neue Angebote, neue Angebote mit Highlight, Gesamtbestand
3. bis zu drei bestbewertete neue Angebote als große Karten
4. weitere neue Angebote als kompakte Tabelle
5. die drei All-Time-Favoriten als große Karten
6. Links zur Originalsuche und zum Markt-Dashboard

Eine Top-Karte zeigt Rang, Score, Titel, Bild oder Platzhalter, Preis, Jahr, Kilometerstand, Ort, Distanz, Online-Datum, Highlights und eine auf ungefähr 220 Zeichen gekürzte Beschreibung. Die kompakte Tabelle zeigt Titel, Preis, Jahr, Kilometerstand und Ort/Distanz.

Gibt es keine neuen Angebote, muss dies deutlich angezeigt werden; All-Time-Favoriten werden trotzdem ausgegeben.

### FR-13: Ausgabesicherheit

Alle aus externen Anzeigen übernommenen Texte und URL-Attribute müssen vor Einbettung in HTML korrekt escaped werden. Externer Inhalt darf kein ausführbares HTML oder JavaScript in die E-Mail einschleusen.

## 9. Markthistorie

### FR-14: SQLite-Datenmodell

Die Datenbank `scanner/market_history.sqlite` enthält mindestens:

- `scans`: Zeitpunkt und aggregierte Kennzahlen je Lauf
- `listings`: stabile Stammdaten mit erstem und letztem Sichtungszeitpunkt
- `observations`: je Scan und Anzeige Preis, Ort, PLZ, Kilometerstand, Modelljahr und Score

Die Kombination aus Scan und Angebots-ID muss eindeutig sein. Beziehungen sind per Foreign Key abzusichern; Beobachtungen müssen nach Angebots-ID und Scan effizient abfragbar sein.

### FR-15: Scan-Kennzahlen

Für jeden erfolgreichen Lauf werden atomar gespeichert:

- Anzahl aller Angebote und Angebote mit parsebarem Preis
- Median, Durchschnitt, Minimum und Maximum der Angebotspreise
- Anzahl neuer und seit dem vorherigen Scan entfernter Angebote
- Anzahl gestiegener und gesunkener Preise

Zeitpunkte werden in UTC im ISO-8601-Format gespeichert. Der erste Scan wertet alle aktuellen Angebote als neu.

### FR-16: Preisänderungen

- Preisänderungen werden durch Vergleich derselben Angebots-ID über aufeinanderfolgende Beobachtungen erkannt.
- Das Dashboard erhält die letzten 50 Änderungen mit Zeitpunkt, Titel, Link, vorherigem und aktuellem Preis.

## 10. Statisches Dashboard

### FR-17: Generierung

- Das Dashboard wird aus einer HTML-Vorlage und in die Seite eingebetteten JSON-Daten generiert.
- Ausgabeziel ist `docs/index.html`.
- Es darf für die Anzeige kein Backend und keine externe JavaScript-Bibliothek benötigen.
- Eingebettete Daten müssen gegen ein vorzeitiges Schließen des Script-Tags abgesichert sein.

### FR-18: Darstellung

Das responsive Dashboard zeigt:

- Zeitpunkt des letzten Scans
- aktuellen Medianpreis samt Veränderung zum vorherigen Scan
- aktuellen beobachteten Bestand
- neue und entfernte Angebote
- Preisspanne und Zahl der Angebote mit Preis
- Zeitreihen für Medianpreis und Bestand
- Tabelle der letzten Preisänderungen mit Links zu den Anzeigen
- Hinweis, dass Angebotspreise keine Verkaufspreise sind und die Statistik durch Datenqualität beeinflusst werden kann

Die Seite muss auf Desktop und Mobilgeräten nutzbar sein. Bei noch fehlenden Daten sind verständliche Leerzustände anzuzeigen.

## 11. Automatisierung und Deployment

### FR-19: GitHub Actions

Ein Workflow muss:

1. täglich per Cron und manuell startbar sein,
2. Python 3.11 einrichten,
3. vor dem Scan alle automatisierten Tests ausführen,
4. den Scanner mit Secrets als Umgebungsvariablen starten,
5. geänderten Seen-State, Favoriten, SQLite-Datenbank und Dashboard committen und auf den Standardbranch pushen,
6. bei unverändertem State keinen leeren Commit erzeugen,
7. den Inhalt von `docs/` über GitHub Pages deployen.

Ein Push, der Dashboard oder Workflow betrifft, darf ein Pages-Deployment auslösen, aber keinen zweiten Scan. Gleichzeitige Workflow-Läufe dürfen sich nicht gegenseitig überschreiben.

Referenzzeitplan: täglich `07:00 UTC`.

## 12. Nichtfunktionale Anforderungen

- **Laufzeit:** Python 3.11 oder neuer.
- **Abhängigkeiten:** Kernimplementierung ausschließlich mit Python-Standardbibliothek; keine Installation zusätzlicher Laufzeitpakete erforderlich.
- **Wartbarkeit:** Abruf, Parsing, Bewertung, Zustand, E-Mail, Historie und Dashboard sind in klar getrennten Funktionen beziehungsweise Modulen organisiert.
- **Robustheit:** Netzwerkfehler werden wiederholt; Markup-Schäden werden vor State-Updates erkannt.
- **Idempotenz:** Eine Anzeige wird anhand ihrer stabilen ID nur einmal als neu gemeldet.
- **Nachvollziehbarkeit:** Der Lauf protokolliert mindestens Start, Ergebnisanzahl, Zahl neuer Angebote, Parser-Feldabdeckung, Versand und gespeicherte Marktkennzahlen.
- **Datenschutz:** Secrets erscheinen weder im Code noch in Logs, HTML, Datenbank oder Commits.
- **Datenquelle:** Abruffrequenz und Implementierung müssen die Nutzungsbedingungen und technischen Grenzen der gewählten Quelle beachten.
- **Barrierearmut:** Dashboard-Grafiken besitzen sinnvolle Beschriftungen; Farbe ist nicht die einzige Informationsträgerin.

## 13. Tests und Abnahmekriterien

Mindestens folgende automatisierte Tests müssen vorhanden sein:

1. Aktuelles Beispiel-Markup wird vollständig in das Angebotsmodell überführt.
2. Der Link zur nächsten Ergebnisseite wird erkannt.
3. Korrupte/unvollständige Ergebnisse führen vor einem State-Update zum Abbruch.
4. Neue Angebote werden nach Score sortiert und die Top 3 mit Online-Datum angezeigt.
5. Weitere neue Angebote erscheinen zwischen neuen Top 3 und All-Time-Favoriten.
6. Alte, hoch bewertete Favoriten bleiben trotz Abwesenheit im aktuellen Markt erhalten.
7. Externe Inhalte werden in der HTML-Mail escaped.
8. Seen-IDs werden deterministisch sortiert gespeichert.
9. Zwei aufeinanderfolgende Snapshots liefern korrekte Mediane sowie Neu-, Abgangs- und Preisänderungszahlen.
10. Das generierte Dashboard enthält die konfigurierten Projekt-/Suchtexte und eingebetteten Daten.

Die Abnahme ist erfolgreich, wenn alle Tests bestehen, ein lokaler Lauf ohne Secrets ausschließlich eine HTML-Vorschau erzeugt und ein konfigurierter Workflow-Lauf Mail, State, Historie und Dashboard aktualisiert sowie das Dashboard veröffentlicht.

## 14. Referenzkonfiguration dieses Projekts

Für eine funktional möglichst genaue Reproduktion von `996Runner` gelten folgende Werte:

- Produktname: `996Runner` / `Porsche 996 Scanner`
- Datenquelle: `kleinanzeigen.de`
- Suche: Porsche 996, Kategorie Autos, Schaltgetriebe
- Such-URL: `https://www.kleinanzeigen.de/s-autos/996/k0c216+autos.shift_s:manuell`
- Referenzort: PLZ `38533`, Koordinaten ungefähr `52.42, 10.60`
- E-Mail: Gmail SMTP
- Dashboard: GitHub Pages
- Ausführung: täglich um `07:00 UTC`
- Branding des Dashboards: dunkles „996 Telemetry“-Marktdaten-Layout
- persistente Dateien:
  - `scanner/last_seen_ids.json`
  - `scanner/all_time_favorites.json`
  - `scanner/market_history.sqlite`
  - `docs/index.html`

## 15. Erwartete Projektstruktur

```text
.
├── .github/workflows/scanner.yml
├── docs/
│   ├── .nojekyll
│   └── index.html
├── scanner/
│   ├── scanner.py
│   ├── market_history.py
│   ├── dashboard_template.html
│   ├── last_seen_ids.json
│   ├── all_time_favorites.json
│   └── market_history.sqlite
├── tests/
│   ├── __init__.py
│   └── test_scanner.py
├── .gitignore
├── README.md
└── Requirements.md
```

## 16. Lieferumfang für eine Neuimplementierung

Eine vollständige Umsetzung umfasst Quellcode, HTML-Vorlage, initial leere State-Dateien beziehungsweise automatisch erzeugbaren State, Datenbankschema, Tests, GitHub-Actions-Workflow, Pages-Konfiguration, `.gitignore` und eine Setup-Anleitung. Die Anleitung beschreibt Gmail-App-Passwort, GitHub Secrets, Workflow-Schreibrechte, Pages-Aktivierung, lokalen Vorschau-Modus, Debug-Modus und die Anpassung von Suche, Standort, Scoring und Zeitplan.



