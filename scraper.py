import json
import time
import random
import hashlib
import os
import requests
from typing import List, Optional
from bs4 import BeautifulSoup
from config import LOCATIONS, PRICE_MIN, PRICE_MAX

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

_SCRAPER_API_KEY: Optional[str] = None


def set_scraper_api_key(key: str):
    global _SCRAPER_API_KEY
    _SCRAPER_API_KEY = key.strip() if key else None


def _fetch(url: str, timeout: int = 20, premium: bool = False) -> Optional[requests.Response]:
    """Fetch a URL, routing through ScraperAPI if a key is set."""
    try:
        if _SCRAPER_API_KEY:
            proxy_url = (
                f"https://api.scraperapi.com"
                f"?api_key={_SCRAPER_API_KEY}"
                f"&url={requests.utils.quote(url, safe='')}"
                f"&render=true"
                f"&country_code=ae"
                + ("&premium=true" if premium else "")
            )
            resp = requests.get(proxy_url, timeout=120)
        else:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp
    except Exception as e:
        print(f"[scraper] fetch error for {url}: {e}")
        return None


def test_connection(url: str = "https://www.propertyfinder.ae/en/search?c=1&fu=0&rp=y&ob=mr&pe=3000000&pb=2500000&l=dubai-hills-estate") -> dict:
    """Test ScraperAPI connection and return diagnostic info."""
    resp = _fetch(url, premium=True)
    if not resp:
        return {"ok": False, "status": None, "has_next_data": False, "preview": "Request failed", "diagnostics": ""}

    soup = BeautifulSoup(resp.text, "html.parser")
    has_next = soup.find("script", {"id": "__NEXT_DATA__"}) is not None

    # Collect diagnostic info about what data is available in the page
    diag_lines = []

    # All script tag types and ids
    for s in soup.find_all("script"):
        sid = s.get("id", "")
        stype = s.get("type", "")
        content_len = len(s.string or "")
        if sid or stype == "application/ld+json" or content_len > 500:
            diag_lines.append(f"<script id='{sid}' type='{stype}' len={content_len}>")

    # JSON-LD blocks
    json_ld = soup.find_all("script", {"type": "application/ld+json"})
    diag_lines.append(f"JSON-LD blocks found: {len(json_ld)}")
    for jld in json_ld[:2]:
        diag_lines.append(f"  JSON-LD: {(jld.string or '')[:200]}")

    # Look for price-like numbers in the page
    import re
    prices = re.findall(r'[\d,]{7,}', resp.text)
    diag_lines.append(f"Price-like numbers found: {prices[:10]}")

    # Page title
    diag_lines.append(f"Title: {soup.title.string if soup.title else 'none'}")

    # Any data attributes with 'price' or 'listing'
    price_attrs = soup.find_all(attrs={"data-price": True})
    diag_lines.append(f"data-price elements: {len(price_attrs)}")

    # Check for window.__data__ or similar in inline scripts
    for s in soup.find_all("script"):
        text = s.string or ""
        if any(kw in text for kw in ["listing", "property", "price", "__data__", "hits"]):
            diag_lines.append(f"Keyword script found (len={len(text)}): {text[:300]}")
            break

    return {
        "ok": resp.status_code == 200,
        "status": resp.status_code,
        "has_next_data": has_next,
        "preview": resp.text[:500],
        "diagnostics": "\n".join(diag_lines),
    }


def _sleep():
    time.sleep(random.uniform(1.0, 2.5))


def _make_id(source: str, external_id: str) -> str:
    return hashlib.md5(f"{source}:{external_id}".encode()).hexdigest()


# ──────────────────────────────────────────────
# Bayut scraper
# ──────────────────────────────────────────────

BAYUT_BASE = "https://www.bayut.com"


def _bayut_search_url(slug: str, page: int = 1) -> str:
    return (
        f"{BAYUT_BASE}/for-sale/property/dubai/{slug}/"
        f"?price_min={PRICE_MIN}&price_max={PRICE_MAX}&page={page}"
    )


