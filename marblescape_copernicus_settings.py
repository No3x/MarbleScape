"""Tk controls for Copernicus Browser selections and live acquisition dates."""

import datetime as dt
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

from marblescape_catalogue_activity import CatalogueActivity
from marblescape_source_layout import SOURCE_COMBO_WIDTH, configure_source_columns

from marblescape_copernicus import (
    ACCOUNT_SETTINGS_URL,
    CopernicusClient,
    DEFAULT_PROFILE,
    LOOKBACK_DAYS,
    catalogue_revision,
    get_product,
    highlights,
    map_zooms,
    map_zooms_for_view,
    missions,
    normalize_profile,
    products,
    supports_cloud_filter,
    themes,
)


LATEST_LABEL = "Latest available"
CUSTOM_HIGHLIGHT_LABEL = "Custom location / no highlight"
COVERAGE_LABELS = {
    "Single latest acquisition": "single",
    "Fill areas without image data with black": "black",
    "Fill gaps with earlier imagery (use latest imagery of valid lookback)": "fill_gaps",
}
LOOKBACK_PERIODS = {
    30: "1 month", 45: "1.5 months", 60: "2 months", 90: "3 months",
    120: "4 months", 180: "6 months", 270: "9 months", 365: "1 year",
    550: "1.5 years", 730: "2 years", 920: "2.5 years", 1095: "3 years",
}
LOOKBACK_LABELS = {
    (f"{days} days ({LOOKBACK_PERIODS[days]})"
     if days in LOOKBACK_PERIODS else f"{days} days"): days
    for days in LOOKBACK_DAYS
}
CLOUD_HINT = (
    "Inclusive tile limit: 20% accepts 20% or less; 0% may find no acquisition. "
    "The estimate is not for the exact map view. 100% allows all acquisitions. "
    "Available only for supported optical layers."
)


def _natural_layer_rank(layer):
    name = re.sub(r"[^a-z0-9]+", "", str(layer.get("name", "")).casefold())
    if name == "truecolor" or name == "truecolour":
        return 0
    if "truecolor" in name or "truecolour" in name:
        return 1
    if "naturalcolor" in name or "naturalcolour" in name:
        return 2
    return 3


def _natural_product_rank(product):
    rank = min((_natural_layer_rank(layer) for layer in product["layers"]), default=3)
    data_types = {layer.get("data_type") for layer in product["layers"]}
    order = ("sentinel-2-l2a", "sentinel-2-l1c", "sentinel-3-olci", "landsat-ot-l1")
    return rank, min((order.index(item) for item in data_types if item in order), default=len(order))


