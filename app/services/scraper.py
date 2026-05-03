"""
Google Hotels reverse-engineered scraper.

Strategy:
  1. GET Google Hotels search page with realistic browser headers via requests (no Selenium).
  2. Extract embedded JSON blobs (AF_initDataCallback / ds:0).
  3. Recursively walk nested arrays using known keys to extract properties, ads, brands.

Known response keys (from HAR analysis):
  - 397419284 → organic property listings (initial page load)
  - 300000000 → sponsored ad listings (initial page load)
  - 441552390 → detail data (images, coordinates, address)
  - 449990728 → nearby places / brands
  - 449069993 → property names (batchexecute pagination)
  - 415404532 → map bounds (batchexecute pagination)
  - 404340221 → search config (batchexecute pagination)

Pagination strategy:
  - Page 1: normal HTML fetch → ds:0 extraction
  - Page 2+: URL params ts + qs + ap=MAE
  - ts = protobuf-encoded search state (location, dates, currency)
  - qs = base64 token from data-next-page-token attribute in HTML
  - Each page's HTML contains data-next-page-token for the NEXT page

Rate-limit mitigation:
  - SOCKS5 proxy rotation per request
  - Randomised User-Agent + Accept-Language headers
  - Exponential back-off on 429 / 503
"""
import base64
import json
import logging
import random
import re
import time
from typing import Any, Optional

import os
import requests

from app.services.webshare import get_random_proxy

logger = logging.getLogger(__name__)

GOOGLE_HOTELS_URL = "https://www.google.com/travel/search"
MAX_RETRIES = 3
BACKOFF_BASE = 1.5

TOR_PROXY_URL = os.getenv("TOR_PROXY_URL", "socks5://127.0.0.1:9050")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

# Known keys from HAR analysis
KEY_PROPERTIES = "397419284"
KEY_ADS = "300000000"
KEY_DETAIL = "441552390"
KEY_NEARBY = "449990728"

AMENITY_MAP = {
    1: "Free Wi-Fi", 2: "Air conditioning", 3: "Restaurant", 4: "Room service",
    5: "Free parking", 6: "Fitness center", 7: "Pool", 8: "Spa",
    9: "Business center", 10: "Laundry", 11: "24-hour front desk", 12: "Bar",
    13: "Breakfast", 14: "Hot tub", 15: "Pet friendly", 16: "Kitchen",
    17: "Airport shuttle", 18: "EV charging", 19: "Beach access", 20: "Casino",
    21: "Child friendly", 22: "Concierge", 23: "Free cancellation", 24: "No smoking",
    25: "Ocean view", 26: "Mountain view", 27: "City view", 28: "Wheelchair accessible",
    29: "Meeting rooms", 30: "Tennis", 31: "Golf", 32: "Water park",
    33: "Private beach", 34: "Rooftop bar", 35: "Fireplace", 36: "Garden",
}


def _random_headers(language: str = "en") -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": f"{language},{language}-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="8"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    }


def _safe_get(obj: Any, *keys, default=None) -> Any:
    for key in keys:
        if obj is None:
            return default
        if isinstance(obj, list):
            if not isinstance(key, int) or key >= len(obj) or key < -len(obj):
                return default
            obj = obj[key]
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return default
    return obj if obj is not None else default


def _build_url_params(location: str, check_in: str, check_out: str,
                      adults: int = 2, children: int = 0,
                      children_ages: str = "", currency: str = "USD",
                      language: str = "en", page: int = 1,
                      sort_by: str = None, price_min: int = None,
                      price_max: int = None, hotel_class: str = None,
                      cursor: str = None) -> dict:
    params = {
        "q": location,
        "hl": language,
        "gl": "us",
        "curr": currency,
    }
    ci_parts = check_in.split("-")
    co_parts = check_out.split("-")
    if len(ci_parts) == 3 and len(co_parts) == 3:
        params["ci"] = ci_parts[1]
        params["co"] = co_parts[1]
        params["cs"] = ci_parts[2]
        params["ce"] = co_parts[2]
    if adults != 2:
        params["adults"] = str(adults)
    if children > 0:
        params["children"] = str(children)
    if children_ages:
        params["children_ages"] = children_ages
    if page > 1:
        params["page"] = str(page)
    if cursor:
        params["kd"] = cursor
    return params


