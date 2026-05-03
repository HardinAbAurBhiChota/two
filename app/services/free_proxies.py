import json
import logging
import os
import random
import threading
import time

logger = logging.getLogger(__name__)

PROXY_FILE = os.getenv("PROXY_FILE", "/app/free-proxy-list.json")

_proxy_list = []
_last_load = 0
_CACHE_TTL = 300
_load_lock = threading.Lock()


def load_free_proxies(force_refresh: bool = False) -> list[dict]:
    global _proxy_list, _last_load

    if not force_refresh and _proxy_list and (time.time() - _last_load) < _CACHE_TTL:
        return _proxy_list

    with _load_lock:
        if not force_refresh and _proxy_list and (time.time() - _last_load) < _CACHE_TTL:
            return _proxy_list

        try:
            if not os.path.exists(PROXY_FILE):
                logger.warning(f"Proxy file not found: {PROXY_FILE}")
                return _proxy_list

            with open(PROXY_FILE, "r") as f:
                data = json.load(f)

            _proxy_list = []
            for p in data.get("proxies", []):
                if p.get("alive", False):
                    proxy_url = p.get("proxy")
                    if proxy_url:
                        _proxy_list.append({
                            "url": proxy_url,
                            "ip": p.get("ip", ""),
                            "port": p.get("port", 0),
                            "protocol": p.get("protocol", ""),
                            "anonymity": p.get("anonymity", ""),
                            "country": p.get("ip_data", {}).get("countryCode", ""),
                            "city": p.get("ip_data", {}).get("city", ""),
                        })

            _last_load = time.time()
            logger.info(f"Loaded {len(_proxy_list)} free residential proxies")
            return _proxy_list
        except Exception as e:
            logger.error(f"Failed to load free proxies: {e}")
            return _proxy_list if _proxy_list else []


def get_random_proxy() -> str | None:
    proxies = load_free_proxies()
    if not proxies:
        return None
    return random.choice(proxies)["url"]


def get_proxies() -> list[dict]:
    return load_free_proxies()