def _parse_bayut_page(html: str, location_name: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    sc_psf = LOCATIONS.get(location_name, {}).get("service_charge_psf", 15)

    # Strategy 1: __NEXT_DATA__ JSON (legacy, may still appear)
    results = _bayut_from_next_data(soup, location_name, sc_psf)
    if results:
        return results

    # Strategy 2: JSON-LD (Schema.org structured data)
    results = _bayut_from_json_ld(soup, location_name, sc_psf)
    if results:
        return results

    # Strategy 3: inline window.__data__ / __APOLLO_STATE__ / similar
    results = _bayut_from_inline_json(soup, location_name, sc_psf)
    if results:
        return results

    # Strategy 4: parse HTML DOM directly (article/li cards)
    results = _bayut_from_html(soup, location_name, sc_psf)
    return results


def _bayut_from_next_data(soup, location_name, sc_psf) -> List[dict]:
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag:
        return []
    try:
        data = json.loads(tag.string)
    except Exception:
        return []

    hits = []
    for path in [
        ["props", "pageProps", "searchResult", "hits"],
        ["props", "pageProps", "listingList", "listings"],
    ]:
        try:
            node = data
            for key in path:
                node = node[key]
            hits = node
            break
        except (KeyError, TypeError):
            continue

    results = []
    for h in hits:
        try:
            price = int(h.get("price", 0))
            area = float(h.get("area", 0))
            if not price or price < PRICE_MIN or price > PRICE_MAX:
                continue
            cover = (h.get("coverPhoto") or h.get("mainPhoto") or {}).get("url", "")
            service_charge = round(sc_psf * area / 12, 0) if area else 0
            results.append({
                "id": _make_id("bayut", str(h.get("externalID", h.get("id", "")))),
                "title": h.get("title", ""),
                "price": price,
                "bedrooms": int(h.get("rooms", 0)),
                "bathrooms": int(h.get("baths", 0)),
                "area_sqft": area,
                "price_per_sqft": round(price / area, 2) if area else 0,
                "location": location_name,
                "community": (h.get("location") or [{}])[-1].get("name", ""),
                "property_type": _get_property_type(h),
                "url": BAYUT_BASE + h.get("slug", ""),
                "source": "Bayut",
                "cover_photo": cover,
                "listed_date": h.get("createdAt", ""),
                "service_charge_estimate": service_charge,
                "latitude": (h.get("geography") or {}).get("lat"),
                "longitude": (h.get("geography") or {}).get("lng"),
                "agent_name": h.get("contactName", ""),
                "agent_phone": (h.get("phoneNumber") or {}).get("mobile", ""),
                "description": h.get("description", "")[:500],
            })
        except Exception:
            continue
    return results


def _bayut_from_json_ld(soup, location_name, sc_psf) -> List[dict]:
    results = []
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            try:
                price = int(str(item.get("price", item.get("offers", {}).get("price", 0))).replace(",", "").replace("AED", "").strip() or 0)
                if not price or price < PRICE_MIN or price > PRICE_MAX:
                    continue
                url = item.get("url", "")
                name = item.get("name", item.get("headline", ""))
                if not name:
                    continue
                results.append({
                    "id": _make_id("bayut_ld", url or name),
                    "title": name,
                    "price": price,
                    "bedrooms": int(item.get("numberOfRooms", item.get("numberOfBedrooms", 0)) or 0),
                    "bathrooms": int(item.get("numberOfBathroomsTotal", 0) or 0),
                    "area_sqft": float(item.get("floorSize", {}).get("value", 0) or 0),
                    "price_per_sqft": 0,
                    "location": location_name,
                    "community": "",
                    "property_type": item.get("@type", "Property"),
                    "url": url,
                    "source": "Bayut",
                    "cover_photo": item.get("image", ""),
                    "listed_date": "",
                    "service_charge_estimate": 0,
                    "latitude": None,
                    "longitude": None,
                    "agent_name": "",
                    "agent_phone": "",
                    "description": item.get("description", "")[:500],
                })
            except Exception:
                continue
    return results


def _bayut_from_inline_json(soup, location_name, sc_psf) -> List[dict]:
    """Look for property data in inline JS variables like window.__data__, __APOLLO_STATE__, etc."""
    import re
    keywords = ["hits", "listings", "properties", "__APOLLO_STATE__", "searchResult"]
    for tag in soup.find_all("script"):
        text = tag.string or ""
        if not any(kw in text for kw in keywords):
            continue
        if len(text) < 200:
            continue
        # Try to find and parse any JSON object/array
        for pattern in [
            r'"hits"\s*:\s*(\[.*?\])\s*[,}]',
            r'"listings"\s*:\s*(\[.*?\])\s*[,}]',
            r'"properties"\s*:\s*(\[.*?\])\s*[,}]',
        ]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    hits = json.loads(match.group(1))
                    if hits and isinstance(hits, list):
                        return _bayut_from_next_data.__wrapped__ if hasattr(_bayut_from_next_data, '__wrapped__') else []
                except Exception:
                    continue
    return []


def _bayut_from_html(soup, location_name, sc_psf) -> List[dict]:
    """Parse property cards directly from rendered HTML."""
    import re
    results = []

    # Bayut renders <article> elements for each listing, or <li> items
    cards = soup.find_all("article")
    if not cards:
        cards = soup.select("li[class*='property'], li[class*='listing'], div[class*='PropertyCard'], div[class*='listing-card']")

    for card in cards:
        try:
            # Price — look for AED amounts
            text = card.get_text(" ", strip=True)
            price_match = re.search(r'AED\s*([\d,]+)', text)
            if not price_match:
                continue
            price = int(price_match.group(1).replace(",", ""))
            if price < PRICE_MIN or price > PRICE_MAX:
                continue

            # URL
            link = card.find("a", href=True)
            url = ""
            if link:
                href = link["href"]
                url = href if href.startswith("http") else BAYUT_BASE + href

            # Title
            title = ""
            for tag in ["h2", "h3", "h4"]:
                el = card.find(tag)
                if el:
                    title = el.get_text(strip=True)
                    break
            if not title and link:
                title = link.get_text(strip=True)[:100]

            # Beds / baths / area — look for patterns like "3 Beds", "2 Baths", "1,500 sqft"
            beds = 0
            baths = 0
            area = 0.0
            bed_m = re.search(r'(\d+)\s*(?:Bed|BR|Bedroom)', text, re.IGNORECASE)
            bath_m = re.search(r'(\d+)\s*(?:Bath|Bathroom)', text, re.IGNORECASE)
            area_m = re.search(r'([\d,]+)\s*(?:sqft|sq\.?\s*ft)', text, re.IGNORECASE)
            if bed_m:
                beds = int(bed_m.group(1))
            if bath_m:
                baths = int(bath_m.group(1))
            if area_m:
                area = float(area_m.group(1).replace(",", ""))

            # Cover photo
            img = card.find("img")
            cover = img.get("src", img.get("data-src", "")) if img else ""

            service_charge = round(sc_psf * area / 12, 0) if area else 0
            price_psf = round(price / area, 2) if area else 0

            if not title or not url:
                continue

            results.append({
                "id": _make_id("bayut_html", url),
                "title": title,
                "price": price,
                "bedrooms": beds,
                "bathrooms": baths,
                "area_sqft": area,
                "price_per_sqft": price_psf,
                "location": location_name,
                "community": "",
                "property_type": "Property",
                "url": url,
                "source": "Bayut",
                "cover_photo": cover,
                "listed_date": "",
                "service_charge_estimate": service_charge,
                "latitude": None,
                "longitude": None,
                "agent_name": "",
                "agent_phone": "",
                "description": "",
            })
        except Exception:
            continue

    return results


def _get_property_type(hit: dict) -> str:
    cats = hit.get("category", [])
    if cats:
        return cats[-1].get("nameSingular", "Property")
    return hit.get("type", {}).get("nameSingular", "Property")


def scrape_bayut(location_name: str) -> List[dict]:
    slug = LOCATIONS[location_name]["bayut_slug"]
    all_results = []

    for page in range(1, 6):
        url = _bayut_search_url(slug, page)
        resp = _fetch(url, premium=bool(_SCRAPER_API_KEY))
        if not resp:
            break
        results = _parse_bayut_page(resp.text, location_name)
        if not results:
            break
        all_results.extend(results)
        _sleep()

    return all_results


# ──────────────────────────────────────────────
# PropertyFinder scraper
# ──────────────────────────────────────────────

PF_BASE = "https://www.propertyfinder.ae"


def _pf_search_url(slug: str, page: int = 1) -> str:
    return (
        f"{PF_BASE}/en/search?c=1&fu=0&rp=y&ob=mr"
        f"&pe={PRICE_MAX}&pb={PRICE_MIN}&l={slug}&page={page}"
    )


def _parse_pf_page(html: str, location_name: str) -> List[dict]:
    import re
    soup = BeautifulSoup(html, "html.parser")
    sc_psf = LOCATIONS.get(location_name, {}).get("service_charge_psf", 15)

    # Strategy 1: __NEXT_DATA__
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if tag:
        try:
            data = json.loads(tag.string)
            hits = []
            for path in [
                ["props", "pageProps", "searchResult", "listings"],
                ["props", "pageProps", "listings"],
                ["props", "pageProps", "searchResult", "hits"],
            ]:
                try:
                    node = data
                    for k in path:
                        node = node[k]
                    hits = node
                    break
                except (KeyError, TypeError):
                    continue

            if hits:
                results = []
                for h in hits:
                    try:
                        price = (
                            int(h["price"]["value"]) if isinstance(h.get("price"), dict)
                            else int(h.get("price", 0))
                        )
                        if not price or price < PRICE_MIN or price > PRICE_MAX:
                            continue
                        area = (
                            float(h["area"]["value"]) if isinstance(h.get("area"), dict)
                            else float(h.get("area", 0))
                        )
                        photos = h.get("photos", [])
                        cover = (
                            photos[0].get("url", "") if photos and isinstance(photos[0], dict)
                            else (photos[0] if photos else "")
                        )
                        service_charge = round(sc_psf * area / 12, 0) if area else 0
                        results.append({
                            "id": _make_id("pf", str(h.get("id", h.get("externalId", "")))),
                            "title": h.get("title", h.get("name", "")),
                            "price": price,
                            "bedrooms": int(h.get("bedrooms", h.get("rooms", 0))),
                            "bathrooms": int(h.get("bathrooms", h.get("baths", 0))),
                            "area_sqft": area,
                            "price_per_sqft": round(price / area, 2) if area else 0,
                            "location": location_name,
                            "community": (h.get("community") or {}).get("name", ""),
                            "property_type": (h.get("type") or {}).get("name", "Property"),
                            "url": PF_BASE + "/en/property/" + str(h.get("id", "")),
                            "source": "PropertyFinder",
                            "cover_photo": cover,
                            "listed_date": h.get("publishedAt", h.get("createdAt", "")),
                            "service_charge_estimate": service_charge,
                            "latitude": (h.get("geography") or {}).get("lat"),
                            "longitude": (h.get("geography") or {}).get("lng"),
                            "agent_name": (h.get("agent") or {}).get("name", ""),
                            "agent_phone": (h.get("agent") or {}).get("phone", ""),
                            "description": h.get("description", "")[:500],
                        })
                    except Exception:
                        continue
                if results:
                    return results
        except Exception:
            pass

    # Strategy 2: JSON-LD
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
            items = data if isinstance(data, list) else [data]
            results = []
            for item in items:
                price_raw = item.get("price", (item.get("offers") or {}).get("price", 0))
                price = int(str(price_raw).replace(",", "").replace("AED", "").strip() or 0)
                if not price or price < PRICE_MIN or price > PRICE_MAX:
                    continue
                url = item.get("url", "")
                name = item.get("name", item.get("headline", ""))
                if not name:
                    continue
                results.append({
                    "id": _make_id("pf_ld", url or name),
                    "title": name,
                    "price": price,
                    "bedrooms": int(item.get("numberOfBedrooms", item.get("numberOfRooms", 0)) or 0),
                    "bathrooms": int(item.get("numberOfBathroomsTotal", 0) or 0),
                    "area_sqft": float((item.get("floorSize") or {}).get("value", 0) or 0),
                    "price_per_sqft": 0,
                    "location": location_name,
                    "community": "",
                    "property_type": item.get("@type", "Property"),
                    "url": url,
                    "source": "PropertyFinder",
                    "cover_photo": item.get("image", ""),
                    "listed_date": "",
                    "service_charge_estimate": 0,
                    "latitude": None, "longitude": None,
                    "agent_name": "", "agent_phone": "",
                    "description": item.get("description", "")[:500],
                })
            if results:
                return results
        except Exception:
            continue

    # Strategy 3: Direct HTML parsing
    results = []
    cards = soup.find_all("article") or soup.select("[class*='card'], [class*='listing'], [class*='property']")
    for card in cards:
        try:
            text = card.get_text(" ", strip=True)
            price_match = re.search(r'AED\s*([\d,]+)', text)
            if not price_match:
                continue
            price = int(price_match.group(1).replace(",", ""))
            if price < PRICE_MIN or price > PRICE_MAX:
                continue
            link = card.find("a", href=True)
            url = ""
            if link:
                href = link["href"]
                url = href if href.startswith("http") else PF_BASE + href
            title = ""
            for t in ["h2", "h3", "h4"]:
                el = card.find(t)
                if el:
                    title = el.get_text(strip=True)
                    break
            if not title or not url:
                continue
            bed_m = re.search(r'(\d+)\s*(?:Bed|BR)', text, re.IGNORECASE)
            bath_m = re.search(r'(\d+)\s*Bath', text, re.IGNORECASE)
            area_m = re.search(r'([\d,]+)\s*sqft', text, re.IGNORECASE)
            area = float(area_m.group(1).replace(",", "")) if area_m else 0.0
            img = card.find("img")
            cover = img.get("src", img.get("data-src", "")) if img else ""
            results.append({
                "id": _make_id("pf_html", url),
                "title": title,
                "price": price,
                "bedrooms": int(bed_m.group(1)) if bed_m else 0,
                "bathrooms": int(bath_m.group(1)) if bath_m else 0,
                "area_sqft": area,
                "price_per_sqft": round(price / area, 2) if area else 0,
                "location": location_name,
                "community": "",
                "property_type": "Property",
                "url": url,
                "source": "PropertyFinder",
                "cover_photo": cover,
                "listed_date": "",
                "service_charge_estimate": round(sc_psf * area / 12, 0) if area else 0,
                "latitude": None, "longitude": None,
                "agent_name": "", "agent_phone": "",
                "description": "",
            })
        except Exception:
            continue
    return results


def scrape_propertyfinder(location_name: str) -> List[dict]:
    slug = LOCATIONS[location_name]["pf_slug"]
    all_results = []

    for page in range(1, 6):
        url = _pf_search_url(slug, page)
        resp = _fetch(url)
        if not resp:
            break
        results = _parse_pf_page(resp.text, location_name)
        if not results:
            break
        all_results.extend(results)
        _sleep()

    return all_results


# ──────────────────────────────────────────────
# Demo data (used when no ScraperAPI key set)
# ──────────────────────────────────────────────

DEMO_PHOTOS = [
    "https://images.unsplash.com/photo-1600596542815-ffad4c1539a9?w=800",
    "https://images.unsplash.com/photo-1600585154340-be6161a56a0c?w=800",
    "https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?w=800",
    "https://images.unsplash.com/photo-1512917774080-9991f1c4c750?w=800",
    "https://images.unsplash.com/photo-1600566753376-12c8ab7fb75b?w=800",
    "https://images.unsplash.com/photo-1600585154526-990dced4db0d?w=800",
]

# Real search URLs per location (price-filtered)
_BAYUT_SEARCH = "https://www.bayut.com/for-sale/property/dubai/{slug}/?price_min=2500000&price_max=3000000"
_PF_SEARCH = "https://www.propertyfinder.ae/en/search?c=1&fu=0&rp=y&ob=mr&pe=3000000&pb=2500000&l={slug}"
_DUBIZZLE_SEARCH = "https://dubai.dubizzle.com/en/properties/residential/buy/?location_slug={slug}&price__gte=2500000&price__lte=3000000"

DEMO_LISTINGS = [
    # The Views
    {"location": "The Views", "title": "Spacious 2BR Golf View | High Floor | Motivated Seller", "price": 2_650_000, "bedrooms": 2, "bathrooms": 2, "area_sqft": 1520, "property_type": "Apartment", "days_on_market": 45, "description": "motivated seller, price negotiable, vacant on transfer", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="the-views")},
    {"location": "The Views", "title": "3BR + Maids | Full Golf Course View | Urgent Sale", "price": 2_950_000, "bedrooms": 3, "bathrooms": 3, "area_sqft": 1980, "property_type": "Apartment", "days_on_market": 120, "description": "urgent sale, owner relocating abroad, below market value", "source": "PropertyFinder", "url": _PF_SEARCH.format(slug="the-views")},
    {"location": "The Views", "title": "2BR | The Links | Rented until Q1 | Good ROI", "price": 2_550_000, "bedrooms": 2, "bathrooms": 2, "area_sqft": 1410, "property_type": "Apartment", "days_on_market": 30, "description": "rented property, good investment, steady rental income", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="the-views")},
    # Dubai Hills
    {"location": "Dubai Hills", "title": "3BR Townhouse | Park View | Single Row | Exclusive", "price": 2_800_000, "bedrooms": 3, "bathrooms": 4, "area_sqft": 2150, "property_type": "Townhouse", "days_on_market": 60, "description": "single row, backs on park, genuine seller, very negotiable", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="dubai-hills-estate")},
    {"location": "Dubai Hills", "title": "2BR Apartment | Golf View Residences 2 | Brand New", "price": 2_700_000, "bedrooms": 2, "bathrooms": 2, "area_sqft": 1350, "property_type": "Apartment", "days_on_market": 15, "description": "brand new handover, direct from developer", "source": "PropertyFinder", "url": _PF_SEARCH.format(slug="dubai-hills-estate")},
    {"location": "Dubai Hills", "title": "3BR Villa | Maple | Distress Sale | Must Sell", "price": 2_980_000, "bedrooms": 3, "bathrooms": 4, "area_sqft": 2400, "property_type": "Villa", "days_on_market": 200, "description": "must sell, bank pressure, distress sale, price reduced from 3.4M, serious seller only", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="dubai-hills-estate")},
    # Arabian Ranches 1
    {"location": "Arabian Ranches 1", "title": "4BR Saheel Villa | Legacy | Upgraded Kitchen", "price": 2_900_000, "bedrooms": 4, "bathrooms": 4, "area_sqft": 2900, "property_type": "Villa", "days_on_market": 90, "description": "upgraded, well maintained, original owners, motivated to sell", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="arabian-ranches")},
    {"location": "Arabian Ranches 1", "title": "3BR Palmera | Single Row | Private Pool | Upgraded", "price": 2_750_000, "bedrooms": 3, "bathrooms": 3, "area_sqft": 2400, "property_type": "Villa", "days_on_market": 150, "description": "price reduced, below market, genuine seller, negotiable price", "source": "PropertyFinder", "url": _PF_SEARCH.format(slug="arabian-ranches")},
    {"location": "Arabian Ranches 1", "title": "4BR Mirador | Corner | Extended | Rare", "price": 2_850_000, "bedrooms": 4, "bathrooms": 4, "area_sqft": 3100, "property_type": "Villa", "days_on_market": 20, "description": "corner unit, extended layout, excellent condition", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="arabian-ranches")},
    # Studio City
    {"location": "Studio City", "title": "2BR | Glitz Residence 3 | High ROI | Fully Furnished", "price": 2_500_000, "bedrooms": 2, "bathrooms": 2, "area_sqft": 1650, "property_type": "Apartment", "days_on_market": 35, "description": "furnished, high rental demand, 7% gross yield, investor deal", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="dubai-studio-city")},
    {"location": "Studio City", "title": "3BR Duplex | Glitz 1 | Spacious | Vacant", "price": 2_600_000, "bedrooms": 3, "bathrooms": 3, "area_sqft": 2100, "property_type": "Apartment", "days_on_market": 110, "description": "vacant immediately, motivated seller, price dropped from 2.9M", "source": "PropertyFinder", "url": _PF_SEARCH.format(slug="dubai-studio-city")},
    # Motorcity
    {"location": "Motorcity", "title": "3BR Townhouse | Pelham | Large Plot | Quiet", "price": 2_550_000, "bedrooms": 3, "bathrooms": 4, "area_sqft": 2350, "property_type": "Townhouse", "days_on_market": 75, "description": "good size plot, quiet community, motivated seller", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="motor-city")},
    {"location": "Motorcity", "title": "4BR Golf Vista | Extended | Must Sell | Open to Offers", "price": 2_700_000, "bedrooms": 4, "bathrooms": 4, "area_sqft": 2700, "property_type": "Townhouse", "days_on_market": 180, "description": "must sell, open to all offers, price reduced multiple times, genuine distress", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="motor-city")},
    {"location": "Motorcity", "title": "3BR | Green Community | Landscaped Garden", "price": 2_620_000, "bedrooms": 3, "bathrooms": 3, "area_sqft": 2200, "property_type": "Townhouse", "days_on_market": 45, "description": "large garden, well maintained, good community", "source": "PropertyFinder", "url": _PF_SEARCH.format(slug="motor-city")},
    # JLT
    {"location": "JLT", "title": "3BR | Cluster D | Lake View | High Floor | Investor Deal", "price": 2_550_000, "bedrooms": 3, "bathrooms": 3, "area_sqft": 1850, "property_type": "Apartment", "days_on_market": 25, "description": "lake view, high floor, tenanted at 130k, great investment", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="jumeirah-lake-towers-jlt")},
    {"location": "JLT", "title": "2BR | Cluster Y | Distress Sale | Below DLD Price", "price": 2_500_000, "bedrooms": 2, "bathrooms": 2, "area_sqft": 1550, "property_type": "Apartment", "days_on_market": 210, "description": "below market value, distress sale, urgent, bank sale, genuine", "source": "PropertyFinder", "url": _PF_SEARCH.format(slug="jumeirah-lake-towers-jlt")},
    {"location": "JLT", "title": "3BR Penthouse | Cluster A | Stunning Views | Rare", "price": 2_950_000, "bedrooms": 3, "bathrooms": 4, "area_sqft": 2200, "property_type": "Penthouse", "days_on_market": 55, "description": "rare penthouse, panoramic views, recently renovated", "source": "Bayut", "url": _BAYUT_SEARCH.format(slug="jumeirah-lake-towers-jlt")},
]