def _extract_ds0_data(html: str) -> Optional[list]:
    idx = html.find("key: 'ds:0', hash:")
    if idx == -1:
        idx = html.find('key: "ds:0", hash:')
    if idx == -1:
        return None

    data_idx = html.find("data:[", idx)
    if data_idx == -1 or data_idx - idx > 500:
        return None

    start = data_idx + 5  # position of the opening '['
    # Find matching closing bracket using depth counter
    depth = 0
    limit = min(len(html), start + 5_000_000)
    i = start
    while i < limit:
        c = html[i]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        return None

    json_str = html[start:i + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def _find_container(ds0: Any, depth: int = 0) -> Optional[list]:
    if depth > 15 or not isinstance(ds0, list):
        return None
    for item in ds0:
        if isinstance(item, list) and len(item) >= 2:
            if isinstance(item[1], dict):
                if KEY_PROPERTIES in item[1] or KEY_ADS in item[1]:
                    return ds0
        result = _find_container(item, depth + 1)
        if result is not None:
            return result
    return None


def _find_key_containers(container: list, key: str, depth: int = 0) -> list:
    results = []
    if depth > 12 or not isinstance(container, list):
        return results
    for item in container:
        if isinstance(item, list) and len(item) >= 2:
            if isinstance(item[1], dict) and key in item[1]:
                results.append(item[1][key])
        sub = _find_key_containers(item, key, depth + 1)
        results.extend(sub)
    return results


def _parse_coords(raw: Any) -> Optional[dict]:
    if isinstance(raw, list) and len(raw) >= 2:
        try:
            return {"latitude": float(raw[0]), "longitude": float(raw[1])}
        except (ValueError, TypeError):
            return None
    if isinstance(raw, dict):
        return {"latitude": raw.get("latitude"), "longitude": raw.get("longitude")}
    return None


def _parse_amenities(raw: Any) -> list[str]:
    if not raw or not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, int):
            name = AMENITY_MAP.get(item)
            if name:
                result.append(name)
            else:
                result.append(f"Amenity {item}")
        elif isinstance(item, list) and item:
            if isinstance(item[0], str):
                result.append(item[0])
            elif isinstance(item[0], int):
                name = AMENITY_MAP.get(item[0])
                if name:
                    result.append(name)
        elif isinstance(item, dict):
            n = item.get("name") or item.get("label")
            if n:
                result.append(str(n))
    return result


def _parse_images(raw: Any) -> list[dict]:
    """Parse images from property entry[5][1].
    Structure: list of [None, [url]] or [None, [url1, url2, ...]]
    """
    if not raw or not isinstance(raw, list):
        return []
    images = []
    for item in raw:
        if isinstance(item, list) and len(item) >= 2:
            # item = [None, [url]] or [None, [url1, url2]]
            url_list = item[1] if isinstance(item[1], list) else [item[1]]
            for url in url_list:
                if isinstance(url, str) and url.startswith("http"):
                    images.append({
                        "thumbnail": url,
                        "original_image": url,
                    })
        elif isinstance(item, list) and item:
            url = item[0]
            if isinstance(url, str) and url.startswith("http"):
                images.append({"thumbnail": url, "original_image": url})
        elif isinstance(item, dict):
            images.append({
                "thumbnail": item.get("thumbnail"),
                "original_image": item.get("original_image")
            })
    return images


def _parse_nearby_places(raw: Any) -> list[dict]:
    if not raw or not isinstance(raw, list):
        return []
    places = []
    for item in raw:
        if not isinstance(item, list):
            continue
        name = _safe_get(item, 0)
        if not name or not isinstance(name, str):
            continue
        transports_raw = _safe_get(item, 1) or []
        transports = []
        if isinstance(transports_raw, list):
            for t in transports_raw:
                if isinstance(t, list) and len(t) >= 2:
                    t_type = _safe_get(t, 0)
                    t_dur = _safe_get(t, 1)
                    if t_type or t_dur:
                        transports.append({"type": t_type, "duration": t_dur})
        places.append({"name": name, "transportations": transports})
    return places


def _parse_rate(raw: Any) -> Optional[dict]:
    if isinstance(raw, list) and len(raw) >= 1:
        return {
            "lowest": _safe_get(raw, 0) if isinstance(_safe_get(raw, 0), str) else None,
            "before_taxes_fees": _safe_get(raw, 1) if isinstance(_safe_get(raw, 1), str) else None
        }
    if isinstance(raw, dict):
        return {
            "lowest": raw.get("lowest"),
            "before_taxes_fees": raw.get("before_taxes_fees")
        }
    return None


def _extract_hotel_class_int(raw: Any) -> Optional[int]:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        m = re.search(r"(\d)", raw)
        return int(m.group(1)) if m else None
    return None


