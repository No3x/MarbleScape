"""One catalogue interface for MarbleScape's public still-image providers."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import threading
import time
from concurrent.futures import Future

from marblescape_himawari import HimawariClient
from marblescape_noaa import NOAAClient
from marblescape_slider import SliderClient
from marblescape_worldview import WorldviewClient
from marblescape_eumetsat import EumetsatCatalogueClient


NOAA_PROVIDERS = frozenset(("goes_east", "goes_west", "solar"))
CATALOGUE_PROVIDERS = (*sorted(NOAA_PROVIDERS), "himawari", "slider", "worldview")
_CACHE_VERSION = 1
_HTTP_CACHE_LIMIT = 16 * 1024 * 1024
_HTTP_ENTRY_LIMIT = 8 * 1024 * 1024
NOAA_CATALOGUE_TTL_SECONDS = 24 * 60 * 60
NOAA_FAILURE_BACKOFF_SECONDS = 60 * 60
STARTUP_CATALOGUE_TTL_SECONDS = 24 * 60 * 60
STARTUP_CATALOGUE_PROVIDERS = {
    "Himawari": "himawari", "CIRA SLIDER": "slider",
    "NASA Worldview": "worldview", "EUMETSAT": "eumetsat",
}


class _CatalogueHttpCache:
    """Keep validated catalogue responses for conditional requests across starts."""

    def __init__(self, path=None):
        self.path = Path(path).resolve() if path else None
        self._lock = threading.RLock()
        self._entries = {}
        if self.path is not None and self.path.is_file():
            try:
                if self.path.stat().st_size <= 24 * 1024 * 1024:
                    value = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(value, dict) and value.get("version") == 1 \
                            and isinstance(value.get("entries"), dict):
                        self._entries = value["entries"]
            except (OSError, ValueError, TypeError):
                pass

    def _save(self):
        if self.path is None:
            return
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps({"version": 1, "entries": self._entries}, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def headers(self, url):
        with self._lock:
            entry = self._entries.get(url, {})
            validators = entry.get("headers", {}) if isinstance(entry, dict) else {}
            result = {}
            if isinstance(validators, dict):
                if validators.get("etag"):
                    result["If-None-Match"] = validators["etag"]
                if validators.get("last-modified"):
                    result["If-Modified-Since"] = validators["last-modified"]
            return result

    def response(self, url, limit):
        with self._lock:
            entry = self._entries.get(url)
            if not isinstance(entry, dict):
                return None
            try:
                body = base64.b64decode(entry["body"], validate=True)
                if len(body) > limit:
                    return None
                return body, dict(entry["headers"])
            except (KeyError, TypeError, ValueError, binascii.Error):
                return None

    def store(self, url, body, headers):
        headers = {str(key).lower(): value for key, value in headers.items()}
        validators = {key: value for key, value in
                      (("etag", headers.get("etag")),
                       ("last-modified", headers.get("last-modified")))
                      if isinstance(value, str) and value}
        if not validators or len(body) > _HTTP_ENTRY_LIMIT:
            with self._lock:
                if self._entries.pop(url, None) is not None:
                    self._save()
            return
        entry = {"headers": validators, "body": base64.b64encode(body).decode("ascii")}
        with self._lock:
            if self._entries.get(url) == entry:
                return
            self._entries.pop(url, None)
            self._entries[url] = entry
            while sum(len(value.get("body", "")) for value in self._entries.values()
                      if isinstance(value, dict)) > _HTTP_CACHE_LIMIT:
                self._entries.pop(next(iter(self._entries)))
            self._save()


class _CatalogueDiskCache:
    """Small, atomic JSON cache containing only provider catalogue metadata."""

    def __init__(self, path=None):
        self.path = Path(path).resolve() if path else None
        self._lock = threading.RLock()
        self._data = {
            "version": _CACHE_VERSION, "providers": {}, "eumetsat": [],
            "copernicus": {}, "noaa_checked_at": 0, "noaa_retry_after": 0,
            "catalogue_checks": {},
        }
        self._load()

    def _load(self):
        if self.path is None or not self.path.is_file():
            return
        try:
            if self.path.stat().st_size > 32 * 1024 * 1024:
                return
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (isinstance(value, dict) and value.get("version") == _CACHE_VERSION
                    and isinstance(value.get("providers"), dict)
                    and isinstance(value.get("eumetsat"), list)
                    and isinstance(value.get("copernicus", {}), dict)
                    and isinstance(value.get("catalogue_checks", {}), dict)):
                self._data = value
                self._data.setdefault("copernicus", {})
                self._data.setdefault("noaa_checked_at", 0)
                self._data.setdefault("noaa_retry_after", 0)
                self._data.setdefault("catalogue_checks", {})
        except (OSError, ValueError, TypeError):
            # A damaged optional cache must never prevent the application start.
            return

    def _save(self):
        if self.path is None:
            return
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(self._data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def areas(self, provider):
        with self._lock:
            value = self._data["providers"].get(provider, {}).get("areas")
            return copy.deepcopy(value) if isinstance(value, list) else None

    def products(self, provider, area_id):
        with self._lock:
            value = self._data["providers"].get(provider, {}).get("products", {}).get(str(area_id))
            return copy.deepcopy(value) if isinstance(value, list) else None

    def store_areas(self, provider, items):
        if not isinstance(items, list):
            return False
        with self._lock:
            entry = self._data["providers"].setdefault(provider, {"areas": [], "products": {}})
            if entry.get("areas") == items:
                return False
            entry["areas"] = copy.deepcopy(items)
            entry.setdefault("products", {})
            self._save()
            return True

    def store_products(self, provider, area_id, items):
        if not isinstance(items, list):
            return False
        with self._lock:
            entry = self._data["providers"].setdefault(provider, {"areas": [], "products": {}})
            entry.setdefault("areas", [])
            products = entry.setdefault("products", {})
            key = str(area_id)
            if products.get(key) == items:
                return False
            products[key] = copy.deepcopy(items)
            self._save()
            return True

    def store_provider(self, provider, areas, products):
        if not isinstance(areas, list) or not isinstance(products, dict):
            return False
        with self._lock:
            current = self._data["providers"].get(provider)
            if current and current.get("areas") == areas and current.get("products") == products:
                return False
            self._data["providers"][provider] = {
                "areas": copy.deepcopy(areas),
                "products": copy.deepcopy(products),
            }
            self._save()
            return True

    def _noaa_complete(self):
        for provider in NOAA_PROVIDERS:
            entry = self._data["providers"].get(provider, {})
            areas = entry.get("areas")
            products = entry.get("products")
            if not isinstance(areas, list) or not areas or not isinstance(products, dict):
                return False
            if any(not isinstance(area, dict) or not isinstance(area.get("id"), str)
                   or not isinstance(products.get(area["id"]), list)
                   or not products[area["id"]] for area in areas):
                return False
        return True

    def noaa_fresh(self):
        with self._lock:
            checked_at = self._data.get("noaa_checked_at", 0)
            age = time.time() - checked_at if isinstance(checked_at, (int, float)) else -1
            return 0 <= age < NOAA_CATALOGUE_TTL_SECONDS and self._noaa_complete()

    def noaa_retry_pending(self):
        with self._lock:
            retry_after = self._data.get("noaa_retry_after", 0)
            return (isinstance(retry_after, (int, float))
                    and time.time() < retry_after and self._noaa_complete())

    def mark_noaa_checked(self):
        with self._lock:
            if not self._noaa_complete():
                return False
            self._data["noaa_checked_at"] = time.time()
            self._data["noaa_retry_after"] = 0
            self._save()
            return True

    def mark_noaa_failed(self):
        with self._lock:
            if not self._noaa_complete():
                return False
            self._data["noaa_retry_after"] = time.time() + NOAA_FAILURE_BACKOFF_SECONDS
            self._save()
            return True

    def _source_complete(self, provider):
        if provider == "eumetsat":
            return isinstance(self._data.get("eumetsat"), list) and bool(self._data["eumetsat"])
        entry = self._data["providers"].get(provider, {})
        areas = entry.get("areas")
        products = entry.get("products")
        if not isinstance(areas, list) or not areas or not isinstance(products, dict):
            return False
        return all(isinstance(area, dict) and isinstance(area.get("id"), str)
                   and isinstance(products.get(area["id"]), list)
                   and bool(products[area["id"]]) for area in areas)

    def source_fresh(self, provider):
        with self._lock:
            state = self._data.get("catalogue_checks", {}).get(provider, {})
            checked_at = state.get("checked_at", 0) if isinstance(state, dict) else 0
            age = time.time() - checked_at if isinstance(checked_at, (int, float)) else -1
            return (0 <= age < STARTUP_CATALOGUE_TTL_SECONDS
                    and self._source_complete(provider))

    def source_retry_pending(self, provider):
        with self._lock:
            state = self._data.get("catalogue_checks", {}).get(provider, {})
            retry_after = state.get("retry_after", 0) if isinstance(state, dict) else 0
            return (isinstance(retry_after, (int, float)) and time.time() < retry_after
                    and self._source_complete(provider))

    def mark_source_checked(self, provider):
        with self._lock:
            if not self._source_complete(provider):
                return False
            self._data["catalogue_checks"][provider] = {
                "checked_at": time.time(), "retry_after": 0,
            }
            self._save()
            return True

    def mark_source_failed(self, provider):
        with self._lock:
            if not self._source_complete(provider):
                return False
            state = self._data["catalogue_checks"].setdefault(provider, {})
            state["retry_after"] = time.time() + NOAA_FAILURE_BACKOFF_SECONDS
            self._save()
            return True

    def eumetsat(self):
        with self._lock:
            value = self._data.get("eumetsat")
            return copy.deepcopy(value) if isinstance(value, list) and value else None

    def store_eumetsat(self, items):
        if not isinstance(items, list) or not items:
            return False
        with self._lock:
            if self._data["eumetsat"] == items:
                return False
            self._data["eumetsat"] = copy.deepcopy(items)
            self._save()
            return True

    @staticmethod
    def _copernicus_key(profile, output_size):
        fields = (
            "configuration", "mission", "product", "layer", "latitude",
            "longitude", "map_zoom", "max_cloud_cover",
        )
        value = {key: profile.get(key) for key in fields}
        value["output_size"] = [int(output_size[0]), int(output_size[1])]
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def copernicus_dates(self, profile, output_size):
        key = self._copernicus_key(profile, output_size)
        with self._lock:
            value = self._data["copernicus"].get(key, {}).get("dates")
            return copy.deepcopy(value) if isinstance(value, list) else None

    def store_copernicus_dates(self, profile, output_size, dates):
        if not isinstance(dates, list):
            return False
        key = self._copernicus_key(profile, output_size)
        with self._lock:
            value = {"dates": copy.deepcopy(dates)}
            if self._data["copernicus"].get(key) == value:
                return False
            self._data["copernicus"][key] = value
            self._save()
            return True

    def summary(self, providers):
        with self._lock:
            areas = products = resolutions = 0
            available = 0
            for provider in providers:
                entry = self._data["providers"].get(provider, {})
                cached_areas = entry.get("areas", [])
                cached_products = entry.get("products", {})
                if cached_areas or cached_products:
                    available += 1
                areas += len(cached_areas) if isinstance(cached_areas, list) else 0
                for values in cached_products.values() if isinstance(cached_products, dict) else ():
                    if isinstance(values, list):
                        products += len(values)
                        resolutions += sum(len(item.get("resolutions", ())) for item in values if isinstance(item, dict))
            return available, areas, products, resolutions


class CatalogueClient:
    """Route provider queries and coordinate a single global metadata refresh."""

    def __init__(self, timeout=90, user_agent="MarbleScape", noaa=None, himawari=None,
                 slider=None, worldview=None, eumetsat=None, cache_path=None, retries=2):
        self._cache = _CatalogueDiskCache(cache_path)
        http_cache_path = Path(cache_path).with_name("catalogue_http.json") if cache_path else None
        self._http_cache = _CatalogueHttpCache(http_cache_path)
        self.retries = max(1, min(9, int(retries)))
        self._fallback_warnings = {}
        self.noaa = noaa if noaa is not None else NOAAClient(timeout=timeout, user_agent=user_agent)
        self.himawari = himawari if himawari is not None else HimawariClient(
            timeout=timeout, user_agent=user_agent
        )
        self.slider = slider if slider is not None else SliderClient(
            timeout=timeout, user_agent=user_agent
        )
        self.worldview = worldview if worldview is not None else WorldviewClient(
            timeout=timeout, user_agent=user_agent
        )
        self.eumetsat = eumetsat if eumetsat is not None else EumetsatCatalogueClient(
            timeout=timeout, user_agent=user_agent,
            cached_catalogue=self._cache.eumetsat(),
            on_catalogue=self._cache.store_eumetsat,
            retries=self.retries,
        )
        for client in (self.noaa, self.himawari, self.slider, self.worldview,
                       self.eumetsat):
            if hasattr(client, "__dict__"):
                client.metadata_cache = self._http_cache
        self._lock = threading.RLock()
        self._noaa_retry_after = 0.0
        self._future = None
        self._status = {"running": False, "done": 0, "total": 0,
                        "message": "", "error": ""}

    def _client(self, provider):
        if provider in NOAA_PROVIDERS:
            return self.noaa
        if provider == "himawari":
            return self.himawari
        if provider == "slider":
            return self.slider
        if provider == "worldview":
            return self.worldview
        raise ValueError("Unknown catalogue provider: " + str(provider))

    def cached_areas(self, provider):
        return self._cache.areas(provider)

    def cached_products(self, provider, area_id):
        return self._cache.products(provider, area_id)

    def catalogue_cached_for_automatic_use(self, provider):
        return (self._cache.source_fresh(provider)
                or self._cache.source_retry_pending(provider))

    def catalogue_offline(self, provider):
        with self._lock:
            return provider in NOAA_PROVIDERS and time.monotonic() < self._noaa_retry_after

    def _noaa_result(self, provider, available):
        if provider in NOAA_PROVIDERS:
            with self._lock:
                self._noaa_retry_after = 0.0 if available else time.monotonic() + 120.0

    def list_areas(self, provider, refresh=False):
        if not refresh and provider in NOAA_PROVIDERS and (
                self._cache.noaa_fresh() or self._cache.noaa_retry_pending()):
            return self._cache.areas(provider)
        if not refresh and provider in STARTUP_CATALOGUE_PROVIDERS.values() \
                and self.catalogue_cached_for_automatic_use(provider):
            return self._cache.areas(provider)
        if not refresh and self.catalogue_offline(provider):
            cached = self._cache.areas(provider)
            if cached:
                return cached
        client = self._client(provider)
        last_value = None
        last_error = None
        for _attempt in range(self.retries + 1 if refresh else 1):
            try:
                last_value = client.list_areas(provider, refresh=refresh)
                warning = str(getattr(client, "catalogue_warning", "") or "").strip()
                if last_value and not warning:
                    self._noaa_result(provider, True)
                    self._fallback_warnings.pop(provider, None)
                    self._cache.store_areas(provider, last_value)
                    return last_value
                last_error = warning or "the provider returned no catalogue entries"
            except Exception as exc:
                last_error = str(exc)
        cached = self._cache.areas(provider)
        if cached:
            self._noaa_result(provider, False)
            self._fallback_warnings[provider] = (
                f"{last_error or 'Catalogue update failed'}; using cached catalogue data."
            )
            return cached
        if last_value:
            return last_value
        raise RuntimeError(last_error or "Catalogue data is unavailable.")

    def list_products(self, provider, area_id, refresh=False):
        if not refresh and provider in NOAA_PROVIDERS and (
                self._cache.noaa_fresh() or self._cache.noaa_retry_pending()):
            cached = self._cache.products(provider, area_id)
            if cached:
                return cached
        if not refresh and provider in STARTUP_CATALOGUE_PROVIDERS.values() \
                and self.catalogue_cached_for_automatic_use(provider):
            cached = self._cache.products(provider, area_id)
            if cached:
                return cached
        if not refresh and self.catalogue_offline(provider):
            cached = self._cache.products(provider, area_id)
            if cached:
                return cached
        client = self._client(provider)
        last_value = None
        last_error = None
        for _attempt in range(self.retries + 1 if refresh else 1):
            try:
                last_value = client.list_products(provider, area_id, refresh=refresh)
                warning = str(getattr(client, "catalogue_warning", "") or "").strip()
                if last_value and not warning:
                    self._noaa_result(provider, True)
                    self._fallback_warnings.pop(provider, None)
                    self._cache.store_products(provider, area_id, last_value)
                    return last_value
                last_error = warning or "the provider returned no catalogue entries"
            except Exception as exc:
                last_error = str(exc)
        cached = self._cache.products(provider, area_id)
        if cached:
            self._noaa_result(provider, False)
            self._fallback_warnings[provider] = (
                f"{last_error or 'Catalogue update failed'}; using cached catalogue data."
            )
            return cached
        if last_value:
            return last_value
        raise RuntimeError(last_error or "Catalogue data is unavailable.")

    @property
    def catalogue_warning(self):
        warnings = []
        for provider in CATALOGUE_PROVIDERS:
            value = self.catalogue_warning_for(provider)
            if value and value not in warnings:
                warnings.append(value)
        return " | ".join(warnings)

    def catalogue_warning_for(self, provider):
        """Return only the warning belonging to the selected image source."""
        values = (
            self._fallback_warnings.get(provider, ""),
            str(getattr(self._client(provider), "catalogue_warning", "") or "").strip(),
        )
        return " ".join(value for index, value in enumerate(values)
                        if value and value not in values[:index])

    def cached_copernicus_dates(self, profile, output_size):
        return self._cache.copernicus_dates(profile, output_size)

    def store_copernicus_dates(self, profile, output_size, dates):
        self._cache.store_copernicus_dates(profile, output_size, dates)

    def _cache_provider(self, provider):
        client = self._client(provider)
        areas = client.list_areas(provider, refresh=False)
        if not areas:
            return False
        products_by_area = {}
        for area in areas:
            area_id = area.get("id") if isinstance(area, dict) else None
            if area_id:
                products = client.list_products(provider, area_id, refresh=False)
                if products:
                    products_by_area[str(area_id)] = products
        return self._cache.store_provider(provider, areas, products_by_area)

    def _cached_source_summary(self, label):
        if label == "EUMETSAT":
            items = self._cache.eumetsat() or []
            return {
                "providers": 1 if items else 0,
                "areas": len({item.get("satellite") for item in items if isinstance(item, dict)}),
                "products": len(items), "resolution_options": 0,
            }
        providers = tuple(NOAA_PROVIDERS) if label == "NOAA" else {
            "Himawari": ("himawari",), "CIRA SLIDER": ("slider",),
            "NASA Worldview": ("worldview",),
        }[label]
        available, areas, products, resolutions = self._cache.summary(providers)
        return {"providers": available, "areas": areas, "products": products,
                "resolution_options": resolutions}

    @property
    def catalogue_refresh_status(self):
        with self._lock:
            return dict(self._status)

    def _progress(self, done, total, message, callback):
        with self._lock:
            self._status.update(done=done, total=total, message=message)
        if callback:
            try:
                callback(done, total, message)
            except Exception:
                pass

    def refresh_all_catalogues(self, refresh=True, progress=None, startup=False):
        with self._lock:
            future = self._future
            owner = future is None
            if owner:
                future = self._future = Future()
                self._status = {"running": True, "done": 0, "total": 5,
                                "message": "Loading NOAA catalogues...", "error": ""}
            status = dict(self._status)
        self._progress(status["done"], status["total"], status["message"], progress)
        if not owner:
            return copy.deepcopy(future.result())
        summaries = []
        errors = []
        notices = []
        updated_sources = 0
        try:
            sources = (
                    ("NOAA", self.noaa), ("Himawari", self.himawari),
                    ("CIRA SLIDER", self.slider),
                    ("NASA Worldview", self.worldview),
                    ("EUMETSAT", self.eumetsat),
            )
            for index, (label, client) in enumerate(sources, 1):
                self._progress(index - 1, len(sources), f"Loading {label} catalogues...", progress)
                cached_eumetsat = self._cache.eumetsat() if label == "EUMETSAT" else None
                cached_noaa = label == "NOAA" and startup and self._cache.noaa_fresh()
                deferred_noaa = (label == "NOAA" and startup and not cached_noaa
                                 and self._cache.noaa_retry_pending())
                source_key = STARTUP_CATALOGUE_PROVIDERS.get(label)
                cached_source = bool(source_key and startup and self._cache.source_fresh(source_key))
                deferred_source = bool(source_key and startup and not cached_source
                                       and self._cache.source_retry_pending(source_key))

                def source_progress(done, total, detail, source_index=index, source_label=label):
                    total = max(0, int(total))
                    done = max(0, int(done))
                    fraction = min(1.0, done / total) if total else 0.0
                    step = f" ({min(done, total)}/{total})" if total else ""
                    self._progress(source_index - 1 + fraction, len(sources),
                                   f"{source_label}: {detail}{step}", progress)

                deferred = deferred_noaa or deferred_source
                from_cache = cached_noaa or deferred_noaa or cached_source or deferred_source
                deferred_message = (label + " online check postponed after a recent failure; "
                                    "using cached catalogue data.")
                summary = ({**self._cached_source_summary(label),
                            "errors": [deferred_message] if deferred else [],
                            "warning": deferred_message if deferred else "",
                            "complete": not deferred, "updated": False}
                           if from_cache else None)
                last_problem = ""
                attempt_count = (
                    0 if from_cache else
                    1 if label == "EUMETSAT" and hasattr(client, "retries")
                    else self.retries + 1 if refresh else 1
                )
                for _attempt in range(attempt_count):
                    try:
                        if label == "EUMETSAT":
                            items = client.catalogue(refresh=refresh)
                            warning = str(getattr(client, "catalogue_warning", "") or "").strip()
                            summary = {
                                "providers": 1,
                                "areas": len({item["satellite"] for item in items}),
                                "products": len(items),
                                "resolution_options": 0,
                                "errors": [], "warning": warning,
                                "complete": not warning,
                            }
                        else:
                            method = client.refresh_all_catalogues
                            if "progress" in inspect.signature(method).parameters:
                                summary = method(refresh=refresh, progress=source_progress)
                            else:
                                summary = method(refresh=refresh)
                        if summary.get("complete"):
                            break
                        last_problem = str(summary.get("warning") or "catalogue update was incomplete")
                        if label == "NOAA":
                            break
                    except Exception as exc:
                        last_problem = str(exc)
                        summary = None
                        if label == "NOAA":
                            break
                if from_cache:
                    if label == "NOAA":
                        self._noaa_result("goes_east", not deferred)
                        providers = NOAA_PROVIDERS
                    else:
                        providers = (source_key,) if source_key != "eumetsat" else ()
                        if label == "EUMETSAT":
                            client.catalogue_warning = deferred_message if deferred else ""
                    for provider in providers:
                        if deferred:
                            self._fallback_warnings[provider] = deferred_message
                        else:
                            self._fallback_warnings.pop(provider, None)
                elif summary is None or not summary.get("complete"):
                    if label == "NOAA":
                        self._noaa_result("goes_east", False)
                        self._cache.mark_noaa_failed()
                        for provider in NOAA_PROVIDERS:
                            self._fallback_warnings[provider] = last_problem
                    elif source_key:
                        self._cache.mark_source_failed(source_key)
                    cached = self._cached_source_summary(label)
                    cached_available = bool(cached["providers"] or cached["products"])
                    detail = f"{label}: {last_problem or 'catalogue update failed'}"
                    if cached_available:
                        detail += "; using cached catalogue data."
                        cached.update(errors=[detail], warning=detail, complete=False)
                        summary = cached
                    elif summary is None:
                        summary = {**cached, "errors": [detail],
                                   "warning": detail, "complete": False}
                else:
                    if label == "NOAA":
                        self._noaa_result("goes_east", True)
                        for provider in NOAA_PROVIDERS:
                            self._fallback_warnings.pop(provider, None)
                    changed = False
                    try:
                        if label == "EUMETSAT":
                            changed = cached_eumetsat != items
                            self._cache.store_eumetsat(items)
                            if refresh:
                                self._cache.mark_source_checked("eumetsat")
                        else:
                            providers = tuple(NOAA_PROVIDERS) if label == "NOAA" else {
                                "Himawari": ("himawari",), "CIRA SLIDER": ("slider",),
                                "NASA Worldview": ("worldview",),
                            }[label]
                            for provider in providers:
                                changed = self._cache_provider(provider) or changed
                            if label == "NOAA" and refresh:
                                self._cache.mark_noaa_checked()
                            elif source_key and refresh:
                                self._cache.mark_source_checked(source_key)
                    except Exception:
                        pass
                    summary["updated"] = changed
                    updated_sources += int(changed)
                summaries.append(summary)
                source_errors = [str(value) for value in summary.get("errors", ())]
                errors.extend(source_errors)
                notice = str(summary.get("warning", "") or "").strip()
                if (notice and notice not in notices
                        and not any(error and error in notice for error in source_errors)):
                    notices.append(notice)
                message = (f"{label} catalogues loaded from cache." if from_cache and not deferred else
                           f"{label} catalogue check postponed; using cache." if deferred else
                           f"{label} catalogues loaded.")
                self._progress(index, len(sources), message, progress)
            warning = ""
            details = errors + [value for value in notices if value not in errors]
            if details:
                warning = "Catalogue refresh is incomplete. " + " | ".join(details[:6])
                if len(details) > 6:
                    warning += " | +%s further errors" % (len(details) - 6)
            result = {
                key: sum(int(value.get(key, 0)) for value in summaries)
                for key in ("providers", "areas", "products", "resolution_options")
            }
            result.update(errors=errors, warning=warning,
                          complete=not details and all(value.get("complete") for value in summaries),
                          updated_sources=updated_sources)
            if result["complete"]:
                message = ("Catalogue update completed." if updated_sources else
                           "Catalogue check completed; local catalogues are already current.")
            else:
                message = "Catalogue refresh completed with unavailable entries."
            with self._lock:
                self._status.update(running=False, done=len(sources), total=len(sources),
                                    message=message, error=warning)
            future.set_result(result)
            return copy.deepcopy(result)
        except BaseException as exc:
            with self._lock:
                self._status.update(running=False, message="Catalogue refresh failed.", error=str(exc))
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                if self._future is future:
                    self._future = None
