"""Hidden-Tk regression tests with no live NOAA requests.

Run with ``python -m unittest discover -s tests``. Tk tests skip on systems
without Tk or a display server.
"""

from copy import deepcopy
import gc
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

try:
    import tkinter as tk
except ImportError:
    tk = None

if tk is not None:
    import marblescape_source_settings as source_settings


class FakeNOAAClient:
    def __init__(self, **kwargs):
        self.calls = []
        self.fail = False
        self.all_refresh_gate = None
        self.all_refresh_started = threading.Event()
        self.all_summary = None
        self.catalogue_refresh_status = {"running": False, "done": 0, "total": 0, "message": "", "error": ""}
        self.retries = 2
        self.copernicus_dates = None
        self.areas = {
            "goes_east": [
                {"id": "full_disk", "label": "Full Disk", "category": "Global"},
                {"id": "test_a", "label": "Austin", "category": "Local"},
                {"id": "test_b", "label": "Boston", "category": "Local"},
                {"id": "storm_one", "label": "Storm One", "category": "Active storms"},
                {"id": "storm_two", "label": "Storm Two", "category": "Active storms"},
            ],
            "goes_west": [
                {"id": "full_disk", "label": "Full Disk", "category": "Global"},
            ],
            "solar": [{"id": "sun", "label": "Sun", "category": "Solar"}],
            "himawari": [
                {"id": "nict_full_disk", "label": "NICT - Full Disk (True Color)",
                 "category": "NICT True Color"},
                {"id": "jma_jpn", "label": "JMA - Japan", "category": "JMA Regions"},
            ],
            "slider": [
                {"id": "goes-19---full_disk", "label": "Full Disk",
                 "category": "GOES-19 (East; 75.2W)"},
                {"id": "gk2a---full_disk", "label": "Full Disk",
                 "category": "GEO-KOMPSAT-2A (128E)"},
            ],
            "worldview": [
                {"id": "VIIRS_NOAA20_CorrectedReflectance_TrueColor",
                 "label": "Corrected Reflectance (True Color, VIIRS, NOAA-20)",
                 "category": "Corrected Reflectance"},
            ],
        }

    def list_areas(self, provider, refresh=False):
        self.calls.append(("areas", provider, refresh))
        if self.fail:
            raise OSError("offline fixture")
        return deepcopy(self.areas[provider])

    def list_products(self, provider, area_id, refresh=False):
        self.calls.append(("products", provider, area_id, refresh))
        if self.fail:
            raise OSError("offline fixture")
        if provider == "solar":
            return [{"id": "Fe171", "label": "171 Angstrom", "resolutions": ["300x300", "1200x1200"]}]
        if provider == "himawari":
            return [{"id": "true_color", "label": "True Color",
                     "resolutions": ["550x550", "11000x11000"]}]
        if provider == "slider":
            return [{"id": "geocolor", "label": "GeoColor",
                     "resolutions": ["678x678", "5424x5424", "10848x10848"]}]
        if provider == "worldview":
            return [
                {"id": "latest", "label": "Latest available (currently 2026-09-14)",
                 "resolutions": ["1024x512", "4096x2048", "8192x4096"]},
                {"id": "2026-09-13", "label": "Fixed · 2026-09-13",
                 "resolutions": ["1024x512", "4096x2048", "8192x4096"]},
            ]
        return [
            {"id": "GEOCOLOR", "label": "GeoColor", "resolutions": ["678x678", "1808x1808"]},
            {"id": "13", "label": "Infrared", "resolutions": ["678x678", "5424x5424"]},
        ]

    def refresh_all_catalogues(self, refresh=True, progress=None):
        self.calls.append(("all", refresh))
        self.catalogue_refresh_status = {"running": True, "done": 1, "total": 4,
                                          "message": "Fixture areas", "error": ""}
        if progress:
            progress(1, 4, "Fixture areas")
        self.all_refresh_started.set()
        if self.all_refresh_gate is not None:
            if not self.all_refresh_gate.wait(5):
                raise RuntimeError("Fixture release timed out")
        if self.fail:
            self.catalogue_refresh_status = {"running": False, "done": 1, "total": 4,
                                              "message": "Refresh failed", "error": "offline fixture"}
            raise OSError("offline fixture")
        summary = self.all_summary or {"providers": 3, "areas": 7, "products": 13,
                                       "resolution_options": 26, "errors": [], "warning": "", "complete": True}
        self.catalogue_refresh_status = {"running": False, "done": 4, "total": 4,
                                          "message": "All NOAA catalogues are ready.", "error": summary["warning"]}
        if progress:
            progress(4, 4, "All NOAA catalogues are ready.")
        return deepcopy(summary)

    def cached_copernicus_dates(self, _profile, _output_size):
        return deepcopy(self.copernicus_dates)

    def store_copernicus_dates(self, _profile, _output_size, dates):
        self.copernicus_dates = deepcopy(dates)