def _parse_ratings(raw: Any) -> list[dict]:
    """Parse ratings from entry[7][1].
    Structure: [[5, 79, 14257], [4, 15, 2766], ...] where [stars, percentage, count]
    or nested: [[[5, 79, 14257], [4, 15, 2766], ...]]
    """
    if not raw or not isinstance(raw, list):
        return []
    ratings = []
    # Handle nested structure
    items = raw
    if len(raw) == 1 and isinstance(raw[0], list):
        items = raw[0]
    for item in items:
        if isinstance(item, list) and len(item) >= 3:
            try:
                ratings.append({"stars": int(item[0]), "count": int(item[2])})
            except (ValueError, TypeError):
                pass
        elif isinstance(item, list) and len(item) >= 2:
            try:
                ratings.append({"stars": int(item[0]), "count": int(item[1])})
            except (ValueError, TypeError):
                pass
    return ratings


def _parse_reviews_breakdown(raw: Any) -> list[dict]:
    if not raw or not isinstance(raw, list):
        return []
    breakdown = []
    for item in raw:
        if isinstance(item, list) and len(item) >= 4:
            name = _safe_get(item, 0)
            if name and isinstance(name, str):
                breakdown.append({
                    "name": name,
                    "description": _safe_get(item, 1) or name,
                    "total_mentioned": _safe_get(item, 2),
                    "positive": _safe_get(item, 3),
                    "negative": _safe_get(item, 4) if len(item) > 4 else None,
                    "neutral": _safe_get(item, 5) if len(item) > 5 else None,
                })
    return breakdown


def _parse_property(entry: list) -> Optional[dict]:
    """Parse a single organic property entry.

    Live index mapping (from HAR analysis):
      [1]  title            [2][0]  coords [lat,lng]    [3]  hotel_class
      [5]  images           [6]  rate/pricing         [7]  reviews [rating, count]
      [11] description      [12] thumbnail            [20] property_token
    """
    try:
        if not isinstance(entry, list) or len(entry) < 21:
            return None
        title = _safe_get(entry, 1)
        if not title or not isinstance(title, str) or len(title) < 3:
            return None

        # Coordinates: entry[2][0] = [lat, lng]
        coords_raw = _safe_get(entry, 2, 0)

        # Hotel class: entry[3] = ['5-star hotel', 5] or int or str
        hotel_class_raw = _safe_get(entry, 3)

        # Images: entry[5][1] = list of [None, [url]]
        images_raw = _safe_get(entry, 5, 1)

        # Rate: entry[6][2] = [None, ['₹11,579', None, 11579, ...], ...]
        rate_block = _safe_get(entry, 6, 2)
        rate_lowest = _safe_get(rate_block, 1, 0) if isinstance(rate_block, list) else None
        rate_before = _safe_get(rate_block, 1, 1) if isinstance(rate_block, list) else None

        # Total rate: entry[6][1][0] = [price_int, 0, ...]
        total_rate_int = _safe_get(entry, 6, 1, 0, 0) if isinstance(_safe_get(entry, 6, 1), list) else None

        # Reviews: entry[7][0] = [rating, count], entry[7][1] = ratings breakdown
        reviews_raw = _safe_get(entry, 7, 0)
        reviews_rating = _safe_get(reviews_raw, 0)
        reviews_count = _safe_get(reviews_raw, 1)
        ratings_raw = _safe_get(entry, 7, 1)

        # Description: entry[11] = [text] or str
        desc_raw = _safe_get(entry, 11)
        desc_val = desc_raw[0] if isinstance(desc_raw, list) and desc_raw else desc_raw
        if not isinstance(desc_val, str):
            desc_val = None

        # Thumbnail: entry[12] = url str
        thumbnail_val = _safe_get(entry, 12) if isinstance(_safe_get(entry, 12), str) else None

        # Property token: entry[20]
        property_token = _safe_get(entry, 20)

        # Link: entry[6][2][4] or entry[6][3] sometimes has a link
        link_val = None
        rate_link = _safe_get(entry, 6, 2, 4) if isinstance(_safe_get(entry, 6, 2), list) else None
        if isinstance(rate_link, list) and rate_link:
            link_val = rate_link[0] if isinstance(rate_link[0], str) else None
        if link_val and link_val.startswith("/"):
            link_val = f"https://www.google.com{link_val}"

        # Nearby places: entry[10] sometimes
        nearby_raw = _safe_get(entry, 10)

        # Amenities: entry[8] or deeper (list of amenity IDs or names)
        amenities_raw = _safe_get(entry, 8)

        # Eco certified: entry[14] or entry[26] sometimes
        eco_raw = _safe_get(entry, 14)
        eco_val = bool(eco_raw) if eco_raw is not None and eco_raw != 0 else None

        # Check-in/out times: not typically in ds:0 data
        check_in_val = None
        check_out_val = None

        # Location rating: entry[7] sometimes has it deeper
        location_rating_val = None

        # Reviews breakdown: deeper in entry[7]
        reviews_breakdown_raw = None

        return {
            "type": "hotel",
            "title": title,
            "description": desc_val,
            "link": link_val,
            "property_token": property_token,
            "gps_coordinates": _parse_coords(coords_raw),
            "check_in_time": check_in_val,
            "check_out_time": check_out_val,
            "rate_per_night": {
                "lowest": rate_lowest if isinstance(rate_lowest, str) else None,
                "before_taxes_fees": rate_before if isinstance(rate_before, str) else None,
            },
            "total_rate": {
                "lowest": rate_lowest if isinstance(rate_lowest, str) else None,
                "before_taxes_fees": rate_before if isinstance(rate_before, str) else None,
            },
            "nearby_places": _parse_nearby_places(nearby_raw),
            "hotel_class": hotel_class_raw[0] if isinstance(hotel_class_raw, list) and hotel_class_raw else (hotel_class_raw if isinstance(hotel_class_raw, str) else None),
            "extracted_hotel_class": hotel_class_raw[1] if isinstance(hotel_class_raw, list) and len(hotel_class_raw) > 1 else _extract_hotel_class_int(hotel_class_raw),
            "images": _parse_images(images_raw),
            "reviews": float(reviews_rating) if reviews_rating is not None and not isinstance(reviews_rating, bool) else None,
            "overall_rating": int(reviews_count) if reviews_count is not None and not isinstance(reviews_count, bool) else None,
            "ratings": _parse_ratings(ratings_raw),
            "location_rating": float(location_rating_val) if location_rating_val is not None else None,
            "reviews_breakdown": _parse_reviews_breakdown(reviews_breakdown_raw),
            "amenities": _parse_amenities(amenities_raw),
            "eco_certified": eco_val,
        }
    except Exception as e:
        logger.debug(f"Property parse error: {e}")
        return None


