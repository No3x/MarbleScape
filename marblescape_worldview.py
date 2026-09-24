"""NASA Worldview/GIBS catalogue discovery and still-image rendering.

Worldview is powered by NASA Global Imagery Browse Services (GIBS).  The
public WMTS capabilities document supplies layer, time and native tile-matrix
metadata.  MarbleScape renders one selected layer through the matching GIBS
WMS endpoint and never scrapes the interactive Worldview application.
"""

from __future__ import annotations

import calendar
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
import xml.etree.ElementTree as ET
from concurrent.futures import Future

from PIL import Image, ImageColor

from marblescape_download_progress import DOWNLOAD_PROGRESS, ResponseTooLargeError, read_response


PROVIDER = "worldview"
SITE_URL = "https://worldview.earthdata.nasa.gov/"
GIBS_BASE = "https://gibs.earthdata.nasa.gov"
CAPABILITIES_URL = GIBS_BASE + "/wmts/epsg4326/best/1.0.0/WMTSCapabilities.xml"
WMS_URL = GIBS_BASE + "/wms/epsg4326/best/wms.cgi"
WMTS_BASE = GIBS_BASE + "/wmts/epsg4326/best"
DEFAULT_LAYER = "VIIRS_NOAA20_CorrectedReflectance_TrueColor"
DEFAULT_PROFILE = {"area": DEFAULT_LAYER, "product": "latest", "resolution": "auto"}
ALLOWED_HOSTS = frozenset(("gibs.earthdata.nasa.gov",))
MAX_CAPABILITIES_BYTES = 16 * 1024 * 1024
MAX_DOMAINS_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 200 * 1024 * 1024
MAX_SOURCE_PIXELS = 33_554_432
MAX_OUTPUT_PIXELS = 33_554_432
MAX_SOURCE_DIMENSION = 8192
MAX_OUTPUT_DIMENSION = 32768
CATALOGUE_TTL = 60 * 60
LATEST_TTL = 60
RECENT_TIME_LIMIT = 100

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,299}")
_TIME_VALUE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?")
_DURATION = re.compile(
    r"P(?:(?P<years>\d+)Y)?(?:(?P<months>\d+)M)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?"
)
_WMTS = "http://www.opengis.net/wmts/1.0"
_OWS = "http://www.opengis.net/ows/1.1"
_NS = {"wmts": _WMTS, "ows": _OWS}


class WorldviewError(RuntimeError):
    """A NASA GIBS response cannot be safely or unambiguously used."""


class UnavailableError(WorldviewError):
    """The selected NASA GIBS image is currently unavailable."""


def _checked_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.port not in (None, 443) or parsed.username is not None
            or parsed.password is not None):
        raise WorldviewError("NASA GIBS returned an unsupported image or catalogue host.")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _clean_text(value, limit=300):
    return " ".join(str(value or "").split())[:limit]


def _resolution_key(value):
    width, height = map(int, value.split("x"))
    return width * height, width, height


def _fallback_catalogue():
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    layers = {}
    for identifier, title, matrix_set, resolutions in (
        (DEFAULT_LAYER, "Corrected Reflectance (True Color, VIIRS, NOAA-20)",
         "250m", ("1024x512", "2048x1024", "4096x2048", "8192x4096")),
        ("VIIRS_NOAA21_CorrectedReflectance_TrueColor",
         "Corrected Reflectance (True Color, VIIRS, NOAA-21)",
         "250m", ("1024x512", "2048x1024", "4096x2048", "8192x4096")),
        ("VIIRS_SNPP_CorrectedReflectance_TrueColor",
         "Corrected Reflectance (True Color, VIIRS, Suomi NPP)",
         "250m", ("1024x512", "2048x1024", "4096x2048", "8192x4096")),
        ("MODIS_Terra_CorrectedReflectance_TrueColor",
         "Corrected Reflectance (True Color, MODIS, Terra)",
         "250m", ("1024x512", "2048x1024", "4096x2048", "8192x4096")),
        ("MODIS_Aqua_CorrectedReflectance_TrueColor",
         "Corrected Reflectance (True Color, MODIS, Aqua)",
         "250m", ("1024x512", "2048x1024", "4096x2048", "8192x4096")),
    ):
        layers[identifier] = {
            "id": identifier,
            "label": title,
            "category": "Corrected Reflectance",
            "matrix_set": matrix_set,
            "resolutions": list(resolutions),
            "time_default": today,
            "time_values": [today],
        }
    return layers


