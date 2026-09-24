"""NOAA STAR still-image catalogues and downloads for MarbleScape.

The public HTML catalogues are the authority for regions and products. Image
directories are the authority for the newest available *requested* resolution.
No undocumented image URLs, satellite fallbacks, animations or API keys are used.
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

from PIL import Image, ImageColor, JpegImagePlugin

from marblescape_download_progress import DOWNLOAD_PROGRESS, ResponseTooLargeError, read_response

BASE_URL = "https://www.star.nesdis.noaa.gov/goes/"
CDN_HOST = "cdn.star.nesdis.noaa.gov"
ALLOWED_HOSTS = frozenset(("www.star.nesdis.noaa.gov", CDN_HOST))
PROVIDERS = ("goes_east", "goes_west", "solar")
MAX_HTML_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 192 * 1024 * 1024
MAX_SOURCE_PIXELS = 500_000_000  # NOAA's 21696 x 21696 full disk is supported.
MAX_DECODE_PIXELS = 64_000_000
MAX_OUTPUT_PIXELS = 33_554_432
PRODUCT_CATALOGUE_TTL = 24 * 60 * 60
_SIZE = re.compile(r"-(\d{2,5}x\d{2,5})\.(?:jpe?g|png)(?:\.zip)?$", re.I)
_DATE = re.compile(r"^(\d{11}|\d{13}|\d{14})_GOES(\d{2})-", re.I)


class NOAAError(RuntimeError):
    """A NOAA response cannot be safely or unambiguously used."""


class UnavailableError(NOAAError):
    """The selected source, region, product or size is not currently available."""


def _checked_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if (p.scheme != "https" or p.hostname not in ALLOWED_HOSTS or p.port not in (None, 443)
            or p.username is not None or p.password is not None):
        raise NOAAError("NOAA returned an unsupported image or catalogue host.")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Document(HTMLParser):
    """Collect actual HTML attributes, including WFO's hidden still-image inputs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.images = []
        self.headings = []
        self.product_labels = {}
        self._anchor = None
        self._heading = None
        self._current_heading = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("h1", "h2", "h3"):
            self._heading = [tag, attrs, []]
        if tag == "a":
            self._anchor = [attrs, []]
        for key in ("href", "src", "value", "data-src"):
            url = attrs.get(key, "")
            if url.startswith(("https://", "//")):
                self.images.append((url, self._current_heading))

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor[1].append(data)
        if self._heading is not None:
            self._heading[2].append(data)

    def handle_endtag(self, tag):
        if self._heading is not None and tag == self._heading[0]:
            name, attrs, parts = self._heading
            label = " ".join("".join(parts).split())
            self.headings.append((name, attrs, label))
            self._current_heading = label
            self._heading = None
        if tag == "a" and self._anchor is not None:
            attrs, parts = self._anchor
            label = " ".join("".join(parts).split())
            self.links.append((attrs.get("href", ""), label, attrs))
            if "channelSwitcher(" in attrs.get("onclick", "") and "_" in attrs.get("id", ""):
                self.product_labels[attrs["id"].split("_", 1)[1]] = label
            self._anchor = None


def _document(html: str) -> _Document:
    doc = _Document()
    doc.feed(html)
    doc.close()
    return doc


def _timestamp(url: str) -> tuple[str, str] | None:
    """ABI: YYYYDDDHHMM[SS]; SUVI adds tenths. All capture times are UTC."""
    name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    match = _DATE.match(name)
    if not match:
        return None
    raw, satellite = match.groups()
    try:
        year, ordinal = int(raw[:4]), int(raw[4:7])
        value = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(days=ordinal - 1)
        if ordinal < 1 or value.year != year:
            return None
        value = value.replace(hour=int(raw[7:9]), minute=int(raw[9:11]),
                              second=int(raw[11:13]) if len(raw) >= 13 else 0,
                              microsecond=int(raw[13]) * 100000 if len(raw) == 14 else 0)
    except (ValueError, OverflowError):
        return None
    return value.isoformat().replace("+00:00", "Z"), "G" + satellite


def _image_identity(url: str) -> dict | None:
    p = urllib.parse.urlsplit(url)
    if p.hostname != CDN_HOST or p.scheme != "https":
        return None
    size = _SIZE.search(p.path)
    timestamp = _timestamp(url)
    if not size or not timestamp:
        return None
    parts = p.path.strip("/").split("/")
    if len(parts) < 4:
        return None
    return {"url": url, "timestamp": timestamp[0], "satellite": timestamp[1],
            "product": parts[-2], "resolution": size.group(1).lower(),
            "directory": urllib.parse.urlunsplit((p.scheme, p.netloc, p.path.rsplit("/", 1)[0] + "/", "", ""))}