def _parse_ad(entry: Any) -> Optional[dict]:
    """Parse a single sponsored ad entry.

    Live index mapping (from HAR analysis):
      [0]  title            [1]  link (/aclk?...)     [2]  price
      [3]  thumbnail        [4]  reviews count        [5]  overall rating
      [6]  source           [7]  source_icon          [9]  amenities
      [10] hotel_class       [11] free_cancellation   [13] property_token
      [16] coordinates [lat,lng]
    """
    try:
        if isinstance(entry, dict):
            return {
                "title": entry.get("title") or entry.get("name"),
                "source": entry.get("source"),
                "source_icon": entry.get("source_icon"),
                "link": entry.get("link") or entry.get("url"),
                "property_token": entry.get("property_token"),
                "gps_coordinates": _parse_coords(entry.get("gps_coordinates")),
                "thumbnail": entry.get("thumbnail"),
                "price": entry.get("price"),
                "reviews": entry.get("reviews"),
                "overall_rating": entry.get("overall_rating"),
                "amenities": _parse_amenities(entry.get("amenities")),
                "hotel_class": _extract_hotel_class_int(entry.get("hotel_class")),
                "free_cancellation": entry.get("free_cancellation"),
            }
        elif isinstance(entry, list) and len(entry) >= 14:
            title = _safe_get(entry, 0)
            if not title or not isinstance(title, str):
                return None

            # Link: prepend https://www.google.com if relative
            link_raw = _safe_get(entry, 1)
            if isinstance(link_raw, str) and link_raw.startswith("/"):
                link_val = f"https://www.google.com{link_raw}"
            elif isinstance(link_raw, str):
                link_val = link_raw
            else:
                link_val = None

            # Thumbnail: entry[3] can be list with URL at [0] (starts with //)
            thumb_raw = _safe_get(entry, 3)
            if isinstance(thumb_raw, list) and thumb_raw:
                thumb_val = thumb_raw[0] if isinstance(thumb_raw[0], str) else None
                if thumb_val and thumb_val.startswith("//"):
                    thumb_val = f"https:{thumb_val}"
            elif isinstance(thumb_raw, str):
                thumb_val = thumb_raw if not thumb_raw.startswith("//") else f"https:{thumb_raw}"
            else:
                thumb_val = None

            # Source icon: entry[7] starts with //
            src_icon_raw = _safe_get(entry, 7)
            if isinstance(src_icon_raw, str) and src_icon_raw.startswith("//"):
                src_icon_val = f"https:{src_icon_raw}"
            else:
                src_icon_val = src_icon_raw if isinstance(src_icon_raw, str) else None

            # Amenities: entry[9] is list of ints (amenity IDs)
            amenities_raw = _safe_get(entry, 9)

            # Free cancellation: entry[11] = 0 or 1
            fc_raw = _safe_get(entry, 11)
            fc_val = bool(fc_raw) if fc_raw is not None else None

            return {
                "title": title,
                "source": _safe_get(entry, 6) if isinstance(_safe_get(entry, 6), str) else None,
                "source_icon": src_icon_val,
                "link": link_val,
                "property_token": _safe_get(entry, 13),
                "gps_coordinates": _parse_coords(_safe_get(entry, 16)),
                "thumbnail": thumb_val,
                "price": _safe_get(entry, 2) if isinstance(_safe_get(entry, 2), (str, int)) else None,
                "reviews": _safe_get(entry, 4) if isinstance(_safe_get(entry, 4), (int, float)) else None,
                "overall_rating": _safe_get(entry, 5) if isinstance(_safe_get(entry, 5), (int, float)) else None,
                "amenities": _parse_amenities(amenities_raw),
                "hotel_class": _safe_get(entry, 10) if isinstance(_safe_get(entry, 10), int) else _extract_hotel_class_int(_safe_get(entry, 10)),
                "free_cancellation": fc_val,
            }
        return None
    except Exception as e:
        logger.debug(f"Ad parse error: {e}")
        return None


