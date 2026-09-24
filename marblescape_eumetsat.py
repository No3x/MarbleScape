"""EUMETSAT catalogue access and dependent settings controls.

The public EUMETSAT viewer catalogue is assembled from its product decoration file
and the small set of Product Navigator fields needed for filtering.  No user
account or API key is required.
"""

from __future__ import annotations

from copy import deepcopy
import json
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk

from marblescape_catalogue_activity import CatalogueActivity
from marblescape_source_layout import SOURCE_COMBO_WIDTH, configure_source_columns
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DECORATIONS_URL = "https://view.eumetsat.int/assets/data/productDecorations.json"
PRODUCT_SEARCH_URL = "https://api.eumetsat.int/product-navigator/csw/_search"
MAX_CATALOGUE_BYTES = 2_000_000
DEFAULT_LAYER = "mtg_fd:rgb_geocolour"
DEFAULT_PROFILE = {
    "theme": "weather_monitoring",
    "satellite": "MTG - 0 Degree",
    "mission": "MTG",
    "product_type": "RGB Composites",
    "layer": DEFAULT_LAYER,
    "orbit_type": "GEO",
    "fill_gaps": False,
    "gap_fill_lookback_hours": 12,
}
GAP_FILL_LOOKBACK_HOURS = (12, 24)

THEMES = (
    ("all", "All data themes"),
    ("atmospheric_composition", "Atmospheric composition"),
    ("climate", "Climate"),
    ("emergency", "Emergency"),
    ("marine", "Marine"),
    ("weather_monitoring", "Weather monitoring"),
)
THEME_LABELS = dict(THEMES)
THEME_IDS = frozenset(THEME_LABELS)

_SATELLITE_CATEGORY = re.compile(
    r"^view\.Satellite\.(.+?)\.(Channels|Products|RGBs)$"
)
_PRODUCT_TYPE_LABELS = {
    "Channels": "Channels",
    "Products": "Visualized Products",
    "RGBs": "RGB Composites",
}
_SATELLITE_LABELS = {
    "MTG 0DEG": "MTG - 0 Degree",
    "MSG 0DEG": "MSG - 0 Degree",
    "MSG IODC": "MSG - IODC",
    "MSG RSS": "MSG - RSS",
    "Sentinel 3A": "Sentinel-3A",
    "Sentinel 3B": "Sentinel-3B",
    "Sentinel 3AB": "Sentinel-3 (A + B)",
}
_FALLBACK_ITEM = {
    "id": "EO:EUM:DAT:0913",
    "label": "GeoColour RGB - MTG - 0 degree",
    "layer": DEFAULT_LAYER,
    "satellite": "MTG - 0 Degree",
    "mission": "MTG",
    "product_type": "RGB Composites",
    "themes": ("weather_monitoring",),
    "orbit_type": "GEO",
    "single_overpass": False,
}


class EumetsatCatalogueError(RuntimeError):
    pass


def _read_limited(response, limit=MAX_CATALOGUE_BYTES):
    length = response.headers.get("Content-Length")
    if length:
        try:
            if int(length) > limit:
                raise EumetsatCatalogueError("EUMETSAT catalogue response is too large.")
        except ValueError:
            pass
    data = response.read(limit + 1)
    if len(data) > limit:
        raise EumetsatCatalogueError("EUMETSAT catalogue response is too large.")
    return data


def _themes_for(categories):
    categories = set(categories)
    result = []
    if {
        "view.Theme.Atmospheric Composition",
        "theme.par.Atmospheric_Composition",
    } & categories:
        result.append("atmospheric_composition")
    if "theme.par.Thematic_Climate_Data_Record" in categories:
        result.append("climate")
    if {"theme.par.Fire", "theme.par.Nowcasting"} & categories:
        result.append("emergency")
    if {"view.Theme.Ocean", "theme.par.Ocean"} & categories:
        result.append("marine")
    if {"view.Theme.Weather", "theme.par.Weather"} & categories:
        result.append("weather_monitoring")
    return tuple(result)


