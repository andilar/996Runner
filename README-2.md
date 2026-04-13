# 🏎 Porsche 996 Scanner

Scannt täglich Kleinanzeigen.de auf neue Porsche 996 Schaltgetriebe-Angebote und schickt dir eine HTML-Zusammenfassung per Gmail.

**Kosten: 0 €** — läuft vollständig auf GitHub Actions Free Tier.

---

## Setup (ca. 10 Minuten)

### 1. GitHub Repository anlegen

```bash
# Diesen Ordner als Git-Repo initialisieren
git init
git add .
git commit -m "Initial commit"

# Neues GitHub Repo erstellen (auf github.com) und pushen
git remote add origin https://github.com/DEIN-USERNAME/996-scanner.git
git push -u origin main
```

### 2. Gmail App-Passwort erstellen

Gmail erlaubt keine normalen Passwörter für SMTP — du brauchst ein **App-Passwort**:

1. Google-Konto → **Sicherheit** → **2-Faktor-Authentifizierung** aktivieren (falls noch nicht)
2. Dann: **Sicherheit** → **App-Passwörter** → "App-Passwort erstellen"
3. Name z.B. "996-Scanner" → Generieren → 16-stelliges Passwort kopieren

### 3. GitHub Secrets setzen

Im GitHub Repository: **Settings → Secrets and variables → Actions → New repository secret**

| Secret-Name        | Wert                              |
|--------------------|-----------------------------------|
| `GMAIL_USER`       | deine.email@gmail.com             |
| `GMAIL_APP_PASSWORD` | Das 16-stellige App-Passwort    |
| `NOTIFY_EMAIL`     | Ziel-E-Mail (kann gleich wie GMAIL_USER sein) |

### 4. Workflow aktivieren

Nach dem ersten Push: **Actions-Tab** im GitHub Repo → Workflow aktivieren falls nötig.

Zum manuellen Testen: **Actions → "Porsche 996 Scanner" → "Run workflow"**

---

## Wie es funktioniert

```
GitHub Actions (cron: täglich 08:00 UTC)
        │
        ▼
scanner/scan.py
        │
        ├── Fetch Kleinanzeigen (996, Schaltgetriebe)
        ├── Vergleich mit gespeicherten IDs (Cache)
        ├── Neue Angebote filtern
        └── HTML-E-Mail via Gmail senden
```

Der **State** (bekannte Anzeigen-IDs) wird im GitHub Actions Cache gespeichert — so merkt sich der Scanner von Tag zu Tag, was er schon gesehen hat.

---

## Suchanfrage anpassen

In `scanner/scan.py` die Variable `SEARCH_URL` ändern:

```python
# Aktuelle Suche: 996, Schaltgetriebe
SEARCH_URL = "https://www.kleinanzeigen.de/s-autos/996/k0c216+autos.shift_s:manuell"

# Beispiel: nur Coupé
SEARCH_URL = "https://www.kleinanzeigen.de/s-autos/996/k0c216+autos.shift_s:manuell+autos.typ_s:coupe"

# Beispiel: auch 997
SEARCH_URL = "https://www.kleinanzeigen.de/s-autos/porsche-997/k0c216+autos.shift_s:manuell"
```

---

## Laufzeiten (GitHub Free Tier)

- Free: **2.000 Minuten/Monat** auf ubuntu-latest
- Dieser Job braucht ca. **30–60 Sekunden** pro Lauf
- Bei 30 Läufen/Monat: ~30 Minuten → weit unter dem Limit ✓

---

## Lokaler Test

```bash
export GMAIL_USER="deine@gmail.com"
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
export NOTIFY_EMAIL="deine@gmail.com"

python scanner/scan.py
```

Ohne Env-Variablen wird stattdessen `scanner/preview.html` erzeugt.