def _parse_xml(body, description):
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise WorldviewError(f"NASA GIBS returned unsafe {description} XML.")
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise WorldviewError(f"NASA GIBS returned invalid {description} XML.") from exc


def _matrix_resolutions(root):
    result = {}
    for matrix_set in root.findall(".//wmts:Contents/wmts:TileMatrixSet", _NS):
        identifier = matrix_set.findtext("ows:Identifier", default="", namespaces=_NS)
        if not _IDENTIFIER.fullmatch(identifier or ""):
            continue
        resolutions = set()
        for matrix in matrix_set.findall("wmts:TileMatrix", _NS):
            try:
                tile_width = int(matrix.findtext("wmts:TileWidth", namespaces=_NS))
                tile_height = int(matrix.findtext("wmts:TileHeight", namespaces=_NS))
                matrix_width = int(matrix.findtext("wmts:MatrixWidth", namespaces=_NS))
                matrix_height = int(matrix.findtext("wmts:MatrixHeight", namespaces=_NS))
                width, height = tile_width * matrix_width, tile_height * matrix_height
            except (TypeError, ValueError):
                continue
            if (width > 0 and height > 0 and max(width, height) <= MAX_SOURCE_DIMENSION
                    and width * height <= MAX_SOURCE_PIXELS):
                resolutions.add(f"{width}x{height}")
        if resolutions:
            result[identifier] = sorted(resolutions, key=_resolution_key)
    return result


def _category_for(title, identifier):
    category = _clean_text(str(title).split("(", 1)[0], 80)
    if not category or category == title and "_" in category:
        category = _clean_text(identifier.split("_", 1)[0].replace("-", " "), 80)
    return category or "Other visualizations"


def _parse_capabilities(body):
    root = _parse_xml(body, "capabilities")
    matrices = _matrix_resolutions(root)
    layers = {}
    for element in root.findall(".//wmts:Contents/wmts:Layer", _NS):
        identifier = _clean_text(element.findtext("ows:Identifier", default="", namespaces=_NS))
        if not _IDENTIFIER.fullmatch(identifier):
            continue
        formats = {str(value.text or "").strip().casefold()
                   for value in element.findall("wmts:Format", _NS)}
        if not formats.intersection(("image/png", "image/jpeg")):
            continue
        matrix_set = element.findtext(
            "wmts:TileMatrixSetLink/wmts:TileMatrixSet", default="", namespaces=_NS
        )
        resolutions = matrices.get(matrix_set, ())
        if not resolutions:
            continue
        title = _clean_text(element.findtext("ows:Title", default=identifier, namespaces=_NS))
        time_default = ""
        time_values = []
        for dimension in element.findall("wmts:Dimension", _NS):
            dimension_id = dimension.findtext("ows:Identifier", default="", namespaces=_NS)
            if str(dimension_id).casefold() != "time":
                continue
            time_default = _clean_text(
                dimension.findtext("wmts:Default", default="", namespaces=_NS), 40
            )
            time_values = [
                _clean_text(value.text, 200)
                for value in dimension.findall("wmts:Value", _NS)
                if _clean_text(value.text, 200)
            ]
            break
        if time_default and not _TIME_VALUE.fullmatch(time_default):
            continue
        layers[identifier] = {
            "id": identifier,
            "label": title or identifier,
            "category": _category_for(title, identifier),
            "matrix_set": matrix_set,
            "resolutions": list(resolutions),
            "time_default": time_default,
            "time_values": time_values,
        }
    if DEFAULT_LAYER not in layers:
        raise WorldviewError("NASA GIBS catalogue does not contain the default true-color layer.")
    return layers