def _extract_organic_hotels(container: list) -> list[dict]:
    properties = []
    key_containers = _find_key_containers(container, KEY_PROPERTIES)
    for kc in key_containers:
        if isinstance(kc, list):
            for entry in kc:
                prop = _parse_property(entry)
                if prop and prop.get("title"):
                    properties.append(prop)
    if not properties:
        for item in container:
            if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict):
                if KEY_PROPERTIES in item[1]:
                    data = item[1][KEY_PROPERTIES]
                    if isinstance(data, list):
                        for entry in data:
                            prop = _parse_property(entry)
                            if prop and prop.get("title"):
                                properties.append(prop)
    return properties


def _extract_sponsored_hotels(container: list) -> list[dict]:
    ads = []
    for item in container:
        if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict):
            if KEY_ADS in item[1]:
                data = item[1][KEY_ADS]
                if isinstance(data, list):
                    # Ad container structure: [None, [...], [ad1, ad2, ...]]
                    # Ads list is typically at the last index that is a list of lists
                    ads_list = None
                    for sub in data:
                        if isinstance(sub, list) and len(sub) > 0:
                            # Check if this looks like a list of ad entries
                            if isinstance(sub[0], list) and len(sub[0]) >= 10:
                                ads_list = sub
                                break
                    if ads_list:
                        for entry in ads_list:
                            ad = _parse_ad(entry)
                            if ad and ad.get("title"):
                                ads.append(ad)
                    else:
                        # Fallback: try parsing each entry directly
                        for entry in data:
                            ad = _parse_ad(entry)
                            if ad and ad.get("title"):
                                ads.append(ad)
    return ads


def _extract_brands(container: list) -> list[dict]:
    brands = []
    for item in container:
        if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], dict):
            if KEY_NEARBY in item[1]:
                data = item[1][KEY_NEARBY]
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, list) and len(entry) >= 3:
                            brand_id = _safe_get(entry, 0)
                            brand_name = _safe_get(entry, 1)
                            children_raw = _safe_get(entry, 2)
                            children = []
                            if isinstance(children_raw, list):
                                for c in children_raw:
                                    if isinstance(c, list) and len(c) >= 2:
                                        children.append({"id": c[0], "name": c[1]})
                            if brand_name:
                                brands.append({
                                    "id": brand_id,
                                    "name": brand_name,
                                    "children": children
                                })
    return brands


def _extract_total_results(html: str) -> Optional[int]:
    match = re.search(r'(\d[\d,]*)\s+results?', html, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1).replace(',', ''))
        except ValueError:
            return None
    return None


def _extract_cursor(html: str) -> Optional[str]:
    patterns = [
        re.compile(r'"(ChgI[A-Za-z0-9+/=_-]+)"'),
        re.compile(r'"(ChoI[A-Za-z0-9+/=_-]+)"'),
        re.compile(r'"(ChkI[A-Za-z0-9+/=_-]+)"'),
        re.compile(r'(/g/11[a-z0-9]+)'),
    ]
    for pattern in patterns:
        matches = pattern.findall(html)
        if matches:
            return matches[0]
    return None


def _extract_next_page_token(html: str) -> Optional[str]:
    """Extract data-next-page-token from HTML pagination element.
    This token is used to build the qs URL parameter for the next page.
    """
    m = re.search(r'data-next-page-token="([^"]+)"', html)
    return m.group(1) if m else None