def _mission_label(source, satellite_category):
    """Return the official mission family represented by a viewer category."""
    values = source.get("satellite", ())
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, (list, tuple)):
        values = ()
    missions = []
    for value in values:
        value = str(value).strip()
        if value and value not in missions:
            missions.append(value)
    if not missions:
        if satellite_category.startswith(("Sentinel 3", "Sentinel-3")):
            missions = ["Sentinel-3"]
        elif satellite_category.startswith("Metop"):
            missions = ["Metop"]
        elif satellite_category.startswith("MTG"):
            missions = ["MTG"]
        elif satellite_category.startswith("MSG"):
            missions = ["MSG"]
        else:
            missions = [satellite_category]
    return " + ".join(missions)


def infer_orbit_type(layer, satellite=""):
    layer = str(layer).strip()
    satellite = str(satellite).strip()
    if (
        layer.startswith("eps:m0")
        or layer.startswith("copernicus:sentinel3")
        or satellite.startswith(("Metop", "Sentinel-3", "Sentinel 3"))
    ):
        return "LEO"
    return "GEO"


def supports_gap_fill(layer, orbit_type="", label=""):
    """Conservatively identify non-accumulated LEO single-overpass layers."""
    layer = str(layer).strip()
    orbit_type = str(orbit_type).strip().upper()
    known_leo_layer = (
        layer.startswith("eps:m0")
        or layer.startswith("copernicus:sentinel3a_")
        or layer.startswith("copernicus:sentinel3b_")
    )
    if orbit_type and orbit_type != "LEO":
        return False
    if not orbit_type and not known_leo_layer:
        return False
    text = f"{label} {layer}".casefold()
    exclusions = (
        "accumulat", "daily_", "daily ", "climate", "blended",
        "orbital track", "orbital_track", "global l3c", "sea ice concentration",
        "fire radiative", "_frp",
    )
    return not any(value in text for value in exclusions)


def build_catalogue(decorations, search_response):
    """Combine official catalogue responses into stable selectable records."""
    if not isinstance(decorations, dict) or not isinstance(search_response, dict):
        raise EumetsatCatalogueError("EUMETSAT catalogue data has an invalid format.")
    hits = search_response.get("hits", {}).get("hits", ())
    if not isinstance(hits, list):
        raise EumetsatCatalogueError("EUMETSAT Product Navigator response is invalid.")
    items = []
    for hit in hits:
        source = hit.get("_source", {}) if isinstance(hit, dict) else {}
        product_id = str(source.get("id", "")).strip()
        decoration = decorations.get(product_id)
        if not product_id or not isinstance(decoration, dict):
            continue
        wms = decoration.get("wmsConfig", {})
        layer = str(wms.get("layer", "")).strip() if isinstance(wms, dict) else ""
        if not layer:
            continue
        categories = source.get("hierarchyLevelName", ())
        if isinstance(categories, str):
            categories = (categories,)
        if not isinstance(categories, (list, tuple)):
            continue
        satellite_categories = []
        for category in categories:
            match = _SATELLITE_CATEGORY.fullmatch(str(category))
            if match:
                satellite_categories.append(match.groups())
        if not satellite_categories:
            continue
        label = str(source.get("datasetTitle", "")).strip() or layer
        themes = _themes_for(categories)
        for satellite, product_type in satellite_categories:
            mission = _mission_label(source, satellite)
            satellite_label = _SATELLITE_LABELS.get(satellite, satellite)
            orbit_type = str(source.get("orbitType", "")).strip().upper()
            if orbit_type not in {"GEO", "LEO"}:
                orbit_type = infer_orbit_type(layer, satellite_label)
            items.append({
                "id": product_id,
                "label": label,
                "layer": layer,
                "satellite": satellite_label,
                "mission": mission,
                "product_type": _PRODUCT_TYPE_LABELS[product_type],
                "themes": themes,
                "orbit_type": orbit_type,
                "single_overpass": supports_gap_fill(
                    layer, orbit_type, label
                ),
            })
    unique = {}
    for item in items:
        key = (
            item["layer"], item["satellite"], item["mission"],
            item["product_type"],
        )
        unique.setdefault(key, item)
    result = sorted(
        unique.values(),
        key=lambda item: (
            item["satellite"].casefold(),
            item["mission"].casefold(),
            item["product_type"].casefold(),
            item["label"].casefold(),
            item["layer"],
        ),
    )
    if not result:
        raise EumetsatCatalogueError("EUMETSAT returned no selectable viewer products.")
    return result


