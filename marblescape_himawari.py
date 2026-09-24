"""Himawari still-image catalogues and rendering for MarbleScape.

NICT supplies tiled full-disk/Japan imagery and all 16 AHI bands. JMA
supplies its published regional still images. Only the newest timestamped PNG
or JPEG image is accepted; animations and movies are deliberately excluded.
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from html.parser import HTMLParser

from PIL import Image, ImageColor

from marblescape_download_progress import DOWNLOAD_PROGRESS, ResponseTooLargeError, read_response


PROVIDER = "himawari"
NICT_SITE_URL = "https://himawari8.nict.go.jp/"
NICT_IMAGE_BASE = "https://jh190005-4.kudpc.kyoto-u.ac.jp/himawari/"
JMA_BASE = "https://ds.data.jma.go.jp/mscweb/data/himawari/"
ALLOWED_HOSTS = frozenset((
    "himawari8.nict.go.jp",
    "himawari.asia",
    "jh190005-4.kudpc.kyoto-u.ac.jp",
    "www.data.jma.go.jp",
    "ds.data.jma.go.jp",
))
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_PIXELS = 33_554_432
MAX_TILES = 400
CATALOGUE_TTL = 24 * 60 * 60


class HimawariError(RuntimeError):
    """A Himawari response cannot be safely or unambiguously used."""


class UnavailableError(HimawariError):
    """The selected Himawari image is not currently available."""


def _checked_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.port not in (None, 443) or parsed.username is not None
            or parsed.password is not None):
        raise HimawariError("Himawari returned an unsupported image or catalogue host.")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _SelectDocument(HTMLParser):
    """Collect option values and labels from named HTML select controls."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.selects = {}
        self._select = None
        self._option = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self._select = attrs.get("name")
            if self._select:
                self.selects.setdefault(self._select, [])
        elif tag == "option" and self._select:
            self._option = [attrs.get("value", ""), []]

    def handle_data(self, data):
        if self._option is not None:
            self._option[1].append(data)

    def handle_endtag(self, tag):
        if tag == "option" and self._option is not None and self._select:
            value, parts = self._option
            label = " ".join("".join(parts).split())
            self.selects[self._select].append((str(value).strip(), label))
            self._option = None
        elif tag == "select":
            self._select = None
            self._option = None


def _selects(html: str) -> dict[str, list[tuple[str, str]]]:
    parser = _SelectDocument()
    parser.feed(html)
    parser.close()
    return parser.selects


def _resolution_key(value):
    width, height = map(int, value.split("x"))
    return width * height, width, height


_NICT_AREAS = (
    {"id": "nict_full_disk", "label": "NICT - Full Disk (True Color)",
     "category": "NICT True Color", "kind": "nict", "dataset": "D531106",
     "tile_size": (550, 550), "counts": (1, 2, 4, 8, 16, 20), "interval": 600},
    {"id": "nict_japan", "label": "NICT - Japan (True Color)",
     "category": "NICT True Color", "kind": "nict", "dataset": "D531107",
     "tile_size": (600, 480), "counts": (1, 2, 4, 5), "interval": 150},
    {"id": "nict_full_disk_bands", "label": "NICT - Full Disk (AHI bands)",
     "category": "NICT Spectral Bands", "kind": "nict", "dataset": "FULL_24h",
     "tile_size": (550, 550), "counts": (1, 2, 4, 8, 10), "interval": 600},
)

_BAND_LABELS = {
    "B01": "Band 01 - Blue visible (0.47 um)",
    "B02": "Band 02 - Green visible (0.51 um)",
    "B03": "Band 03 - Red visible (0.64 um)",
    "B04": "Band 04 - Near infrared (0.86 um)",
    "B05": "Band 05 - Near infrared (1.6 um)",
    "B06": "Band 06 - Near infrared (2.3 um)",
    "B07": "Band 07 - Short-wave infrared (3.9 um)",
    "B08": "Band 08 - Water vapor (6.2 um)",
    "B09": "Band 09 - Water vapor (6.9 um)",
    "B10": "Band 10 - Water vapor (7.3 um)",
    "B11": "Band 11 - Infrared (8.6 um)",
    "B12": "Band 12 - Ozone (9.6 um)",
    "B13": "Band 13 - Infrared (10.4 um)",
    "B14": "Band 14 - Infrared (11.2 um)",
    "B15": "Band 15 - Infrared (12.4 um)",
    "B16": "Band 16 - Infrared (13.3 um)",
}