def _resolution_key(value):
    width, height = map(int, value.split("x"))
    return width * height, width, height


def _time_key(timestamp):
    # ISO strings with and without fractional seconds do not sort identically
    # to instants ("00Z" sorts after "00.1Z"). Compare aware UTC datetimes.
    return dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _area_from_link(href, label, attrs, satellite):
    url = urllib.parse.urljoin(BASE_URL, href)
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != "www.star.nesdis.noaa.gov":
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("sat", [None])[0] != satellite:
        return None
    page = parsed.path.rsplit("/", 1)[-1]
    label = re.sub(r"\s*-\s*all channels$", "", label, flags=re.I).strip()
    params = {"sat": satellite}
    if page == "fulldisk.php":
        area_id, category, label = "full_disk", "Full Disk", "Full Disk"
    elif page == "conus.php":
        area_id, category = "conus", "CONUS / PACUS"
        label = "PACUS" if "PACUS" in (label + attrs.get("title", "")) else "CONUS"
    elif page == "sector.php" and query.get("sector"):
        params["sector"] = query["sector"][0]
        area_id, category = "sector_" + params["sector"], "Regions"
    elif page == "meso.php" and query.get("lat") and query.get("lon"):
        params.update(lat=query["lat"][0], lon=query["lon"][0])
        slot = re.fullmatch(re.escape(satellite) + r"M([12])", attrs.get("id", ""))
        area_id = "meso_m" + slot[1] if slot else "meso_" + params["lat"] + "-" + params["lon"]
        category = "Mesoscale" if slot else "Mesoscale locations"
        if slot:
            label = "Meso M" + slot[1] + " (" + params["lat"] + " / " + params["lon"] + ")"
    else:
        return None
    return {"id": area_id, "label": label or area_id, "category": category,
            "url": BASE_URL + page + "?" + urllib.parse.urlencode(params), "satellite": satellite}


def _in_area(url: str, area: dict) -> bool:
    path = urllib.parse.urlsplit(url).path
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(area["url"]).query)
    area_id = area["id"]
    if area_id == "sun":
        return "/SUVI/FD/" in path
    if area_id == "full_disk":
        return bool(re.search(r"/(?:ABI|GLM)/FD/", path))
    if area_id == "conus":
        return "/CONUS/" in path
    if area_id.startswith("sector_"):
        return "/SECTOR/" + query["sector"][0] + "/" in path
    if area_id.startswith("meso_"):
        return "/MESO/" + query["lat"][0] + "-" + query["lon"][0] + "/" in path
    if area_id.startswith("wfo_"):
        return path.startswith("/WFO/" + area_id[4:] + "/")
    if area_id.startswith("storm_"):
        return path.startswith("/FLOATER/" + area_id[6:] + "/")
    return False


def _parse_products(html: str, area: dict, satellite: str | None = None) -> list[dict]:
    doc = _document(html)
    products = {}
    for url, heading in doc.images:
        url = urllib.parse.urljoin(BASE_URL, url)
        item = _image_identity(url)
        if not item or not _in_area(url, area) or (satellite and item["satellite"] != satellite):
            continue
        key = item["product"]
        label = doc.product_labels.get(key) or heading or key
        # Global introductory headings must never become a product label.
        if len(label) > 65 or any(word in label for word in ("Latest", "Weather Forecast", "Javascript")):
            label = key
        product = products.setdefault(key, {"id": key, "label": label, "resolutions": [], "images": {}})
        if product["label"] == key and label != key:
            product["label"] = label
        old = product["images"].get(item["resolution"])
        if old is None or _time_key(item["timestamp"]) > _time_key(old["timestamp"]):
            product["images"][item["resolution"]] = item
    for product in products.values():
        product["resolutions"] = sorted(product["images"], key=_resolution_key)
    return list(products.values())