def normalize_profile(profile):
    if profile is None:
        profile = {}
    if not isinstance(profile, dict):
        raise ValueError("EUMETSAT source settings must be a table.")
    result = dict(DEFAULT_PROFILE)
    for field in ("theme", "satellite", "product_type", "layer"):
        value = profile.get(field, result[field])
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ValueError(f"Invalid EUMETSAT {field} selection.")
        result[field] = value.strip()
    mission = profile.get("mission") or _mission_label({}, result["satellite"])
    if not isinstance(mission, str) or not mission.strip() or len(mission) > 500:
        raise ValueError("Invalid EUMETSAT mission selection.")
    result["mission"] = mission.strip()
    orbit_type = profile.get("orbit_type") or infer_orbit_type(
        result["layer"], result["satellite"]
    )
    if not isinstance(orbit_type, str) or orbit_type.strip().upper() not in {"GEO", "LEO"}:
        raise ValueError("Invalid EUMETSAT orbit_type selection.")
    result["orbit_type"] = orbit_type.strip().upper()
    fill_gaps = profile.get("fill_gaps", result["fill_gaps"])
    if type(fill_gaps) is not bool:
        raise ValueError("EUMETSAT fill_gaps must be true or false.")
    result["fill_gaps"] = fill_gaps
    lookback = profile.get(
        "gap_fill_lookback_hours", result["gap_fill_lookback_hours"]
    )
    if type(lookback) is not int or lookback not in GAP_FILL_LOOKBACK_HOURS:
        raise ValueError("EUMETSAT gap fill lookback must be 12 or 24 hours.")
    result["gap_fill_lookback_hours"] = lookback
    if result["theme"] not in THEME_IDS:
        result["theme"] = "all"
    if not supports_gap_fill(result["layer"], result["orbit_type"]):
        result["fill_gaps"] = False
    return result