def _build_qs_param(next_page_token: str, page_num: int) -> str:
    """Build the qs URL parameter from data-next-page-token.
    
    The qs param is a base64-encoded protobuf:
    - Page 2: 12 04 <3 token bytes> 3d 38 0d
    - Page 3+: 12 04 <3 token bytes> 3d 38 0d 48 00
    """
    token_bytes = next_page_token.encode('ascii')[:3]
    if page_num <= 2:
        proto = bytes([0x12, 0x04]) + token_bytes + bytes([0x3d, 0x38, 0x0d])
    else:
        proto = bytes([0x12, 0x04]) + token_bytes + bytes([0x3d, 0x38, 0x0d, 0x48, 0x00])
    return base64.urlsafe_b64encode(proto).decode('ascii').rstrip('=')


def _build_ts_param(mid: str, place_id: str, location: str,
                    check_in: str, check_out: str, currency: str = "INR") -> str:
    """Build the ts URL parameter (protobuf-encoded search state).
    
    The ts param encodes: location mid, place_id, name, dates, currency.
    It stays constant across all pages for a given search.
    """
    ci_parts = check_in.split("-")
    co_parts = check_out.split("-")
    ci_month = int(ci_parts[1]) if len(ci_parts) == 3 else 5
    ci_day = int(ci_parts[2]) if len(ci_parts) == 3 else 9
    co_month = int(co_parts[1]) if len(co_parts) == 3 else 5
    co_day = int(co_parts[2]) if len(co_parts) == 3 else 10
    year = int(ci_parts[0]) if len(ci_parts) == 3 else 2026

    proto = bytearray()
    proto.extend(b'\x08\x01')  # field 1 = 1
    proto.extend(b'\x12\x0a\x0a\x02\x08\x03\x0a\x02\x08\x03\x10\x00')  # field 2: sort config

    # Field 3: location block
    loc_block = bytearray()
    loc_block.extend(b'\x0a\x09')
    loc_block.extend(mid.encode())
    loc_block.extend(b'\x32\x25')
    loc_block.extend(place_id.encode())
    loc_block.extend(b'\x3a\x08')
    loc_block.extend(location.encode())
    loc_inner = bytearray(b'\x12')
    loc_inner.append(len(loc_block))
    loc_inner.extend(loc_block)
    proto.extend(b'\x1a')
    proto.append(len(loc_inner))
    proto.extend(loc_inner)

    # Field 4: date info (varint year, month, day)
    year_bytes = bytearray()
    y = year
    while y > 127:
        year_bytes.append((y & 0x7f) | 0x80)
        y >>= 7
    year_bytes.append(y)

    proto.extend(b'\x12\x1a\x12\x14\x0a\x07\x08')
    proto.extend(year_bytes)
    proto.extend([ci_month, ci_day])
    proto.extend(b'\x12\x07\x08')
    proto.extend(year_bytes)
    proto.extend([co_month, co_day, 0x01])

    # Field 6: pagination state
    proto.extend(b'\x32\x02\x10\x00')

    # Field 5: currency
    proto.extend(b'\x2a\x07\x0a\x05\x3a\x03')
    proto.extend(currency.encode())

    return base64.urlsafe_b64encode(bytes(proto)).decode('ascii').rstrip('=')


def _extract_mid(html: str) -> str:
    """Extract /m/ ID for the location from the HTML."""
    matches = re.findall(r'/m/([a-z0-9]+)', html)
    for m in matches:
        mid = f"/m/{m}"
        if len(m) >= 3 and m != "storepages":
            return mid
    return "/m/03fxfy"


def _extract_place_id(html: str) -> str:
    """Extract hex place ID from the HTML."""
    m = re.search(r'(0x[a-f0-9]+:0x[a-f0-9]+)', html)
    return m.group(1) if m else "0x375a5a287f9133ff:0x2bbd1332436bde32"


def _extract_currency_for_ts(currency: str) -> str:
    """Map display currency to the currency code used in ts param.
    Google uses the local currency in the ts param, not the display currency.
    """
    # Common mappings: for Indian locations use INR, etc.
    # For now, return the currency as-is; the ts param uses the same code
    return currency