def _parse_time(value):
    if not _TIME_VALUE.fullmatch(str(value or "")):
        raise ValueError("Invalid GIBS time value")
    text = str(value)
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc), "T" not in text


def _parse_duration(value):
    match = _DURATION.fullmatch(str(value or ""))
    if not match:
        return None
    parts = {key: int(number or 0) for key, number in match.groupdict().items()}
    return parts if any(parts.values()) else None


def _shift_back(value, duration):
    years, months = duration["years"], duration["months"]
    total_months = value.year * 12 + value.month - 1 - years * 12 - months
    year, month_index = divmod(total_months, 12)
    if year < 1:
        return None
    day = min(value.day, calendar.monthrange(year, month_index + 1)[1])
    shifted = value.replace(year=year, month=month_index + 1, day=day)
    return shifted - dt.timedelta(
        days=duration["days"], hours=duration["hours"],
        minutes=duration["minutes"], seconds=duration["seconds"],
    )


def _format_time(value, date_only):
    value = value.astimezone(dt.timezone.utc).replace(microsecond=0)
    return value.date().isoformat() if date_only else value.isoformat().replace("+00:00", "Z")


def _expand_recent(values, default="", limit=RECENT_TIME_LIMIT):
    result = set()
    for expression in values:
        for item in str(expression).split(","):
            parts = item.strip().split("/")
            try:
                if len(parts) == 1:
                    value, date_only = _parse_time(parts[0])
                    result.add(_format_time(value, date_only))
                    continue
                if len(parts) != 3:
                    continue
                start, start_date_only = _parse_time(parts[0])
                end, end_date_only = _parse_time(parts[1])
                duration = _parse_duration(parts[2])
                if duration is None or end < start:
                    continue
                current = end
                date_only = start_date_only and end_date_only
                for _ in range(limit):
                    if current < start:
                        break
                    result.add(_format_time(current, date_only))
                    previous = _shift_back(current, duration)
                    if previous is None or previous >= current:
                        break
                    current = previous
            except (ValueError, OverflowError):
                continue
    if default and _TIME_VALUE.fullmatch(default):
        result.add(default)
    return sorted(result, key=lambda value: _parse_time(value)[0], reverse=True)[:limit]


def _latest_from_domains(body):
    root = _parse_xml(body, "time-domain")
    for domain in root.iter():
        if domain.tag.rsplit("}", 1)[-1] != "DimensionDomain":
            continue
        children = {child.tag.rsplit("}", 1)[-1]: child for child in domain}
        identifier = children.get("Identifier")
        value = children.get("Domain")
        if _clean_text(identifier.text if identifier is not None else "").casefold() == "time":
            values = _expand_recent(
                [_clean_text(value.text if value is not None else "", 1000)], limit=1
            )
            return values[0] if values else ""
    return ""


def _build_map_url(layer, time_value, resolution):
    width, height = map(int, resolution.split("x"))
    query = {
        "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0",
        "LAYERS": layer, "STYLES": "", "FORMAT": "image/png",
        "TRANSPARENT": "TRUE", "CRS": "EPSG:4326",
        # WMS 1.3.0 uses latitude/longitude order for EPSG:4326.
        "BBOX": "-90,-180,90,180", "WIDTH": str(width), "HEIGHT": str(height),
    }
    if time_value:
        query["TIME"] = time_value
    return WMS_URL + "?" + urllib.parse.urlencode(query)