@unittest.skipIf(tk is None, "Tkinter is not installed")
class SourceSettingsTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.patch_client = mock.patch.object(source_settings, "NOAAClient", FakeNOAAClient)
        self.patch_client.start()
        self.controllers = []
        self.threads_before = set(threading.enumerate())

    def tearDown(self):
        for controller in self.controllers:
            controller.close()
            gate = getattr(controller._client, "all_refresh_gate", None)
            if gate is not None:
                gate.set()
        try:
            self._join_workers()
        finally:
            self.root.destroy()
            self.controllers.clear()
            controller = None
            gc.collect()
            self.patch_client.stop()
        self.assertEqual(self.callback_errors, [], "An exception escaped a Tk callback")

    def _join_workers(self):
        for thread in set(threading.enumerate()) - self.threads_before:
            if thread.name.startswith("MarbleScape-catalogue"):
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive(), "A fixture catalogue worker did not finish")

    def make_settings(self, provider="eumetsat", profiles=None, **kwargs):
        if profiles is None:
            profiles = deepcopy(source_settings.DEFAULT_PROFILES)
        kwargs.setdefault("client", FakeNOAAClient())
        controller = source_settings.SourceSettings(self.root, provider, profiles, **kwargs)
        self.controllers.append(controller)
        return controller

    def wait_for_catalogue(self, controller):
        deadline = time.monotonic() + 5
        while controller._loading:
            if time.monotonic() > deadline:
                self.fail("Timed out waiting for the fixture catalogue")
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    @staticmethod
    def select_provider(controller, provider):
        controller._provider_var.set(source_settings.image_source_label(provider))
        controller._select_provider()
        if provider in source_settings.GOES_SATELLITES:
            controller._goes_var.set(source_settings.GOES_SATELLITES[provider])
            controller._select_goes_satellite()

    def test_goes_source_groups_satellites_without_losing_selections(self):
        settings = self.make_settings("goes_west")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._provider_combo["values"].count("NOAA GOES"), 1)
        self.assertNotIn("GOES-East", settings._provider_combo["values"])
        self.assertNotIn("GOES-West", settings._provider_combo["values"])
        self.assertEqual(settings._provider_var.get(), "NOAA GOES")
        self.assertEqual(settings._goes_var.get(), "GOES-West")
        self.assertTrue(settings._goes_combo.grid_info())
        settings._goes_var.set("GOES-East")
        settings._select_goes_satellite()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[0], "goes_east")
        self.select_provider(settings, "solar")
        self.wait_for_catalogue(settings)
        self.assertFalse(settings._goes_combo.grid_info())
        settings._provider_var.set("NOAA GOES")
        settings._select_provider()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[0], "goes_east")
        settings.set_selection("goes_west", source_settings.DEFAULT_PROFILES)
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._goes_var.get(), "GOES-West")
        self.assertEqual(settings.get_selection()[0], "goes_west")

    def test_eumetsat_needs_no_catalogue_and_init_does_not_call_on_change(self):
        changes = []
        settings = self.make_settings(on_change=changes.append)
        self.assertEqual(changes, [])
        self.assertEqual(settings._client.calls, [])
        self.assertEqual(settings.get_selection()[0], "eumetsat")
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertEqual(changes, ["goes_east"])

    def test_hidden_eumetsat_update_does_not_restore_its_view(self):
        changes = []
        settings = self.make_settings(on_change=changes.append)
        self.select_provider(settings, "copernicus")
        self.assertEqual(changes, ["copernicus"])
        settings._eumetsat_changed()
        self.assertEqual(changes, ["copernicus"])
        self.select_provider(settings, "eumetsat")
        settings._eumetsat_changed()
        self.assertEqual(changes, ["copernicus", "eumetsat", "eumetsat"])

    def test_default_new_source_waits_for_catalogue_validation(self):
        settings = self.make_settings("goes_east", profiles={})
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "auto")

    def test_every_new_resolution_source_starts_on_automatic(self):
        settings = self.make_settings("goes_east", profiles={})
        for provider in ("goes_east", "goes_west", "solar", "himawari", "slider", "worldview"):
            with self.subTest(provider=provider):
                if provider != "goes_east":
                    self.select_provider(settings, provider)
                self.wait_for_catalogue(settings)
                self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")
                self.assertEqual(settings.get_selection()[1][provider]["resolution"], "auto")

    def test_himawari_auto_default_and_catalogue_activity(self):
        settings = self.make_settings("himawari")
        self.assertTrue(settings._catalogue_activity.active)
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["himawari"]["resolution"], "auto")
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")
        self.assertEqual(settings._catalogue_activity.completion.get(), "Completed.")
        settings._refresh()
        self.assertTrue(settings._catalogue_activity.active)
        self.assertEqual(settings._catalogue_activity.completion.get(), "")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._catalogue_activity.completion.get(), "Completed.")

    def test_eumetsat_catalogue_activity_completes_without_network(self):
        from marblescape_eumetsat import _FALLBACK_ITEM

        client = FakeNOAAClient()
        client.eumetsat = SimpleNamespace(catalogue=lambda refresh=False: [dict(_FALLBACK_ITEM)])
        settings = self.make_settings(client=client)
        eumetsat = settings.eumetsat_settings
        self.assertTrue(eumetsat._activity.active)
        deadline = time.monotonic() + 3
        while eumetsat._activity.active:
            if time.monotonic() > deadline:
                self.fail("EUMETSAT catalogue fixture did not complete")
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(eumetsat._activity.completion.get(), "Completed.")

    def test_area_filter_does_not_change_the_selected_area(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        settings._category_var.set("Local")
        settings._select_category()
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.wait_for_catalogue(settings)
        settings._filter_var.set("bOsToN")
        self.assertEqual(list(settings._area_by_label), ["Boston [test_b]"])
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "test_a")
        settings._area_var.set("Boston [test_b]")
        settings._select_area()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "test_b")

    def test_product_sizes_and_profiles_are_retained_without_mutating_input(self):
        profiles = deepcopy(source_settings.DEFAULT_PROFILES)
        original = deepcopy(profiles)
        settings = self.make_settings("goes_east", profiles=profiles)
        self.wait_for_catalogue(settings)
        settings._product_var.set("Infrared [13]")
        settings._select_product()
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "auto")
        settings._resolution_var.set("5424x5424")
        settings._select_resolution()
        self.select_provider(settings, "solar")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._product_label["text"], "Channel")
        self.assertFalse(settings._area_combo.winfo_manager())
        self.assertEqual(settings.get_selection()[1]["solar"]["resolution"], "auto")
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"],
                         {"area": "full_disk", "product": "13", "resolution": "5424x5424"})
        self.assertEqual(profiles, original)
        result = settings.get_selection()[1]
        result["goes_east"]["product"] = "mutated"
        self.assertEqual(settings.get_selection()[1]["goes_east"]["product"], "13")

    def test_cached_goes_catalogue_is_usable_during_offline_refresh(self):
        class CachedClient(FakeNOAAClient):
            def cached_areas(self, provider):
                return deepcopy(self.areas[provider])

            def cached_products(self, provider, _area_id):
                return [{"id": "GEOCOLOR", "label": "GeoColor",
                         "resolutions": ["678x678", "1808x1808"]}]

            def catalogue_offline(self, _provider):
                return True

        client = CachedClient()
        client.fail = True
        client.catalogue_refresh_status["running"] = True
        settings = self.make_settings("goes_east", client=client)
        self.assertEqual(client.calls, [])
        for combo in (settings._area_combo, settings._product_combo,
                      settings._resolution_combo):
            self.assertEqual(str(combo["state"]), "readonly")
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")

        self.select_provider(settings, "goes_west")
        self.assertEqual(client.calls, [])
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")
        self.assertEqual(str(settings._area_combo["state"]), "readonly")
        self.assertEqual(str(settings._product_combo["state"]), "readonly")

        settings._refresh()
        self.wait_for_catalogue(settings)
        self.assertEqual(str(settings._area_combo["state"]), "readonly")
        self.assertEqual(str(settings._product_combo["state"]), "readonly")
        self.assertEqual(str(settings._resolution_combo["state"]), "readonly")

    def test_himawari_new_area_prefers_true_color_reproduction(self):
        settings = self.make_settings("himawari")
        self.wait_for_catalogue(settings)
        settings._profiles["himawari"]["product"] = ""
        settings._receive_products([
            {"id": "dnc", "label": "Natural Color RGB", "resolutions": ["800x600"]},
            {"id": "b13", "label": "Infrared", "resolutions": ["800x600"]},
            {"id": "trm", "label": "True Color Reproduction Image", "resolutions": ["800x600"]},
        ])
        self.assertEqual(settings._profiles["himawari"]["product"], "trm")

    def test_worldview_new_layer_category_prefers_true_color(self):
        client = FakeNOAAClient()
        client.areas["worldview"].extend([
            {"id": "VIIRS_NDVI", "label": "Vegetation index", "category": "Other"},
            {"id": "MODIS_TrueColor", "label": "Corrected Reflectance (True Color)", "category": "Other"},
        ])
        settings = self.make_settings("worldview", client=client)
        self.wait_for_catalogue(settings)
        settings._category_var.set("Other")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._profiles["worldview"]["area"], "MODIS_TrueColor")

    def test_single_document_sources_refresh_metadata_only_once(self):
        for provider in ("slider", "worldview"):
            with self.subTest(provider=provider):
                client = FakeNOAAClient()
                settings = self.make_settings(provider, client=client)
                self.wait_for_catalogue(settings)
                client.calls.clear()
                settings._refresh()
                self.wait_for_catalogue(settings)
                self.assertIn(("areas", provider, True), client.calls)
                self.assertIn(("products", provider, settings._profiles[provider]["area"], False), client.calls)
                settings.close()

    def test_copernicus_configuration_defaults_to_true_color_layer(self):
        settings = self.make_settings("copernicus")
        copernicus = settings.copernicus_settings
        copernicus._configuration_var.set("Wildfires")
        copernicus._select_configuration()
        self.assertEqual(copernicus._mission_var.get(), "Sentinel-2")
        self.assertEqual(copernicus._selected_product()["name"], "Wildfires (S2L2A)")
        self.assertEqual(copernicus._selected_layer()["name"].casefold(), "true color")

    def test_copernicus_new_configuration_avoids_gas_and_keeps_saved_false_color(self):
        settings = self.make_settings("copernicus")
        copernicus = settings.copernicus_settings
        copernicus._mission_var.set("Sentinel-5P")
        copernicus._select_mission()
        copernicus._configuration_var.set("Wildfires")
        copernicus._select_configuration()
        self.assertEqual(copernicus._mission_var.get(), "Sentinel-2")
        false_color = next(item for item in copernicus._selected_product()["layers"]
                           if item["name"].casefold() == "false color")
        saved = dict(source_settings.DEFAULT_COPERNICUS_PROFILE)
        saved.update(configuration="WILDFIRES", mission="Sentinel-2",
                     product=copernicus._selected_product()["id"], layer=false_color["id"])
        copernicus.set_profile(saved)
        self.assertEqual(copernicus._selected_layer()["id"], false_color["id"])

    def test_himawari_uses_shared_area_product_and_resolution_controls(self):
        settings = self.make_settings("himawari")
        self.wait_for_catalogue(settings)
        provider, profiles = settings.get_selection()
        self.assertEqual(provider, "himawari")
        self.assertEqual(profiles["himawari"], {
            "area": "nict_full_disk", "product": "true_color", "resolution": "auto"
        })
        self.assertTrue(settings._area_combo.winfo_manager())
        self.assertIn("Largest available (11000x11000)", settings._resolution_combo["values"])
        self.assertEqual(settings._product_label["text"], "Product / layer")

    def test_slider_uses_satellite_sector_product_and_clean_tiles_note(self):
        settings = self.make_settings("slider")
        self.wait_for_catalogue(settings)
        provider, profiles = settings.get_selection()
        self.assertEqual(provider, "slider")
        self.assertEqual(profiles["slider"], {
            "area": "goes-19---full_disk", "product": "geocolor",
            "resolution": "auto",
        })
        self.assertEqual(settings._category_label["text"], "Satellite")
        self.assertEqual(settings._area_label["text"], "Sector")
        self.assertTrue(settings._slider_note.winfo_manager())
        self.assertIn("without map borders", settings._slider_note["text"])

    def test_worldview_uses_layer_date_and_render_resolution_controls(self):
        settings = self.make_settings("worldview")
        self.wait_for_catalogue(settings)
        provider, profiles = settings.get_selection()
        self.assertEqual(provider, "worldview")
        self.assertEqual(profiles["worldview"], {
            "area": "VIIRS_NOAA20_CorrectedReflectance_TrueColor",
            "product": "latest", "resolution": "auto",
        })
        self.assertEqual(settings._category_label["text"], "Layer category")
        self.assertEqual(settings._filter_label["text"], "Filter layers")
        self.assertEqual(settings._area_label["text"], "Imagery layer")
        self.assertEqual(settings._product_label["text"], "Date / time")
        self.assertEqual(settings._resolution_label["text"], "Render resolution")
        self.assertIn("Largest available (8192x4096)", settings._resolution_combo["values"])
        self.assertIn("latest available acquisition", settings._status_var.get())

    def test_offline_refresh_preserves_a_saved_complete_selection(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        before = settings.get_selection()
        settings._client.fail = True
        settings._refresh()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection(), before)
        self.assertIn("Saved selection can still be used", settings._status_var.get())
        self.assertEqual(str(settings._refresh_button["state"]), "normal")
        self.assertIn(("areas", "goes_east", True), settings._client.calls)

    def test_failed_changed_selection_cannot_save_or_corrupt_inactive_profile(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        before = settings.get_selection()[1]["goes_east"]
        settings._client.fail = True
        settings._category_var.set("Local")
        settings._select_category()
        self.wait_for_catalogue(settings)
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.select_provider(settings, "eumetsat")
        self.assertEqual(settings.get_selection()[1]["goes_east"], before)

    def test_missing_saved_area_requires_explicit_replacement(self):
        profiles = deepcopy(source_settings.DEFAULT_PROFILES)
        profiles["goes_east"]["area"] = "retired_storm"
        settings = self.make_settings("goes_east", profiles=profiles)
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._area_var.get(), "retired_storm")
        self.assertEqual(settings.get_selection()[1]["goes_east"], profiles["goes_east"])
        self.assertIn("no longer listed", settings._status_var.get())
        self.assertIn("new images may be unavailable", settings._status_var.get())
        self.assertEqual(str(settings._area_combo["state"]), "readonly")
        self.assertTrue(settings._area_combo["values"])
        self.assertEqual(settings._client.calls, [("areas", "goes_east", True)])
        settings._category_var.set("Local")
        settings._select_category()
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "test_a")

    def test_superseded_queued_requests_do_not_call_noaa(self):
        settings = self.make_settings()
        settings._client_lock.acquire()
        try:
            for provider in ("goes_east", "goes_west", "solar"):
                self.select_provider(settings, provider)
        finally:
            settings._client_lock.release()
        self.wait_for_catalogue(settings)
        self._join_workers()
        self.assertEqual(settings._client.calls,
                         [("areas", "solar", True), ("products", "solar", "sun", True)])
        self.assertEqual(settings.provider, "solar")
        self.assertEqual([area["id"] for area in settings._areas], ["sun"])

    def test_old_completed_results_cannot_replace_new_provider(self):
        settings = self.make_settings()
        old_generation = settings._generation
        self.select_provider(settings, "solar")
        settings._results.put((old_generation, "goes_east", "areas",
                               [{"id": "wrong", "label": "Wrong", "category": "Wrong"}],
                               None, False))
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.provider, "solar")
        self.assertEqual([area["id"] for area in settings._areas], ["sun"])

    def test_partial_catalogue_warning_is_visible_and_selection_stays_usable(self):
        settings = self.make_settings()
        settings._client.catalogue_warning = "Some local NOAA areas could not be loaded."
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertIn(settings._client.catalogue_warning, settings._status_var.get())
        self.assertEqual(settings.get_selection()[0], "goes_east")

    def test_close_cancels_queued_requests_and_poll_timer(self):
        settings = self.make_settings()
        settings._client_lock.acquire()
        try:
            self.select_provider(settings, "goes_east")
            settings.close()
        finally:
            settings._client_lock.release()
        self._join_workers()
        self.assertEqual(settings._client.calls, [])
        self.assertIsNone(settings._after_id)
        settings.close()

    def test_largest_option_tracks_product_size_and_keeps_concrete_saved_size(self):
        profiles = deepcopy(source_settings.DEFAULT_PROFILES)
        profiles["goes_east"]["resolution"] = "678x678"
        settings = self.make_settings("goes_east", profiles)
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._resolution_var.get(), "678x678")
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "678x678")
        self.assertIn("Largest available (1808x1808)", settings._resolution_combo["values"])
        settings._product_var.set("Infrared [13]")
        settings._select_product()
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "auto")
        settings._resolution_var.set("678x678")
        settings._select_resolution()
        settings._resolution_var.set("Largest available (5424x5424)")
        settings._select_resolution()
        self.assertEqual(settings.get_selection()[1]["goes_east"]["resolution"], "largest")
        settings._category_var.set("Local")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings._resolution_var.get(), "Automatic (recommended)")

    def test_shared_client_and_eumetsat_preset_container(self):
        client = FakeNOAAClient()
        settings = self.make_settings(client=client)
        self.assertIs(settings._client, client)
        self.assertEqual(settings.eumetsat_frame.grid_info()["row"], 1)
        self.assertTrue(settings.eumetsat_frame.winfo_manager())
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertFalse(settings.eumetsat_frame.winfo_manager())
        self.assertEqual(settings._filter_label["text"], "Filter areas")
        self.assertIn("selected category", settings._filter_hint["text"])
        self.select_provider(settings, "eumetsat")
        self.assertTrue(settings.eumetsat_frame.winfo_manager())

    def test_copernicus_dropdowns_location_flags_and_credentials_are_saved(self):
        settings = self.make_settings(
            "copernicus", copernicus_auth={"client_id": "client", "client_secret": "secret"}
        )
        cop = settings.copernicus_settings
        self.assertTrue(cop.frame.winfo_manager())
        self.assertFalse(settings.eumetsat_frame.winfo_manager())
        self.assertFalse(settings._area_combo.winfo_manager())
        self.assertEqual(len(cop._configuration_combo["values"]), 13)
        self.assertEqual(cop._mission_var.get(), "Sentinel-2 Mosaics")
        self.assertEqual(cop.get_profile()["product"], "MARBLESCAPE::S2-QUARTERLY")
        self.assertEqual(cop.get_profile()["layer"], "TRUE_COLOR_CLOUDLESS")
        cop._mission_var.set("Sentinel-2")
        cop._select_mission()
        self.assertEqual(cop.get_profile()["product"], "DEFAULT-THEME::a91f72")
        cop._coverage_var.set("Fill gaps with earlier imagery (use latest imagery of valid lookback)")
        cop._select_coverage()
        self.assertEqual(cop._zoom_combo["values"], tuple(str(value) for value in range(7, 19)))
        self.assertEqual(cop.get_profile()["date"], "latest")
        self.assertEqual(cop.get_profile()["coverage_mode"], "fill_gaps")
        self.assertEqual(cop.get_profile()["lookback_days"], 14)
        self.assertEqual(cop.get_profile()["max_cloud_cover"], 30)
        self.assertEqual(str(cop._cloud_scale["state"]), "normal")
        cop._cloud_scale.set(15)
        self.assertEqual(cop.get_profile()["max_cloud_cover"], 15)
        lookback_values = cop._lookback_combo["values"]
        self.assertEqual(lookback_values[:4], ("3 days", "7 days", "14 days", "21 days"))
        self.assertEqual(lookback_values[4], "30 days (1 month)")
        self.assertEqual(lookback_values[7], "90 days (3 months)")
        self.assertEqual(lookback_values[8], "120 days (4 months)")
        self.assertEqual(lookback_values[-1], "1095 days (3 years)")
        self.assertTrue(all("|" not in value for value in lookback_values))
        cop._lookback_var.set(lookback_values[-1])
        self.assertEqual(cop.get_profile()["lookback_days"], 1095)
        cop._lookback_var.set(lookback_values[2])
        self.assertEqual(str(cop._lookback_combo["state"]), "readonly")
        cop._coverage_var.set("Single latest acquisition")
        cop._select_coverage()
        self.assertEqual(str(cop._cloud_scale["state"]), "normal")
        self.assertEqual(str(cop._lookback_combo["state"]), "disabled")
        l1c_label = next(label for label, product in cop._product_by_label.items()
                         if product["name"] == "Sentinel-2 L1C")
        cop._zoom_var.set("7")
        cop._product_var.set(l1c_label)
        cop._select_product()
        self.assertEqual(cop._zoom_combo["values"], tuple(str(value) for value in range(10, 19)))
        self.assertEqual(cop._zoom_var.get(), "10")
        with mock.patch(
            "marblescape_copernicus_settings.webbrowser.open_new_tab", return_value=True
        ) as open_portal:
            cop._oauth_button.invoke()
        open_portal.assert_called_once_with(
            "https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings"
        )
        self.assertIn("Opened the free Copernicus OAuth", cop._status_var.get())

        cop._mission_var.set("Sentinel-1")
        cop._select_mission()
        self.assertEqual(str(cop._cloud_scale["state"]), "disabled")
        self.assertEqual(str(cop._brightness_scale["state"]), "disabled")
        cop._mission_var.set("Sentinel-2")
        cop._select_mission()
        self.assertEqual(str(cop._cloud_scale["state"]), "normal")
        self.assertEqual(cop.get_profile()["product"], "DEFAULT-THEME::a91f72")
        cop._mission_var.set("Sentinel-1")
        cop._select_mission()
        cop._latitude_var.set("52.52")
        cop._longitude_var.set("13.405")
        cop._labels_var.set(False)
        cop._coverage_var.set("Fill areas without image data with black")
        cop._select_coverage()
        self.assertEqual(str(cop._lookback_combo["state"]), "disabled")
        provider, profiles = settings.get_selection()
        self.assertEqual(provider, "copernicus")
        self.assertEqual(profiles["copernicus"]["mission"], "Sentinel-1")
        self.assertEqual(profiles["copernicus"]["latitude"], 52.52)
        self.assertEqual(profiles["copernicus"]["longitude"], 13.405)
        self.assertFalse(profiles["copernicus"]["map_labels"])
        self.assertEqual(profiles["copernicus"]["coverage_mode"], "black")
        self.assertEqual(profiles["copernicus"]["lookback_days"], 14)
        self.assertEqual(profiles["copernicus"]["max_cloud_cover"], 15)
        self.assertEqual(settings.get_copernicus_auth(),
                         {"client_id": "client", "client_secret": "secret"})

    def test_copernicus_catalogue_activity_reports_completion(self):
        settings = self.make_settings(
            "copernicus", copernicus_auth={"client_id": "client", "client_secret": "secret"}
        )
        copernicus = settings.copernicus_settings
        with mock.patch("marblescape_copernicus_settings.CopernicusClient") as client:
            client.return_value.list_dates.return_value = ["2026-09-21"]
            copernicus.refresh_dates()
            self.assertTrue(copernicus._activity.active)
            deadline = time.monotonic() + 3
            while copernicus._activity.active:
                if time.monotonic() > deadline:
                    self.fail("Copernicus catalogue fixture did not complete")
                self.root.update()
                time.sleep(0.01)
        self.assertEqual(copernicus._activity.completion.get(), "Completed.")
        self.assertIn("2026 Q3", copernicus._date_combo["values"])

        missing = self.make_settings("copernicus")
        with self.assertRaises(ValueError):
            missing.get_selection()

    def test_copernicus_mosaic_controls_and_account_credits(self):
        settings = self.make_settings("copernicus")
        cop = settings.copernicus_settings
        cop._mission_var.set("Sentinel-2 Mosaics")
        cop._select_mission()
        self.assertEqual(cop._selected_product()["name"], "Sentinel-2 Quarterly Mosaics")
        self.assertEqual(cop._date_label["text"], "Quarter")
        self.assertEqual(str(cop._cloud_scale["state"]), "disabled")
        self.assertEqual(str(cop._brightness_scale["state"]), "normal")
        cop._brightness_var.set(75)
        self.assertEqual(cop.get_profile()["brightness"], 75)
        self.assertEqual(str(cop._coverage_combo["state"]), "disabled")
        cop._update_date_choices(["2026-04-01"], "latest")
        self.assertIn("2026 Q2", cop._date_combo["values"])

        annual = next(label for label, product in cop._product_by_label.items()
                      if product["name"] == "WorldCover Annual Cloudless Mosaics")
        cop._product_var.set(annual)
        cop._select_product()
        self.assertEqual(cop._date_label["text"], "Year")
        self.assertEqual(cop._zoom_combo["values"][0], "9")
        cop._update_date_choices(["2021-01-01"], "latest")
        self.assertIn("2021", cop._date_combo["values"])

        usage = {"role": "copernicus-general-quota"}
        for category in ("processingUnitsMonthly", "requestsMonthly"):
            usage[category] = {"configuration": "30000", "consumed": "123",
                               "remaining": "29877"}
        cop._usage_results.put((cop._usage_generation, usage, ""))
        cop.frame.after_cancel(cop._after_id)
        cop._after_id = None
        cop._poll()
        self.assertEqual(cop._credits_role_var.get(), "Role: copernicus-general-quota")
        self.assertEqual(cop._credits_values[("requestsMonthly", "remaining")].get(), "29877")
        cop._client_id_var.set("different client")
        self.assertEqual(cop._credits_role_var.get(), "Role: -")
        self.assertEqual(cop._credits_values[("requestsMonthly", "remaining")].get(), "-")

    def test_copernicus_catalogue_retries_then_uses_cached_dates(self):
        client = FakeNOAAClient()
        client.copernicus_dates = ["2026-09-20"]
        with mock.patch("marblescape_copernicus_settings.CopernicusClient") as cop_client:
            cop_client.return_value.list_dates.side_effect = OSError("catalogue offline")
            settings = self.make_settings(
                "copernicus", client=client,
                copernicus_auth={"client_id": "client", "client_secret": "secret"},
            )
            deadline = time.monotonic() + 3
            while settings.copernicus_settings._activity.active:
                if time.monotonic() > deadline:
                    self.fail("Copernicus cached catalogue fixture did not complete")
                self.root.update()
                time.sleep(0.01)
        self.assertEqual(cop_client.return_value.list_dates.call_count, 3)
        self.assertIn("2026 Q3", settings.copernicus_settings._date_combo["values"])
        self.assertIn("using cached catalogue data", settings.copernicus_settings._status_var.get())

    def test_active_storm_category_selects_and_commits_first_storm(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        settings._category_var.set("Active storms")
        settings._select_category()
        self.assertEqual(settings._area_var.get(), "Storm One [storm_one]")
        with self.assertRaises(ValueError):
            settings.get_selection()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"],
                         {"area": "storm_one", "product": "GEOCOLOR", "resolution": "auto"})
        self.select_provider(settings, "solar")
        self.wait_for_catalogue(settings)
        self.select_provider(settings, "goes_east")
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "storm_one")

    def test_empty_selected_category_clears_the_previous_area(self):
        settings = self.make_settings("goes_east")
        self.wait_for_catalogue(settings)
        settings._areas = [area for area in settings._areas if area["category"] != "Active storms"]
        settings._category_var.set("Active storms")
        settings._select_category()
        self.assertEqual(settings._area_var.get(), "")
        self.assertEqual(settings._product_var.get(), "")
        self.assertEqual(settings._profiles["goes_east"]["area"], "")
        with self.assertRaises(ValueError):
            settings.get_selection()
        settings._category_var.set("Local")
        settings._select_category()
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[1]["goes_east"]["area"], "test_a")

    def test_set_selection_loads_a_copy_and_rejects_obsolete_catalogue_results(self):
        changes = []
        settings = self.make_settings(on_change=changes.append)
        saved = deepcopy(source_settings.DEFAULT_PROFILES)
        saved["solar"]["resolution"] = "300x300"
        self.select_provider(settings, "goes_east")
        settings.set_selection("solar", saved)
        saved["solar"]["resolution"] = "largest"
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.get_selection()[0], "solar")
        self.assertEqual(settings.get_selection()[1]["solar"]["resolution"], "300x300")
        self.assertEqual(settings._resolution_var.get(), "300x300")
        self.assertEqual(changes, ["goes_east", "solar"])
        self.assertEqual([area["id"] for area in settings._areas], ["sun"])

    def test_set_selection_from_worker_calls_back_only_on_tk_thread(self):
        callback_threads = []
        settings = self.make_settings(on_change=lambda provider: callback_threads.append(threading.get_ident()))
        saved = deepcopy(source_settings.DEFAULT_PROFILES)
        worker = threading.Thread(target=settings.set_selection, args=("solar", saved))
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(settings.provider, "eumetsat")
        deadline = time.monotonic() + 5
        while settings.provider != "solar":
            if time.monotonic() > deadline:
                self.fail("Queued source profile was not applied")
            self.root.update()
            time.sleep(0.01)
        self.wait_for_catalogue(settings)
        self.assertEqual(callback_threads, [threading.get_ident()])

    def test_full_refresh_reports_progress_across_provider_and_profile_changes(self):
        client = FakeNOAAClient()
        client.all_refresh_gate = threading.Event()
        settings = self.make_settings(client=client)
        settings._refresh_all()
        self.assertTrue(client.all_refresh_started.wait(1))
        self.root.update()
        settings._sync_global_refresh()
        self.assertIn("1/4", settings._all_status_var.get())
        self.assertEqual(settings._all_progress_var.get(), 25)
        self.assertTrue(settings._all_progress_running)
        self.assertEqual(str(settings._all_progress["mode"]), "indeterminate")
        self.assertEqual(str(settings._all_refresh_button["state"]), "disabled")
        settings._refresh_all()
        self.assertEqual(client.calls.count(("all", True)), 1)
        self.select_provider(settings, "goes_east")
        settings.set_selection("solar", source_settings.DEFAULT_PROFILES)
        client.all_refresh_gate.set()
        deadline = time.monotonic() + 5
        while settings._all_running:
            if time.monotonic() > deadline:
                self.fail("Full refresh did not finish")
            self.root.update()
            time.sleep(0.01)
        self.wait_for_catalogue(settings)
        self.assertEqual(settings.provider, "solar")
        self.assertIn("3 sources, 7 areas", settings._all_status_var.get())
        self.assertEqual(settings._all_progress_var.get(), 100)
        self.assertFalse(settings._all_progress_running)
        self.assertEqual(settings._all_completion_var.get(), "Completed.")
        self.assertEqual(str(settings._all_refresh_button["state"]), "normal")

    def test_initial_startup_refresh_is_observed_without_starting_a_second_job(self):
        client = FakeNOAAClient()
        client.catalogue_refresh_status = {"running": True, "done": 3, "total": 10,
                                           "message": "Startup catalogue loading", "error": ""}
        settings = self.make_settings(client=client)
        self.assertIn("3/10", settings._all_status_var.get())
        self.assertTrue(settings._all_progress_running)
        self.assertEqual(str(settings._all_refresh_button["state"]), "disabled")
        settings._refresh_all()
        self.assertEqual(client.calls, [])
        client.catalogue_refresh_status = {"running": False, "done": 10, "total": 10,
                                           "message": "All NOAA catalogues are ready.", "error": ""}
        settings._sync_global_refresh()
        self.assertIn("ready", settings._all_status_var.get())
        self.assertEqual(settings._all_completion_var.get(), "Completed.")
        self.assertEqual(str(settings._all_refresh_button["state"]), "normal")

    def test_partial_full_refresh_warning_is_visible_without_disabling_current_selection(self):
        client = FakeNOAAClient()
        client.all_summary = {"providers": 3, "areas": 7, "products": 12, "resolution_options": 24,
                              "errors": ["One storm unavailable"], "warning": "One storm unavailable", "complete": False}
        settings = self.make_settings("goes_east", client=client)
        self.wait_for_catalogue(settings)
        settings._refresh_all()
        deadline = time.monotonic() + 5
        while settings._all_running:
            if time.monotonic() > deadline:
                self.fail("Full refresh did not finish")
            self.root.update()
            time.sleep(0.01)
        self.wait_for_catalogue(settings)
        self.assertIn("One storm unavailable", settings._all_details_var.get())
        self.assertEqual(settings._all_completion_var.get(), "Finished with issues.")
        self.assertTrue(settings._all_separator.grid_info())
        self.assertGreater(
            int(settings._all_details_label.grid_info()["row"]),
            6,
        )
        self.assertEqual(settings.get_selection()[0], "goes_east")

    def test_close_does_not_cancel_shared_full_catalogue_refresh(self):
        client = FakeNOAAClient()
        client.all_refresh_gate = threading.Event()
        settings = self.make_settings(client=client)
        settings._refresh_all()
        self.assertTrue(client.all_refresh_started.wait(1))
        settings.close()
        client.all_refresh_gate.set()
        self._join_workers()
        self.assertFalse(client.catalogue_refresh_status["running"])
        self.assertIsNone(settings._after_id)


if __name__ == "__main__":
    unittest.main()
