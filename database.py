import sqlite3
import json
from datetime import datetime
from typing import Optional
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS properties (
            id TEXT PRIMARY KEY,
            title TEXT,
            price INTEGER,
            bedrooms INTEGER,
            bathrooms INTEGER,
            area_sqft REAL,
            price_per_sqft REAL,
            location TEXT,
            community TEXT,
            property_type TEXT,
            url TEXT,
            source TEXT,
            cover_photo TEXT,
            listed_date TEXT,
            first_seen TEXT,
            last_seen TEXT,
            days_on_market INTEGER DEFAULT 0,
            is_distress INTEGER DEFAULT 0,
            distress_score REAL DEFAULT 0,
            service_charge_estimate REAL DEFAULT 0,
            latitude REAL,
            longitude REAL,
            agent_name TEXT,
            agent_phone TEXT,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id TEXT,
            price INTEGER,
            recorded_at TEXT,
            FOREIGN KEY (property_id) REFERENCES properties(id)
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            property_id TEXT,
            message TEXT,
            created_at TEXT,
            is_read INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS refresh_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ran_at TEXT,
            properties_found INTEGER,
            properties_new INTEGER,
            status TEXT
        );
    """)
    conn.commit()
    conn.close()


def upsert_property(prop: dict):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().isoformat()

    existing = c.execute("SELECT price, first_seen FROM properties WHERE id = ?", (prop["id"],)).fetchone()

    if existing:
        old_price = existing["price"]
        first_seen = existing["first_seen"]
        days_on_market = (datetime.now() - datetime.fromisoformat(first_seen)).days

        c.execute("""
            UPDATE properties SET
                title=?, price=?, bedrooms=?, bathrooms=?, area_sqft=?,
                price_per_sqft=?, cover_photo=?, last_seen=?, days_on_market=?,
                is_distress=?, distress_score=?, service_charge_estimate=?,
                agent_name=?, agent_phone=?, description=?
            WHERE id=?
        """, (
            prop.get("title"), prop.get("price"), prop.get("bedrooms"),
            prop.get("bathrooms"), prop.get("area_sqft"), prop.get("price_per_sqft"),
            prop.get("cover_photo"), now, days_on_market,
            prop.get("is_distress", 0), prop.get("distress_score", 0),
            prop.get("service_charge_estimate", 0),
            prop.get("agent_name"), prop.get("agent_phone"),
            prop.get("description"), prop["id"],
        ))

        if old_price and old_price != prop.get("price"):
            c.execute(
                "INSERT INTO price_history (property_id, price, recorded_at) VALUES (?, ?, ?)",
                (prop["id"], prop.get("price"), now),
            )
            alert_msg = f"Price {'dropped' if prop['price'] < old_price else 'increased'} from AED {old_price:,} to AED {prop['price']:,}"
            c.execute(
                "INSERT INTO alerts (type, property_id, message, created_at) VALUES (?, ?, ?, ?)",
                ("price_change", prop["id"], alert_msg, now),
            )

        is_new = False
    else:
        c.execute("""
            INSERT INTO properties (
                id, title, price, bedrooms, bathrooms, area_sqft, price_per_sqft,
                location, community, property_type, url, source, cover_photo,
                listed_date, first_seen, last_seen, days_on_market,
                is_distress, distress_score, service_charge_estimate,
                latitude, longitude, agent_name, agent_phone, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            prop.get("id"), prop.get("title"), prop.get("price"),
            prop.get("bedrooms"), prop.get("bathrooms"), prop.get("area_sqft"),
            prop.get("price_per_sqft"), prop.get("location"), prop.get("community"),
            prop.get("property_type"), prop.get("url"), prop.get("source"),
            prop.get("cover_photo"), prop.get("listed_date"), now, now, 0,
            prop.get("is_distress", 0), prop.get("distress_score", 0),
            prop.get("service_charge_estimate", 0),
            prop.get("latitude"), prop.get("longitude"),
            prop.get("agent_name"), prop.get("agent_phone"),
            prop.get("description"),
        ))

        if prop.get("is_distress"):
            c.execute(
                "INSERT INTO alerts (type, property_id, message, created_at) VALUES (?, ?, ?, ?)",
                ("distress_deal", prop["id"], f"New distress deal detected: {prop.get('title')}", now),
            )
        else:
            c.execute(
                "INSERT INTO alerts (type, property_id, message, created_at) VALUES (?, ?, ?, ?)",
                ("new_listing", prop["id"], f"New listing: {prop.get('title')} at AED {prop.get('price', 0):,}", now),
            )

        is_new = True

    conn.commit()
    conn.close()
    return is_new


def get_all_properties(filters: dict = None) -> list:
    conn = get_conn()
    c = conn.cursor()
    query = "SELECT * FROM properties WHERE 1=1"
    params = []

    if filters:
        if filters.get("min_price"):
            query += " AND price >= ?"
            params.append(filters["min_price"])
        if filters.get("max_price"):
            query += " AND price <= ?"
            params.append(filters["max_price"])
        if filters.get("locations"):
            placeholders = ",".join("?" * len(filters["locations"]))
            query += f" AND location IN ({placeholders})"
            params.extend(filters["locations"])
        if filters.get("bedrooms"):
            query += " AND bedrooms >= ?"
            params.append(filters["bedrooms"])
        if filters.get("distress_only"):
            query += " AND is_distress = 1"
        if filters.get("property_type"):
            query += " AND property_type = ?"
            params.append(filters["property_type"])

    query += " ORDER BY distress_score DESC, price ASC"
    rows = c.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_price_history(property_id: str) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT price, recorded_at FROM price_history WHERE property_id = ? ORDER BY recorded_at",
        (property_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_alerts(unread_only: bool = False) -> list:
    conn = get_conn()
    query = "SELECT a.*, p.title, p.url FROM alerts a LEFT JOIN properties p ON a.property_id = p.id"
    if unread_only:
        query += " WHERE a.is_read = 0"
    query += " ORDER BY a.created_at DESC LIMIT 50"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_alerts_read():
    conn = get_conn()
    conn.execute("UPDATE alerts SET is_read = 1")
    conn.commit()
    conn.close()


def log_refresh(properties_found: int, properties_new: int, status: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO refresh_log (ran_at, properties_found, properties_new, status) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), properties_found, properties_new, status),
    )
    conn.commit()
    conn.close()


def get_last_refresh() -> Optional[dict]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM refresh_log ORDER BY ran_at DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def get_market_stats() -> dict:
    conn = get_conn()
    c = conn.cursor()
    stats = {}

    for row in c.execute("""
        SELECT location,
               COUNT(*) as count,
               AVG(price) as avg_price,
               AVG(price_per_sqft) as avg_psf,
               MIN(price) as min_price,
               MAX(price) as max_price,
               AVG(days_on_market) as avg_dom
        FROM properties
        GROUP BY location
    """).fetchall():
        stats[row["location"]] = dict(row)

    conn.close()
    return stats
