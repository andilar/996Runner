"""SQLite-backed market history and static dashboard generation."""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    scanned_at TEXT NOT NULL UNIQUE,
    listing_count INTEGER NOT NULL,
    priced_count INTEGER NOT NULL,
    median_price_eur REAL,
    average_price_eur REAL,
    minimum_price_eur INTEGER,
    maximum_price_eur INTEGER,
    new_count INTEGER NOT NULL,
    removed_count INTEGER NOT NULL,
    price_up_count INTEGER NOT NULL,
    price_down_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS listings (
    ad_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    ad_id TEXT NOT NULL REFERENCES listings(ad_id) ON DELETE CASCADE,
    price_eur INTEGER,
    location TEXT NOT NULL,
    postal_code TEXT NOT NULL,
    mileage_km INTEGER,
    model_year INTEGER,
    score REAL NOT NULL,
    PRIMARY KEY (scan_id, ad_id)
);

CREATE INDEX IF NOT EXISTS observations_ad_id_idx
    ON observations(ad_id, scan_id);
"""


def _price_eur(value: str) -> int | None:
    """Parse the formats emitted by the scanner without importing it."""
    import re

    match = re.search(r"(\d{1,3}(?:\.\d{3})+|\d{3,6})", value or "")
    return int(match.group(1).replace(".", "")) if match else None


def _mileage_km(value: str) -> int | None:
    import re

    match = re.search(r"(\d[\d.\s]*)", value or "")
    return int(re.sub(r"\D", "", match.group(1))) if match else None


def initialize_database(database_path: str | Path) -> None:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)


def record_market_snapshot(
    database_path: str | Path,
    listings: Iterable[dict],
    score_fn,
    scanned_at: datetime | None = None,
) -> dict:
    """Atomically record a scan and return its aggregate metrics."""
    path = Path(database_path)
    initialize_database(path)
    timestamp = (scanned_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp_text = timestamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    current = {str(item["id"]): item for item in listings}

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        previous_scan = connection.execute(
            "SELECT id FROM scans ORDER BY scanned_at DESC, id DESC LIMIT 1"
        ).fetchone()
        previous_prices: dict[str, int | None] = {}
        if previous_scan:
            previous_prices = {
                row["ad_id"]: row["price_eur"]
                for row in connection.execute(
                    "SELECT ad_id, price_eur FROM observations WHERE scan_id = ?",
                    (previous_scan["id"],),
                )
            }

        current_prices = {
            ad_id: _price_eur(item.get("price", "")) for ad_id, item in current.items()
        }
        prices = [price for price in current_prices.values() if price is not None]
        previous_ids = set(previous_prices)
        current_ids = set(current)
        changed = [
            (previous_prices[ad_id], current_prices[ad_id])
            for ad_id in previous_ids & current_ids
            if previous_prices[ad_id] is not None
            and current_prices[ad_id] is not None
            and previous_prices[ad_id] != current_prices[ad_id]
        ]

        metrics = {
            "scanned_at": timestamp_text,
            "listing_count": len(current),
            "priced_count": len(prices),
            "median_price_eur": statistics.median(prices) if prices else None,
            "average_price_eur": round(statistics.fmean(prices), 2) if prices else None,
            "minimum_price_eur": min(prices) if prices else None,
            "maximum_price_eur": max(prices) if prices else None,
            "new_count": len(current_ids - previous_ids) if previous_scan else len(current_ids),
            "removed_count": len(previous_ids - current_ids),
            "price_up_count": sum(after > before for before, after in changed),
            "price_down_count": sum(after < before for before, after in changed),
        }

        cursor = connection.execute(
            """
            INSERT INTO scans (
                scanned_at, listing_count, priced_count, median_price_eur,
                average_price_eur, minimum_price_eur, maximum_price_eur,
                new_count, removed_count, price_up_count, price_down_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(metrics.values()),
        )
        scan_id = cursor.lastrowid

        for ad_id, item in current.items():
            connection.execute(
                """
                INSERT INTO listings (ad_id, title, url, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(ad_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    ad_id,
                    item.get("title", ""),
                    item.get("url", ""),
                    timestamp_text,
                    timestamp_text,
                ),
            )
            score = score_fn(item)[0]
            year = item.get("year")
            connection.execute(
                """
                INSERT INTO observations (
                    scan_id, ad_id, price_eur, location, postal_code,
                    mileage_km, model_year, score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    ad_id,
                    current_prices[ad_id],
                    item.get("location", ""),
                    item.get("plz", ""),
                    _mileage_km(item.get("km", "")),
                    int(year) if str(year).isdigit() else None,
                    score,
                ),
            )

    return metrics


def dashboard_data(database_path: str | Path) -> dict:
    initialize_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        scans = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM scans ORDER BY scanned_at"
            )
        ]
        changes = [
            dict(row)
            for row in connection.execute(
                """
                WITH priced AS (
                    SELECT o.ad_id, o.price_eur, o.scan_id, s.scanned_at,
                           LAG(o.price_eur) OVER (
                               PARTITION BY o.ad_id ORDER BY s.scanned_at
                           ) AS previous_price
                    FROM observations o
                    JOIN scans s ON s.id = o.scan_id
                    WHERE o.price_eur IS NOT NULL
                )
                SELECT p.scanned_at, p.ad_id, l.title, l.url,
                       p.previous_price, p.price_eur AS current_price
                FROM priced p
                JOIN listings l ON l.ad_id = p.ad_id
                WHERE p.previous_price IS NOT NULL
                  AND p.previous_price != p.price_eur
                ORDER BY p.scanned_at DESC
                LIMIT 50
                """
            )
        ]
    return {"scans": scans, "price_changes": changes}


def generate_dashboard(database_path: str | Path, output_path: str | Path) -> None:
    data = json.dumps(dashboard_data(database_path), ensure_ascii=False).replace("</", "<\\/")
    template = Path(__file__).with_name("dashboard_template.html").read_text(encoding="utf-8")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("__MARKET_DATA__", data), encoding="utf-8")
