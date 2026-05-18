import json
import time
import random
import hashlib
import requests
from datetime import datetime
from typing import List, Tuple
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

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _sleep():
    time.sleep(random.uniform(1.5, 3.5))


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


def _parse_bayut_page(html: str, location_name: str, source_url: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        return []

    try:
        data = json.loads(script_tag.string)
    except (json.JSONDecodeError, TypeError):
        return []

    # Navigate the nested structure — Bayut stores results under different paths
    hits = []
    try:
        sr = data["props"]["pageProps"]["searchResult"]
        hits = sr.get("hits", [])
    except (KeyError, TypeError):
        pass

    if not hits:
        # Try alternate path
        try:
            hits = data["props"]["pageProps"]["listingList"]["listings"]
        except (KeyError, TypeError):
            pass

    results = []
    for h in hits:
        try:
            prop_id = _make_id("bayut", str(h.get("externalID", h.get("id", ""))))
            price = int(h.get("price", 0))
            area = float(h.get("area", 0))
            price_psf = round(price / area, 2) if area > 0 else 0

            loc_parts = h.get("location", [])
            community = loc_parts[-1].get("name", "") if loc_parts else ""

            cover = ""
            if h.get("coverPhoto"):
                cover = h["coverPhoto"].get("url", "")
            elif h.get("mainPhoto"):
                cover = h["mainPhoto"].get("url", "")

            from config import LOCATIONS as LOC_CONFIG
            sc_psf = LOC_CONFIG.get(location_name, {}).get("service_charge_psf", 15)
            service_charge = round(sc_psf * area / 12, 0)  # monthly

            results.append({
                "id": prop_id,
                "title": h.get("title", ""),
                "price": price,
                "bedrooms": int(h.get("rooms", 0)),
                "bathrooms": int(h.get("baths", 0)),
                "area_sqft": area,
                "price_per_sqft": price_psf,
                "location": location_name,
                "community": community,
                "property_type": _get_property_type(h),
                "url": BAYUT_BASE + h.get("slug", ""),
                "source": "Bayut",
                "cover_photo": cover,
                "listed_date": h.get("createdAt", ""),
                "service_charge_estimate": service_charge,
                "latitude": h.get("geography", {}).get("lat"),
                "longitude": h.get("geography", {}).get("lng"),
                "agent_name": h.get("contactName", ""),
                "agent_phone": h.get("phoneNumber", {}).get("mobile", "") if isinstance(h.get("phoneNumber"), dict) else "",
                "description": h.get("description", "")[:500],
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
    page = 1

    while page <= 5:  # cap at 5 pages per location
        url = _bayut_search_url(slug, page)
        try:
            resp = SESSION.get(url, timeout=15)
            resp.raise_for_status()
            results = _parse_bayut_page(resp.text, location_name, url)
            if not results:
                break
            all_results.extend(results)
            page += 1
            _sleep()
        except requests.RequestException as e:
            print(f"[Bayut] Error fetching {location_name} page {page}: {e}")
            break

    return all_results


# ──────────────────────────────────────────────
# PropertyFinder scraper
# ──────────────────────────────────────────────

PF_BASE = "https://www.propertyfinder.ae"


def _pf_search_url(slug: str, page: int = 1) -> str:
    return (
        f"{PF_BASE}/en/search?c=1&fu=0&rp=y&ob=mr"
        f"&pe={PRICE_MAX}&pb={PRICE_MIN}"
        f"&l={slug}&page={page}"
    )


def _parse_pf_page(html: str, location_name: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")

    # PropertyFinder also uses Next.js
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        return []

    try:
        data = json.loads(script_tag.string)
    except (json.JSONDecodeError, TypeError):
        return []

    hits = []
    try:
        hits = data["props"]["pageProps"]["searchResult"]["listings"]
    except (KeyError, TypeError):
        pass

    if not hits:
        try:
            hits = data["props"]["pageProps"]["listings"]
        except (KeyError, TypeError):
            pass

    results = []
    for h in hits:
        try:
            prop_id = _make_id("pf", str(h.get("id", h.get("externalId", ""))))
            price = int(h.get("price", {}).get("value", 0)) if isinstance(h.get("price"), dict) else int(h.get("price", 0))
            area = float(h.get("area", {}).get("value", 0)) if isinstance(h.get("area"), dict) else float(h.get("area", 0))
            price_psf = round(price / area, 2) if area > 0 else 0

            from config import LOCATIONS as LOC_CONFIG
            sc_psf = LOC_CONFIG.get(location_name, {}).get("service_charge_psf", 15)
            service_charge = round(sc_psf * area / 12, 0)

            cover = ""
            photos = h.get("photos", [])
            if photos:
                cover = photos[0].get("url", "") if isinstance(photos[0], dict) else photos[0]

            results.append({
                "id": prop_id,
                "title": h.get("title", h.get("name", "")),
                "price": price,
                "bedrooms": int(h.get("bedrooms", h.get("rooms", 0))),
                "bathrooms": int(h.get("bathrooms", h.get("baths", 0))),
                "area_sqft": area,
                "price_per_sqft": price_psf,
                "location": location_name,
                "community": h.get("community", {}).get("name", "") if isinstance(h.get("community"), dict) else "",
                "property_type": h.get("type", {}).get("name", "Property") if isinstance(h.get("type"), dict) else h.get("type", "Property"),
                "url": PF_BASE + "/en/property/" + str(h.get("id", "")),
                "source": "PropertyFinder",
                "cover_photo": cover,
                "listed_date": h.get("publishedAt", h.get("createdAt", "")),
                "service_charge_estimate": service_charge,
                "latitude": h.get("geography", {}).get("lat") if isinstance(h.get("geography"), dict) else None,
                "longitude": h.get("geography", {}).get("lng") if isinstance(h.get("geography"), dict) else None,
                "agent_name": h.get("agent", {}).get("name", "") if isinstance(h.get("agent"), dict) else "",
                "agent_phone": h.get("agent", {}).get("phone", "") if isinstance(h.get("agent"), dict) else "",
                "description": h.get("description", "")[:500],
            })
        except Exception:
            continue

    return results


def scrape_propertyfinder(location_name: str) -> List[dict]:
    slug = LOCATIONS[location_name]["pf_slug"]
    all_results = []
    page = 1

    while page <= 5:
        url = _pf_search_url(slug, page)
        try:
            resp = SESSION.get(url, timeout=15)
            resp.raise_for_status()
            results = _parse_pf_page(resp.text, location_name)
            if not results:
                break
            all_results.extend(results)
            page += 1
            _sleep()
        except requests.RequestException as e:
            print(f"[PF] Error fetching {location_name} page {page}: {e}")
            break

    return all_results


# ──────────────────────────────────────────────
# Dubizzle scraper (bonus source)
# ──────────────────────────────────────────────

DUBIZZLE_SLUGS = {
    "The Views": "the-views",
    "Dubai Hills": "dubai-hills-estate",
    "Arabian Ranches 1": "arabian-ranches-1",
    "Studio City": "dubai-studio-city",
    "Motorcity": "motor-city",
    "JLT": "jumeirah-lake-towers-jlt",
}


def scrape_dubizzle(location_name: str) -> List[dict]:
    slug = DUBIZZLE_SLUGS.get(location_name, "")
    if not slug:
        return []

    url = (
        f"https://dubizzle.com/en/properties/residential/buy/?location={slug}"
        f"&price__gte={PRICE_MIN}&price__lte={PRICE_MAX}"
    )
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        return []

    try:
        data = json.loads(script_tag.string)
        hits = data["props"]["pageProps"]["listings"]["results"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return []

    results = []
    for h in hits:
        try:
            price = int(h.get("price", 0))
            area = float(h.get("area", 0))
            price_psf = round(price / area, 2) if area > 0 else 0

            from config import LOCATIONS as LOC_CONFIG
            sc_psf = LOC_CONFIG.get(location_name, {}).get("service_charge_psf", 15)
            service_charge = round(sc_psf * area / 12, 0)

            results.append({
                "id": _make_id("dubizzle", str(h.get("id", ""))),
                "title": h.get("title", ""),
                "price": price,
                "bedrooms": int(h.get("bedrooms", 0)),
                "bathrooms": int(h.get("bathrooms", 0)),
                "area_sqft": area,
                "price_per_sqft": price_psf,
                "location": location_name,
                "community": h.get("locationName", ""),
                "property_type": h.get("categoryName", "Property"),
                "url": "https://dubizzle.com" + h.get("absoluteUrl", ""),
                "source": "Dubizzle",
                "cover_photo": h.get("mainThumbnailUrl", ""),
                "listed_date": h.get("createdAt", ""),
                "service_charge_estimate": service_charge,
                "latitude": None,
                "longitude": None,
                "agent_name": h.get("agentName", ""),
                "agent_phone": "",
                "description": h.get("description", "")[:500],
            })
        except Exception:
            continue

    return results


# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────

def scrape_all(progress_callback=None):
    """Scrape all sources and locations. Returns (properties, errors)."""
    all_properties = []
    errors = []
    location_names = list(LOCATIONS.keys())
    total = len(location_names) * 2  # bayut + propertyfinder per location
    done = 0

    for loc in location_names:
        # Bayut
        try:
            results = scrape_bayut(loc)
            all_properties.extend(results)
        except Exception as e:
            errors.append(f"Bayut/{loc}: {e}")

        done += 1
        if progress_callback:
            progress_callback(done / total, f"Bayut: {loc}")

        # PropertyFinder
        try:
            results = scrape_propertyfinder(loc)
            all_properties.extend(results)
        except Exception as e:
            errors.append(f"PropertyFinder/{loc}: {e}")

        done += 1
        if progress_callback:
            progress_callback(done / total, f"PropertyFinder: {loc}")

    # Dubizzle (bonus, no progress tracking)
    for loc in location_names:
        try:
            results = scrape_dubizzle(loc)
            all_properties.extend(results)
        except Exception as e:
            errors.append(f"Dubizzle/{loc}: {e}")

    # Deduplicate by id
    seen = set()
    unique = []
    for p in all_properties:
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)

    return unique, errors
