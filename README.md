# 996Runner

Täglicher Scanner für Porsche 996 Schaltwagen-Angebote auf Kleinanzeigen.de.
Sendet eine HTML-Zusammenfassung per Gmail mit den drei bestbewerteten neuen
Angeboten, weiteren Neuzugängen und den drei besten jemals gefundenen Angeboten.

Die öffentliche Marktübersicht unter
https://andilar.github.io/996Runner/ zeigt den historischen Medianpreis,
den beobachteten Bestand sowie Zu-, Abgänge und Preisänderungen.

## Wie es funktioniert

- GitHub Actions führt das Script täglich aus (Standard: 07:00 UTC).
- Der Scanner ruft die Suche auf und vergleicht mit `scanner/last_seen_ids.json`.
- Alle aktuellen Angebote werden bewertet:
  - **Motorrevision** (+100), **Preis** (+0..50), **Entfernung zu 38533 Vordorf** (+0..30)
  - Plus Boni für Scheckheft, 1. Hand, Facelift, Wenig KM
- Oben erscheinen die Top 3 der neuen Angebote als große Cards mit Online-Datum.
- Unten stehen die persistent gespeicherten All-Time-Top-3; weitere neue Angebote
  werden dazwischen als kompakte Tabelle angezeigt.

## Repo-Struktur

```
.
├── .github/
│   └── workflows/
│       └── scanner.yml        # GitHub Actions Workflow
├── docs/
│   └── index.html             # Generierte GitHub-Pages-Marktübersicht
├── scanner/
│   ├── scanner.py             # Hauptscript
│   ├── market_history.py      # SQLite-Historie und Dashboard-Generator
│   ├── market_history.sqlite  # Persistente Marktbeobachtungen
│   ├── last_seen_ids.json     # Bereits gemeldete Anzeigen
│   └── all_time_favorites.json # Persistente All-Time-Top-3
├── .gitignore
└── README.md
```

## Setup

### 1. Repo auf GitHub anlegen

Repo `996Runner` auf GitHub erstellen, lokal initialisieren und pushen:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin git@github.com:<dein-user>/996Runner.git
git push -u origin main
```

### 2. Gmail App-Passwort erzeugen

Normales Gmail-Passwort funktioniert nicht – es wird ein App-Passwort benötigt:

1. Bei Google 2FA aktivieren (falls noch nicht): https://myaccount.google.com/security
2. App-Passwort erstellen: https://myaccount.google.com/apppasswords
3. Als App-Name z.B. "996 Scanner" eintragen → das 16-stellige Passwort kopieren

### 3. GitHub Secrets setzen

Im Repo unter **Settings → Secrets and variables → Actions → New repository secret**
drei Secrets anlegen:

| Name                 | Wert                                    |
|----------------------|-----------------------------------------|
| `GMAIL_USER`         | `dein.name@gmail.com`                   |
| `GMAIL_APP_PASSWORD` | Das 16-stellige App-Passwort von oben   |
| `NOTIFY_EMAIL`       | Empfänger-Adresse (z.B. dieselbe Gmail) |

### 4. Workflow-Berechtigungen prüfen

Damit der Bot `last_seen_ids.json` zurück committen kann:

**Settings → Actions → General → Workflow permissions**:
- "Read and write permissions" auswählen
- Speichern

### 5. Erster Lauf

Im Tab **Actions** → "996 Scanner" → "Run workflow" → manuell starten.
Beim ersten Lauf werden alle aktuellen Angebote als "neu" gemeldet
(Mail wird entsprechend lang). Ab dem zweiten Lauf nur noch echte Neuzugänge.

Unter **Settings → Pages → Build and deployment** als Quelle **GitHub Actions**
auswählen. Jeder erfolgreiche Scan aktualisiert anschließend automatisch die
öffentliche Marktübersicht.

## Lokal testen

```bash
# Ohne E-Mail-Versand, schreibt nur scanner/preview.html und verändert keinen State
python3 scanner/scanner.py

# Mit E-Mail
export GMAIL_USER="..."
export GMAIL_APP_PASSWORD="..."
export NOTIFY_EMAIL="..."
python3 scanner/scanner.py

# Mit Debug-HTML-Dump (für Parser-Anpassungen)
DEBUG=1 python3 scanner/scanner.py
# → schreibt scanner/debug_last_fetch.html
```

## Anpassungen

**Suche ändern:** `SEARCH_URL` oben im Script anpassen.
Die URL einfach von Kleinanzeigen mit eingestellten Filtern kopieren.

**Heimat-PLZ ändern:** `HOME_PLZ` und `HOME_LAT_LON` anpassen.
Koordinaten z.B. von https://www.openstreetmap.org ablesen.

**Scoring-Gewichte ändern:** in `compute_score()` die drei Zahlen (100/50/30) anpassen.

**Scan-Zeitpunkt:** in `.github/workflows/scanner.yml` den `cron`-Ausdruck ändern.
GitHub nutzt UTC – für 09:00 deutscher Zeit Sommerzeit `0 7 * * *`,
für ganzjährig 09:00 Ortszeit gibt's keinen einfachen Cron, dafür müsste man
zwei Schedules anlegen.