def fetch_travel_page(session: requests.Session, location: str, language: str = "en",
                      currency: str = "USD", check_in: str = "", check_out: str = "",
                      adults: int = 2, children: int = 0, children_ages: str = "",
                      page: int = 1, cursor: str = None,
                      proxy_url: str = None, timeout: int = 30) -> Optional[str]:
    params = _build_url_params(
        location=location, check_in=check_in, check_out=check_out,
        adults=adults, children=children, children_ages=children_ages,
        currency=currency, language=language, page=page, cursor=cursor
    )
    headers = _random_headers(language)
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(
                GOOGLE_HOTELS_URL, params=params, headers=headers,
                proxies=proxies, timeout=timeout,
                allow_redirects=True
            )
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (429, 503):
                wait = BACKOFF_BASE ** attempt + random.uniform(0, 1)
                logger.warning(f"Rate limited (HTTP {resp.status_code}). Retry {attempt}/{MAX_RETRIES} in {wait:.1f}s")
                time.sleep(wait)
                continue
            logger.error(f"HTTP {resp.status_code} fetching page {page}")
            return None
        except requests.exceptions.ProxyError as e:
            logger.warning(f"Proxy error: {e}. Retry {attempt}/{MAX_RETRIES}")
            time.sleep(BACKOFF_BASE ** attempt)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error: {e}. Retry {attempt}/{MAX_RETRIES}")
            time.sleep(BACKOFF_BASE ** attempt)
    return None


def scrape_page(session: requests.Session, location: str, language: str,
                currency: str, check_in: str, check_out: str,
                adults: int, children: int, children_ages: str,
                page: int, cursor: str = None,
                proxy_url: str = None, timeout: int = 30) -> dict:
    html = fetch_travel_page(
        session, location=location, language=language, currency=currency,
        check_in=check_in, check_out=check_out, adults=adults,
        children=children, children_ages=children_ages,
        page=page, cursor=cursor, proxy_url=proxy_url, timeout=timeout
    )
    if not html:
        return {"ads": [], "properties": [], "brands": [], "total_results": None, "cursor": None, "html": None}

    ds0 = _extract_ds0_data(html)
    if not ds0:
        return {"ads": [], "properties": [], "brands": [], "total_results": None, "cursor": None, "html": html}

    container = _find_container(ds0)
    if not container:
        return {"ads": [], "properties": [], "brands": [], "total_results": None, "cursor": None, "html": html}

    ads = _extract_sponsored_hotels(container)
    properties = _extract_organic_hotels(container)
    brands = _extract_brands(container)
    total_results = _extract_total_results(html)
    next_cursor = _extract_cursor(html)

    return {
        "ads": ads,
        "properties": properties,
        "brands": brands,
        "total_results": total_results,
        "cursor": next_cursor,
        "html": html,
    }