class NOAAClient:
    """Thread-safe, bounded HTTP client for GOES-East, GOES-West and SUVI.

    Catalogues are cached in memory. A region refresh discovers changed active
    mesoscale slots/storms; WFO ownership is verified from actual image names.
    Public methods return independent dictionaries safe for UI worker threads.
    """

    def __init__(self, timeout=90, user_agent="MarbleScape/NOAA"):
        self.timeout = max(1, min(float(timeout), 180))
        self.user_agent = str(user_agent)
        self._lock = threading.RLock()
        self._discovery_lock = threading.RLock()
        self._satellites = {}
        self._base_areas = {}
        self._base_time = 0.0
        self._areas = {}
        self._areas_time = 0.0
        self._products = {}
        self._product_pages = {}
        self._classified = {}
        self._directories = {}
        self._inflight = {}
        self._refresh_future = None
        self._refresh_listeners = []
        self._refresh_status = {"running": False, "done": 0, "total": 0, "message": "", "error": ""}
        self._catalogue_errors = []
        self.catalogue_warning = ""

    def _coordinated(self, key, operation):
        """Share one in-flight operation; network work never holds the cache lock."""
        with self._lock:
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = self._inflight[key] = Future()
        if not owner:
            return future.result()
        try:
            result = operation()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(result)
            return result
        finally:
            with self._lock:
                if self._inflight.get(key) is future:
                    del self._inflight[key]

    def _request(self, url, limit, headers=None, track=False):
        if track:
            DOWNLOAD_PROGRESS.raise_if_cancelled()
        _checked_url(url)
        request_headers = {"User-Agent": self.user_agent, "Cache-Control": "no-cache"}
        cache = getattr(self, "metadata_cache", None) if not track else None
        if cache:
            request_headers.update(cache.headers(url))
        request_headers.update(headers or {})
        request = urllib.request.Request(url, headers=request_headers)
        try:
            # Each request owns its opener: independent WFO requests can run concurrently.
            # Catalogue pages are small, but the STAR front end can respond
            # slowly during maintenance while its CDN remains available.
            # Keep image transfers on the configured timeout and give metadata
            # enough time to survive a temporarily overloaded front end.
            timeout = self.timeout if track else min(self.timeout, 45.0)
            with urllib.request.build_opener(_Redirects()).open(request, timeout=timeout) as response:
                _checked_url(response.geturl())
                try:
                    body = read_response(response, limit, track=track)
                except ResponseTooLargeError:
                    raise NOAAError("NOAA response exceeds the permitted download size.")
                response_headers = dict(response.headers.items())
                if cache:
                    cache.store(url, body, response_headers)
                return body, response_headers
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                if cache and (saved := cache.response(url, limit)) is not None:
                    exc.close()
                    return saved
                response_headers = dict(exc.headers.items())
                exc.close()
                return None, response_headers
            raise UnavailableError("NOAA request failed (HTTP %s): %s" % (exc.code, url)) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise UnavailableError("NOAA is currently unreachable: %s" % exc) from exc

    def _html(self, url):
        def load():
            body, _ = self._request(url, MAX_HTML_BYTES)
            return body.decode("utf-8", errors="replace")
        return self._coordinated(("html", url), load)

    def _ensure_base(self, refresh=False):
        with self._lock:
            if self._base_areas and not refresh and time.monotonic() - self._base_time < 300:
                return
        return self._coordinated(("base",), lambda: self._ensure_base_impl(refresh))

    def _ensure_base_impl(self, refresh=False):
        with self._discovery_lock:
            if self._base_areas and not refresh and time.monotonic() - self._base_time < 300:
                return
            doc = _document(self._html(BASE_URL + "index.php"))
            satellites = {}
            for _, attrs, label in doc.headings:
                side = "goes_east" if "GOES-East" in label else "goes_west" if "GOES-West" in label else None
                match = re.search(r"GOES[- ]?(\d{2})", attrs.get("title", "") + " " + label)
                if side and match:
                    satellites[side] = "G" + match[1]
            if set(satellites) != {"goes_east", "goes_west"}:
                raise NOAAError("The NOAA catalogue no longer identifies both GOES satellites.")
            areas = {provider: {} for provider in satellites}
            for provider, satellite in satellites.items():
                for href, label, attrs in doc.links:
                    area = _area_from_link(href, label, attrs, satellite)
                    if area:
                        previous = areas[provider].get(area["id"])
                        if previous is None or "list-group-item" in attrs.get("class", ""):
                            areas[provider][area["id"]] = area
                if "full_disk" not in areas[provider]:
                    raise NOAAError("The NOAA catalogue has no full disk for " + provider)
            with self._lock:
                if self._satellites and satellites != self._satellites:
                    self._products.clear()
                    self._product_pages.clear()
                    self._classified.clear()
                    self._directories.clear()
                    self._areas.clear()
                for side, current in areas.items():
                    previous = self._base_areas.get(side, {})
                    for area_id, old_area in previous.items():
                        new_area = current.get(area_id)
                        if new_area is None or old_area["url"] != new_area["url"]:
                            self._products.pop((side, area_id), None)
                self._satellites = satellites
                self._base_areas = areas
                self._base_time = time.monotonic()

    def _classify_area(self, area, refresh=False):
        return self._coordinated(("classify", area["url"]), lambda: self._classify_area_impl(area, refresh))

    def _classify_area_impl(self, area, refresh=False):
        with self._lock:
            cached = self._classified.get(area["url"])
        if cached and not refresh and time.monotonic() - cached[0] < 86400:
            return cached[1]
        html = self._html(area["url"])
        satellites = {item["satellite"] for url, _ in _document(html).images
                      if (item := _image_identity(urllib.parse.urljoin(BASE_URL, url))) and _in_area(url, area)}
        if not satellites:
            raise UnavailableError("NOAA has no timestamped still images for " + area["label"])
        with self._lock:
            provider_satellites = dict(self._satellites)
        parsed = {satellite: _parse_products(html, area, satellite) for satellite in satellites}
        with self._lock:
            loaded = time.monotonic()
            self._classified[area["url"]] = (loaded, satellites)
            for satellite, products in parsed.items():
                self._product_pages[(area["url"], satellite)] = (loaded, products)
            for provider, satellite in provider_satellites.items():
                if satellite in satellites:
                    self._products[(provider, area["id"])] = (loaded, parsed[satellite], area["url"], satellite)
        return satellites

    def list_areas(self, provider, refresh=False):
        self._validate_provider(provider)
        if provider == "solar":
            self._ensure_base(refresh=refresh)
            return [self._solar_area()]
        def load():
            self._list_areas_impl("goes_east", refresh=refresh)
            with self._lock:
                return copy.deepcopy(self._areas)
        catalogues = self._coordinated(("areas",), load)
        return copy.deepcopy(catalogues[provider])

    def _list_areas_impl(self, provider, refresh=False):
        self._validate_provider(provider)
        warnings = []
        try:
            self._ensure_base(refresh=refresh)
        except NOAAError as exc:
            if not self._base_areas:
                raise
            warnings.append("Using the last known GOES region catalogue: " + str(exc))
            if self._metadata_unreachable(exc):
                # One outage must not trigger hundreds of WFO/product timeouts.
                with self._lock:
                    if not self._areas:
                        self._areas = {
                            side: sorted(copy.deepcopy(list(values.values())),
                                         key=lambda area: area["label"].casefold())
                            for side, values in self._base_areas.items()
                        }
                    self._areas_time = time.monotonic()
                    self._catalogue_errors = list(warnings)
                    self.catalogue_warning = "NOAA catalogue is incomplete or partly cached. " + warnings[0]
                    return copy.deepcopy(self._areas[provider])
        if provider == "solar":
            return [self._solar_area()]
        with self._discovery_lock:
            if self._areas and not refresh and time.monotonic() - self._areas_time < 300:
                return copy.deepcopy(self._areas[provider])
            pages = ("meso.php", "wfo_index.php", "floater_index.php")
            def load_page(page):
                try:
                    return _document(self._html(BASE_URL + page)), None
                except NOAAError as exc:
                    return None, str(exc)
            with ThreadPoolExecutor(max_workers=3) as pool:
                loaded_pages = list(pool.map(load_page, pages))
            documents = []
            missing_categories = set()
            for page, (doc, error) in zip(pages, loaded_pages):
                documents.append(doc or _Document())
                if error:
                    warnings.append(page + ": " + error)
                    missing_categories.update({"meso.php": {"Mesoscale locations"},
                                               "wfo_index.php": {"Weather Forecast Offices"},
                                               "floater_index.php": {"Active storms"}}[page])
            with self._lock:
                catalog = copy.deepcopy(self._base_areas)
                provider_satellites = dict(self._satellites)
                if missing_categories:
                    for side, values in self._areas.items():
                        for area in values:
                            if area["category"] in missing_categories and area.get("satellite") == self._satellites[side]:
                                catalog[side][area["id"]] = copy.deepcopy(area)
            for side, satellite in provider_satellites.items():
                for href, label, attrs in documents[0].links:
                    area = _area_from_link(href, label, attrs, satellite)
                    if area and area["id"].startswith("meso_"):
                        catalog[side][area["id"]] = area
            additional = {}
            for doc, page, param, prefix, category in (
                    (documents[1], "wfo.php", "wfo", "wfo_", "Weather Forecast Offices"),
                    (documents[2], "floater.php", "stormid", "storm_", "Active storms")):
                for href, label, attrs in doc.links:
                    p = urllib.parse.urlsplit(urllib.parse.urljoin(BASE_URL, href))
                    query = urllib.parse.parse_qs(p.query)
                    if (p.hostname != "www.star.nesdis.noaa.gov" or not p.path.endswith("/" + page)
                            or not query.get(param)):
                        continue
                    value = query[param][0]
                    if not re.fullmatch(r"[A-Za-z0-9]+", value):
                        continue
                    key = prefix + value
                    if label and label != "All storm views":
                        additional[key] = {"id": key, "label": label, "category": category,
                                           "url": BASE_URL + page + "?" + urllib.parse.urlencode({param: value})}
            # WFO pages have no satellite selector. Verify their ownership instead
            # of silently showing GOES-West imagery under GOES-East (or vice versa).
            offline = threading.Event()
            failure_lock = threading.Lock()
            failures = 0

            def classify(area):
                nonlocal failures
                if offline.is_set():
                    with self._lock:
                        cached = self._classified.get(area["url"])
                    return area, cached[1] if cached else set(), None, True
                try:
                    return area, self._classify_area(area, refresh=refresh), None, False
                except NOAAError as exc:
                    if self._metadata_unreachable(exc):
                        with failure_lock:
                            failures += 1
                            if failures >= 3:
                                offline.set()
                    with self._lock:
                        cached = self._classified.get(area["url"])
                    return area, cached[1] if cached else set(), str(exc), False
            skipped = 0
            with ThreadPoolExecutor(max_workers=6) as pool:
                tasks = [pool.submit(classify, area) for area in additional.values()]
                for done, task in enumerate(as_completed(tasks), 1):
                    area, satellites, error, was_skipped = task.result()
                    skipped += was_skipped
                    if error:
                        warnings.append(area["label"] + ": " + error)
                    for side, satellite in provider_satellites.items():
                        if satellite in satellites:
                            catalog[side][area["id"]] = dict(area, satellite=satellite)
                    if self._refresh_future is not None:
                        self._catalogue_progress(
                            0, 0, f"Discovering NOAA areas ({done}/{len(tasks)})..."
                        )
            if skipped:
                warnings.append(f"{skipped} NOAA areas skipped after repeated network failures.")
            order = {"Full Disk": 0, "CONUS / PACUS": 1, "Regions": 2, "Mesoscale": 3,
                     "Mesoscale locations": 4, "Weather Forecast Offices": 5, "Active storms": 6}
            result = {side: sorted(values.values(), key=lambda area: (order.get(area["category"], 99), area["label"].casefold()))
                      for side, values in catalog.items()}
            with self._lock:
                self._areas, self._areas_time = result, time.monotonic() - (270 if warnings else 0)
                self.catalogue_warning = ("NOAA catalogue is incomplete or partly cached. " + " | ".join(warnings[:5])
                                          + (" | +%s further unavailable areas" % (len(warnings) - 5) if len(warnings) > 5 else "")) if warnings else ""
                self._catalogue_errors = list(warnings)
            return copy.deepcopy(result[provider])

    def _solar_area(self):
        with self._lock:
            satellite = self._satellites["goes_east"]
        return {"id": "sun", "label": "Sun / SUVI", "category": "Solar", "satellite": satellite,
                "url": BASE_URL + "SUVI.php?sat=" + satellite}

    @staticmethod
    def _validate_provider(provider):
        if provider not in PROVIDERS:
            raise NOAAError("Unknown NOAA provider: " + str(provider))

    def _area(self, provider, area_id, refresh=False):
        self._validate_provider(provider)
        self._ensure_base(refresh=refresh)
        if provider == "solar":
            if area_id != "sun":
                raise UnavailableError("Solar/Sun only supports the Sun / SUVI area.")
            return self._solar_area()
        with self._lock:
            area = self._base_areas[provider].get(area_id)
            if area:
                return copy.deepcopy(area)
            known = self._areas.get(provider, [])
            if not refresh:
                area = next((value for value in known if value["id"] == area_id), None)
                if area and time.monotonic() - self._areas_time < 300:
                    return copy.deepcopy(area)
        area = next((value for value in self.list_areas(provider, refresh=refresh) if value["id"] == area_id), None)
        if area is None:
            raise UnavailableError("The selected NOAA area is no longer available: " + str(area_id))
        return area

    def _products_for_area(self, provider, area, after=None):
        """Use a stable area snapshot, even while another thread refreshes slots."""
        page_key = (area["url"], area["satellite"])
        def load():
            with self._lock:
                cached = self._product_pages.get(page_key)
            if (cached and time.monotonic() - cached[0] < PRODUCT_CATALOGUE_TTL
                    and (after is None or cached[0] >= after)):
                return cached
            products = _parse_products(self._html(area["url"]), area, area["satellite"])
            if not products:
                raise UnavailableError("NOAA currently offers no supported still images for " + area["label"])
            cached = (time.monotonic(), products)
            with self._lock:
                self._product_pages[page_key] = cached
            return cached
        cached = self._coordinated(("products",) + page_key, load)
        with self._lock:
            self._products[(provider, area["id"])] = (cached[0], cached[1], area["url"], area["satellite"])
        return cached[1]

    @staticmethod
    def _metadata_unreachable(error):
        message = str(error)
        return isinstance(error, UnavailableError) and (
            "currently unreachable" in message
            or bool(re.search(r"HTTP (?:429|5\d\d)", message))
        )

    def list_products(self, provider, area_id, refresh=False):
        after = time.monotonic() if refresh else None
        # A product refresh must not repeat the entire area/WFO discovery.
        # The caller refreshes areas first when that is required.
        area = self._area(provider, area_id)
        products = self._products_for_area(provider, area, after=after)
        return [{"id": value["id"], "label": value["label"], "resolutions": list(value["resolutions"])}
                for value in products]

    def _directory_images(self, directory):
        return self._coordinated(("directory", directory), lambda: self._directory_images_impl(directory))

    def _directory_images_impl(self, directory):
        with self._lock:
            cached = self._directories.get(directory)
        # Revalidate on every requested update, including a manual refresh just
        # after the previous one. Only an HTTP 304 may reuse the parsed listing.
        # No image itself uses a mutable alias.
        headers = {}
        if cached:
            if cached[2].get("etag"):
                headers["If-None-Match"] = cached[2]["etag"]
            if cached[2].get("last-modified"):
                headers["If-Modified-Since"] = cached[2]["last-modified"]
        body, response_headers = self._request(directory, MAX_HTML_BYTES, headers)
        if body is None:
            if not cached:
                raise NOAAError("NOAA returned an unexpected unchanged catalogue.")
            result = cached[1]
            validators = cached[2]
        else:
            doc = _document(body.decode("utf-8", errors="replace"))
            result = {}
            for href, _, _ in doc.links:
                item = _image_identity(urllib.parse.urljoin(directory, href))
                if not item or item["directory"] != directory:
                    continue
                key = (item["satellite"], item["product"], item["resolution"])
                if key not in result or _time_key(item["timestamp"]) > _time_key(result[key]["timestamp"]):
                    result[key] = item
            validators = {key.lower(): value for key, value in response_headers.items()}
        with self._lock:
            self._directories[directory] = (time.monotonic(), result, validators)
        return result

    def latest(self, provider, area_id, product_id, resolution):
        area = self._area(provider, area_id)
        products = self._products_for_area(provider, area)
        product = next((value for value in products if value["id"] == product_id), None)
        if product is None:
            raise UnavailableError("The selected NOAA product is not available: " + str(product_id))
        if resolution == "largest":
            resolution = max(product["resolutions"], key=_resolution_key)
        if resolution not in product["resolutions"]:
            raise UnavailableError("The selected NOAA resolution is not available: " + str(resolution))
        prototype = product["images"][resolution]
        candidates = self._directory_images(prototype["directory"])
        frame = candidates.get((area["satellite"], product_id, resolution))
        if frame is None:
            raise UnavailableError("NOAA has no timestamped image for the selected product and resolution.")
        if not _in_area(frame["url"], area):
            raise NOAAError("NOAA returned an image for a different region.")
        interval = 60 if area_id.startswith("meso_") else 300 if area_id == "conus" else 600
        if provider == "solar":
            interval = 240
        elif "/GLM/" in frame["url"] or product_id.startswith(("EXTENT", "FED")):
            interval = 300
        elif product_id == "DMW":
            interval = 3600
        return dict(frame, source=provider, area=area_id, area_label=area["label"],
                    product_label=product["label"], expected_interval_seconds=interval)

    @property
    def catalogue_refresh_status(self):
        """A thread-safe progress snapshot suitable for GUI polling."""
        with self._lock:
            return dict(self._refresh_status)

    @staticmethod
    def _call_progress(callback, done, total, message):
        if callback is not None:
            try:
                callback(done, total, message)
            except Exception:
                # A closed UI must not cancel the shared runtime catalogue job.
                pass

    def _catalogue_progress(self, done, total, message):
        with self._lock:
            self._refresh_status.update(done=done, total=total, message=message)
            listeners = tuple(self._refresh_listeners)
        for listener in listeners:
            self._call_progress(listener, done, total, message)

    def refresh_all_catalogues(self, refresh=True, progress=None):
        """Warm all three providers' region/product metadata, never image data.

        Call this blocking method from a background worker. Concurrent callers
        join one running refresh. Progress callbacks run on worker threads and
        must marshal GUI updates themselves; ``catalogue_refresh_status`` can
        instead be polled from the GUI. A forced refresh rereads metadata; a
        startup refresh with ``refresh=False`` reuses valid cached catalogues.

        Return keys: providers, areas, products, resolution_options (counts),
        errors (list of strings), warning (string), complete (boolean).
        """
        with self._lock:
            future = self._refresh_future
            owner = future is None
            if owner:
                future = self._refresh_future = Future()
                self._refresh_listeners = []
                self._refresh_status = {"running": True, "done": 0, "total": 0,
                                        "message": "Discovering NOAA areas...", "error": ""}
            if progress is not None:
                self._refresh_listeners.append(progress)
            status = dict(self._refresh_status)
        self._call_progress(progress, status["done"], status["total"], status["message"])
        if not owner:
            return copy.deepcopy(future.result())
        try:
            result = self._refresh_all_catalogues_impl(bool(refresh))
        except BaseException as exc:
            with self._lock:
                self._refresh_status.update(running=False, error=str(exc), message="NOAA catalogue refresh failed.")
            future.set_exception(exc)
            raise
        else:
            with self._lock:
                self._refresh_status.update(running=False, error=result["warning"])
            future.set_result(result)
            return copy.deepcopy(result)
        finally:
            with self._lock:
                if self._refresh_future is future:
                    self._refresh_future = None
                    self._refresh_listeners = []

    def _refresh_all_catalogues_impl(self, refresh):
        started = time.monotonic()
        pairs = []
        errors = []
        providers = 0
        for index, provider in enumerate(PROVIDERS):
            try:
                # The first GOES discovery loads both hemispheres and all WFOs.
                # Forcing each provider separately would repeat the same work.
                areas = self.list_areas(provider, refresh=refresh and index == 0)
                pairs.extend((provider, area) for area in areas)
                providers += 1
            except NOAAError as exc:
                errors.append(str(exc))
                # Every NOAA provider needs the same base index. When no base
                # catalogue exists, repeating the identical request for West
                # and Solar only adds two more timeouts and duplicate errors.
                with self._lock:
                    base_available = bool(self._base_areas)
                if not base_available:
                    break
        with self._lock:
            errors.extend(self._catalogue_errors)
        total = len(pairs)
        self._catalogue_progress(0, total, "Loading NOAA product catalogues...")
        products_count = resolution_count = 0
        offline = threading.Event()
        failure_lock = threading.Lock()
        failures = 0
        if any("Using the last known GOES region catalogue" in error for error in errors):
            offline.set()
        def load(pair):
            nonlocal failures
            if offline.is_set():
                return 0, 0, None, True
            provider, area = pair
            try:
                products = self._products_for_area(provider, area, after=started if refresh else None)
                return len(products), sum(len(product["resolutions"]) for product in products), None, False
            except NOAAError as exc:
                if self._metadata_unreachable(exc):
                    with failure_lock:
                        failures += 1
                        if failures >= 3:
                            offline.set()
                return 0, 0, provider + " / " + area["label"] + ": " + str(exc), False
        skipped = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            tasks = {pool.submit(load, pair): pair for pair in pairs}
            for done, task in enumerate(as_completed(tasks), 1):
                count, sizes, error, was_skipped = task.result()
                products_count += count
                resolution_count += sizes
                skipped += was_skipped
                if error:
                    errors.append(error)
                provider, area = tasks[task]
                self._catalogue_progress(done, total, provider + " / " + area["label"])
        if skipped:
            errors.append(f"{skipped} NOAA product catalogues skipped after repeated network failures.")
        errors = list(dict.fromkeys(errors))
        warning = ""
        if errors:
            warning = "NOAA catalogue refresh is incomplete. " + " | ".join(errors[:5])
            if len(errors) > 5:
                warning += " | +%s further errors" % (len(errors) - 5)
        with self._lock:
            self.catalogue_warning = warning
        self._catalogue_progress(total, total, "NOAA catalogue refresh incomplete." if errors else "All NOAA catalogues are ready.")
        return {"providers": providers, "areas": total, "products": products_count,
                "resolution_options": resolution_count, "errors": errors, "warning": warning,
                "complete": not errors and providers == len(PROVIDERS)}

    def fetch_image(self, frame, output_size, fit_mode="fit", zoom=1.0, background="#000000"):
        """Download a timestamped still, safely decode, and return an RGB PNG."""
        url = frame.get("url", "")
        identity = _image_identity(url)
        if identity is None:
            raise NOAAError("Only timestamped NOAA JPEG and PNG still images are supported.")
        for key in ("satellite", "product", "resolution", "timestamp"):
            if frame.get(key) != identity[key]:
                raise NOAAError("NOAA frame identity does not match its image URL.")
        try:
            width, height = (int(value) for value in output_size)
            zoom = float(zoom)
            color = ImageColor.getrgb(background)
        except (TypeError, ValueError) as exc:
            raise NOAAError("Invalid output size, zoom or background color.") from exc
        if width <= 0 or height <= 0 or width * height > MAX_OUTPUT_PIXELS or max(width, height) > 32768:
            raise NOAAError("The requested desktop image is too large or has invalid dimensions.")
        if fit_mode not in ("fit", "crop") or not math.isfinite(zoom) or not 0.05 <= zoom <= 20:
            raise NOAAError("The image fit mode or zoom is invalid.")
        body, _ = self._request(url, MAX_IMAGE_BYTES, track=True)
        if url.lower().endswith(".zip"):
            body = _unpack_still(body, url.rsplit("/", 1)[-1][:-4])
        return _render_still(body, (width, height), identity["resolution"], fit_mode, zoom, color)