_JMA_PRODUCTS = {
    "b13": "B13 (Infrared)",
    "b03": "B03 (Visible)",
    "b08": "B08 (Water Vapor)",
    "b07": "B07 (Short Wave Infrared)",
    "dms": "Day Microphysics RGB",
    "ngt": "Night Microphysics RGB",
    "dst": "Dust RGB",
    "arm": "Airmass RGB",
    "dsl": "Day Snow-Fog RGB",
    "dnc": "Natural Color RGB",
    "tre": "True Color RGB (Enhanced)",
    "trm": "True Color Reproduction Image",
    "cve": "Day Convective Storm RGB",
    "snd": "Sandwich",
    "vir": "B03 combined with B13",
    "irv": "B03 and B13 at night",
    "ash": "Ash RGB",
}

_JMA_STANDARD = (
    ("fd_", "Full Disk", "JMA Full Disk", "601x601"),
    ("aus", "Australia", "JMA Regions", "901x701"),
    ("nzl", "New Zealand", "JMA Regions", "701x701"),
    ("jpn", "Japan", "JMA Regions", "801x641"),
    ("ca1", "Central Asia", "JMA Regions", "1001x601"),
    ("se1", "Southeast Asia 1", "JMA Regions", "701x601"),
    ("se2", "Southeast Asia 2", "JMA Regions", "701x601"),
    ("se3", "Southeast Asia 3", "JMA Regions", "1101x501"),
    ("se4", "South Asia", "JMA Regions", "601x601"),
    ("pi1", "Pacific Islands 1", "JMA Pacific Islands", "701x601"),
    ("pi2", "Pacific Islands 2", "JMA Pacific Islands", "601x501"),
    ("pi3", "Pacific Islands 3", "JMA Pacific Islands", "1201x501"),
    ("pi4", "Pacific Islands 4", "JMA Pacific Islands", "601x481"),
    ("pi5", "Pacific Islands 5", "JMA Pacific Islands", "601x481"),
    ("pi6", "Pacific Islands 6", "JMA Pacific Islands", "601x481"),
    ("pi7", "Pacific Islands 7", "JMA Pacific Islands", "601x481"),
    ("pi8", "Pacific Islands 8", "JMA Pacific Islands", "501x501"),
    ("pi9", "Pacific Islands 9", "JMA Pacific Islands", "1001x541"),
    ("pia", "Pacific Islands 10", "JMA Pacific Islands", "601x481"),
    ("ha1", "High-Resolution Asia 1", "JMA High Resolution", "551x441"),
    ("ha2", "High-Resolution Asia 2", "JMA High Resolution", "701x501"),
    ("ha3", "High-Resolution Asia 3", "JMA High Resolution", "551x441"),
    ("ha4", "High-Resolution Asia 4", "JMA High Resolution", "651x501"),
    ("ha5", "High-Resolution Asia 5", "JMA High Resolution", "551x551"),
    ("ha6", "High-Resolution Asia 6", "JMA High Resolution", "601x501"),
    ("hp1", "High-Resolution Pacific Islands 1", "JMA High Resolution", "801x401"),
    ("hp2", "High-Resolution Pacific Islands 2", "JMA High Resolution", "501x601"),
    ("hp3", "High-Resolution Pacific Islands 3", "JMA High Resolution", "601x501"),
)


def _jma_area(raw_id, label, category, resolution, family="standard", directory=None):
    return {"id": "jma_" + raw_id, "label": "JMA - " + label, "category": category,
            "kind": "jma", "raw_id": raw_id, "resolution": resolution,
            "family": family, "directory": directory or raw_id,
            "interval": 150 if family == "target" else 600}