def scrape_hotels(location: str, check_in: str, check_out: str,
                  adults: int = 2, children: int = 0, children_ages: str = "",
                  currency: str = "USD", language: str = "en",
                  max_pages: int = 0, proxy_url: str = None,
                  timeout: int = 30) -> dict:
    proxy_list = []
    if not proxy_url:
        proxy_list = get_proxies()
        if proxy_list:
            logger.info(f"Loaded {len(proxy_list)} Webshare proxies for rotation")
            proxy_url = proxy_list[0]["url"]
        else:
            logger.warning("No Webshare proxy available, trying without proxy")

    session = requests.Session()
    all_ads = []
    all_properties = []
    all_brands = []
    total_results = None
    calculated_max_pages = 1000
    proxy_index = 0

    def get_next_proxy():
        nonlocal proxy_index
        if proxy_list:
            proxy = proxy_list[proxy_index % len(proxy_list)]["url"]
            proxy_index += 1
            return proxy
        return proxy_url

    # Page 1: fetch via normal HTML request
    logger.info("Scraping page 1 (HTML fetch)...")
    result = scrape_page(
        session, location=location, language=language, currency=currency,
        check_in=check_in, check_out=check_out, adults=adults,
        children=children, children_ages=children_ages,
        page=1, proxy_url=get_next_proxy(), timeout=timeout
    )

    if result.get("total_results"):
        total_results = result["total_results"]
        calculated_max_pages = (total_results + 19) // 20
        logger.info(f"Total results: {total_results}, pages needed: {calculated_max_pages}")

    page_ads = result.get("ads", [])
    page_props = result.get("properties", [])
    page_brands = result.get("brands", [])

    logger.info(f"Page 1: {len(page_ads)} ads, {len(page_props)} properties")

    all_ads.extend(page_ads)
    all_properties.extend(page_props)
    all_brands.extend(page_brands)

    if not page_props and not page_ads:
        logger.info("No results on page 1, stopping.")
        return _build_scrape_result(all_ads, all_properties, all_brands, total_results)

    # Extract pagination data from page 1 HTML
    html = result.get("html")
    next_page_token = _extract_next_page_token(html) if html else None

    if not next_page_token:
        logger.info("No next-page-token found in page 1, pagination not available.")
        return _build_scrape_result(all_ads, all_properties, all_brands, total_results)

    # Build ts param (stays constant across all pages)
    mid = _extract_mid(html) if html else "/m/03fxfy"
    place_id = _extract_place_id(html) if html else "0x375a5a287f9133ff:0x2bbd1332436bde32"
    ts_currency = _extract_currency_for_ts(currency)
    ts = _build_ts_param(mid, place_id, location, check_in, check_out, ts_currency)

    logger.info(f"Pagination: ts={ts[:30]}..., next_token={next_page_token}")

    # Pages 2+: fetch with ts + qs URL params
    for page in range(2, 1001):
        if max_pages > 0 and page > max_pages:
            break
        if max_pages == 0 and page > calculated_max_pages:
            logger.info(f"Reached calculated max pages ({calculated_max_pages})")
            break
        if not next_page_token:
            logger.info("No next-page-token — pagination complete.")
            break

        qs = _build_qs_param(next_page_token, page)
        logger.info(f"Scraping page {page} (ts+qs, token={next_page_token})...")

        # Build URL params for next page
        params = _build_url_params(
            location=location, check_in=check_in, check_out=check_out,
            adults=adults, children=children, children_ages=children_ages,
            currency=currency, language=language
        )
        params["ts"] = ts
        params["qs"] = qs
        params["ap"] = "MAE"

        headers = _random_headers(language)
        current_proxy = get_next_proxy()
        proxies = {"http": current_proxy, "https": current_proxy} if current_proxy else None

        page_html = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = session.get(GOOGLE_HOTELS_URL, params=params,
                                   headers=headers, proxies=proxies, timeout=timeout)
                if resp.status_code == 200:
                    page_html = resp.text
                    break
                if resp.status_code in (429, 503):
                    wait = BACKOFF_BASE ** attempt + random.uniform(0, 1)
                    logger.warning(f"Rate limited (HTTP {resp.status_code}). Retry {attempt}/{MAX_RETRIES} in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                logger.error(f"HTTP {resp.status_code} on page {page}")
                break
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request error on page {page}: {e}. Retry {attempt}/{MAX_RETRIES}")
                time.sleep(BACKOFF_BASE ** attempt)

        if not page_html:
            logger.info(f"Failed to fetch page {page}, stopping.")
            break

        ds0 = _extract_ds0_data(page_html)
        if not ds0:
            logger.info(f"No ds:0 data on page {page}, stopping.")
            break

        container = _find_container(ds0)
        if not container:
            logger.info(f"No container on page {page}, stopping.")
            break

        page_props = _extract_organic_hotels(container)
        page_ads = _extract_sponsored_hotels(container)
        page_brands = _extract_brands(container)

        logger.info(f"Page {page}: {len(page_ads)} ads, {len(page_props)} properties")

        if not page_props and not page_ads:
            logger.info(f"No more results after page {page}")
            break

        all_ads.extend(page_ads)
        all_properties.extend(page_props)
        all_brands.extend(page_brands)

        # Extract next page token for the following page
        next_page_token = _extract_next_page_token(page_html)

    raw_ads_count = len(all_ads)
    raw_props_count = len(all_properties)

    return _build_scrape_result(all_ads, all_properties, all_brands, total_results,
                                raw_ads_count, raw_props_count)


def _build_scrape_result(all_ads, all_properties, all_brands, total_results,
                         raw_ads_count=None, raw_props_count=None) -> dict:
    """Deduplicate and build the final scrape result."""
    if raw_ads_count is None:
        raw_ads_count = len(all_ads)
    if raw_props_count is None:
        raw_props_count = len(all_properties)

    seen_tokens = set()
    unique_props = []
    for p in all_properties:
        token = p.get("property_token") or p.get("title")
        if token and token not in seen_tokens:
            seen_tokens.add(token)
            unique_props.append(p)

    seen_ad_tokens = set()
    unique_ads = []
    for a in all_ads:
        token = a.get("property_token") or a.get("title")
        if token and token not in seen_ad_tokens:
            seen_ad_tokens.add(token)
            unique_ads.append(a)

    seen_brand_ids = set()
    unique_brands = []
    for b in all_brands:
        bid = b.get("id") or b.get("name")
        if bid and bid not in seen_brand_ids:
            seen_brand_ids.add(bid)
            unique_brands.append(b)

    logger.info(f"Deduplication: {raw_props_count} raw properties -> {len(unique_props)} unique")
    logger.info(f"Deduplication: {raw_ads_count} raw ads -> {len(unique_ads)} unique")

    return {
        "pagination": {
            "next_page_token": None,
            "total_results": total_results,
        },
        "ads": unique_ads,
        "brands": unique_brands,
        "properties": unique_props,
        "_raw_counts": {
            "raw_ads": raw_ads_count,
            "raw_properties": raw_props_count,
        }
    }
