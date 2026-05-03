import logging
import os
import random
import threading
import time
import requests

logger = logging.getLogger(__name__)

WEBSHARE_API_KEY = os.getenv("WEBSHARE_API_KEY", "uskrwhejw3qkumybo3odnp38p34hribyqegyll6v")
WEBSHARE_API_URL = "https://proxy.webshare.io/api/v2/proxy/list/"

_proxy_list = []
_last_fetch = 0
_CACHE_TTL = 300
_fetch_lock = threading.Lock()


def get_proxies(force_refresh: bool = False) -> list[dict]:
    global _proxy_list, _last_fetch

    if not force_refresh and _proxy_list and (time.time() - _last_fetch) < _CACHE_TTL:
        return _proxy_list

    with _fetch_lock:
        if not force_refresh and _proxy_list and (time.time() - _last_fetch) < _CACHE_TTL:
            return _proxy_list

        try:
            headers = {"Authorization": f"Token {WEBSHARE_API_KEY}"}
            params = {"mode": "direct", "page": 1, "page_size": 10, "valid": "true"}
            resp = requests.get(WEBSHARE_API_URL, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            _proxy_list = []
            for p in data.get("results", []):
                proxy_url = f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
                _proxy_list.append({
                    "url": proxy_url,
                    "address": p["proxy_address"],
                    "port": p["port"],
                    "country": p.get("country_code", ""),
                    "city": p.get("city_name", ""),
                })

            _last_fetch = time.time()
            logger.info(f"Fetched {len(_proxy_list)} Webshare proxies")
            return _proxy_list
        except Exception as e:
            logger.error(f"Failed to fetch Webshare proxies: {e}")
            return _proxy_list if _proxy_list else []


def get_random_proxy() -> str | None:
    proxies = get_proxies()
    if not proxies:
        return None
    return random.choice(proxies)["url"]