_JMA_AREAS = tuple(_jma_area(*values) for values in _JMA_STANDARD) + (
    _jma_area("r2s", "Southeast Asia - Heavy rainfall", "JMA Heavy Rainfall", "751x451", "heavy"),
    _jma_area("r2w", "Southeast Asia - Heavy rainfall (Large)", "JMA Heavy Rainfall", "1501x901", "heavy"),
    _jma_area("r5s", "South Pacific Islands - Heavy rainfall", "JMA Heavy Rainfall", "751x451", "heavy"),
    _jma_area("r5w", "South Pacific Islands - Heavy rainfall (Large)", "JMA Heavy Rainfall", "1501x901", "heavy"),
    _jma_area("ho1", "Pacific Island 1 (Samoa) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "601x1001", "high_rainfall", "hox"),
    _jma_area("ho2", "Pacific Island 2 (Tuvalu) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "501x601", "high_rainfall", "hox"),
    _jma_area("ho3", "Pacific Island 3 (Tonga) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "601x751", "high_rainfall", "hox"),
    _jma_area("ho4", "Pacific Island 4 (Micronesia - Palikir) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "601x501", "high_rainfall", "hox"),
    _jma_area("ho5", "Pacific Island 5 (Micronesia - Weno) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "601x501", "high_rainfall", "hox"),
    _jma_area("ho6", "Pacific Island 6 (Palau) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "601x501", "high_rainfall", "hox"),
    _jma_area("ho7", "Pacific Island 7 (Kiribati) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "601x501", "high_rainfall", "hox"),
    _jma_area("ho8", "Pacific Island 8 (Nauru) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "601x501", "high_rainfall", "hox"),
    _jma_area("ho9", "Pacific Island 9 (Marshall Islands) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "601x501", "high_rainfall", "hox"),
    _jma_area("hoa", "Pacific Island 10 (Niue) - Heavy rainfall", "JMA High-Resolution Heavy Rainfall", "601x501", "high_rainfall", "hox"),
    _jma_area("tga", "Target area observation", "JMA Target Area", "600x600", "target"),
    dict(_jma_area("fd_", "Target-area position", "JMA Target Area", "601x601", "position"),
         id="jma_target_position"),
)


def _static_areas():
    return [copy.deepcopy(value) for value in _NICT_AREAS + _JMA_AREAS]


def _jma_page(area):
    family = area["family"]
    if family == "standard":
        return JMA_BASE + "sat_img.php?" + urllib.parse.urlencode({"area": area["raw_id"]})
    if family == "heavy":
        return JMA_BASE + "sat_hrp.php?" + urllib.parse.urlencode({"area": area["raw_id"]})
    if family == "high_rainfall":
        return JMA_BASE + "sat_hox.php?" + urllib.parse.urlencode({"area": area["raw_id"]})
    if family == "target":
        return JMA_BASE + "sat_tgb.php"
    return JMA_BASE + "sat_tga.php"


_MONTHS = {name: index for index, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"), 1)}
_JMA_TIME = re.compile(
    r"^(\d{1,2}):(\d{2})(?::(\d{2}))?\s+UTC\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$"
)


def _parse_jma_time(label):
    match = _JMA_TIME.fullmatch(" ".join(str(label).split()))
    if not match or match[5] not in _MONTHS:
        raise HimawariError("JMA returned an unrecognized latest-image time.")
    hour, minute, second, day, month, year = match.groups()
    try:
        value = dt.datetime(int(year), _MONTHS[month], int(day), int(hour),
                            int(minute), int(second or 0), tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise HimawariError("JMA returned an invalid latest-image time.") from exc
    return value.isoformat().replace("+00:00", "Z")


def _validate_output(output_size, fit_mode, zoom, background):
    try:
        width, height = (int(value) for value in output_size)
        zoom = float(zoom)
        color = ImageColor.getrgb(background)
    except (TypeError, ValueError) as exc:
        raise HimawariError("Invalid output size, zoom or background color.") from exc
    if width <= 0 or height <= 0 or width * height > MAX_OUTPUT_PIXELS or max(width, height) > 32768:
        raise HimawariError("The requested desktop image is too large or has invalid dimensions.")
    if fit_mode not in ("fit", "crop") or not math.isfinite(zoom) or not 0.05 <= zoom <= 20:
        raise HimawariError("The image fit mode or zoom is invalid.")
    return (width, height), zoom, color[:3]


def _render_image(image, output_size, fit_mode, zoom, color):
    out_width, out_height = output_size
    scale = (min if fit_mode == "fit" else max)(
        out_width / image.width, out_height / image.height
    ) * zoom
    draw_width, draw_height = image.width * scale, image.height * scale
    offset_x, offset_y = (out_width - draw_width) / 2, (out_height - draw_height) / 2
    left, top = max(0, round(offset_x)), max(0, round(offset_y))
    right, bottom = min(out_width, round(offset_x + draw_width)), min(out_height, round(offset_y + draw_height))
    box = (max(0, (left - offset_x) / scale), max(0, (top - offset_y) / scale),
           min(image.width, (right - offset_x) / scale), min(image.height, (bottom - offset_y) / scale))
    with Image.new("RGB", output_size, color) as canvas:
        if right > left and bottom > top:
            with image.resize((right - left, bottom - top), Image.Resampling.LANCZOS, box=box) as resized:
                canvas.paste(resized, (left, top))
        output = io.BytesIO()
        canvas.save(output, "PNG")
        return output.getvalue()


class HimawariClient:
    """Thread-safe access to current NICT tiles and JMA regional stills."""

    def __init__(self, timeout=90, user_agent="MarbleScape/Himawari"):
        self.timeout = max(1, min(float(timeout), 180))
        self.user_agent = str(user_agent)
        self._lock = threading.RLock()
        self._inflight = {}
        self._areas = _static_areas()
        self._areas_time = 0.0
        self._products = {}
        self._nict_base = NICT_IMAGE_BASE
        self._refresh_future = None
        self._refresh_status = {"running": False, "done": 0, "total": 0,
                                "message": "", "error": ""}
        self.catalogue_warning = ""

    def _coordinated(self, key, operation):
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

    def _request(self, url, limit, track=False):
        if track:
            DOWNLOAD_PROGRESS.raise_if_cancelled()
        _checked_url(url)
        cache = getattr(self, "metadata_cache", None) if not track else None
        request_headers = {
            "User-Agent": self.user_agent,
            "Cache-Control": "no-cache",
        }
        if cache:
            request_headers.update(cache.headers(url))
        request = urllib.request.Request(url, headers=request_headers)
        try:
            timeout = self.timeout if track else min(self.timeout, 20.0)
            with urllib.request.build_opener(_Redirects()).open(request, timeout=timeout) as response:
                _checked_url(response.geturl())
                try:
                    body = read_response(response, limit, track=track)
                except ResponseTooLargeError:
                    raise HimawariError("Himawari response exceeds the permitted download size.")
                response_headers = dict(response.headers.items())
                if cache:
                    cache.store(url, body, response_headers)
                return body, response_headers
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cache and (saved := cache.response(url, limit)) is not None:
                exc.close()
                return saved
            raise UnavailableError("Himawari request failed (HTTP %s): %s" % (exc.code, url)) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise UnavailableError("Himawari is currently unreachable: %s" % exc) from exc

    def _html(self, url):
        body, _ = self._request(url, MAX_DOCUMENT_BYTES)
        return body.decode("utf-8", errors="replace")

    def _refresh_nict_base(self):
        script = self._html(urllib.parse.urljoin(NICT_SITE_URL, "js/env.js"))
        match = re.search(r"\bimgBaseUrl\s*:\s*['\"]([^'\"]+)['\"]", script)
        if not match:
            raise HimawariError("NICT no longer publishes a supported image base URL.")
        image_base = urllib.parse.urljoin(NICT_SITE_URL, match.group(1))
        _checked_url(image_base)
        if not image_base.endswith("/"):
            image_base += "/"
        with self._lock:
            self._nict_base = image_base
        return image_base

    @staticmethod
    def _validate_provider(provider):
        if provider != PROVIDER:
            raise HimawariError("Unknown Himawari provider: " + str(provider))

    def list_areas(self, provider, refresh=False):
        self._validate_provider(provider)
        with self._lock:
            if not refresh:
                return copy.deepcopy(self._areas)
        try:
            return self._coordinated(("areas",), self._refresh_areas)
        except HimawariError as exc:
            # NICT and the static JMA area list remain useful during a JMA
            # catalogue outage; report the fallback instead of blocking them.
            with self._lock:
                self.catalogue_warning = "Using the last known Himawari areas: " + str(exc)
                self._areas_time = time.monotonic()
                return copy.deepcopy(self._areas)

    def _refresh_areas(self):
        html = self._html(JMA_BASE + "sat_img.php?area=fd_")
        listed = dict(_selects(html).get("slt_area", ()))
        if not listed or "fd_" not in listed:
            raise HimawariError("JMA no longer lists the expected Himawari areas.")
        areas = list(_NICT_AREAS)
        for area in _JMA_AREAS:
            if area["family"] != "standard" or area["raw_id"] in listed:
                updated = dict(area)
                if area["family"] == "standard":
                    updated["label"] = "JMA - " + listed[area["raw_id"]]
                areas.append(updated)
        with self._lock:
            self._areas = copy.deepcopy(areas)
            self._areas_time = time.monotonic()
            self.catalogue_warning = ""
        return copy.deepcopy(areas)

    def _area(self, area_id):
        with self._lock:
            area = next((value for value in self._areas if value["id"] == area_id), None)
        if area is None:
            area = next((value for value in _static_areas() if value["id"] == area_id), None)
        if area is None:
            raise UnavailableError("The selected Himawari area is not available: " + str(area_id))
        return copy.deepcopy(area)

    @staticmethod
    def _nict_products(area):
        width, height = area["tile_size"]
        resolutions = [f"{width * count}x{height * count}" for count in area["counts"]]
        if area["dataset"] == "FULL_24h":
            return [{"id": key, "label": label, "resolutions": list(resolutions)}
                    for key, label in _BAND_LABELS.items()]
        return [{"id": "true_color", "label": "True Color", "resolutions": resolutions}]

    @staticmethod
    def _jma_fallback_products(area):
        if area["family"] == "target":
            labels = {"snd": "Sandwich", "irv": _JMA_PRODUCTS["irv"],
                      "cve": _JMA_PRODUCTS["cve"], "ash": "Ash RGB"}
        elif area["family"] == "position":
            labels = {"dsk": "B13 (Infrared) - target-area position"}
        elif area["family"] == "high_rainfall":
            labels = {"hrp": "Heavy rainfall potential areas",
                      "cve": _JMA_PRODUCTS["cve"], "snd": _JMA_PRODUCTS["snd"],
                      "trm": _JMA_PRODUCTS["trm"]}
        else:
            labels = dict(_JMA_PRODUCTS)
            if area["family"] == "heavy":
                labels = {"hrp": "Heavy rainfall potential areas",
                          **{key: value for key, value in labels.items() if key != "ash"}}
        return [{"id": key, "label": label, "resolutions": [area["resolution"]]}
                for key, label in labels.items()]

    def list_products(self, provider, area_id, refresh=False):
        self._validate_provider(provider)
        area = self._area(area_id)
        if area["kind"] == "nict":
            return self._nict_products(area)
        key = area["family"]
        with self._lock:
            cached = self._products.get(key)
        if cached and not refresh and time.monotonic() - cached[0] < CATALOGUE_TTL:
            labels = cached[1]
        elif refresh:
            def load():
                options = _selects(self._html(_jma_page(area))).get("slt_element", ())
                labels = {value: label for value, label in options
                          if re.fullmatch(r"[a-z0-9]{3}", value)}
                if not labels:
                    raise HimawariError("JMA returned no supported still-image products.")
                with self._lock:
                    self._products[key] = (time.monotonic(), labels)
                return labels
            labels = self._coordinated(("products", key), load)
        else:
            return self._jma_fallback_products(area)
        return [{"id": value, "label": label, "resolutions": [area["resolution"]]}
                for value, label in labels.items()]

    def _latest_nict(self, area, product_id, resolution):
        products = self._nict_products(area)
        product = next((value for value in products if value["id"] == product_id), None)
        if product is None:
            raise UnavailableError("The selected NICT product is not available: " + str(product_id))
        if resolution == "largest":
            resolution = max(product["resolutions"], key=_resolution_key)
        if resolution not in product["resolutions"]:
            raise UnavailableError("The selected NICT resolution is not available: " + str(resolution))
        body, _ = self._request(
            self._nict_base + "img/" + area["dataset"] + "/latest.json",
            MAX_DOCUMENT_BYTES,
        )
        try:
            document = json.loads(body.decode("utf-8"))
            timestamp = dt.datetime.strptime(document["date"], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=dt.timezone.utc
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HimawariError("NICT returned invalid latest-image metadata.") from exc
        width, height = map(int, resolution.split("x"))
        tile_width, tile_height = area["tile_size"]
        if width % tile_width or height % tile_height or width // tile_width != height // tile_height:
            raise HimawariError("NICT source resolution does not match its tile grid.")
        count = width // tile_width
        if count not in area["counts"] or count * count > MAX_TILES:
            raise HimawariError("NICT source resolution exceeds the supported tile grid.")
        date_code = timestamp.strftime("%Y%m%d%H%M%S")
        first_url = self._nict_tile_url(area, product_id, count, date_code, 0, 0, self._nict_base)
        return {"source": PROVIDER, "kind": "nict", "area": area["id"],
                "area_label": area["label"], "dataset": area["dataset"],
                "product": product_id, "product_label": product["label"],
                "resolution": resolution, "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "expected_interval_seconds": area["interval"], "count": count,
                "tile_size": tuple(area["tile_size"]), "date_code": date_code,
                "image_base": self._nict_base, "url": first_url}

    @staticmethod
    def _nict_tile_url(area, product_id, count, date_code, x, y, image_base=NICT_IMAGE_BASE):
        date_path = f"{date_code[:4]}/{date_code[4:6]}/{date_code[6:8]}"
        time_code = date_code[8:]
        if area["dataset"] == "FULL_24h":
            path = (f"img/FULL_24h/{product_id}/{count}d/{area['tile_size'][0]}/"
                    f"{date_path}/{time_code}_{x}_{y}.png")
        else:
            path = (f"img/{area['dataset']}/{count}d/{area['tile_size'][0]}/"
                    f"{date_path}/{time_code}_{x}_{y}.png")
        return urllib.parse.urljoin(image_base, path)

    def _latest_jma(self, area, product_id, resolution):
        products = self.list_products(PROVIDER, area["id"])
        product = next((value for value in products if value["id"] == product_id), None)
        if product is None:
            raise UnavailableError("The selected JMA product is not available: " + str(product_id))
        if resolution == "largest":
            resolution = area["resolution"]
        if resolution != area["resolution"]:
            raise UnavailableError("The selected JMA resolution is not available: " + str(resolution))
        options = _selects(self._html(_jma_page(area))).get("slt_time", ())
        if not options:
            raise UnavailableError("JMA currently lists no Himawari image times for this area.")
        time_code, label = options[0]
        if not re.fullmatch(r"\d{4}(?:_[1-4])?", time_code):
            raise HimawariError("JMA returned an unsupported latest-image identifier.")
        timestamp = _parse_jma_time(label)
        raw = area["raw_id"]
        directory = area["directory"]
        url = urllib.parse.urljoin(JMA_BASE, f"img/{directory}/{raw}_{product_id}_{time_code}.jpg")
        return {"source": PROVIDER, "kind": "jma", "area": area["id"],
                "area_label": area["label"], "family": area["family"], "raw_id": raw,
                "directory": directory,
                "product": product_id, "product_label": product["label"],
                "resolution": resolution, "timestamp": timestamp,
                "expected_interval_seconds": area["interval"], "time_code": time_code, "url": url}

    def latest(self, provider, area_id, product_id, resolution):
        self._validate_provider(provider)
        area = self._area(area_id)
        if area["kind"] == "nict":
            return self._latest_nict(area, product_id, resolution)
        return self._latest_jma(area, product_id, resolution)

    @property
    def catalogue_refresh_status(self):
        with self._lock:
            return dict(self._refresh_status)

    def _progress(self, done, total, message, callback=None):
        with self._lock:
            self._refresh_status.update(done=done, total=total, message=message)
        if callback:
            callback(done, total, message)

    def refresh_all_catalogues(self, refresh=True, progress=None):
        with self._lock:
            future = self._refresh_future
            owner = future is None
            if owner:
                future = self._refresh_future = Future()
                self._refresh_status = {"running": True, "done": 0, "total": 7,
                                        "message": "Loading Himawari areas...", "error": ""}
        if not owner:
            return copy.deepcopy(future.result())
        try:
            errors = []
            try:
                self._refresh_nict_base()
            except HimawariError as exc:
                errors.append("NICT: " + str(exc))
            self._progress(1, 7, "NICT image service loaded.", progress)
            try:
                areas = self.list_areas(PROVIDER, refresh=refresh or not self._areas_time)
            except HimawariError as exc:
                areas = _static_areas()
                errors.append("areas: " + str(exc))
            with self._lock:
                area_warning = self.catalogue_warning
            if area_warning:
                errors.append("areas: " + area_warning)
            jma_unreachable = "currently unreachable" in area_warning
            families = ("standard", "heavy", "high_rainfall", "target", "position")
            products_count = resolution_count = 0
            for done, family in enumerate(families, 1):
                self._progress(done + 1, 7, "Loading JMA " + family + " products...", progress)
                area = next(value for value in areas if value.get("family") == family)
                try:
                    if jma_unreachable:
                        products = self._jma_fallback_products(area)
                    else:
                        products = self.list_products(
                            PROVIDER, area["id"], refresh=refresh or family not in self._products
                        )
                except HimawariError as exc:
                    products = self._jma_fallback_products(area)
                    errors.append(family + ": " + str(exc))
                    if isinstance(exc, UnavailableError) and "currently unreachable" in str(exc):
                        jma_unreachable = True
                family_areas = [value for value in areas if value.get("family") == family]
                products_count += len(products) * len(family_areas)
                resolution_count += sum(len(value["resolutions"]) for value in products) * len(family_areas)
            for area in areas:
                if area["kind"] == "nict":
                    products = self._nict_products(area)
                    products_count += len(products)
                    resolution_count += sum(len(value["resolutions"]) for value in products)
            warning = ""
            if errors:
                warning = "Himawari catalogue refresh is incomplete. " + " | ".join(errors[:4])
            with self._lock:
                self.catalogue_warning = warning
            self._progress(7, 7, "Himawari catalogue refresh incomplete." if errors
                           else "All Himawari catalogues are ready.", progress)
            result = {"providers": 1, "areas": len(areas), "products": products_count,
                      "resolution_options": resolution_count, "errors": errors,
                      "warning": warning, "complete": not errors}
        except BaseException as exc:
            with self._lock:
                self._refresh_status.update(running=False, error=str(exc),
                                            message="Himawari catalogue refresh failed.")
            future.set_exception(exc)
            raise
        else:
            with self._lock:
                self._refresh_status.update(running=False, error=warning)
            future.set_result(result)
            return copy.deepcopy(result)
        finally:
            with self._lock:
                if self._refresh_future is future:
                    self._refresh_future = None

    def fetch_image(self, frame, output_size, fit_mode="fit", zoom=1.0, background="#000000"):
        output_size, zoom, color = _validate_output(output_size, fit_mode, zoom, background)
        if frame.get("source") != PROVIDER or frame.get("kind") not in ("nict", "jma"):
            raise HimawariError("The Himawari frame identity is invalid.")
        area = self._area(frame.get("area"))
        expected = self.latest_identity(area, frame)
        for key, value in expected.items():
            if frame.get(key) != value:
                raise HimawariError("Himawari frame identity does not match its image URL.")
        if frame["kind"] == "jma":
            body, _ = self._request(frame["url"], MAX_IMAGE_BYTES, track=True)
            try:
                with Image.open(io.BytesIO(body), formats=("JPEG",)) as source:
                    if getattr(source, "is_animated", False) or getattr(source, "n_frames", 1) != 1:
                        raise HimawariError("Animated Himawari images are not supported.")
                    if source.size != tuple(map(int, frame["resolution"].split("x"))):
                        raise HimawariError("JMA image dimensions differ from the selected source resolution.")
                    source.load()
                    with source.convert("RGB") as rgb:
                        return _render_image(rgb, output_size, fit_mode, zoom, color)
            except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
                if isinstance(exc, HimawariError):
                    raise
                raise HimawariError("JMA returned a damaged or unsupported still image: %s" % exc) from exc
        return self._render_nict(frame, area, output_size, fit_mode, zoom, color)

    def latest_identity(self, area, frame):
        if frame.get("kind") == "jma":
            if (not re.fullmatch(r"[a-z0-9]{3}", str(frame.get("product", "")))
                    or not re.fullmatch(r"\d{4}(?:_[1-4])?", str(frame.get("time_code", "")))):
                return {"url": "", "raw_id": area["raw_id"],
                        "resolution": area["resolution"]}
            expected_url = urllib.parse.urljoin(
                JMA_BASE,
                f"img/{area['directory']}/{area['raw_id']}_{frame.get('product')}_{frame.get('time_code')}.jpg",
            )
            return {"url": expected_url, "raw_id": area["raw_id"],
                    "directory": area["directory"], "resolution": area["resolution"]}
        date_code = str(frame.get("date_code", ""))
        count = frame.get("count")
        allowed = {value["id"] for value in HimawariClient._nict_products(area)}
        expected_url = self._nict_tile_url(
            area, frame.get("product"), count, date_code, 0, 0, self._nict_base
        ) if (re.fullmatch(r"\d{14}", date_code) and isinstance(count, int)
              and count in area["counts"] and frame.get("product") in allowed) else ""
        width, height = area["tile_size"]
        return {"url": expected_url, "dataset": area["dataset"],
                "image_base": self._nict_base, "tile_size": tuple(area["tile_size"]),
                "resolution": f"{width * count}x{height * count}" if isinstance(count, int) else ""}

    def _decode_nict_tile(self, body, expected_size):
        try:
            with Image.open(io.BytesIO(body), formats=("PNG",)) as image:
                if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                    raise HimawariError("Animated NICT images are not supported.")
                if image.size != expected_size:
                    raise HimawariError("NICT tile dimensions differ from the selected source resolution.")
                image.load()
                return image.convert("RGBA")
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            if isinstance(exc, HimawariError):
                raise
            raise HimawariError("NICT returned a damaged or unsupported PNG tile: %s" % exc) from exc

    def _load_nict_tile(self, frame, area, x, y):
        DOWNLOAD_PROGRESS.raise_if_cancelled()
        url = self._nict_tile_url(
            area, frame["product"], frame["count"], frame["date_code"], x, y,
            self._nict_base,
        )
        foreground, _ = self._request(url, MAX_IMAGE_BYTES, track=True)
        tile_size = tuple(area["tile_size"])
        with self._decode_nict_tile(foreground, tile_size) as layer:
            if area["dataset"] != "FULL_24h":
                return x, y, layer.convert("RGB")
            base_url = urllib.parse.urljoin(
                self._nict_base,
                f"img/FULL_24h/BlueMarble/{frame['count']}d/{tile_size[0]}/BlueMarble_{x}_{y}.png",
            )
            base, _ = self._request(base_url, MAX_IMAGE_BYTES, track=True)
            with self._decode_nict_tile(base, tile_size) as background:
                return x, y, Image.alpha_composite(background, layer).convert("RGB")

    def _render_nict(self, frame, area, output_size, fit_mode, zoom, color):
        count = frame["count"]
        tile_width, tile_height = area["tile_size"]
        source_width, source_height = tile_width * count, tile_height * count
        out_width, out_height = output_size
        scale = (min if fit_mode == "fit" else max)(
            out_width / source_width, out_height / source_height
        ) * zoom
        draw_width, draw_height = source_width * scale, source_height * scale
        offset_x, offset_y = (out_width - draw_width) / 2, (out_height - draw_height) / 2
        visible = []
        for y in range(count):
            for x in range(count):
                left = round(offset_x + x * tile_width * scale)
                top = round(offset_y + y * tile_height * scale)
                right = round(offset_x + (x + 1) * tile_width * scale)
                bottom = round(offset_y + (y + 1) * tile_height * scale)
                if right > 0 and bottom > 0 and left < out_width and top < out_height:
                    visible.append((x, y, left, top, right, bottom))
        if not visible or len(visible) > MAX_TILES:
            raise HimawariError("The selected NICT tile grid cannot be rendered safely.")
        positions = {(x, y): (left, top, right, bottom)
                     for x, y, left, top, right, bottom in visible}
        with Image.new("RGB", output_size, color) as canvas:
            workers = min(8, len(visible))
            coordinates = iter((x, y) for x, y, *_ in visible)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                tasks = {}
                for _ in range(min(len(visible), workers * 2)):
                    x, y = next(coordinates)
                    tasks[pool.submit(self._load_nict_tile, frame, area, x, y)] = (x, y)
                try:
                    while tasks:
                        DOWNLOAD_PROGRESS.raise_if_cancelled()
                        completed, _ = wait(tasks, return_when=FIRST_COMPLETED)
                        for task in completed:
                            tasks.pop(task)
                            x, y, tile = task.result()
                            try:
                                left, top, right, bottom = positions[(x, y)]
                                width, height = right - left, bottom - top
                                if width > 0 and height > 0:
                                    with tile.resize((width, height), Image.Resampling.LANCZOS) as resized:
                                        crop = (max(0, -left), max(0, -top),
                                                min(width, out_width - left), min(height, out_height - top))
                                        if crop[2] > crop[0] and crop[3] > crop[1]:
                                            with resized.crop(crop) as clipped:
                                                canvas.paste(clipped, (max(0, left), max(0, top)))
                            finally:
                                tile.close()
                            try:
                                next_x, next_y = next(coordinates)
                            except StopIteration:
                                pass
                            else:
                                DOWNLOAD_PROGRESS.raise_if_cancelled()
                                pending = pool.submit(
                                    self._load_nict_tile, frame, area, next_x, next_y
                                )
                                tasks[pending] = (next_x, next_y)
                except BaseException:
                    for pending in tasks:
                        pending.cancel()
                    raise
            output = io.BytesIO()
            canvas.save(output, "PNG")
            return output.getvalue()