class CopernicusSettings:
    def __init__(self, parent, profile=None, auth=None, timeout=90,
                 user_agent="MarbleScape", output_size=(1920, 1080), on_change=None,
                 catalogue_client=None, catalogue_retries=2):
        self.frame = ttk.Frame(parent)
        configure_source_columns(self.frame)
        self._timeout = timeout
        self._user_agent = user_agent
        self._output_size = output_size
        self._on_change = on_change
        self._catalogue_client = catalogue_client
        self._catalogue_retries = max(1, min(9, int(catalogue_retries)))
        self._closed = False
        self._generation = 0
        self._results = queue.Queue()
        self._usage_results = queue.Queue()
        self._usage_generation = 0
        self._after_id = None
        self._usage_after_id = None
        self._updating = True
        auth = auth if isinstance(auth, dict) else {}

        self._mission_var = tk.StringVar(self.frame)
        self._configuration_var = tk.StringVar(self.frame)
        self._product_var = tk.StringVar(self.frame)
        self._layer_var = tk.StringVar(self.frame)
        self._highlight_var = tk.StringVar(self.frame)
        self._date_var = tk.StringVar(self.frame)
        self._latitude_var = tk.StringVar(self.frame)
        self._longitude_var = tk.StringVar(self.frame)
        self._zoom_var = tk.StringVar(self.frame)
        self._labels_var = tk.BooleanVar(self.frame)
        self._coverage_var = tk.StringVar(self.frame)
        self._lookback_var = tk.StringVar(self.frame)
        self._cloud_var = tk.IntVar(self.frame)
        self._cloud_value_var = tk.StringVar(self.frame)
        self._brightness_var = tk.IntVar(self.frame)
        self._brightness_value_var = tk.StringVar(self.frame)
        self._client_id_var = tk.StringVar(self.frame, str(auth.get("client_id", "")))
        self._secret_var = tk.StringVar(self.frame, str(auth.get("client_secret", "")))
        self._status_var = tk.StringVar(self.frame)
        self._credits_status_var = tk.StringVar(self.frame, "Credits not loaded.")
        self._credits_role_var = tk.StringVar(self.frame, "Role: -")
        self._credits_since_var = tk.StringVar(self.frame)
        self._credits_values = {
            (category, field): tk.StringVar(self.frame, "-")
            for category in ("processingUnitsMonthly", "requestsMonthly")
            for field in ("configuration", "consumed", "remaining")
        }

        self._theme_by_label = {}
        self._product_by_label = {}
        self._layer_by_label = {}
        self._highlight_by_label = {}
        self._date_by_label = {LATEST_LABEL: "latest"}

        self._mission_combo = self._combo(0, "Mission / dataset", self._mission_var)
        self._configuration_combo = self._combo(1, "Configuration", self._configuration_var)
        self._product_combo = self._combo(2, "Product", self._product_var)
        self._layer_combo = self._combo(3, "Layer", self._layer_var)
        self._highlight_combo = self._combo(4, "Highlight", self._highlight_var)
        self._date_combo = self._combo(5, "Date / time", self._date_var)
        self._date_label = self.frame.grid_slaves(row=5, column=0)[0]
        self._latitude_entry = self._entry(6, "Latitude", self._latitude_var)
        self._longitude_entry = self._entry(7, "Longitude", self._longitude_var)
        self._zoom_combo = self._combo(8, "Map zoom", self._zoom_var)
        self._coverage_combo = self._combo(
            9, "Coverage mode", self._coverage_var, tuple(COVERAGE_LABELS)
        )
        self._lookback_combo = self._combo(
            10, "Maximum lookback", self._lookback_var, tuple(LOOKBACK_LABELS)
        )
        ttk.Label(self.frame, text="Maximum cloud cover").grid(
            row=11, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        cloud_frame = ttk.Frame(self.frame)
        cloud_frame.grid(row=11, column=1, sticky="ew")
        cloud_frame.columnconfigure(0, weight=1)
        self._cloud_scale = tk.Scale(
            cloud_frame, from_=0, to=100, resolution=5, orient="horizontal",
            showvalue=False, variable=self._cloud_var, command=self._cloud_changed,
            highlightthickness=0,
        )
        self._cloud_scale.grid(row=0, column=0, sticky="ew")
        ttk.Label(cloud_frame, textvariable=self._cloud_value_var, width=5).grid(
            row=0, column=1, padx=(6, 0), sticky="e"
        )
        self._cloud_hint = ttk.Label(
            self.frame, wraplength=560, justify="left", text=CLOUD_HINT,
        )
        self._cloud_hint.grid(row=12, column=0, columnspan=2, pady=(0, 3), sticky="w")
        ttk.Label(self.frame, text="Mosaic brightness").grid(
            row=13, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        brightness_frame = ttk.Frame(self.frame)
        brightness_frame.grid(row=13, column=1, sticky="ew")
        brightness_frame.columnconfigure(0, weight=1)
        self._brightness_scale = tk.Scale(
            brightness_frame, from_=25, to=200, resolution=5, orient="horizontal",
            showvalue=False, variable=self._brightness_var, command=self._brightness_changed,
            highlightthickness=0,
        )
        self._brightness_scale.grid(row=0, column=0, sticky="ew")
        ttk.Label(brightness_frame, textvariable=self._brightness_value_var, width=5).grid(
            row=0, column=1, padx=(6, 0), sticky="e"
        )
        ttk.Checkbutton(
            self.frame,
            text="Map labels (places, roads, POIs and boundaries)",
            variable=self._labels_var,
            command=self._changed,
        ).grid(row=14, column=0, columnspan=2, pady=3, sticky="w")

        auth_frame = ttk.LabelFrame(self.frame, text="Copernicus Data Space access", padding=6)
        auth_frame.grid(row=15, column=0, columnspan=2, pady=(7, 3), sticky="ew")
        auth_frame.columnconfigure(1, weight=1)
        ttk.Label(auth_frame, text="OAuth Client ID").grid(
            row=0, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        ttk.Entry(auth_frame, textvariable=self._client_id_var).grid(
            row=0, column=1, pady=3, sticky="ew"
        )
        ttk.Label(auth_frame, text="OAuth Client secret").grid(
            row=1, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        ttk.Entry(auth_frame, textvariable=self._secret_var, show="●").grid(
            row=1, column=1, pady=3, sticky="ew"
        )
        credits_frame = ttk.LabelFrame(auth_frame, text="Credits", padding=6)
        credits_frame.grid(row=2, column=0, columnspan=2, pady=(7, 3), sticky="ew")
        credits_frame.columnconfigure((1, 2, 3), weight=1)
        ttk.Label(credits_frame, textvariable=self._credits_role_var).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        self._credits_refresh_button = ttk.Button(
            credits_frame, text="Refresh credits", command=self.refresh_usage
        )
        self._credits_refresh_button.grid(row=0, column=3, sticky="e")
        for column, variable in enumerate(("Configured", self._credits_since_var, "Remaining"), 1):
            if isinstance(variable, str):
                ttk.Label(credits_frame, text=variable).grid(row=1, column=column, padx=5)
            else:
                ttk.Label(credits_frame, textvariable=variable).grid(row=1, column=column, padx=5)
        for row, category, label in (
            (2, "processingUnitsMonthly", "Processing units"),
            (3, "requestsMonthly", "Requests"),
        ):
            ttk.Label(credits_frame, text=label).grid(row=row, column=0, sticky="w")
            for column, field in enumerate(("configuration", "consumed", "remaining"), 1):
                ttk.Label(credits_frame, textvariable=self._credits_values[(category, field)]).grid(
                    row=row, column=column, padx=5, sticky="e"
                )
        ttk.Label(credits_frame, textvariable=self._credits_status_var).grid(
            row=4, column=0, columnspan=4, pady=(4, 0), sticky="w"
        )
        self._credits_since_var.set("Consumed since " + dt.date.today().replace(day=1).strftime("%d-%m-%Y"))
        ttk.Label(
            auth_frame,
            text=("Create a free CDSE OAuth client under User Settings > OAuth clients. "
                  "No Planet subscription or Configuration Instance is required. On Windows "
                  "the secret is saved encrypted for the current user."),
            wraplength=560,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, pady=(3, 0), sticky="w")
        self._oauth_button = ttk.Button(
            auth_frame, text="Open free OAuth client settings", command=self._open_oauth_settings
        )
        self._oauth_button.grid(row=4, column=0, columnspan=2, pady=(6, 0), sticky="w")

        action_frame = ttk.Frame(self.frame)
        action_frame.grid(row=16, column=0, columnspan=2, pady=(5, 0), sticky="ew")
        self._refresh_button = ttk.Button(
            action_frame, text="Refresh Copernicus catalogue", command=self.refresh_dates
        )
        self._refresh_button.grid(row=0, column=0, padx=(0, 8), sticky="w")
        ttk.Label(
            action_frame,
            text="Loads all available acquisition dates for the selected location; this can take a while.",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(
            self.frame, textvariable=self._status_var, wraplength=570, justify="left"
        ).grid(row=17, column=0, columnspan=2, pady=(4, 0), sticky="w")
        self._activity = CatalogueActivity(self.frame, row=18)

        self._configuration_combo.bind("<<ComboboxSelected>>", self._select_configuration)
        self._mission_combo.bind("<<ComboboxSelected>>", self._select_mission)
        self._product_combo.bind("<<ComboboxSelected>>", self._select_product)
        self._layer_combo.bind("<<ComboboxSelected>>", self._select_layer)
        self._highlight_combo.bind("<<ComboboxSelected>>", self._select_highlight)
        self._date_combo.bind("<<ComboboxSelected>>", self._custom_date_changed)
        self._zoom_combo.bind("<<ComboboxSelected>>", self._custom_location_changed)
        self._coverage_combo.bind("<<ComboboxSelected>>", self._select_coverage)
        self._lookback_combo.bind("<<ComboboxSelected>>", self._changed)
        self._latitude_var.trace_add("write", self._custom_location_changed)
        self._longitude_var.trace_add("write", self._custom_location_changed)
        self._client_id_var.trace_add("write", self._credentials_changed)
        self._secret_var.trace_add("write", self._credentials_changed)
        self.frame.bind("<Destroy>", self._destroyed, add="+")

        self.set_profile(profile or DEFAULT_PROFILE)
        self._updating = False
        self._after_id = self.frame.after(100, self._poll)
        if self._client_id_var.get() and self._secret_var.get():
            self._usage_after_id = self.frame.after(250, self._auto_refresh_usage)

    def _auto_refresh_usage(self):
        self._usage_after_id = None
        self.refresh_usage()

    def _combo(self, row, label, variable, values=()):
        ttk.Label(self.frame, text=label).grid(
            row=row, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        widget = ttk.Combobox(
            self.frame, textvariable=variable, values=values,
            state="readonly", width=SOURCE_COMBO_WIDTH,
        )
        widget.grid(row=row, column=1, pady=3, sticky="ew")
        return widget

    def _entry(self, row, label, variable):
        ttk.Label(self.frame, text=label).grid(
            row=row, column=0, padx=(0, 10), pady=3, sticky="w"
        )
        widget = ttk.Entry(self.frame, textvariable=variable)
        widget.grid(row=row, column=1, pady=3, sticky="ew")
        return widget

    @staticmethod
    def _unique_labels(items, name_key="name"):
        result = {}
        for item in items:
            base = str(item[name_key])
            label = base
            if label in result:
                label = f"{base} [{item['id']}]"
            result[label] = item
        return result

    def _label_for_id(self, mapping, item_id):
        return next((label for label, item in mapping.items() if item["id"] == item_id), "")

    def _build_theme_choices(self, selected_id):
        self._theme_by_label = self._unique_labels(themes())
        self._configuration_combo.configure(values=tuple(self._theme_by_label), state="readonly")
        self._configuration_var.set(self._label_for_id(self._theme_by_label, selected_id))

    def _selected_theme(self):
        return self._theme_by_label.get(self._configuration_var.get())

    def _selected_product(self):
        return self._product_by_label.get(self._product_var.get())

    def _selected_layer(self):
        return self._layer_by_label.get(self._layer_var.get())

    def _build_mission_choices(self, selected, prefer_natural=False):
        theme = self._selected_theme()
        choices = missions(theme["id"]) if theme else []
        if choices:
            natural = min(choices, key=lambda mission: min(
                (_natural_product_rank(item) for item in products(theme["id"], mission)),
                default=(3, 4),
            ))
            current_has_natural = selected in choices and min(
                (_natural_product_rank(item)[0] for item in products(theme["id"], selected)),
                default=3,
            ) < 3
            if selected not in choices or (prefer_natural and not current_has_natural):
                selected = natural
        self._mission_combo.configure(values=choices, state="readonly" if choices else "disabled")
        self._mission_var.set(selected if selected in choices else "")

    def _build_product_choices(self, selected_id):
        theme = self._selected_theme()
        items = products(theme["id"], self._mission_var.get()) if theme else []
        self._product_by_label = self._unique_labels(items)
        if selected_id not in {item["id"] for item in items} and items:
            preferred_id = DEFAULT_PROFILE["product"] \
                if theme["id"] == DEFAULT_PROFILE["configuration"] \
                and self._mission_var.get() == DEFAULT_PROFILE["mission"] else ""
            if self._mission_var.get() == "Sentinel-1 Mosaics":
                preferred_id = "MARBLESCAPE::S1-IW-MONTHLY"
            selected_id = preferred_id if preferred_id in {item["id"] for item in items} \
                else min(items, key=_natural_product_rank)["id"]
        self._product_combo.configure(
            values=tuple(self._product_by_label), state="readonly" if items else "disabled"
        )
        self._product_var.set(self._label_for_id(self._product_by_label, selected_id))
        return selected_id

    def _build_layer_choices(self, selected_id):
        product = self._selected_product()
        items = product["layers"] if product else []
        self._layer_by_label = self._unique_labels(items)
        if selected_id not in {item["id"] for item in items} and items:
            selected_id = min(items, key=_natural_layer_rank)["id"]
        self._layer_combo.configure(
            values=tuple(self._layer_by_label), state="readonly" if items else "disabled"
        )
        self._layer_var.set(self._label_for_id(self._layer_by_label, selected_id))
        self._refresh_cloud_control()
        self._refresh_coverage_controls()
        granularity = (self._selected_layer() or {}).get("date_granularity")
        self._date_label.configure(text={
            "year": "Year", "quarter": "Quarter", "month": "Month"
        }.get(granularity, "Date / time"))
        return selected_id

    def _refresh_cloud_control(self):
        layer = self._selected_layer() or {}
        self._cloud_scale.configure(state="normal" if supports_cloud_filter(layer) else "disabled")
        self._brightness_scale.configure(
            state="normal" if "date_granularity" in layer else "disabled"
        )
        if layer.get("date_granularity") == "year":
            hint = (
                "The annual cloudless mosaic covers 2020 and 2021. Its source requires zoom 9 "
                "or higher; smaller Process requests do not remove that resolution limit."
            )
        elif layer.get("date_granularity") in {"month", "quarter"}:
            hint = (
                "This precomputed mosaic has no cloud filter. Wide views use the source's "
                "lower-resolution collection."
            )
        else:
            hint = CLOUD_HINT
        self._cloud_hint.configure(text=hint)

    def _cloud_changed(self, value):
        self._cloud_value_var.set(f"{int(float(value))}%")
        self._changed()

    def _brightness_changed(self, value):
        self._brightness_value_var.set(f"{int(float(value))}%")
        self._changed()

    def _build_zoom_choices(self, selected=None):
        layer = self._selected_layer()
        values = map_zooms(layer)
        try:
            size = self._output_size() if callable(self._output_size) else self._output_size
            values = map_zooms_for_view(layer, self._latitude_var.get(), size) or values
        except ValueError:
            pass
        try:
            selected = int(self._zoom_var.get() if selected is None else selected)
        except (TypeError, ValueError):
            selected = DEFAULT_PROFILE["map_zoom"]
        if selected not in values:
            selected = min(values, key=lambda value: abs(value - selected))
        self._zoom_combo.configure(values=tuple(str(value) for value in values), state="readonly")
        self._zoom_var.set(str(selected))

    def _build_highlight_choices(self, selected_id=""):
        theme = self._selected_theme()
        items = highlights(theme["id"]) if theme else []
        self._highlight_by_label = {CUSTOM_HIGHLIGHT_LABEL: None}
        self._highlight_by_label.update(self._unique_labels(items))
        self._highlight_combo.configure(values=tuple(self._highlight_by_label), state="readonly")
        label = next((label for label, item in self._highlight_by_label.items()
                      if item and item["id"] == selected_id), CUSTOM_HIGHLIGHT_LABEL)
        self._highlight_var.set(label)

    def _set_date_choices(self, selected):
        self._update_date_choices([], selected)

    def _date_choice_label(self, value):
        granularity = (self._selected_layer() or {}).get("date_granularity")
        if granularity is None:
            return value
        date = dt.date.fromisoformat(value)
        if granularity == "year":
            return str(date.year)
        if granularity == "quarter":
            return f"{date.year} Q{(date.month - 1) // 3 + 1}"
        if granularity == "month":
            return date.strftime("%Y-%m")
        return value

    def _update_date_choices(self, dates, selected):
        available = list(dates)
        if selected != "latest" and selected not in available:
            available.insert(0, selected)
        self._date_by_label = {LATEST_LABEL: "latest"}
        for value in available:
            self._date_by_label[self._date_choice_label(value)] = value
        self._date_combo.configure(values=tuple(self._date_by_label), state="readonly")
        selected_label = LATEST_LABEL if selected == "latest" else self._date_choice_label(selected)
        self._date_var.set(selected_label)

    def _set_coverage_controls(self, mode, lookback_days):
        label = next((label for label, value in COVERAGE_LABELS.items() if value == mode), "")
        self._coverage_var.set(label)
        lookback_label = next(
            (label for label, value in LOOKBACK_LABELS.items() if value == lookback_days), ""
        )
        self._lookback_var.set(lookback_label)
        self._refresh_coverage_controls()

    def _refresh_coverage_controls(self):
        layer = self._selected_layer() or {}
        if "date_granularity" in layer:
            self._coverage_var.set(next(label for label, value in COVERAGE_LABELS.items()
                                        if value == "single"))
            self._coverage_combo.configure(state="disabled")
            self._lookback_combo.configure(state="disabled")
        else:
            self._coverage_combo.configure(state="readonly")
            self._lookback_combo.configure(
                state="readonly" if COVERAGE_LABELS.get(self._coverage_var.get()) == "fill_gaps"
                else "disabled"
            )

    def set_profile(self, profile):
        profile = normalize_profile(profile)
        self._generation += 1
        self._activity.finish(False)
        self._refresh_button.configure(state="normal")
        self._updating = True
        try:
            self._build_theme_choices(profile["configuration"])
            self._build_mission_choices(profile["mission"])
            self._build_product_choices(profile["product"])
            self._build_layer_choices(profile["layer"])
            self._latitude_var.set(f"{profile['latitude']:.8f}".rstrip("0").rstrip("."))
            self._longitude_var.set(f"{profile['longitude']:.8f}".rstrip("0").rstrip("."))
            self._build_zoom_choices(profile["map_zoom"])
            self._build_highlight_choices(profile["highlight"])
            self._set_date_choices(profile["date"])
            self._set_coverage_controls(profile["coverage_mode"], profile["lookback_days"])
            self._cloud_var.set(profile["max_cloud_cover"])
            self._cloud_value_var.set(f"{profile['max_cloud_cover']}%")
            self._brightness_var.set(profile["brightness"])
            self._brightness_value_var.set(f"{profile['brightness']}%")
            self._labels_var.set(profile["map_labels"])
            self._status_var.set(
                "Bundled Copernicus Browser catalogue " + catalogue_revision()[:12]
                + ". Latest available imagery is the default."
            )
        finally:
            self._updating = False

    def _current_ids(self):
        theme = self._selected_theme()
        product = self._selected_product()
        layer = self._layer_by_label.get(self._layer_var.get())
        highlight = self._highlight_by_label.get(self._highlight_var.get())
        return theme, product, layer, highlight

    def get_profile(self):
        theme, product, layer, highlight = self._current_ids()
        try:
            latitude = float(self._latitude_var.get().strip())
            longitude = float(self._longitude_var.get().strip())
            map_zoom = int(self._zoom_var.get().strip())
        except ValueError as exc:
            raise ValueError("Copernicus latitude, longitude and map zoom must be valid numbers.") from exc
        value = {
            "configuration": theme["id"] if theme else "",
            "mission": self._mission_var.get(),
            "product": product["id"] if product else "",
            "layer": layer["id"] if layer else "",
            "highlight": highlight["id"] if highlight else "",
            "date": self._date_by_label.get(self._date_var.get(), self._date_var.get()),
            "latitude": latitude,
            "longitude": longitude,
            "map_zoom": map_zoom,
            "map_labels": bool(self._labels_var.get()),
            "coverage_mode": COVERAGE_LABELS.get(self._coverage_var.get(), ""),
            "lookback_days": LOOKBACK_LABELS.get(self._lookback_var.get()),
            "max_cloud_cover": self._cloud_var.get(),
            "brightness": self._brightness_var.get(),
        }
        value = normalize_profile(value)
        size = self._output_size() if callable(self._output_size) else self._output_size
        allowed = map_zooms_for_view(layer, latitude, size)
        if map_zoom not in allowed:
            if not allowed:
                raise ValueError(
                    "The configured Copernicus output cannot fit at any zoom supported by this product."
                )
            raise ValueError(
                f"Copernicus map zoom {map_zoom} cannot contain the configured output at this latitude; "
                f"choose {allowed[0]} through {allowed[-1]}."
            )
        return value

    def _select_coverage(self, _event=None):
        if self._updating:
            return
        self._refresh_coverage_controls()
        self._changed()

    def get_auth(self, require=False):
        client_id = self._client_id_var.get().strip()
        secret = self._secret_var.get()
        if len(client_id) > 500 or len(secret) > 4000:
            raise ValueError("Copernicus OAuth credentials are too long.")
        if require and not (client_id and secret):
            raise ValueError(
                "Enter a Copernicus Sentinel Hub OAuth Client ID and Client secret before selecting Copernicus."
            )
        return {"client_id": client_id, "client_secret": secret}

    def _reset_date_to_latest(self):
        self._set_date_choices("latest")

    def _select_configuration(self, _event=None):
        if self._updating:
            return
        self._updating = True
        try:
            self._build_mission_choices(self._mission_var.get(), prefer_natural=True)
            self._build_product_choices("")
            self._build_layer_choices("")
            self._build_zoom_choices()
            self._build_highlight_choices()
            self._reset_date_to_latest()
        finally:
            self._updating = False
        self._changed()

    def _select_mission(self, _event=None):
        if self._updating:
            return
        self._updating = True
        try:
            self._build_product_choices("")
            self._build_layer_choices("")
            self._build_zoom_choices()
            self._build_highlight_choices()
            self._reset_date_to_latest()
        finally:
            self._updating = False
        self._changed()

    def _select_product(self, _event=None):
        if self._updating:
            return
        self._updating = True
        try:
            self._build_layer_choices("")
            self._build_zoom_choices()
            self._build_highlight_choices()
            self._reset_date_to_latest()
        finally:
            self._updating = False
        self._changed()

    def _select_layer(self, _event=None):
        if self._updating:
            return
        self._updating = True
        try:
            self._build_zoom_choices()
            self._build_highlight_choices()
            self._reset_date_to_latest()
        finally:
            self._updating = False
        self._changed()

    def _select_highlight(self, _event=None):
        if self._updating:
            return
        item = self._highlight_by_label.get(self._highlight_var.get())
        if item is None:
            self._changed()
            return
        theme = self._selected_theme()
        product = get_product(theme["id"], item["product"])
        if product is None:
            return
        self._updating = True
        try:
            self._build_mission_choices(product["missions"][0])
            self._build_product_choices(product["id"])
            self._build_layer_choices(item["layer"])
            self._build_zoom_choices(item["map_zoom"])
            self._latitude_var.set(str(item["latitude"]))
            self._longitude_var.set(str(item["longitude"]))
            self._set_date_choices(item["date"])
            self._status_var.set("Highlight loaded. Its saved acquisition date is selected.")
        finally:
            self._updating = False
        self._changed()

    def _custom_location_changed(self, *_args):
        if self._updating:
            return
        self._updating = True
        try:
            self._highlight_var.set(CUSTOM_HIGHLIGHT_LABEL)
        finally:
            self._updating = False
        self._changed()

    def _custom_date_changed(self, *_args):
        if self._updating:
            return
        self._updating = True
        try:
            self._highlight_var.set(CUSTOM_HIGHLIGHT_LABEL)
        finally:
            self._updating = False
        self._changed()

    def _changed(self, *_args):
        if self._updating:
            return
        if self._activity.active:
            self._generation += 1
            self._activity.finish(False)
            self._refresh_button.configure(state="normal")
        if self._on_change:
            try:
                self._on_change()
            except (ValueError, tk.TclError):
                pass

    def _open_oauth_settings(self):
        try:
            opened = webbrowser.open_new_tab(ACCOUNT_SETTINGS_URL)
        except (OSError, webbrowser.Error) as exc:
            self._status_var.set("Could not open Copernicus OAuth settings: " + str(exc))
            return
        if opened is False:
            self._status_var.set(
                "Open " + ACCOUNT_SETTINGS_URL + " and choose User Settings > OAuth clients."
            )
        else:
            self._status_var.set("Opened the free Copernicus OAuth client settings in your browser.")

    def _clear_usage(self):
        self._credits_role_var.set("Role: -")
        for variable in self._credits_values.values():
            variable.set("-")

    def _credentials_changed(self, *_args):
        if self._closed:
            return
        self._usage_generation += 1
        self._credits_refresh_button.configure(state="normal")
        self._clear_usage()
        self._credits_status_var.set("Credentials changed. Refresh credits to update the account.")

    def refresh_usage(self):
        if self._closed:
            return
        self._usage_generation += 1
        generation = self._usage_generation
        self._clear_usage()
        try:
            auth = self.get_auth(require=True)
        except ValueError as exc:
            self._credits_status_var.set(str(exc))
            return
        self._credits_refresh_button.configure(state="disabled")
        self._credits_status_var.set("Loading account credits...")

        def worker():
            try:
                client = CopernicusClient(
                    auth["client_id"], auth["client_secret"], self._timeout,
                    self._user_agent, network_attempts=1,
                )
                result = client.account_usage()
                self._usage_results.put((generation, result, ""))
            except Exception as exc:
                self._usage_results.put((generation, None, str(exc)))

        threading.Thread(target=worker, name="MarbleScape-Copernicus-credits", daemon=True).start()

    def refresh_dates(self):
        try:
            profile = self.get_profile()
            auth = self.get_auth(require=False)
            size = self._output_size() if callable(self._output_size) else self._output_size
            width, height = int(size[0]), int(size[1])
        except Exception as exc:
            self._activity.finish(False)
            self._status_var.set(str(exc))
            return
        self._generation += 1
        generation = self._generation
        self._refresh_button.configure(state="disabled")
        self._status_var.set("Loading all available acquisition dates from Copernicus...")
        self._activity.start()

        def worker():
            dates = None
            problem = "Copernicus OAuth credentials are not configured."
            if auth["client_id"] and auth["client_secret"]:
                client = CopernicusClient(
                    auth["client_id"], auth["client_secret"], self._timeout,
                    self._user_agent, network_attempts=1,
                )
                for _attempt in range(self._catalogue_retries + 1):
                    try:
                        dates = client.list_dates(profile, (width, height))
                        problem = ""
                        break
                    except Exception as exc:
                        problem = str(exc)
            cache = self._catalogue_client
            if dates is not None:
                if callable(getattr(cache, "store_copernicus_dates", None)):
                    cache.store_copernicus_dates(profile, (width, height), dates)
                self._results.put((generation, dates, None, False))
                return
            cached = (
                cache.cached_copernicus_dates(profile, (width, height))
                if callable(getattr(cache, "cached_copernicus_dates", None)) else None
            )
            if cached is not None:
                self._results.put((generation, cached, problem, True))
            else:
                self._results.put((generation, None, problem, False))

        threading.Thread(target=worker, name="MarbleScape-Copernicus-catalogue", daemon=True).start()

    def _poll(self):
        self._after_id = None
        if self._closed:
            return
        try:
            while True:
                generation, dates, error, cached = self._results.get_nowait()
                if generation != self._generation:
                    continue
                self._refresh_button.configure(state="normal")
                if error and not cached:
                    self._activity.finish(False)
                    self._status_var.set("Copernicus catalogue unavailable: " + error[:260])
                    continue
                selected = self._date_by_label.get(self._date_var.get(), "latest")
                self._update_date_choices(dates, selected)
                self._status_var.set(
                    f"Copernicus catalogue loaded: {len(dates)} available date(s). "
                    "Latest available remains the default."
                    + (("\nCatalogue notice: " + error[:260] + "; using cached catalogue data.")
                       if cached and error else "")
                )
                self._activity.finish(not cached)
        except queue.Empty:
            pass
        try:
            while True:
                generation, usage, error = self._usage_results.get_nowait()
                if generation != self._usage_generation:
                    continue
                self._credits_refresh_button.configure(state="normal")
                if error:
                    self._credits_status_var.set("Credits unavailable: " + error[:240])
                    continue
                self._credits_role_var.set("Role: " + usage["role"])
                for category in ("processingUnitsMonthly", "requestsMonthly"):
                    for field in ("configuration", "consumed", "remaining"):
                        self._credits_values[(category, field)].set(usage[category][field])
                self._credits_since_var.set(
                    "Consumed since " + dt.date.today().replace(day=1).strftime("%d-%m-%Y")
                )
                self._credits_status_var.set("Credits updated.")
        except queue.Empty:
            pass
        if not self._closed:
            self._after_id = self.frame.after(100, self._poll)

    def _destroyed(self, event):
        if event.widget is self.frame:
            self.close()

    def close(self):
        self._closed = True
        self._generation += 1
        self._usage_generation += 1
        self._activity.close()
        if self._usage_after_id is not None:
            try:
                self.frame.after_cancel(self._usage_after_id)
            except tk.TclError:
                pass
            self._usage_after_id = None
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
