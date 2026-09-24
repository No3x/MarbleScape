"""Source-specific image settings with network catalogue work off the Tk thread."""

from copy import deepcopy
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk

from marblescape_catalogues import CatalogueClient
from marblescape_catalogue_activity import CatalogueActivity
from marblescape_noaa import NOAAClient
from marblescape_copernicus import (
    DEFAULT_PROFILE as DEFAULT_COPERNICUS_PROFILE,
    normalize_profile as normalize_copernicus_profile,
)
from marblescape_copernicus_settings import CopernicusSettings
from marblescape_eumetsat import (
    EumetsatSettings,
    normalize_profile as normalize_eumetsat_profile,
)
from marblescape_source_layout import SOURCE_COMBO_WIDTH, configure_source_columns
from marblescape_source_defaults import default_source_profiles


PROVIDER_LABELS = {
    "eumetsat": "EUMETSAT",
    "goes_east": "GOES-East",
    "goes_west": "GOES-West",
    "solar": "Solar / Sun (SUVI)",
    "himawari": "Himawari",
    "slider": "CIRA SLIDER",
    "copernicus": "Copernicus Browser",
    "worldview": "NASA Worldview",
}
GOES_SATELLITES = {"goes_east": "GOES-East", "goes_west": "GOES-West"}
IMAGE_SOURCE_CHOICES = {
    "eumetsat": "EUMETSAT",
    "goes": "NOAA GOES",
    "solar": "Solar / Sun (SUVI)",
    "himawari": "Himawari",
    "slider": "CIRA SLIDER",
    "copernicus": "Copernicus Browser",
    "worldview": "NASA Worldview",
}


def image_source_label(provider):
    return IMAGE_SOURCE_CHOICES["goes"] if provider in GOES_SATELLITES else IMAGE_SOURCE_CHOICES[provider]


DEFAULT_PROFILES = default_source_profiles()

CATALOGUE_PROVIDERS = frozenset(
    ("goes_east", "goes_west", "solar", "himawari", "slider", "worldview")
)


def _natural_color_rank(item):
    """Rank natural-looking imagery before thematic composites in a new selection."""
    name = re.sub(r"[^a-z0-9]+", "", (str(item.get("label", "")) + " " + str(item.get("id", ""))).casefold())
    if "truecolorreproduction" in name:
        return 0
    if "truecolor" in name or "truecolour" in name or "geocolor" in name or "geocolour" in name:
        return 1
    if "naturalcolor" in name or "naturalcolour" in name:
        return 2
    return 3