def load_demo_data() -> List[dict]:
    results = []
    for i, item in enumerate(DEMO_LISTINGS):
        loc = item["location"]
        area = item["area_sqft"]
        price = item["price"]
        sc_psf = LOCATIONS.get(loc, {}).get("service_charge_psf", 15)
        loc_cfg = LOCATIONS.get(loc, {})

        results.append({
            "id": _make_id("demo", str(i)),
            "title": item["title"],
            "price": price,
            "bedrooms": item["bedrooms"],
            "bathrooms": item["bathrooms"],
            "area_sqft": area,
            "price_per_sqft": round(price / area, 2),
            "location": loc,
            "community": loc,
            "property_type": item["property_type"],
            "url": item["url"],
            "source": item["source"] + " (Demo)",
            "cover_photo": DEMO_PHOTOS[i % len(DEMO_PHOTOS)],
            "listed_date": "",
            "days_on_market": item["days_on_market"],
            "service_charge_estimate": round(sc_psf * area / 12, 0),
            "latitude": loc_cfg.get("lat"),
            "longitude": loc_cfg.get("lon"),
            "agent_name": "Demo Agent",
            "agent_phone": "",
            "description": item.get("description", ""),
        })

    return results


# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────

def scrape_all(progress_callback=None):
    """
    Scrape all sources. Returns (properties, errors, used_demo).
    Falls back to demo data if no ScraperAPI key and sites block direct access.
    """
    if not _SCRAPER_API_KEY:
        # Try direct scraping first; fall back to demo if it fails
        results, errors = _do_scrape(progress_callback)
        if results:
            return results, errors, False
        return load_demo_data(), ["Sites are blocking direct access. Showing demo data. Add a ScraperAPI key to fetch live listings."], True

    results, errors = _do_scrape(progress_callback)
    return results, errors, False


def _do_scrape(progress_callback=None):
    """
    Scrapes PropertyFinder only — Bayut serves CAPTCHAs to all automated requests.
    """
    all_properties = []
    errors = []
    location_names = list(LOCATIONS.keys())
    total = len(location_names)
    done = 0

    for loc in location_names:
        try:
            results = scrape_propertyfinder(loc)
            all_properties.extend(results)
            if not results:
                errors.append(f"PropertyFinder/{loc}: 0 results (CAPTCHA or page structure change)")
        except Exception as e:
            errors.append(f"PropertyFinder/{loc}: {e}")

        done += 1
        if progress_callback:
            progress_callback(done / total, f"PropertyFinder: {loc}")

    # Deduplicate
    seen = set()
    unique = []
    for p in all_properties:
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)

    return unique, errors