def _unpack_still(body: bytes, expected_name: str) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) != 1:
                raise NOAAError("NOAA image ZIP must contain exactly one still image.")
            entry = entries[0]
            if (entry.filename != expected_name or entry.is_dir() or entry.flag_bits & 1
                    or entry.file_size > MAX_IMAGE_BYTES or entry.file_size <= 0):
                raise NOAAError("NOAA image ZIP contains an unexpected or oversized file.")
            with archive.open(entry) as stream:
                result = stream.read(MAX_IMAGE_BYTES + 1)
                if len(result) > MAX_IMAGE_BYTES:
                    raise NOAAError("NOAA image ZIP exceeds the permitted image size.")
                return result
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        if isinstance(exc, NOAAError):
            raise
        raise NOAAError("NOAA returned an invalid image ZIP.") from exc


def _render_still(body, output_size, resolution, fit_mode, zoom, color):
    try:
        stream = io.BytesIO(body)
        # Calling the JPEG driver directly permits our *own* explicit 500 MP
        # header bound, followed by libjpeg's reduced decoding. Pillow's global
        # decompression-bomb limit stays intact for all other application code.
        if body.startswith(b"\xff\xd8\xff"):
            source = JpegImagePlugin.JpegImageFile(stream)
        elif body.startswith(b"\x89PNG\r\n\x1a\n"):
            source = Image.open(stream, formats=("PNG",))
        else:
            raise NOAAError("NOAA returned an unsupported image; GIFs and videos are excluded.")
        with source:
            if getattr(source, "is_animated", False) or getattr(source, "n_frames", 1) != 1:
                raise NOAAError("Animated NOAA images are not supported.")
            expected_size = tuple(map(int, resolution.split("x")))
            if source.size != expected_size:
                raise NOAAError("NOAA image dimensions differ from the selected source resolution.")
            if source.width * source.height > MAX_SOURCE_PIXELS or max(source.size) > 25000:
                raise NOAAError("NOAA source image exceeds the supported pixel limit.")
            if source.format == "JPEG":
                target = tuple(max(1, min(4000, int(value * max(1, zoom)))) for value in output_size)
                source.draft("RGB", target)
            if source.width * source.height > MAX_DECODE_PIXELS:
                raise NOAAError("NOAA image cannot be decoded within the supported memory limit.")
            source.load()
            rgb = source.convert("RGB")
        with rgb:
            out_width, out_height = output_size
            scale_fn = min if fit_mode == "fit" else max
            scale = scale_fn(out_width / rgb.width, out_height / rgb.height) * zoom
            draw_width, draw_height = rgb.width * scale, rgb.height * scale
            offset_x, offset_y = (out_width - draw_width) / 2, (out_height - draw_height) / 2
            left, top = max(0, round(offset_x)), max(0, round(offset_y))
            right, bottom = min(out_width, round(offset_x + draw_width)), min(out_height, round(offset_y + draw_height))
            box = (max(0, (left - offset_x) / scale), max(0, (top - offset_y) / scale),
                   min(rgb.width, (right - offset_x) / scale), min(rgb.height, (bottom - offset_y) / scale))
            with Image.new("RGB", output_size, color[:3]) as canvas:
                if right > left and bottom > top:
                    with rgb.resize((right - left, bottom - top), Image.Resampling.LANCZOS, box=box) as resized:
                        canvas.paste(resized, (left, top))
                output = io.BytesIO()
                canvas.save(output, "PNG")
                return output.getvalue()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise NOAAError("NOAA returned a damaged or unsupported still image: %s" % exc) from exc