def _complete(profile, provider=None):
    if provider == "eumetsat":
        try:
            normalize_eumetsat_profile(profile)
            return True
        except (TypeError, ValueError):
            return False
    if provider == "copernicus":
        try:
            normalize_copernicus_profile(profile)
            return True
        except (TypeError, ValueError):
            return False
    return (
        isinstance(profile, dict)
        and isinstance(profile.get("area"), str) and bool(profile["area"].strip())
        and isinstance(profile.get("product"), str) and bool(profile["product"].strip())
        and (profile.get("resolution") in {"auto", "largest"}
             or bool(re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", str(profile.get("resolution", "")))))
    )


class SourceSettings:
    """Embed ``frame`` in the Image tab and call ``close`` when its dialog closes.

    ``get_selection`` returns (provider, copied profiles), or raises ValueError
    if a newly selected catalogue combination has not finished loading or Copernicus
    credentials are missing. The caller owns persistence. Bundled source
    selections remain usable offline.
    """

    def __init__(self, parent, provider, profiles, timeout=90,
                 user_agent="MarbleScape", on_change=None, client=None,
                 copernicus_auth=None, output_size=(1920, 1080),
                 eumetsat_layer=None):
        self.frame = ttk.LabelFrame(parent, text="Source", padding=8)
        configure_source_columns(self.frame)
        self._on_change = on_change
        self._view_defaults_requested = False
        self._replace_profiles(profiles)
        self._provider = provider if provider in PROVIDER_LABELS else "eumetsat"
        self._client = client if client is not None else CatalogueClient(
            timeout=timeout, user_agent=user_agent,
            noaa=NOAAClient(timeout=timeout, user_agent=user_agent),
        )
        self._client_lock = threading.Lock()
        self._results = queue.Queue()
        self._all_results = queue.Queue()
        self._selection_requests = queue.Queue()
        self._ui_thread = threading.get_ident()
        self._generation = 0
        self._worker_state = {
            "closed": False,
            "generation": self._generation,
            "provider": self._provider,
        }
        self._all_job = 0
        self._all_running = False
        self._global_refresh_seen_running = False
        self._global_refresh_snapshot = None
        self._closed = False
        self._after_id = None
        self._areas = []
        self._products = []
        self._area_by_label = {}
        self._product_by_label = {}
        self._resolution_by_label = {}
        self._loading = False
        self._goes_provider = self._provider if self._provider in GOES_SATELLITES else "goes_east"
        self._provider_var = tk.StringVar(self.frame, image_source_label(self._provider))
        self._goes_var = tk.StringVar(self.frame, GOES_SATELLITES[self._goes_provider])
        self._category_var = tk.StringVar(self.frame)
        self._filter_var = tk.StringVar(self.frame)
        self._area_var = tk.StringVar(self.frame)
        self._product_var = tk.StringVar(self.frame)
        self._resolution_var = tk.StringVar(self.frame)
        self._status_var = tk.StringVar(self.frame)
        self._all_status_var = tk.StringVar(self.frame)
        self._all_progress_var = tk.DoubleVar(self.frame, 0)
        self._provider_combo, _ = self._combo(
            0, "Image source", self._provider_var, tuple(IMAGE_SOURCE_CHOICES.values()))
        self._provider_combo.bind("<<ComboboxSelected>>", self._select_provider)
        self._goes_combo, self._goes_label = self._combo(
            1, "Satellite", self._goes_var, tuple(GOES_SATELLITES.values())
        )
        self._goes_combo.bind("<<ComboboxSelected>>", self._select_goes_satellite)
        # EUMETSAT owns a dependent catalogue; the host adds view controls below it.
        self.eumetsat_frame = ttk.Frame(self.frame)
        self.eumetsat_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        configure_source_columns(self.eumetsat_frame)
        self.eumetsat_settings = EumetsatSettings(
            self.eumetsat_frame,
            self._profiles["eumetsat"],
            timeout=timeout,
            user_agent=user_agent,
            on_change=self._eumetsat_changed,
            client=getattr(self._client, "eumetsat", None),
            auto_refresh=False,
        )
        if eumetsat_layer:
            self.eumetsat_settings.set_profile(
                self._profiles["eumetsat"], layer=eumetsat_layer
            )
        self.eumetsat_settings.frame.grid(
            row=0, column=0, columnspan=2, sticky="ew"
        )
        self.eumetsat_view_frame = self.eumetsat_settings.view_frame
        self._category_combo, self._category_label = self._combo(
            2, "Area category", self._category_var)
        self._category_combo.bind("<<ComboboxSelected>>", self._select_category)
        self._filter_label = ttk.Label(self.frame, text="Filter areas")
        self._filter_label.grid(row=3, column=0, padx=(0, 10), pady=3, sticky="w")
        self._filter_entry = ttk.Entry(self.frame, textvariable=self._filter_var)
        self._filter_entry.grid(row=3, column=1, pady=3, sticky="ew")
        self._filter_var.trace_add("write", self._filter_areas)
        self._filter_hint = ttk.Label(self.frame, text="Filters area names and IDs within the selected category.",
                                      wraplength=420, justify="left")
        self._filter_hint.grid(row=4, column=1, pady=(0, 3), sticky="w")
        self._area_combo, self._area_label = self._combo(5, "Area / location", self._area_var)
        self._area_combo.bind("<<ComboboxSelected>>", self._select_area)
        self._product_combo, self._product_label = self._combo(6, "Product / layer", self._product_var)
        self._product_combo.bind("<<ComboboxSelected>>", self._select_product)
        self._resolution_combo, self._resolution_label = self._combo(
            7, "Source resolution", self._resolution_var)
        self._resolution_combo.bind("<<ComboboxSelected>>", self._select_resolution)
        self._resolution_hint = ttk.Label(
            self.frame,
            text="Larger source images retain more detail when zooming or cropping. "
                 "Desktop size is set below; Render quality controls EUMETSAT WMS supersampling.",
            wraplength=420, justify="left")
        self._resolution_hint.grid(row=8, column=1, pady=(0, 5), sticky="w")
        self.generic_view_frame = ttk.Frame(self.frame)
        self.generic_view_frame.grid(
            row=9, column=0, columnspan=2, pady=(2, 0), sticky="ew"
        )
        configure_source_columns(self.generic_view_frame)
        self._slider_note = ttk.Label(
            self.frame,
            text="CIRA product tiles are loaded without map borders or latitude/longitude lines.",
            wraplength=570, justify="left",
        )
        self._slider_note.grid(row=10, column=0, columnspan=2, pady=(3, 3), sticky="w")
        self._actions = ttk.Frame(self.frame)
        self._actions.grid(row=11, column=0, columnspan=2, pady=(5, 0), sticky="ew")
        self._actions.columnconfigure(1, weight=1)
        self._all_refresh_button = ttk.Button(self._actions, text="Refresh all catalogues", command=self._refresh_all)
        self._all_refresh_button.grid(row=0, column=0, padx=(0, 8), sticky="w")
        self._refresh_button = ttk.Button(self._actions, text="Refresh catalogue", command=self._refresh)
        self._refresh_button.grid(row=0, column=1, sticky="w")
        ttk.Label(self._actions, text="Loading products and sizes for all public catalogues can take several minutes.",
                  wraplength=570, justify="left").grid(row=1, column=0, columnspan=2, pady=(4, 0), sticky="w")
        self._status_label = ttk.Label(self._actions, textvariable=self._status_var,
                                       wraplength=570, justify="left")
        self._status_label.grid(row=2, column=0, columnspan=2, pady=(4, 0), sticky="w")
        self._catalogue_activity = CatalogueActivity(self._actions, row=3)
        self._all_status_label = ttk.Label(self._actions, textvariable=self._all_status_var,
                                           wraplength=570, justify="left")
        self._all_status_label.grid(row=4, column=0, columnspan=2, pady=(4, 0), sticky="w")
        self._all_progress = ttk.Progressbar(self._actions, maximum=100, mode="indeterminate")
        self._all_progress.grid(row=5, column=0, columnspan=2, pady=(4, 0), sticky="ew")
        self._all_progress_running = False
        self._all_completion_var = tk.StringVar(self._actions, value="")
        ttk.Label(self._actions, textvariable=self._all_completion_var).grid(
            row=6, column=0, columnspan=2, pady=(3, 0), sticky="w"
        )
        self._all_separator = ttk.Separator(self._actions, orient="horizontal")
        self._all_separator.grid(
            row=7, column=0, columnspan=2, pady=(6, 4), sticky="ew"
        )
        self._all_details_var = tk.StringVar(self._actions, value="")
        self._all_details_label = ttk.Label(
            self._actions, textvariable=self._all_details_var,
            wraplength=570, justify="left",
        )
        self._all_details_label.grid(
            row=8, column=0, columnspan=2, sticky="w"
        )
        self._all_separator.grid_remove()
        self._all_details_label.grid_remove()
        self._all_progress.grid_remove()
        self._all_status_label.grid_remove()
        self._area_widgets = (self._category_label, self._category_combo,
                              self._filter_label, self._filter_entry, self._filter_hint,
                              self._area_label, self._area_combo)
        self._product_widgets = (self._product_label, self._product_combo,
                                 self._resolution_label, self._resolution_combo,
                                 self._resolution_hint, self._status_label)
        self._noaa_widgets = (*self._area_widgets, *self._product_widgets, self._actions)
        self.copernicus_settings = CopernicusSettings(
            self.frame,
            self._profiles["copernicus"],
            auth=copernicus_auth,
            timeout=timeout,
            user_agent=user_agent,
            output_size=output_size,
            on_change=self._copernicus_changed,
            catalogue_client=self._client,
            catalogue_retries=getattr(self._client, "retries", 2),
        )
        self.copernicus_settings.frame.grid(
            row=2, column=0, columnspan=2, sticky="ew"
        )
        self.frame.bind("<Destroy>", self._destroyed, add="+")
        self._display_provider()
        self._sync_global_refresh()
        self._after_id = self.frame.after(100, self._poll)

    def _replace_profiles(self, profiles):
        self._profiles = deepcopy(DEFAULT_PROFILES)
        self._usable = {key: False for key in DEFAULT_PROFILES}
        for key, profile in (profiles or {}).items():
            if key in self._profiles and isinstance(profile, dict):
                self._profiles[key].update(deepcopy(profile))
                self._usable[key] = _complete(profile, key)
        self._last_valid_profiles = deepcopy(DEFAULT_PROFILES)
        for key, profile in self._profiles.items():
            if self._usable[key]:
                self._last_valid_profiles[key] = deepcopy(profile)

    def set_selection(self, provider, profiles):
        """Load saved image settings; calls from workers are queued for Tk.

        On the UI thread the change is immediate. The caller's dictionaries are
        copied, and on_change runs on the UI thread after the form is updated.
        An ongoing full catalogue refresh is independent of this selection.
        """
        if provider not in PROVIDER_LABELS:
            raise ValueError("Unknown image source.")
        if profiles is not None and not isinstance(profiles, dict):
            raise ValueError("Source profiles must be a dictionary.")
        if self._closed:
            return
        profiles = deepcopy(profiles)
        if threading.get_ident() != self._ui_thread:
            self._selection_requests.put((provider, profiles))
            return
        self._replace_profiles(profiles)
        self._provider = provider
        if provider in GOES_SATELLITES:
            self._goes_provider = provider
            self._goes_var.set(GOES_SATELLITES[provider])
        self._provider_var.set(image_source_label(provider))
        self.eumetsat_settings.set_profile(self._profiles["eumetsat"])
        self._display_provider()
        if self._on_change:
            self._on_change(provider)

    @property
    def provider(self):
        return self._provider

    @property
    def view_defaults_requested(self):
        return self._view_defaults_requested

    def _combo(self, row, text, variable, choices=()):
        label = ttk.Label(self.frame, text=text)
        label.grid(row=row, column=0, padx=(0, 10), pady=3, sticky="w")
        combo = ttk.Combobox(self.frame, textvariable=variable, values=choices,
                             state="readonly", width=SOURCE_COMBO_WIDTH)
        combo.grid(row=row, column=1, pady=3, sticky="ew")
        return combo, label

    def _set_visible(self, widgets, visible):
        for widget in widgets:
            widget.grid() if visible else widget.grid_remove()

    def _select_provider(self, _event=None):
        label = self._provider_var.get()
        selected = next((key for key, value in IMAGE_SOURCE_CHOICES.items() if value == label), None)
        if selected == "goes":
            selected = self._goes_provider
        if selected is None or selected == self._provider:
            return
        self._provider = selected
        self._display_provider()
        if self._on_change:
            self._view_defaults_requested = True
            try:
                self._on_change(self._provider)
            finally:
                self._view_defaults_requested = False

    def _select_goes_satellite(self, _event=None):
        selected = next((key for key, value in GOES_SATELLITES.items()
                         if value == self._goes_var.get()), None)
        if selected is None:
            return
        self._goes_provider = selected
        if self._provider not in GOES_SATELLITES or selected == self._provider:
            return
        self._provider = selected
        self._display_provider()
        if self._on_change:
            self._view_defaults_requested = True
            try:
                self._on_change(selected)
            finally:
                self._view_defaults_requested = False

    def _display_provider(self):
        self._advance_generation()
        self._catalogue_activity.finish(False)
        self._areas = []
        self._products = []
        self._area_by_label = {}
        self._product_by_label = {}
        self._resolution_by_label = {}
        self._filter_var.set("")
        self._category_var.set("")
        is_catalogue = self._provider in CATALOGUE_PROVIDERS
        is_copernicus = self._provider == "copernicus"
        self._set_visible((self._goes_label, self._goes_combo), self._provider in GOES_SATELLITES)
        self._set_visible((self.eumetsat_frame,), self._provider == "eumetsat")
        self._set_visible(self._area_widgets, is_catalogue and self._provider != "solar")
        self._set_visible(self._product_widgets, is_catalogue)
        self._set_visible((self._refresh_button, self._status_label), is_catalogue)
        self._set_visible((self._slider_note,), self._provider == "slider")
        self._set_visible(
            (self.generic_view_frame,),
            is_catalogue and not is_copernicus,
        )
        self._set_visible((self.copernicus_settings.frame,), is_copernicus)
        self._product_label.configure(
            text=("Channel" if self._provider == "solar" else
                  "Date / time" if self._provider == "worldview" else "Product / layer")
        )
        self._category_label.configure(
            text=("Satellite" if self._provider == "slider" else
                  "Layer category" if self._provider == "worldview" else "Area category")
        )
        self._filter_label.configure(
            text=("Filter sectors" if self._provider == "slider" else
                  "Filter layers" if self._provider == "worldview" else "Filter areas")
        )
        self._filter_hint.configure(
            text=("Filters sector names and IDs within the selected satellite."
                  if self._provider == "slider" else
                  "Filters NASA visualization names and IDs within the selected category."
                  if self._provider == "worldview" else
                  "Filters area names and IDs within the selected category.")
        )
        self._area_label.configure(
            text=("Sector" if self._provider == "slider" else
                  "Imagery layer" if self._provider == "worldview" else "Area / location")
        )
        self._resolution_label.configure(
            text="Render resolution" if self._provider == "worldview" else "Source resolution"
        )
        self._resolution_hint.configure(
            text=("Each CIRA source size selects a tile-pyramid level. Larger levels retain more detail "
                  "but require more separate tile downloads. Automatic uses active monitors in a multi-display setup."
                  if self._provider == "slider" else
                  "NASA GIBS renders the selected global layer at this size. The output size and image "
                  "placement are configured under General > Output. Automatic uses active monitors in a multi-display setup."
                  if self._provider == "worldview" else
                  "Larger source images retain more detail when zooming or cropping. "
                  "Automatic uses active monitors in a multi-display setup, plus fit/crop and zoom. "
                  "Render quality controls EUMETSAT WMS supersampling.")
        )
        if is_copernicus:
            self._loading = False
            self.copernicus_settings.set_profile(self._profiles["copernicus"])
            self.copernicus_settings.refresh_dates()
            return
        if self._provider == "eumetsat":
            self._loading = False
            status = getattr(self._client, "catalogue_refresh_status", {}) or {}
            use_cache = getattr(self._client, "catalogue_cached_for_automatic_use", None)
            cached = callable(use_cache) and use_cache("eumetsat")
            self.eumetsat_settings.refresh(not bool(status.get("running")) and not cached)
            return
        if not is_catalogue:
            self._loading = False
            self._refresh_button.configure(state="disabled")
            return
        self._show_saved_profile()
        if not self._show_cached_catalogue():
            status = getattr(self._client, "catalogue_refresh_status", {}) or {}
            self._load_areas(refresh=not bool(status.get("running")))

    def _show_cached_catalogue(self):
        cached_areas = getattr(self._client, "cached_areas", None)
        cached_products = getattr(self._client, "cached_products", None)
        if not callable(cached_areas) or not callable(cached_products):
            return False
        areas = cached_areas(self._provider)
        if not areas:
            return False
        self._receive_areas(areas, refresh=False, request_products=False)
        profile = self._profiles[self._provider]
        if not any(item["id"] == profile["area"] for item in areas):
            return True
        products = cached_products(self._provider, profile["area"])
        if products:
            self._receive_products(products)
            return True
        self._load_products(refresh=False)
        return True

    def _show_saved_profile(self):
        profile = self._profiles[self._provider]
        for combo, variable, value in (
            (self._area_combo, self._area_var, profile.get("area", "")),
            (self._product_combo, self._product_var, profile.get("product", "")),
            (self._resolution_combo, self._resolution_var, profile.get("resolution", "")),
        ):
            label = ("Automatic (recommended)" if value == "auto" else
                     "Largest available" if value == "largest" else value)
            variable.set(label)
            combo.configure(values=(label,) if label else (), state="disabled")
        self._category_combo.configure(values=(), state="disabled")
        self._filter_entry.configure(state="disabled")

    def _request(self, kind, refresh=False, area_id=None):
        self._advance_generation()
        generation, provider = self._generation, self._provider
        self._loading = True
        source = PROVIDER_LABELS.get(provider, provider)
        if provider == "worldview":
            message = ("Loading NASA Worldview layers..." if kind == "areas"
                       else "Loading dates and render sizes...")
        else:
            message = (f"Loading {source} areas..." if kind == "areas"
                       else "Loading products and image sizes...")
        self._status_var.set(message)
        self._catalogue_activity.start()
        self._refresh_button.configure(state="disabled", text="Refresh catalogue")
        client, client_lock, results = self._client, self._client_lock, self._results
        worker_state = self._worker_state

        def worker():
            try:
                # Do not queue an obsolete request behind a slow catalogue call.
                # The same check remains inside the lock to close the race
                # between this fast path and acquiring the shared client.
                if (worker_state["closed"] or generation != worker_state["generation"]
                        or provider != worker_state["provider"]):
                    return
                with client_lock:
                    # A newer selection may have superseded this request while
                    # it waited for the client. Only inspect plain Python state
                    # here; all widget access stays on the Tk thread.
                    if (worker_state["closed"] or generation != worker_state["generation"]
                            or provider != worker_state["provider"]):
                        return
                    value = (client.list_areas(provider, refresh=refresh) if kind == "areas"
                             else client.list_products(provider, area_id, refresh=refresh))
                results.put((generation, provider, kind, value, None, refresh))
            except Exception as exc:
                results.put((generation, provider, kind, None, str(exc), refresh))

        threading.Thread(target=worker, name="MarbleScape-catalogue", daemon=True).start()

    def _load_areas(self, refresh=False):
        if not self._areas:
            self._category_combo.configure(state="disabled")
            self._filter_entry.configure(state="disabled")
            self._area_combo.configure(state="disabled")
        if not self._products:
            self._product_combo.configure(state="disabled")
            self._resolution_combo.configure(state="disabled")
        self._request("areas", refresh=refresh)

    def _advance_generation(self):
        self._generation += 1
        self._worker_state.update(
            closed=self._closed,
            generation=self._generation,
            provider=self._provider,
        )

    def _load_products(self, refresh=False):
        profile = self._profiles[self._provider]
        if not self._products or not self._usable[self._provider]:
            self._products = []
            self._product_by_label = {}
            self._product_var.set(profile.get("product", ""))
            resolution = profile.get("resolution", "")
            self._resolution_var.set(
                "Automatic (recommended)" if resolution == "auto" else
                "Largest available" if resolution == "largest" else resolution
            )
            self._product_combo.configure(state="disabled")
            self._resolution_combo.configure(state="disabled")
        self._request("products", refresh=refresh, area_id=profile["area"])

    def _poll(self):
        self._after_id = None
        if self._closed:
            return
        try:
            while True:
                provider, profiles = self._selection_requests.get_nowait()
                self.set_selection(provider, profiles)
        except queue.Empty:
            pass
        try:
            while True:
                kind, job, value = self._all_results.get_nowait()
                if job != self._all_job:
                    continue
                if kind == "progress":
                    self._show_all_progress(*value)
                else:
                    self._finish_all(*value)
        except queue.Empty:
            pass
        self._sync_global_refresh()
        if self._all_progress_running and str(self._all_progress["mode"]) == "indeterminate":
            self._all_progress.configure(value=(time.monotonic() * 35.0) % 100.0)
        try:
            while True:
                generation, provider, kind, value, error, refresh = self._results.get_nowait()
                if generation != self._generation or provider != self._provider:
                    continue
                self._loading = False
                self._refresh_button.configure(state="normal")
                if error:
                    self._show_error(error)
                    continue
                try:
                    if kind == "areas":
                        self._receive_areas(value, refresh)
                    else:
                        self._receive_products(value)
                except (ValueError, KeyError, TypeError) as exc:
                    self._show_error(str(exc))
        except queue.Empty:
            pass
        if not self._closed:
            self._after_id = self.frame.after(100, self._poll)

    def _show_error(self, error):
        self._catalogue_activity.finish(False)
        suffix = (" Saved selection can still be used." if self._usable.get(self._provider)
                  else " Retry before saving this selection.")
        self._status_var.set("Source catalogue unavailable: " + str(error)[:180] + suffix)
        self._refresh_button.configure(state="normal", text="Retry catalogue")

    @staticmethod
    def _labels(items):
        # Including IDs keeps duplicate location/product names distinguishable.
        return {f"{item['label']} [{item['id']}]": item for item in items}

    def _receive_areas(self, areas, refresh, request_products=True):
        if not areas:
            noun = "layers" if self._provider == "worldview" else "areas"
            raise ValueError(f"No {noun} are currently listed for this source.")
        self._areas = areas
        profile = self._profiles[self._provider]
        selected = next((item for item in areas if item["id"] == profile["area"]), None)
        categories = list(dict.fromkeys(str(item.get("category", "Areas")) for item in areas))
        self._category_combo.configure(values=categories, state="readonly")
        if selected is not None:
            self._category_var.set(str(selected.get("category", "Areas")))
        elif self._category_var.get() not in categories:
            self._category_var.set(categories[0])
        self._filter_entry.configure(state="normal")
        self._filter_areas()
        if selected is None:
            # Moving/retired storm sectors can disappear. Keep the configured
            # source visible until the user deliberately chooses a replacement.
            suffix = (" You can keep the saved selection, but new images may be unavailable."
                      if self._usable[self._provider]
                      else " Select an available area before saving.")
            noun = "layer" if self._provider == "worldview" else "area"
            self._status_var.set(f"The selected {noun} is no longer listed by its provider." + suffix)
            self._catalogue_activity.finish(False)
            self._refresh_button.configure(state="normal", text="Refresh catalogue")
            return
        if not request_products:
            return
        self._catalogue_activity.set_progress(1, 2)
        # CIRA and NASA publish areas and products in one catalogue document.
        # The area request above already refreshed it; rereading it here can
        # trigger a second slow network transfer for the same button click.
        offline = getattr(self._client, "catalogue_offline", None)
        if callable(offline) and offline(self._provider):
            cached_products = getattr(self._client, "cached_products", None)
            products = cached_products(self._provider, profile["area"]) \
                if callable(cached_products) else None
            if products:
                self._receive_products(products)
                return
        self._load_products(refresh=refresh and self._provider not in ("slider", "worldview"))

    def _filter_areas(self, *_args):
        if not self._areas:
            return
        category = self._category_var.get()
        needle = self._filter_var.get().strip().casefold()
        matches = [item for item in self._areas
                   if str(item.get("category", "Areas")) == category
                   and (not needle or needle in (item["label"] + " " + item["id"]).casefold())]
        self._area_by_label = self._labels(matches)
        self._area_combo.configure(values=list(self._area_by_label), state="readonly")
        selected_id = self._profiles[self._provider]["area"]
        selected = next((item for item in self._areas if item["id"] == selected_id), None)
        # Filtering narrows the popup only; it must not silently change a saved area.
        if selected:
            self._area_var.set(f"{selected['label']} [{selected['id']}]")

    def _select_category(self, _event=None):
        self._filter_var.set("")
        self._filter_areas()
        if not self._area_by_label:
            self._advance_generation()
            self._loading = False
            self._profiles[self._provider].update(area="", product="", resolution="auto")
            self._usable[self._provider] = False
            self._products = []
            self._product_by_label = {}
            self._resolution_by_label = {}
            for variable in (self._area_var, self._product_var, self._resolution_var):
                variable.set("")
            for combo in (self._area_combo, self._product_combo, self._resolution_combo):
                combo.configure(values=(), state="disabled")
            self._status_var.set("No areas are currently available in this category. Select another category or refresh the catalogue.")
            self._catalogue_activity.finish(False)
            self._refresh_button.configure(state="normal")
            return
        label = next(iter(self._area_by_label))
        if self._provider == "worldview":
            label = min(self._area_by_label, key=lambda value: _natural_color_rank(self._area_by_label[value]))
        self._area_var.set(label)
        self._select_area()

    def _select_area(self, _event=None):
        selected = self._area_by_label.get(self._area_var.get())
        if selected is None:
            return
        profile = self._profiles[self._provider]
        if selected["id"] == profile["area"]:
            return
        profile.update(area=selected["id"], product="", resolution="auto")
        self._usable[self._provider] = False
        self._load_products()

    def _receive_products(self, products):
        products = [item for item in products if item.get("resolutions")]
        if not products:
            self._usable[self._provider] = False
            raise ValueError("No still images are currently listed for this area.")
        self._products = products
        self._product_by_label = self._labels(products)
        profile = self._profiles[self._provider]
        selected = next((item for item in products if item["id"] == profile["product"]), None)
        if selected is None:
            preferred = DEFAULT_PROFILES[self._provider]["product"]
            selected = next(
                (item for item in products if item["id"] == preferred),
                min(products, key=_natural_color_rank),
            )
            profile["resolution"] = "auto"
        profile["product"] = selected["id"]
        self._product_combo.configure(values=list(self._product_by_label), state="readonly")
        self._product_var.set(f"{selected['label']} [{selected['id']}]")
        self._set_resolutions(selected)

    def _select_product(self, _event=None):
        selected = self._product_by_label.get(self._product_var.get())
        if selected is None:
            return
        profile = self._profiles[self._provider]
        if profile["product"] != selected["id"]:
            profile["resolution"] = "auto"
        profile["product"] = selected["id"]
        self._set_resolutions(selected)

    def _set_resolutions(self, product):
        profile = self._profiles[self._provider]
        resolutions = [str(value) for value in product["resolutions"]
                       if re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", str(value))]
        if not resolutions:
            self._usable[self._provider] = False
            raise ValueError("No supported still-image sizes are listed for this product.")
        largest = max(resolutions, key=lambda value: (
            int(value.split("x")[0]) * int(value.split("x")[1]),
            int(value.split("x")[0]), int(value.split("x")[1])))
        largest_label = f"Largest available ({largest})"
        automatic_label = "Automatic (recommended)"
        self._resolution_by_label = {
            automatic_label: "auto",
            largest_label: "largest",
            **{value: value for value in resolutions},
        }
        resolution = profile.get("resolution", "auto")
        if resolution not in {"auto", "largest"} and resolution not in resolutions:
            resolution = "auto"
        self._resolution_combo.configure(values=list(self._resolution_by_label), state="readonly")
        self._resolution_var.set(
            automatic_label if resolution == "auto" else
            largest_label if resolution == "largest" else resolution
        )
        profile["resolution"] = resolution
        self._usable[self._provider] = True
        self._last_valid_profiles[self._provider] = deepcopy(profile)
        if self._provider == "worldview":
            if product["id"] == "timeless":
                status = "This NASA GIBS visualization is not time-dependent."
            elif product["id"] == "latest":
                status = "NASA GIBS resolves the latest available acquisition when the image is checked."
            else:
                status = f"NASA GIBS uses the selected fixed acquisition: {product['id']}."
        else:
            status = ("Latest available still image is used. Automatic resolution chooses the "
                      "smallest available source size that avoids upscaling the configured output.")
        warning_for = getattr(self._client, "catalogue_warning_for", None)
        catalogue_warning = (
            str(warning_for(self._provider) or "").strip()
            if callable(warning_for)
            else str(getattr(self._client, "catalogue_warning", "") or "").strip()
        )
        if catalogue_warning:
            status += "\nCatalogue notice: " + catalogue_warning
        self._status_var.set(status)
        self._catalogue_activity.finish(True)
        self._refresh_button.configure(state="normal", text="Refresh catalogue")

    def _select_resolution(self, _event=None):
        resolution = self._resolution_by_label.get(self._resolution_var.get())
        if resolution is not None:
            self._profiles[self._provider]["resolution"] = resolution
            self._last_valid_profiles[self._provider] = deepcopy(self._profiles[self._provider])

    def _copernicus_changed(self):
        try:
            profile = self.copernicus_settings.get_profile()
        except ValueError:
            self._usable["copernicus"] = False
            return
        self._profiles["copernicus"] = deepcopy(profile)
        self._last_valid_profiles["copernicus"] = deepcopy(profile)
        self._usable["copernicus"] = True

    def _eumetsat_changed(self):
        try:
            profile = self.eumetsat_settings.get_profile()
        except ValueError:
            self._usable["eumetsat"] = False
            return
        self._profiles["eumetsat"] = deepcopy(profile)
        self._last_valid_profiles["eumetsat"] = deepcopy(profile)
        self._usable["eumetsat"] = True
        if self._on_change and self._provider == "eumetsat":
            self._on_change("eumetsat")

    def select_eumetsat_layer(self, layer):
        self.eumetsat_settings.select_layer(layer)

    def get_eumetsat_layer(self):
        return self.eumetsat_settings.get_profile()["layer"]

    def get_copernicus_auth(self, require=False):
        return self.copernicus_settings.get_auth(
            require=require or self._provider == "copernicus"
        )

    def _refresh(self):
        if self._provider in CATALOGUE_PROVIDERS:
            self._load_areas(refresh=True)

    def _refresh_all(self):
        if self._closed or self._all_running:
            return
        status = getattr(self._client, "catalogue_refresh_status", {})
        if isinstance(status, dict) and status.get("running"):
            self._sync_global_refresh()
            return
        self._all_job += 1
        job = self._all_job
        self._all_running = True
        self._all_refresh_button.configure(state="disabled")
        self._show_all_progress(0, 0, "Preparing all public image sources...")
        client, results = self._client, self._all_results

        def progress(done, total, message):
            if not self._closed:
                results.put(("progress", job, (done, total, message)))

        def worker():
            # This is a shared-client operation: closing this dialog or loading
            # another image profile must not interrupt the catalogue update.
            try:
                summary = client.refresh_all_catalogues(refresh=True, progress=progress)
                if not self._closed:
                    results.put(("complete", job, (summary, None)))
            except Exception as exc:
                if not self._closed:
                    results.put(("complete", job, (None, str(exc))))

        threading.Thread(target=worker, name="MarbleScape-catalogue-all", daemon=True).start()

    def _show_all_progress(self, done, total, message):
        if not self._all_progress_running:
            self._all_completion_var.set("")
            self._set_all_details("")
            self._all_progress.configure(mode="indeterminate")
            self._all_progress_running = True
        done, total = max(0.0, float(done)), max(0, int(total))
        count = f" ({min(total, int(done))}/{total})" if total else ""
        self._all_status_var.set(f"Refreshing all catalogues{count}: {message}")
        self._all_progress_var.set(min(100, 100 * done / total) if total else 0)
        # An unknown step moves like the download indicator; a reported
        # substep shows its real fraction of the five-source refresh.
        fractional = done != int(done)
        self._all_progress.configure(mode="determinate" if fractional else "indeterminate")
        if fractional:
            self._all_progress.configure(value=self._all_progress_var.get())
        self._all_status_label.grid()
        self._all_progress.grid()

    def _stop_all_progress(self, success):
        self._all_progress_running = False
        self._all_progress.configure(
            mode="determinate", value=100 if success else self._all_progress_var.get()
        )
        self._all_completion_var.set("Completed." if success else "Finished with issues.")

    def _set_all_details(self, message):
        message = str(message or "").strip()
        self._all_details_var.set(message)
        if message:
            self._all_separator.grid()
            self._all_details_label.grid()
        else:
            self._all_separator.grid_remove()
            self._all_details_label.grid_remove()

    def _finish_all(self, summary, error):
        self._all_running = False
        self._global_refresh_seen_running = False
        self._all_refresh_button.configure(state="normal")
        complete = False
        details = ""
        if error:
            message = "Catalogue refresh could not be completed."
            details = "Catalogue update finished with unavailable entries: " + str(error)
        else:
            summary = summary if isinstance(summary, dict) else {}
            complete = bool(summary.get("complete"))
            counts = (f"{summary.get('providers', 0)} sources, {summary.get('areas', 0)} areas, "
                      f"{summary.get('products', 0)} products and {summary.get('resolution_options', 0)} size options")
            warning = str(summary.get("warning") or "").strip()
            if not warning and summary.get("errors"):
                warning = " | ".join(str(value) for value in summary["errors"][:3])
            if summary.get("complete", not warning):
                message = (("All catalogues already current: " if summary.get("updated_sources") == 0
                            else "All catalogues updated: ") + counts + ".")
            else:
                message = "Available catalogue data: " + counts + "."
            if warning:
                details = "Catalogue update finished with unavailable entries:\n" + warning
            self._all_progress_var.set(100)
        self._all_status_var.set(message)
        self._stop_all_progress(complete)
        self._set_all_details(details)
        self._all_status_label.grid()
        self._all_progress.grid()
        if self._provider in CATALOGUE_PROVIDERS:
            if not self._show_cached_catalogue():
                self._load_areas(refresh=False)

    def _sync_global_refresh(self):
        """Observe startup/other-dialog jobs using a copied, thread-safe status."""
        status = getattr(self._client, "catalogue_refresh_status", None)
        if not isinstance(status, dict):
            return
        running = bool(status.get("running"))
        self._all_refresh_button.configure(state="disabled" if running or self._all_running else "normal")
        snapshot = tuple(status.get(key) for key in ("running", "done", "total", "message", "error"))
        if snapshot == self._global_refresh_snapshot:
            return
        first_status = self._global_refresh_snapshot is None
        self._global_refresh_snapshot = snapshot
        if running:
            self._global_refresh_seen_running = True
            self._show_all_progress(status.get("done", 0), status.get("total", 0), status.get("message", "Loading..."))
        elif not self._all_running and (self._global_refresh_seen_running or first_status):
            was_running = self._global_refresh_seen_running
            self._global_refresh_seen_running = False
            message = str(status.get("message") or "")
            error = str(status.get("error") or "")
            if error:
                self._set_all_details(
                    "Catalogue update finished with unavailable entries:\n" + error
                )
                message = "Available catalogue data remains usable where cached."
            else:
                self._set_all_details("")
                if was_running and not message:
                    message = "All catalogues updated."
            if message:
                self._all_status_var.set(message)
                self._all_status_label.grid()
                self._all_progress_var.set(100 if not error else self._all_progress_var.get())
                self._all_progress.grid()
            if was_running:
                self._stop_all_progress(not error)
            if was_running and self._provider in CATALOGUE_PROVIDERS:
                if not self._show_cached_catalogue():
                    self._load_areas(refresh=False)
            elif was_running and self._provider == "eumetsat":
                self.eumetsat_settings.refresh(False)

    def get_selection(self):
        if self._provider == "eumetsat":
            profile = self.eumetsat_settings.get_profile()
            self._profiles["eumetsat"] = deepcopy(profile)
            self._last_valid_profiles["eumetsat"] = deepcopy(profile)
            self._usable["eumetsat"] = True
        elif self._provider == "copernicus":
            profile = self.copernicus_settings.get_profile()
            self.get_copernicus_auth(require=True)
            self._profiles["copernicus"] = deepcopy(profile)
            self._last_valid_profiles["copernicus"] = deepcopy(profile)
            self._usable["copernicus"] = True
        elif self._provider != "eumetsat":
            profile = self._profiles[self._provider]
            if not _complete(profile, self._provider) or not self._usable[self._provider]:
                if self._loading:
                    raise ValueError("Please wait for the selected area, product and image sizes to load.")
                raise ValueError("Select an area, product and source resolution, or retry the catalogue.")
        # A provider changed and then left while loading must not persist an
        # incomplete profile; retain its last usable selection in that case.
        return self._provider, deepcopy(self._last_valid_profiles)

    def _destroyed(self, event):
        if event.widget is self.frame:
            self.close()

    def close(self):
        self._closed = True
        self._catalogue_activity.close()
        if self._all_progress_running:
            self._all_progress_running = False
        self._advance_generation()
        self.eumetsat_settings.close()
        self.copernicus_settings.close()
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
