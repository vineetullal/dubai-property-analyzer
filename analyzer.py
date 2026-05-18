import math
from config import LOCATIONS, NEARBY_AMENITIES


# ──────────────────────────────────────────────
# Distress deal detection
# ──────────────────────────────────────────────

def score_distress(prop: dict) -> tuple[float, list[str]]:
    """
    Returns (score 0-100, list of distress signals).
    Score >= 60 = likely distress deal.
    """
    score = 0
    signals = []

    loc = prop.get("location", "")
    avg_psf = LOCATIONS.get(loc, {}).get("avg_price_psf", 1400)
    psf = prop.get("price_per_sqft", 0)

    if psf and avg_psf:
        discount_pct = (avg_psf - psf) / avg_psf * 100
        if discount_pct >= 20:
            score += 40
            signals.append(f"Price/sqft {discount_pct:.0f}% below area average (AED {psf:.0f} vs avg AED {avg_psf:.0f})")
        elif discount_pct >= 10:
            score += 20
            signals.append(f"Price/sqft {discount_pct:.0f}% below area average")
        elif discount_pct >= 5:
            score += 10
            signals.append(f"Price/sqft slightly below area average ({discount_pct:.0f}%)")

    dom = prop.get("days_on_market", 0)
    if dom >= 180:
        score += 30
        signals.append(f"Listed for {dom} days (6+ months — motivated seller likely)")
    elif dom >= 90:
        score += 20
        signals.append(f"Listed for {dom} days (90+ days — negotiation opportunity)")
    elif dom >= 60:
        score += 10
        signals.append(f"Listed for {dom} days")

    desc = (prop.get("description") or "").lower()
    distress_keywords = [
        ("motivated seller", 15), ("urgent sale", 15), ("must sell", 15),
        ("below market", 10), ("negotiable", 8), ("vacant", 5),
        ("owner occupied", 3), ("distress", 20), ("bank sale", 25),
        ("foreclosure", 25), ("below asking", 10), ("price reduced", 12),
        ("genuine seller", 8), ("serious seller", 8),
    ]
    for kw, pts in distress_keywords:
        if kw in desc:
            score += pts
            signals.append(f'Listing mentions "{kw}"')

    return min(score, 100), signals


def mark_distress(properties: list[dict]) -> list[dict]:
    for p in properties:
        score, signals = score_distress(p)
        p["distress_score"] = score
        p["is_distress"] = 1 if score >= 50 else 0
        p["distress_signals"] = signals
    return properties


# ──────────────────────────────────────────────
# Proximity scoring
# ──────────────────────────────────────────────

def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Distance in km between two lat/lon points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def compute_proximity(prop: dict) -> dict:
    """
    Returns nearest amenity of each category and overall proximity score (0-100).
    Uses location centroid if property lat/lon is missing.
    """
    lat = prop.get("latitude")
    lon = prop.get("longitude")
    loc = prop.get("location", "")

    if not lat or not lon:
        cfg = LOCATIONS.get(loc, {})
        lat = cfg.get("lat")
        lon = cfg.get("lon")

    if not lat or not lon:
        return {"proximity_score": 50, "nearest": {}}

    nearest = {}
    total_score = 0
    weights = {"Metro Stations": 35, "Schools": 30, "Hospitals": 15, "Malls": 20}

    for category, places in NEARBY_AMENITIES.items():
        best_name = None
        best_dist = float("inf")
        for name, (plat, plon) in places.items():
            d = _haversine(lat, lon, plat, plon)
            if d < best_dist:
                best_dist = d
                best_name = name

        nearest[category] = {"name": best_name, "distance_km": round(best_dist, 2)}

        # Score: 100 if <0.5km, 50 if ~2km, 0 if >5km
        dist_score = max(0, 100 - (best_dist / 5) * 100)
        total_score += dist_score * weights.get(category, 25) / 100

    return {
        "proximity_score": round(total_score, 1),
        "nearest": nearest,
    }


# ──────────────────────────────────────────────
# Market value assessment
# ──────────────────────────────────────────────

def value_assessment(prop: dict) -> str:
    loc = prop.get("location", "")
    avg_psf = LOCATIONS.get(loc, {}).get("avg_price_psf", 1400)
    psf = prop.get("price_per_sqft", 0)

    if not psf or not avg_psf:
        return "Unknown"

    discount = (avg_psf - psf) / avg_psf * 100

    if discount >= 15:
        return "Excellent Value"
    elif discount >= 5:
        return "Good Value"
    elif discount >= -5:
        return "Fair Market Value"
    elif discount >= -15:
        return "Above Market"
    else:
        return "Overpriced"


def enrich_properties(properties: list[dict]) -> list[dict]:
    """Add distress scores, proximity, and value assessments."""
    properties = mark_distress(properties)
    for p in properties:
        prox = compute_proximity(p)
        p["proximity_score"] = prox["proximity_score"]
        p["nearest_amenities"] = prox["nearest"]
        p["value_assessment"] = value_assessment(p)
    return properties


# ──────────────────────────────────────────────
# Negotiation intelligence
# ──────────────────────────────────────────────

def negotiation_tips(prop: dict) -> list[str]:
    tips = []
    dom = prop.get("days_on_market", 0)
    score = prop.get("distress_score", 0)
    psf = prop.get("price_per_sqft", 0)
    avg_psf = LOCATIONS.get(prop.get("location", ""), {}).get("avg_price_psf", 1400)

    if dom >= 90:
        tips.append(f"Property has been listed for {dom} days — open with 8-12% below asking.")
    elif dom >= 60:
        tips.append(f"Property has been listed {dom} days — try 5-8% below asking.")
    else:
        tips.append("Recently listed — standard 3-5% below asking is reasonable.")

    if score >= 60:
        tips.append("High distress indicators — seller likely motivated. Push for 10-15% discount + waived DLD fees.")

    if psf and avg_psf and psf > avg_psf * 1.1:
        tips.append(f"Price/sqft is above area average (AED {psf:.0f} vs AED {avg_psf:.0f}) — use comparables as leverage.")

    tips.append("Request inclusion of all white goods and AC units in sale price.")
    tips.append("Ask for a 30-day NOC period and seller to cover DLD registration fees (4% of price).")

    return tips