class WorldviewClient:
    """Thread-safe client for NASA Worldview imagery exposed through GIBS."""

    def __init__(self, timeout=90, user_agent="MarbleScape/Worldview", opener=None):
        self.timeout = max(1, min(float(timeout), 180))
        self.user_agent = str(user_agent)
        self._opener = opener
        self._lock = threading.RLock()
        self._inflight = {}
        self._catalogue = _fallback_catalogue()
        self._catalogue_time = 0.0
        self._latest = {}
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
            value = operation()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(value)
            return value
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
            "User-Agent": self.user_agent, "Cache-Control": "no-cache",
        }
        if cache:
            request_headers.update(cache.headers(url))
        request = urllib.request.Request(url, headers=request_headers)
        try:
            opener = self._opener or urllib.request.build_opener(_Redirects())
            timeout = self.timeout if track else min(self.timeout, 20.0)
            with opener.open(request, timeout=timeout) as response:
                _checked_url(response.geturl())
                try:
                    body = read_response(response, limit, track=track)
                except ResponseTooLargeError:
                    raise WorldviewError("NASA GIBS response exceeds the permitted download size.")
                response_headers = dict(response.headers.items())
                if cache:
                    cache.store(url, body, response_headers)
                return body, response_headers
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cache and (saved := cache.response(url, limit)) is not None:
                exc.close()
                return saved
            raise UnavailableError(
                "NASA GIBS request failed (HTTP %s): %s" % (exc.code, url)
            ) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise UnavailableError("NASA GIBS is currently unreachable: %s" % exc) from exc

    @staticmethod
    def _validate_provider(provider):
        if provider != PROVIDER:
            raise WorldviewError("Unknown NASA Worldview provider: " + str(provider))

    def _refresh_catalogue(self):
        body, _headers = self._request(CAPABILITIES_URL, MAX_CAPABILITIES_BYTES)
        catalogue = _parse_capabilities(body)
        with self._lock:
            self._catalogue = catalogue
            self._catalogue_time = time.monotonic()
            self.catalogue_warning = ""
        return catalogue

    def _document(self, refresh=False):
        with self._lock:
            fresh = self._catalogue_time and time.monotonic() - self._catalogue_time < CATALOGUE_TTL
            catalogue = self._catalogue
        if refresh or not fresh:
            try:
                return self._coordinated(("catalogue",), self._refresh_catalogue)
            except (WorldviewError, UnicodeError) as exc:
                with self._lock:
                    self.catalogue_warning = str(exc)
                    self._catalogue_time = time.monotonic()
                if catalogue:
                    return catalogue
                raise
        return catalogue

    def list_areas(self, provider, refresh=False):
        self._validate_provider(provider)
        with self._lock:
            fresh = self._catalogue_time and time.monotonic() - self._catalogue_time < CATALOGUE_TTL
        if refresh or not fresh:
            self._document(refresh=refresh)
        with self._lock:
            catalogue = self._catalogue
            return [copy.deepcopy(catalogue[key]) for key in sorted(
                catalogue,
                key=lambda key: (catalogue[key]["category"].casefold(),
                                 catalogue[key]["label"].casefold(), key.casefold()),
            )]

    def _layer(self, layer_id, refresh=False):
        if not isinstance(layer_id, str) or not _IDENTIFIER.fullmatch(layer_id):
            raise WorldviewError("Invalid NASA Worldview layer identifier.")
        with self._lock:
            fresh = self._catalogue_time and time.monotonic() - self._catalogue_time < CATALOGUE_TTL
        if refresh or not fresh:
            self._document(refresh=refresh)
        with self._lock:
            layer = copy.deepcopy(self._catalogue.get(layer_id))
        if layer is None:
            raise UnavailableError("The selected NASA Worldview layer is unavailable: " + layer_id)
        return layer

    def list_products(self, provider, area_id, refresh=False):
        self._validate_provider(provider)
        layer = self._layer(area_id, refresh=refresh)
        resolutions = list(layer["resolutions"])
        if not layer["time_default"]:
            return [{"id": "timeless", "label": "Timeless", "resolutions": resolutions}]
        products = [{
            "id": "latest",
            "label": "Latest available (currently %s)" % layer["time_default"],
            "resolutions": resolutions,
        }]
        products.extend({
            "id": value, "label": "Fixed · " + value, "resolutions": resolutions,
        } for value in _expand_recent(
            layer["time_values"], layer["time_default"], RECENT_TIME_LIMIT
        ))
        return products

    def _latest_time(self, layer):
        if not layer["time_default"]:
            return ""
        with self._lock:
            cached = self._latest.get(layer["id"])
            if cached and time.monotonic() - cached[0] < LATEST_TTL:
                return cached[1]
        now = dt.datetime.now(dt.timezone.utc)
        start = (now - dt.timedelta(days=7)).date().isoformat()
        end = (now + dt.timedelta(days=1)).date().isoformat()
        identifier = urllib.parse.quote(layer["id"], safe="")
        matrix_set = urllib.parse.quote(layer["matrix_set"], safe="")
        url = (f"{WMTS_BASE}/1.0.0/{identifier}/default/{matrix_set}/all/"
               f"{start}--{end}.xml")
        try:
            body, _headers = self._request(url, MAX_DOMAINS_BYTES)
            latest = _latest_from_domains(body) or layer["time_default"]
        except WorldviewError:
            latest = layer["time_default"]
        with self._lock:
            self._latest[layer["id"]] = (time.monotonic(), latest)
        return latest

    def latest(self, provider, area_id, product_id, resolution):
        self._validate_provider(provider)
        layer = self._layer(area_id)
        if not layer["time_default"]:
            if product_id not in ("latest", "timeless"):
                raise UnavailableError("The selected NASA Worldview visualization is timeless.")
            time_value = ""
            fixed = False
        elif product_id == "latest":
            time_value = self._latest_time(layer)
            fixed = False
        else:
            if not layer["time_default"] or not _TIME_VALUE.fullmatch(str(product_id or "")):
                raise UnavailableError("The selected NASA Worldview time is invalid or unavailable.")
            time_value = str(product_id)
            fixed = True
        if resolution == "largest":
            resolution = max(layer["resolutions"], key=_resolution_key)
        if resolution not in layer["resolutions"]:
            raise UnavailableError(
                "The selected NASA Worldview render resolution is unavailable: " + str(resolution)
            )
        timestamp = "timeless"
        if time_value:
            parsed, _date_only = _parse_time(time_value)
            timestamp = parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        interval = 86400
        durations = []
        for expression in layer["time_values"]:
            parts = str(expression).split("/")
            duration = _parse_duration(parts[2]) if len(parts) == 3 else None
            if duration:
                seconds = (duration["days"] * 86400 + duration["hours"] * 3600
                           + duration["minutes"] * 60 + duration["seconds"])
                if seconds:
                    durations.append(seconds)
        if durations:
            interval = min(durations)
        return {
            "source": PROVIDER,
            "area": layer["id"],
            "area_label": layer["label"],
            "product": product_id,
            "product_label": ("Timeless" if not layer["time_default"] else
                              "Latest available" if not fixed else "Fixed · " + time_value),
            "resolution": resolution,
            "time": time_value,
            "timestamp": timestamp,
            "fixed_time": fixed,
            "expected_interval_seconds": interval,
            "url": _build_map_url(layer["id"], time_value, resolution),
        }

    def refresh_all_catalogues(self, refresh=True):
        layers = self.list_areas(PROVIDER, refresh=refresh)
        return {
            "providers": 1,
            "areas": len({layer["category"] for layer in layers}),
            "products": len(layers),
            "resolution_options": sum(len(layer["resolutions"]) for layer in layers),
            "errors": [],
            "warning": self.catalogue_warning,
            "complete": not self.catalogue_warning,
        }

    def fetch_image(self, frame, output_size, fit_mode="fit", zoom=1.0,
                    background="#000000"):
        try:
            width, height = map(int, output_size)
            zoom = float(zoom)
            color = ImageColor.getrgb(str(background))
        except (TypeError, ValueError) as exc:
            raise WorldviewError("NASA Worldview output settings are invalid.") from exc
        if (width <= 0 or height <= 0 or width * height > MAX_OUTPUT_PIXELS
                or max(width, height) > MAX_OUTPUT_DIMENSION or fit_mode not in ("fit", "crop")
                or not math.isfinite(zoom) or not 0.05 <= zoom <= 20):
            raise WorldviewError("NASA Worldview output settings exceed the supported limits.")
        if frame.get("source") != PROVIDER:
            raise WorldviewError("NASA Worldview frame belongs to another provider.")
        layer = self._layer(frame.get("area", ""))
        resolution = str(frame.get("resolution", ""))
        if resolution not in layer["resolutions"]:
            raise WorldviewError("NASA Worldview frame has an unsupported render resolution.")
        product = str(frame.get("product", ""))
        time_value = str(frame.get("time", ""))
        fixed = bool(frame.get("fixed_time"))
        if layer["time_default"]:
            valid_identity = (
                (fixed and product == time_value and bool(_TIME_VALUE.fullmatch(time_value)))
                or (not fixed and product == "latest" and bool(_TIME_VALUE.fullmatch(time_value)))
            )
        else:
            valid_identity = not fixed and product in ("latest", "timeless") and not time_value
        if not valid_identity:
            raise WorldviewError("NASA Worldview frame has inconsistent layer or time metadata.")
        expected_url = _build_map_url(layer["id"], time_value, resolution)
        if frame.get("url") != expected_url:
            raise WorldviewError("NASA Worldview frame identity does not match its image URL.")
        body, headers = self._request(expected_url, MAX_IMAGE_BYTES, track=True)
        content_type = next(
            (value for key, value in headers.items() if key.casefold() == "content-type"), ""
        ).split(";", 1)[0].strip().casefold()
        if content_type and content_type not in ("image/png", "image/jpeg"):
            raise UnavailableError("NASA GIBS returned a service error instead of an image.")
        try:
            with Image.open(io.BytesIO(body), formats=("PNG", "JPEG")) as source:
                if getattr(source, "is_animated", False) or getattr(source, "n_frames", 1) != 1:
                    raise WorldviewError("Animated NASA Worldview images are not supported.")
                expected_size = tuple(map(int, resolution.split("x")))
                if source.size != expected_size:
                    raise WorldviewError(
                        "NASA GIBS image dimensions differ from the selected render resolution."
                    )
                if source.width * source.height > MAX_SOURCE_PIXELS:
                    raise WorldviewError("NASA GIBS image exceeds the supported pixel limit.")
                source.load()
                rgba = source.convert("RGBA")
            with rgba:
                with Image.new("RGB", rgba.size, color[:3]) as rgb:
                    rgb.paste(rgba, (0, 0), rgba)
                    scale_fn = min if fit_mode == "fit" else max
                    scale = scale_fn(width / rgb.width, height / rgb.height) * zoom
                    draw_width, draw_height = rgb.width * scale, rgb.height * scale
                    offset_x, offset_y = (width - draw_width) / 2, (height - draw_height) / 2
                    left, top = max(0, round(offset_x)), max(0, round(offset_y))
                    right = min(width, round(offset_x + draw_width))
                    bottom = min(height, round(offset_y + draw_height))
                    box = (
                        max(0, (left - offset_x) / scale),
                        max(0, (top - offset_y) / scale),
                        min(rgb.width, (right - offset_x) / scale),
                        min(rgb.height, (bottom - offset_y) / scale),
                    )
                    with Image.new("RGB", (width, height), color[:3]) as canvas:
                        if right > left and bottom > top:
                            with rgb.resize(
                                (right - left, bottom - top), Image.Resampling.LANCZOS, box=box
                            ) as resized:
                                canvas.paste(resized, (left, top))
                        output = io.BytesIO()
                        canvas.save(output, "PNG")
                        return output.getvalue()
        except WorldviewError:
            raise
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise WorldviewError("NASA GIBS returned a damaged or unsupported image: %s" % exc) from exc