class EumetsatCatalogueClient:
    def __init__(self, timeout=90, user_agent="MarbleScape", cached_catalogue=None,
                 on_catalogue=None, retries=2):
        self.timeout = float(timeout)
        self.user_agent = str(user_agent)
        self._lock = threading.Lock()
        self._catalogue = deepcopy(cached_catalogue) if cached_catalogue else None
        self._on_catalogue = on_catalogue
        self.retries = max(1, min(9, int(retries)))
        self.catalogue_warning = ""

    def _json_get(self, url):
        cache = getattr(self, "metadata_cache", None)
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if cache:
            headers.update(cache.headers(url))
        request = Request(
            url,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = _read_limited(response)
                if cache:
                    cache.store(url, body, dict(response.headers.items()))
        except HTTPError as exc:
            saved = cache.response(url, MAX_CATALOGUE_BYTES) if exc.code == 304 and cache else None
            exc.close()
            if saved is None:
                raise
            body, _headers = saved
        return json.loads(body.decode("utf-8-sig"))

    def _search(self):
        body = json.dumps({
            "from": 0,
            "size": 300,
            "_source": [
                "id", "datasetTitle", "hierarchyLevelName", "orbitType", "satellite",
            ],
            "sort": ["datasetTitle.raw"],
            "query": {"match_all": {}},
        }, separators=(",", ":")).encode("utf-8")
        request = Request(
            PRODUCT_SEARCH_URL,
            data=body,
            method="POST",
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(_read_limited(response).decode("utf-8-sig"))

    def catalogue(self, refresh=False):
        with self._lock:
            if self._catalogue is not None and not refresh:
                return deepcopy(self._catalogue)
            problem = None
            catalogue = None
            for _attempt in range(self.retries + 1 if refresh else 1):
                try:
                    catalogue = build_catalogue(self._json_get(DECORATIONS_URL), self._search())
                    break
                except Exception as exc:
                    problem = exc
            if catalogue is None:
                message = f"EUMETSAT catalogue is currently unavailable: {problem}"
                if self._catalogue is not None:
                    self.catalogue_warning = message + "; using cached catalogue data."
                    return deepcopy(self._catalogue)
                if isinstance(problem, EumetsatCatalogueError):
                    raise problem
                raise EumetsatCatalogueError(message) from problem
            changed = catalogue != self._catalogue
            if changed:
                self._catalogue = catalogue
            self.catalogue_warning = ""
            if changed and self._on_catalogue:
                try:
                    self._on_catalogue(catalogue)
                except Exception:
                    pass
            return deepcopy(catalogue)


def _preferred(items, current_layer=None):
    if current_layer:
        matched = next((item for item in items if item["layer"] == current_layer), None)
        if matched:
            return matched
    def rank(item):
        name = re.sub(r"[^a-z0-9]+", "", (item["label"] + " " + item["layer"]).casefold())
        if "truecolorreproduction" in name or "truecolourreproduction" in name:
            return 0
        if any(value in name for value in ("geocolour", "geocolor", "truecolour", "truecolor")):
            return 1
        if "naturalcolour" in name or "naturalcolor" in name:
            return 2
        if "olcil1rgb" in name or "olcil1brgb" in name:
            return 3
        return 4
    return min(items, key=rank) if items else None


class EumetsatSettings:
    """Dependent theme, mission, product type and WMS layer controls."""

    def __init__(self, parent, profile, timeout=90, user_agent="MarbleScape",
                 on_change=None, client=None, auto_refresh=True):
        self.frame = ttk.Frame(parent)
        configure_source_columns(self.frame)
        self._profile = normalize_profile(profile)
        self._on_change = on_change
        self._client = client or EumetsatCatalogueClient(timeout, user_agent)
        fallback = dict(_FALLBACK_ITEM)
        fallback.update(
            label=self._profile["layer"],
            layer=self._profile["layer"],
            satellite=self._profile["satellite"],
            mission=self._profile["mission"],
            product_type=self._profile["product_type"],
            themes=(self._profile["theme"],)
            if self._profile["theme"] != "all" else (),
            orbit_type=self._profile["orbit_type"],
            single_overpass=supports_gap_fill(
                self._profile["layer"], self._profile["orbit_type"]
            ),
        )
        self._catalogue = [fallback]
        self._queue = queue.Queue()
        self._closed = False
        self._generation = 0
        self._after_id = None
        self._theme_var = tk.StringVar(self.frame)
        self._satellite_var = tk.StringVar(self.frame)
        self._mission_var = tk.StringVar(self.frame)
        self._type_var = tk.StringVar(self.frame)
        self._layer_var = tk.StringVar(self.frame)
        self._fill_gaps_var = tk.BooleanVar(
            self.frame, value=self._profile["fill_gaps"]
        )
        self._lookback_var = tk.StringVar(self.frame)
        self._gap_fill_hint_var = tk.StringVar(self.frame)
        self._status_var = tk.StringVar(self.frame)
        self._theme_combo = self._combo(0, "Data theme", self._theme_var)
        self._satellite_combo = self._combo(
            1, "Satellite / service", self._satellite_var
        )
        self._mission_combo = self._combo(2, "Mission", self._mission_var)
        self._type_combo = self._combo(3, "Product type", self._type_var)
        self._layer_combo = self._combo(4, "Product / layer", self._layer_var)
        self.view_frame = ttk.Frame(self.frame)
        self.view_frame.grid(row=5, column=0, columnspan=2, pady=(2, 0), sticky="ew")
        configure_source_columns(self.view_frame)
        self._theme_combo.bind("<<ComboboxSelected>>", self._theme_changed)
        self._satellite_combo.bind("<<ComboboxSelected>>", self._satellite_changed)
        self._mission_combo.bind("<<ComboboxSelected>>", self._mission_changed)
        self._type_combo.bind("<<ComboboxSelected>>", self._type_changed)
        self._layer_combo.bind("<<ComboboxSelected>>", self._layer_changed)
        self._fill_gaps_check = ttk.Checkbutton(
            self.frame,
            text="Fill gaps with earlier imagery",
            variable=self._fill_gaps_var,
            command=self._fill_gaps_changed,
        )
        self._fill_gaps_check.grid(
            row=6, column=0, columnspan=2, pady=(5, 2), sticky="w"
        )
        self._lookback_combo = self._combo(
            7, "Maximum lookback", self._lookback_var
        )
        self._lookback_combo.configure(
            values=tuple(f"{value} hours" for value in GAP_FILL_LOOKBACK_HOURS)
        )
        self._lookback_combo.bind("<<ComboboxSelected>>", self._lookback_changed)
        ttk.Label(
            self.frame, textvariable=self._gap_fill_hint_var,
            wraplength=600, justify="left",
        ).grid(row=8, column=0, columnspan=2, pady=(0, 2), sticky="w")
        self._refresh_button = ttk.Button(
            self.frame, text="Refresh EUMETSAT catalogue",
            command=lambda: self.refresh(True),
        )
        self._refresh_button.grid(row=9, column=0, pady=(5, 2), sticky="w")
        ttk.Label(
            self.frame,
            text=("Theme, service, mission, product type and layer combinations come "
                  "from the public EUMETSAT catalogue. Projection is independent "
                  "because the viewer reprojects the selected layer."),
            wraplength=480, justify="left",
        ).grid(row=9, column=1, padx=(8, 0), pady=(5, 2), sticky="w")
        ttk.Label(
            self.frame, textvariable=self._status_var,
            wraplength=600, justify="left",
        ).grid(row=10, column=0, columnspan=2, pady=(2, 4), sticky="w")
        self._activity = CatalogueActivity(self.frame, row=11)
        self.frame.bind("<Destroy>", self._destroyed, add="+")
        self._populate(preserve=True)
        if auto_refresh:
            self.refresh(False)
        self._after_id = self.frame.after(100, self._poll)

    def _combo(self, row, label, variable):
        ttk.Label(self.frame, text=label).grid(
            row=row, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        combo = ttk.Combobox(
            self.frame, textvariable=variable, state="readonly", width=SOURCE_COMBO_WIDTH
        )
        combo.grid(row=row, column=1, pady=3, sticky="ew")
        return combo

    @staticmethod
    def _layer_label(item):
        return f"{item['label']} [{item['layer']}]"

    def _matching(self, theme=None, satellite=None, mission=None, product_type=None):
        theme = theme or self._profile["theme"]
        return [
            item for item in self._catalogue
            if (theme == "all" or theme in item["themes"])
            and (satellite is None or item["satellite"] == satellite)
            and (mission is None or item["mission"] == mission)
            and (product_type is None or item["product_type"] == product_type)
        ]

    def _select_item(self, item):
        self._profile.update(
            satellite=item["satellite"],
            mission=item["mission"],
            product_type=item["product_type"],
            layer=item["layer"],
            orbit_type=item["orbit_type"] or self._profile.get("orbit_type", "GEO"),
        )

    def _update_gap_fill_controls(self, selected):
        eligible = bool(selected.get("single_overpass"))
        if not eligible:
            self._profile["fill_gaps"] = False
            self._fill_gaps_var.set(False)
            self._fill_gaps_check.state(["disabled"])
            self._lookback_combo.configure(state="disabled")
            self._gap_fill_hint_var.set(
                "Gap filling is unavailable for geostationary, accumulated, daily, "
                "climate, track and sparse event layers."
            )
            return
        self._fill_gaps_check.state(["!disabled"])
        self._fill_gaps_var.set(self._profile["fill_gaps"])
        self._lookback_combo.configure(
            state="readonly" if self._profile["fill_gaps"] else "disabled"
        )
        self._gap_fill_hint_var.set(
            "Only transparent No Data pixels are filled from earlier passes; newest "
            "pixels remain on top. The result can contain multiple acquisition times."
        )

    def _populate(self, preserve=False):
        if preserve:
            candidates = [
                item for item in self._catalogue
                if item["layer"] == self._profile["layer"]
            ]
            selected = next(
                (item for item in candidates
                 if item["satellite"] == self._profile["satellite"]
                 and item["mission"] == self._profile["mission"]
                 and item["product_type"] == self._profile["product_type"]),
                _preferred(candidates, self._profile["layer"]),
            )
            if selected is not None:
                self._select_item(selected)
                if (
                    self._profile["theme"] != "all"
                    and self._profile["theme"] not in selected["themes"]
                ):
                    self._profile["theme"] = (
                        selected["themes"][0] if selected["themes"] else "all"
                    )
        if self._profile["theme"] not in THEME_IDS:
            self._profile["theme"] = "all"
        theme_label = THEME_LABELS[self._profile["theme"]]
        self._theme_combo.configure(values=[label for _key, label in THEMES])
        self._theme_var.set(theme_label)

        themed = self._matching()
        if not themed:
            self._profile["theme"] = "all"
            self._theme_var.set(THEME_LABELS["all"])
            themed = self._matching()
        satellites = sorted({item["satellite"] for item in themed}, key=str.casefold)
        if self._profile["satellite"] not in satellites:
            preferred = _preferred(themed, self._profile["layer"] if preserve else None)
            self._profile["satellite"] = preferred["satellite"] if preferred else satellites[0]
        self._satellite_combo.configure(values=satellites)
        self._satellite_var.set(self._profile["satellite"])

        service = self._matching(satellite=self._profile["satellite"])
        missions = sorted({item["mission"] for item in service}, key=str.casefold)
        if self._profile["mission"] not in missions:
            preferred = _preferred(service, self._profile["layer"] if preserve else None)
            self._profile["mission"] = preferred["mission"] if preferred else missions[0]
        self._mission_combo.configure(values=missions)
        self._mission_var.set(self._profile["mission"])

        mission = self._matching(
            satellite=self._profile["satellite"], mission=self._profile["mission"]
        )
        types = sorted({item["product_type"] for item in mission}, key=str.casefold)
        if self._profile["product_type"] not in types:
            preferred = _preferred(mission, self._profile["layer"] if preserve else None)
            self._profile["product_type"] = preferred["product_type"] if preferred else types[0]
        self._type_combo.configure(values=types)
        self._type_var.set(self._profile["product_type"])

        products = self._matching(
            satellite=self._profile["satellite"],
            mission=self._profile["mission"],
            product_type=self._profile["product_type"],
        )
        selected = _preferred(products, self._profile["layer"] if preserve else None)
        if selected is None:
            raise EumetsatCatalogueError("No EUMETSAT products match this selection.")
        self._select_item(selected)
        labels = [self._layer_label(item) for item in products]
        self._layer_combo.configure(values=labels)
        self._layer_var.set(self._layer_label(selected))
        self._lookback_var.set(f"{self._profile['gap_fill_lookback_hours']} hours")
        self._update_gap_fill_controls(selected)

    def _emit(self):
        if self._on_change:
            self._on_change()

    def _theme_changed(self, _event=None):
        self._profile["theme"] = next(
            key for key, label in THEMES if label == self._theme_var.get()
        )
        self._profile.update(satellite="", mission="", product_type="")
        self._populate(preserve=False)
        self._emit()

    def _satellite_changed(self, _event=None):
        self._profile["satellite"] = self._satellite_var.get()
        self._profile.update(mission="", product_type="")
        self._populate(preserve=False)
        self._emit()

    def _mission_changed(self, _event=None):
        self._profile["mission"] = self._mission_var.get()
        self._profile["product_type"] = ""
        self._populate(preserve=False)
        self._emit()

    def _type_changed(self, _event=None):
        self._profile["product_type"] = self._type_var.get()
        self._populate(preserve=False)
        self._emit()

    def _layer_changed(self, _event=None):
        selected = next(
            (item for item in self._matching(
                satellite=self._profile["satellite"],
                mission=self._profile["mission"],
                product_type=self._profile["product_type"],
            ) if self._layer_label(item) == self._layer_var.get()),
            None,
        )
        if selected:
            self._select_item(selected)
            self._update_gap_fill_controls(selected)
            self._emit()

    def _fill_gaps_changed(self):
        self._profile["fill_gaps"] = bool(self._fill_gaps_var.get())
        self._lookback_combo.configure(
            state="readonly" if self._profile["fill_gaps"] else "disabled"
        )
        self._emit()

    def _lookback_changed(self, _event=None):
        text = self._lookback_var.get().partition(" ")[0]
        self._profile["gap_fill_lookback_hours"] = int(text)
        self._emit()

    def refresh(self, refresh=False):
        if self._closed:
            return
        self._generation += 1
        generation = self._generation
        self._refresh_button.state(["disabled"])
        self._status_var.set("Loading EUMETSAT themes, missions and products...")
        self._activity.start()
        client, results = self._client, self._queue

        def worker():
            try:
                result = client.catalogue(refresh=refresh)
                results.put((generation, result, None))
            except Exception as exc:
                results.put((generation, None, str(exc)))

        threading.Thread(
            target=worker, name="MarbleScape-EUMETSAT-catalogue", daemon=True
        ).start()

    def _poll(self):
        self._after_id = None
        if self._closed:
            return
        while True:
            try:
                generation, result, error = self._queue.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue
            self._refresh_button.state(["!disabled"])
            if error:
                self._activity.finish(False)
                self._status_var.set(
                    "Catalogue unavailable; the saved EUMETSAT layer remains usable. " + error
                )
                continue
            self._catalogue = result
            try:
                self._populate(preserve=True)
                warning = str(getattr(self._client, "catalogue_warning", "") or "").strip()
                self._status_var.set(
                    f"EUMETSAT catalogue loaded: {len(result)} selectable products."
                    + (("\nCatalogue notice: " + warning) if warning else "")
                )
                self._activity.finish(not warning)
                self._emit()
            except Exception as exc:
                self._activity.finish(False)
                self._status_var.set("Unable to display the EUMETSAT catalogue: " + str(exc))
        self._after_id = self.frame.after(100, self._poll)

    def set_profile(self, profile, layer=None):
        self._profile = normalize_profile(profile)
        if layer:
            self._profile["layer"] = str(layer)
        if not any(item["layer"] == self._profile["layer"] for item in self._catalogue):
            self._catalogue.append({
                "id": "saved",
                "label": self._profile["layer"],
                "layer": self._profile["layer"],
                "satellite": self._profile["satellite"],
                "mission": self._profile["mission"],
                "product_type": self._profile["product_type"],
                "themes": (self._profile["theme"],)
                if self._profile["theme"] != "all" else (),
                "orbit_type": self._profile["orbit_type"],
                "single_overpass": supports_gap_fill(
                    self._profile["layer"], self._profile["orbit_type"]
                ),
            })
        self._populate(preserve=True)

    def select_layer(self, layer):
        layer = str(layer).strip()
        candidates = [item for item in self._catalogue if item["layer"] == layer]
        selected = next(
            (item for item in candidates
             if item["satellite"] == self._profile["satellite"]
             and item["mission"] == self._profile["mission"]),
            _preferred(candidates, layer),
        )
        if selected is None:
            # Preserve a valid custom/temporarily unavailable WMS layer.
            self._profile["layer"] = layer
            return
        self._profile.update(
            theme=(selected["themes"][0] if selected["themes"] else "all"),
            satellite=selected["satellite"],
            mission=selected["mission"],
            product_type=selected["product_type"],
            layer=selected["layer"],
            orbit_type=selected["orbit_type"] or self._profile["orbit_type"],
        )
        self._populate(preserve=True)
        self._emit()

    def get_profile(self):
        self._profile["fill_gaps"] = bool(self._fill_gaps_var.get())
        return normalize_profile(self._profile)

    def _destroyed(self, event):
        if event.widget is self.frame:
            self.close()

    def close(self):
        self._closed = True
        self._generation += 1
        self._activity.close()
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
