"""Source configuration and runtime regression tests without live network access."""

from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from PIL import Image

import marblescape_download as app


class SourceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.saved_configuration = app.capture_loaded_configuration()
        self.saved_clients = dict(app.NOAA_CLIENTS)
        self.saved_himawari_clients = dict(app.HIMAWARI_CLIENTS)
        self.saved_slider_clients = dict(app.SLIDER_CLIENTS)
        self.saved_worldview_clients = dict(app.WORLDVIEW_CLIENTS)
        self.saved_catalogue_clients = dict(app.CATALOGUE_CLIENTS)
        self.saved_image_status = dict(app.IMAGE_STATUS)
        self.events = (
            app.APPLICATION_STOP_EVENT,
            app.CONFIGURATION_RELOAD_EVENT,
            app.FORCE_UPDATE_EVENT,
        )
        self.saved_events = [event.is_set() for event in self.events]
        for event in self.events:
            event.clear()
        self.temporary = tempfile.TemporaryDirectory(prefix="marblescape-runtime-")
        self.root = Path(self.temporary.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(
            app, "PROFILE_LIBRARY_PATH", self.root / "profiles.toml"
        ))
        self.log = self.stack.enter_context(patch.object(app, "log"))
        self.wallpaper = self.stack.enter_context(patch.object(app, "set_windows_wallpaper"))
        self.wms = self.stack.enter_context(patch.object(
            app, "download_capabilities", side_effect=AssertionError("Unexpected WMS request")
        ))
        self.stack.enter_context(patch.object(
            app, "urlopen", side_effect=AssertionError("Unexpected live network request")
        ))
        app.load_configuration(app.DEFAULT_CONFIG_TEMPLATE_PATH)
        app.OUTPUT_ROOT_WINDOWS = self.root
        app.OUTPUT_ROOT_LINUX = self.root
        app.CUSTOM_LATEST_FOLDER = ""
        app.CUSTOM_HISTORY_FOLDER = ""
        app.ACTIVE_CONFIG_PATH = self.root / "settings.toml"
        app.ACTIVE_PROFILE_LIBRARY_PATH = self.root / "profiles.toml"
        app.ENABLE_HISTORY = False
        app.SET_WINDOWS_WALLPAPER = False
        app.WINDOWS_PAUSE_ON_EXIT = False
        app.WIDTH, app.HEIGHT, app.ASPECT_RATIO = 32, 18, "16:9"
        app.IMAGE_SOURCE = "goes_east"
        app.NOAA_CLIENTS.clear()
        app.HIMAWARI_CLIENTS.clear()
        app.SLIDER_CLIENTS.clear()
        app.WORLDVIEW_CLIENTS.clear()
        app.CATALOGUE_CLIENTS.clear()
        app.IMAGE_STATUS.update(provider=None, timestamp=None, error="", interval_minutes=10)
        app.refresh_output_paths()
        app.set_current_image_path(None)
        self.frame = {
            "url": "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/FD/GEOCOLOR/20262541200_GOES19-ABI-FD-GEOCOLOR-1808x1808.jpg",
            "timestamp": "2026-09-11T12:00:00Z",
            "interval_minutes": 10,
        }
        self.client = SimpleNamespace(
            latest=Mock(return_value=dict(self.frame)),
            fetch_image=Mock(side_effect=self.make_png),
            list_products=Mock(return_value=[{
                "id": "GEOCOLOR", "label": "GeoColor", "resolutions": ["1808x1808"]
            }]),
        )
        self.client_factory = self.stack.enter_context(patch.object(
            app, "NOAAClient", return_value=self.client
        ))

    def tearDown(self):
        try:
            app.restore_loaded_configuration(self.saved_configuration)
            app.NOAA_CLIENTS.clear()
            app.NOAA_CLIENTS.update(self.saved_clients)
            app.HIMAWARI_CLIENTS.clear()
            app.HIMAWARI_CLIENTS.update(self.saved_himawari_clients)
            app.SLIDER_CLIENTS.clear()
            app.SLIDER_CLIENTS.update(self.saved_slider_clients)
            app.WORLDVIEW_CLIENTS.clear()
            app.WORLDVIEW_CLIENTS.update(self.saved_worldview_clients)
            app.CATALOGUE_CLIENTS.clear()
            app.CATALOGUE_CLIENTS.update(self.saved_catalogue_clients)
            app.IMAGE_STATUS.clear()
            app.IMAGE_STATUS.update(self.saved_image_status)
            for event, was_set in zip(self.events, self.saved_events):
                event.set() if was_set else event.clear()
        finally:
            self.stack.close()
            self.temporary.cleanup()

    @staticmethod
    def make_png(frame, dimensions, **options):
        del frame, options
        output = io.BytesIO()
        with Image.new("RGB", dimensions, (24, 100, 160)) as image:
            image.save(output, format="PNG")
        return output.getvalue()

    def assert_installed_png(self):
        files = app.get_latest_image_files()
        self.assertEqual(len(files), 1)
        with Image.open(files[0]) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (32, 18))
            image.load()
        return files[0]

    def test_default_content_paths_are_created(self):
        app.ENABLE_HISTORY = True

        app.ensure_directories()

        self.assertEqual(app.CONTENT_DIR, self.root / "content")
        self.assertEqual(app.LATEST_DIR, self.root / "content" / "latest")
        self.assertEqual(app.HISTORY_DIR, self.root / "content" / "history")
        self.assertTrue(app.LATEST_DIR.is_dir())
        self.assertTrue(app.HISTORY_DIR.is_dir())

    def test_latest_and_history_cannot_be_stored_inside_managed_cache_paths(self):
        safe = self.root / "safe"
        unsafe_folders = (
            self.root / "content" / "cache",
            self.root / "content" / "cache" / "nested",
            self.root / "content" / "cache.sqlite3",
            self.root / "content" / "cache.sqlite3" / "nested",
        )
        for folder in unsafe_folders:
            with self.subTest(folder=folder):
                with self.assertRaises(ValueError):
                    app.validate_image_folders(folder, safe)

    def test_clear_profile_cache_preserves_latest_and_requests_active_refresh(self):
        app.ensure_directories()
        latest = app.LATEST_DIR / "marblescape_base.png"
        latest.write_bytes(self.make_png({}, (32, 18)))
        profile_id = "a" * 32
        cached, _previous = app.get_profile_cache().install(
            profile_id, {"configuration": 1}, {"source": 1},
            self.make_png({}, (32, 18)), (32, 18),
        )
        app.set_current_image_path(cached, profile_id)

        cleared = app.clear_profile_image_cache()

        self.assertEqual(cleared["files"], 1)
        self.assertTrue(latest.exists())
        self.assertIsNone(app.get_current_image_path())
        self.assertTrue(app.FORCE_UPDATE_EVENT.is_set())
        app.FORCE_UPDATE_EVENT.clear()

    def test_profile_cache_archives_only_replaced_content(self):
        app.ENABLE_HISTORY = True
        app.ensure_directories()
        profile_id = "b" * 32
        first = self.make_png({}, (32, 18))
        buffer = io.BytesIO()
        with Image.new("RGB", (32, 18), (180, 30, 40)) as image:
            image.save(buffer, format="PNG")
        second = buffer.getvalue()

        app.save_profile_image(profile_id, {"configuration": 1}, {"source": 1}, first, (32, 18))
        app.save_profile_image(profile_id, {"configuration": 1}, {"source": 1}, first, (32, 18))
        self.assertEqual(app.get_history_files(), [])
        app.save_profile_image(profile_id, {"configuration": 1}, {"source": 2}, second, (32, 18))

        history = app.get_history_files()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].read_bytes(), first)
        self.assertEqual(app.get_profile_cache().status()["files"], 1)

    def test_clear_history_removes_only_managed_history_images(self):
        app.ENABLE_HISTORY = True
        app.ensure_directories()
        first = self.make_png({}, (32, 18))
        second = self.make_png({}, (16, 9))
        managed = (
            app.HISTORY_DIR / "marblescape_2026-09-13_120000.png",
        )
        managed[0].write_bytes(first)
        other_product = app.HISTORY_DIR / "other_2026-09-13_110000.png"
        other_product.write_bytes(second)
        unrelated_png = app.HISTORY_DIR / "keep.png"
        unrelated_text = app.HISTORY_DIR / "keep.txt"
        unrelated_png.write_bytes(first)
        unrelated_text.write_text("keep", encoding="utf-8")
        latest = app.LATEST_DIR / "marblescape_current.png"
        latest.write_bytes(first)
        cached, _previous = app.get_profile_cache().install(
            "c" * 32, {"configuration": 1}, {"source": 1}, first, (32, 18)
        )

        cleared = app.clear_history_images()

        self.assertEqual(cleared, {"files": 1, "bytes": len(first)})
        self.assertTrue(all(not path.exists() for path in managed))
        self.assertTrue(other_product.exists())
        self.assertTrue(unrelated_png.exists())
        self.assertTrue(unrelated_text.exists())
        self.assertTrue(latest.exists())
        self.assertTrue(cached.exists())

    def test_minimal_config_uses_current_defaults(self):
        config = self.root / "minimal.toml"
        config.write_text('[service]\nupdate_interval_minutes = 7.0\n', encoding="utf-8")
        app.SOURCE_PROFILES["goes_east"]["area"] = "changed_area"
        app.SHOW_DOWNLOAD_SPEED = False
        app.DOWNLOAD_SPEED_UNIT = "Mbit/s"
        app.SHOW_DOWNLOAD_PROGRESS = False
        app.SHOW_DOWNLOAD_PROGRESS_BAR = False
        app.KEEP_COMPLETED_DOWNLOAD_VISIBLE = True
        app.load_configuration(config)
        self.assertEqual(app.IMAGE_SOURCE, "eumetsat")
        self.assertEqual(app.SOURCE_PROFILES["goes_east"]["area"], "full_disk")
        self.assertEqual(app.SOURCE_PROFILES["solar"]["product"], "Fe171")
        self.assertTrue(app.SHOW_DOWNLOAD_SPEED)
        self.assertEqual(app.DOWNLOAD_SPEED_UNIT, "automatic")
        self.assertTrue(app.SHOW_DOWNLOAD_PROGRESS)
        self.assertTrue(app.SHOW_DOWNLOAD_PROGRESS_BAR)
        self.assertFalse(app.KEEP_COMPLETED_DOWNLOAD_VISIBLE)
        self.assertEqual(app.UPDATE_INTERVAL_MINUTES, 7.0)
        self.assertEqual(app.DISPLAY_TIME_ZONE, "system")
        self.assertEqual(
            app.PROFILE_LIST_VISIBLE_COLUMNS,
            app.DEFAULT_PROFILE_LIST_COLUMNS,
        )

    def test_profile_list_columns_are_loaded_and_saved_in_configuration(self):
        config = self.root / "profile-list.toml"
        config.write_text(
            '[profile_list]\nvisible_columns = ["name", "location", "latitude", "longitude"]\n',
            encoding="utf-8",
        )
        app.load_configuration(config)
        self.assertEqual(
            app.PROFILE_LIST_VISIBLE_COLUMNS,
            ("name", "location", "latitude", "longitude"),
        )

        older = '[source]\nprovider = "eumetsat"\n'
        updated = app.ensure_profile_list_configuration_section(older)
        updated = app.replace_toml_section_value(
            updated,
            "profile_list",
            "visible_columns",
            ["name", "latitude", "longitude"],
        )
        self.assertEqual(
            tomllib.loads(updated)["profile_list"]["visible_columns"],
            ["name", "latitude", "longitude"],
        )

    def test_source_defaults_and_normalized_profiles_are_independent_copies(self):
        provider, first = app.normalize_source_configuration("eumetsat", {})
        _, second = app.normalize_source_configuration("eumetsat", {})
        self.assertEqual(provider, "eumetsat")
        for source in app.AUTO_RESOLUTION_PROVIDERS:
            self.assertEqual(app.DEFAULT_SOURCE_PROFILES[source]["resolution"], "auto")
            self.assertEqual(second[source]["resolution"], "auto")
        first["goes_east"]["product"] = "13"
        self.assertEqual(second["goes_east"]["product"], "GEOCOLOR")
        self.assertEqual(app.DEFAULT_SOURCE_PROFILES["goes_east"]["product"], "GEOCOLOR")
        self.assertEqual(second["himawari"], {
            "area": "nict_full_disk", "product": "true_color", "resolution": "auto"
        })
        self.assertEqual(second["slider"], {
            "area": "goes-19---full_disk", "product": "geocolor", "resolution": "auto"
        })
        self.assertEqual(second["worldview"], {
            "area": "VIIRS_NOAA20_CorrectedReflectance_TrueColor",
            "product": "latest", "resolution": "auto",
        })
        source = {"solar": {"area": "sun", "product": "Fe094", "resolution": "600x600"}}
        _, normalized = app.normalize_source_configuration("solar", source)
        source["solar"]["product"] = "Fe304"
        self.assertEqual(normalized["solar"]["product"], "Fe094")

    def test_first_start_uses_auto_and_later_preserves_user_resolution(self):
        template = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        packaged_defaults = tomllib.loads(template)
        for provider in app.AUTO_RESOLUTION_PROVIDERS:
            self.assertEqual(packaged_defaults["sources"][provider]["resolution"], "auto")
        for provider in app.AUTO_RESOLUTION_PROVIDERS:
            template = app.replace_toml_section_value(
                template, f"sources.{provider}", "resolution", "largest"
            )
        template_path = self.root / "old-defaults.toml"
        config_path = self.root / "fresh-settings.toml"
        template_path.write_text(template, encoding="utf-8")
        with patch.object(app, "DEFAULT_CONFIG_PATH", config_path), \
             patch.object(app, "DEFAULT_CONFIG_TEMPLATE_PATH", template_path):
            app.load_configuration(config_path)
            created = tomllib.loads(config_path.read_text(encoding="utf-8"))
            for provider in app.AUTO_RESOLUTION_PROVIDERS:
                self.assertEqual(created["sources"][provider]["resolution"], "auto")
                self.assertEqual(app.SOURCE_PROFILES[provider]["resolution"], "auto")
            updated = app.replace_toml_section_value(
                config_path.read_text(encoding="utf-8"),
                "sources.goes_west", "resolution", "largest",
            )
            config_path.write_text(updated, encoding="utf-8")
            app.load_configuration(config_path)
            self.assertEqual(app.SOURCE_PROFILES["goes_west"]["resolution"], "largest")
            self.assertEqual(app.SOURCE_PROFILES["goes_east"]["resolution"], "auto")

    def test_automatic_resolution_uses_smallest_source_that_avoids_upscaling(self):
        client = SimpleNamespace(list_products=Mock(return_value=[{
            "id": "GEOCOLOR",
            "resolutions": ["900x540", "1800x1080", "3600x2160"],
        }]))
        profile = {"area": "full_disk", "product": "GEOCOLOR", "resolution": "auto"}

        self.assertEqual(
            app.choose_automatic_source_resolution(
                client, "goes_east", profile, (1920, 1080), "fit", 1.0
            ),
            "1800x1080",
        )
        self.assertEqual(
            app.choose_automatic_source_resolution(
                client, "goes_east", profile, (1920, 1080), "crop", 1.0
            ),
            "3600x2160",
        )
        self.assertEqual(
            app.choose_automatic_source_resolution(
                client, "goes_east", profile, (1920, 1080), "fit", 2.0
            ),
            "3600x2160",
        )

    def test_automatic_resolution_covers_all_active_monitors(self):
        client = SimpleNamespace(list_products=Mock(return_value=[{
            "id": "GEOCOLOR",
            "resolutions": ["1280x720", "2560x1440", "3072x2048"],
        }]))
        profile = {"area": "full_disk", "product": "GEOCOLOR", "resolution": "auto"}
        monitors = [
            {"id": "A", "rect": (0, 0, 2560, 1440)},
            {"id": "B", "rect": (-1050, 0, 0, 1680)},
        ]
        with patch.object(app, "SET_WINDOWS_WALLPAPER", True), \
             patch.object(app, "VIEW_MODE", "crop"), \
             patch.object(app, "ZOOM", 1.0), \
             patch.object(app, "WINDOWS_WALLPAPER_POSITION", "fit"), \
             patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS", {}), \
             patch.object(app, "WINDOWS_WALLPAPER_MONITOR_OUTPUTS", {}), \
             patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors):
            self.assertEqual(app.automatic_source_output_size((1280, 720)),
                             (2560, 1680))
            self.assertEqual(app._resolved_profile_resolution(
                client, "goes_east", profile, (1280, 720)), "3072x2048")
            profile["resolution"] = "2560x1440"
            self.assertEqual(app._resolved_profile_resolution(
                client, "goes_east", profile, (1280, 720)), "2560x1440")
            with patch.object(app, "WINDOWS_WALLPAPER_MONITOR_OUTPUTS", {
                "B": {"width": 4000, "height": 0, "aspect_ratio": "16:9"},
            }):
                self.assertEqual(app.automatic_source_output_size((1280, 720)),
                                 (4000, 2250))

    def test_automatic_resolution_ignores_monitors_with_no_wallpaper(self):
        monitors = [
            {"id": "A", "rect": (0, 0, 2560, 1440)},
            {"id": "B", "rect": (2560, 0, 3840, 720)},
        ]
        with patch.object(app, "SET_WINDOWS_WALLPAPER", True), \
             patch.object(app, "WINDOWS_WALLPAPER_POSITION", "fit"), \
             patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS", {"A": "none"}), \
             patch.object(app, "WINDOWS_WALLPAPER_MONITOR_OUTPUTS", {}), \
             patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors):
            self.assertEqual(app.automatic_source_output_size((800, 600)),
                             (1280, 720))

    def test_disabled_updates_reuse_matching_latest_without_provider_contact(self):
        app.IMAGE_SOURCE = "eumetsat"
        app.CHECK_FOR_SOURCE_UPDATES = False
        app.ensure_directories()
        signature = app.image_cache_configuration_key(app.capture_loaded_configuration())
        data = self.make_png({}, (32, 18))
        installed = app.save_latest_image(data, signature, (32, 18))
        self.assertIsNotNone(installed)

        app.main(["--once"], configuration_loaded=True)

        self.wms.assert_not_called()
        self.client.latest.assert_not_called()
        self.assertEqual(app.get_current_image_path(), installed)

    def test_force_update_bypasses_matching_saved_image_when_checks_are_disabled(self):
        app.IMAGE_SOURCE = "goes_east"
        app.CHECK_FOR_SOURCE_UPDATES = False
        app.ensure_directories()
        signature = app.image_cache_configuration_key(app.capture_loaded_configuration())
        app.save_latest_image(self.make_png({}, (32, 18)), signature, (32, 18))
        app.FORCE_UPDATE_EVENT.set()

        app.main(["--once"], configuration_loaded=True)

        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_called_once()

    def test_invalid_source_profiles_are_rejected_before_network_access(self):
        cases = [
            ("unknown", {}),
            ("goes_east", []),
            ("goes_east", {"goes_east": "invalid"}),
            ("goes_east", {"goes_east": {"area": ""}}),
            ("goes_east", {"goes_east": {"product": 13}}),
            ("goes_east", {"goes_east": {"resolution": "0x1080"}}),
            ("goes_east", {"goes_east": {"resolution": "1920 by 1080"}}),
        ]
        for provider, profiles in cases:
            with self.subTest(provider=provider, profiles=profiles):
                with self.assertRaises(ValueError):
                    app.normalize_source_configuration(provider, profiles)
        self.client_factory.assert_not_called()
        self.wms.assert_not_called()

    def test_capture_and_restore_do_not_alias_source_profiles(self):
        app.SOURCE_PROFILES["goes_east"]["product"] = "13"
        snapshot = app.capture_loaded_configuration()
        app.IMAGE_SOURCE = "solar"
        app.SOURCE_PROFILES["goes_east"]["product"] = "01"
        self.assertEqual(snapshot["SOURCE_PROFILES"]["goes_east"]["product"], "13")
        app.restore_loaded_configuration(snapshot)
        self.assertEqual(app.IMAGE_SOURCE, "goes_east")
        self.assertEqual(app.SOURCE_PROFILES["goes_east"]["product"], "13")
        app.SOURCE_PROFILES["goes_east"]["product"] = "02"
        self.assertEqual(snapshot["SOURCE_PROFILES"]["goes_east"]["product"], "13")

    def test_display_time_zone_does_not_change_the_image_cache_key(self):
        configuration = app.capture_loaded_configuration()
        changed = deepcopy(configuration)
        changed["DISPLAY_TIME_ZONE"] = "utc"
        self.assertEqual(
            app.image_configuration_key(configuration),
            app.image_configuration_key(changed),
        )

    def test_example_remains_valid_for_eumetsat_without_live_lookup(self):
        app.load_configuration(app.DEFAULT_CONFIG_TEMPLATE_PATH)
        self.assertEqual(app.IMAGE_SOURCE, "eumetsat")
        app.validate_configuration()
        self.assertEqual(app.get_selected_wms_layer_name(), "mtg_fd:rgb_geocolour")
        self.wms.assert_not_called()

    def test_noaa_render_plan_does_not_resolve_wms_or_cap_source_to_4000(self):
        app.WIDTH, app.HEIGHT, app.ASPECT_RATIO = 7680, 4320, "16:9"
        app.PROJECTION = "unused projection"
        app.LAYER_CONFIG = []
        with patch.object(app, "get_active_view", side_effect=AssertionError("WMS view")), \
             patch.object(app, "resolve_configured_layers", side_effect=AssertionError("WMS layer")), \
             patch.object(app, "get_render_dimensions", side_effect=AssertionError("WMS size cap")):
            app.validate_configuration()
            plan = app.prepare_runtime_render_plan({})
        self.assertEqual(plan[:4], (7680, 4320, 7680, 4320))
        self.assertEqual(plan[9], "noaa")
        self.client_factory.assert_not_called()
        self.wms.assert_not_called()

    def test_once_each_noaa_source_installs_png_without_wms_or_active_config_write(self):
        sentinel = b"Do not modify this configuration."
        app.ACTIVE_CONFIG_PATH.write_bytes(sentinel)
        for provider in ("goes_east", "goes_west", "solar"):
            with self.subTest(provider=provider):
                app.IMAGE_SOURCE = provider
                app.main(["--once"], configuration_loaded=True)
                profile = app.SOURCE_PROFILES[provider]
                args = self.client.latest.call_args.args
                self.assertEqual(args[:3], (provider, profile["area"], profile["product"]))
                self.assertNotEqual(args[3], "auto")
                self.assert_installed_png()
                self.assertEqual(app.IMAGE_STATUS["provider"], provider)
                self.assertEqual(app.ACTIVE_CONFIG_PATH.read_bytes(), sentinel)
        self.wms.assert_not_called()
        self.wallpaper.assert_not_called()
        self.assertEqual(self.client.fetch_image.call_count, 3)

    def test_once_himawari_source_installs_png_without_wms(self):
        app.IMAGE_SOURCE = "himawari"
        frame = dict(self.frame, source="himawari", kind="nict")
        client = SimpleNamespace(
            latest=Mock(return_value=frame),
            fetch_image=Mock(side_effect=self.make_png),
        )
        with patch.object(app, "get_himawari_client", return_value=client):
            app.main(["--once"], configuration_loaded=True)
        profile = app.SOURCE_PROFILES["himawari"]
        client.latest.assert_called_once_with(
            "himawari", profile["area"], profile["product"], "largest"
        )
        client.fetch_image.assert_called_once()
        self.assertEqual(app.IMAGE_STATUS["provider"], "himawari")
        self.assert_installed_png()
        self.wms.assert_not_called()

    def test_once_slider_source_installs_latest_clean_tiled_png_without_wms(self):
        app.IMAGE_SOURCE = "slider"
        frame = dict(self.frame, source="slider", expected_interval_seconds=600)
        client = SimpleNamespace(
            latest=Mock(return_value=frame),
            fetch_image=Mock(side_effect=self.make_png),
        )
        with patch.object(app, "get_slider_client", return_value=client):
            app.main(["--once"], configuration_loaded=True)
        profile = app.SOURCE_PROFILES["slider"]
        client.latest.assert_called_once_with(
            "slider", profile["area"], profile["product"], "largest"
        )
        client.fetch_image.assert_called_once()
        self.assertEqual(app.IMAGE_STATUS["provider"], "slider")
        self.assert_installed_png()
        self.wms.assert_not_called()

    def test_once_worldview_source_installs_latest_gibs_png_without_eumetsat_wms(self):
        app.IMAGE_SOURCE = "worldview"
        frame = dict(
            self.frame, source="worldview", expected_interval_seconds=86400,
            fixed_time=False,
        )
        client = SimpleNamespace(
            latest=Mock(return_value=frame),
            fetch_image=Mock(side_effect=self.make_png),
        )
        with patch.object(app, "get_worldview_client", return_value=client):
            app.main(["--once"], configuration_loaded=True)
        profile = app.SOURCE_PROFILES["worldview"]
        client.latest.assert_called_once_with(
            "worldview", profile["area"], profile["product"], "largest"
        )
        client.fetch_image.assert_called_once()
        self.assertEqual(app.IMAGE_STATUS["provider"], "worldview")
        self.assert_installed_png()
        self.wms.assert_not_called()

    def test_print_urls_and_validate_config_do_not_fetch_or_install_images(self):
        for option in ("--print-urls", "--validate-config"):
            with self.subTest(option=option):
                output = io.StringIO()
                with redirect_stdout(output):
                    app.main([option], configuration_loaded=True)
                if option == "--print-urls":
                    self.assertIn(self.frame["url"], output.getvalue())
                else:
                    self.assertTrue(any(
                        "valid against the NOAA catalog" in str(call)
                        for call in self.log.call_args_list
                    ))
        self.client.fetch_image.assert_not_called()
        self.wms.assert_not_called()
        self.assertFalse(app.LATEST_DIR.exists())

    def test_once_download_failure_propagates_and_runner_returns_nonzero(self):
        self.client.fetch_image.side_effect = RuntimeError("image incomplete")
        with self.assertRaisesRegex(RuntimeError, "image incomplete"):
            app.main(["--once"], configuration_loaded=True)
        self.assertEqual(app.get_latest_image_files(), [])
        self.assertIsNone(app.IMAGE_STATUS["timestamp"])
        self.assertIn("image incomplete", app.IMAGE_STATUS["error"])
        exit_code = app.run_application(
            ["--once"], configuration_loaded=True, pause_on_error=False
        )
        self.assertNotEqual(exit_code, 0)
        self.wms.assert_not_called()

    def test_failed_download_keeps_previous_wallpaper(self):
        app.main(["--once"], configuration_loaded=True)
        existing = self.assert_installed_png()
        original = existing.read_bytes()
        self.client.latest.return_value = dict(
            self.frame,
            timestamp="2026-09-11T12:10:00Z",
            url=self.frame["url"].replace("1200_", "1210_"),
        )
        self.client.fetch_image.side_effect = RuntimeError("HTTP 503")
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            app.main(["--once"], configuration_loaded=True)
        self.assertEqual(app.get_latest_image_files(), [existing])
        self.assertEqual(existing.read_bytes(), original)

    def test_reload_during_fetch_prevents_installing_obsolete_frame(self):
        def fetch_then_change_source(frame, dimensions, **options):
            data = self.make_png(frame, dimensions, **options)
            app.CONFIGURATION_RELOAD_EVENT.set()
            return data
        self.client.fetch_image.side_effect = fetch_then_change_source
        with patch.object(app, "save_latest_image") as save_image:
            with self.assertRaisesRegex(RuntimeError, "configuration changed"):
                app.perform_update("noaa", [{"frame": self.frame}], 32, 18, 32, 18)
        save_image.assert_not_called()
        self.assertIsNone(app.IMAGE_STATUS["timestamp"])

    def test_user_cancel_keeps_existing_image_and_is_not_a_source_error(self):
        existing = app.LATEST_DIR / "marblescape_existing.png"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(self.make_png({}, (32, 18)))
        app.set_current_image_path(existing)

        def cancel_fetch(frame, dimensions, **options):
            del frame, dimensions, options
            self.assertTrue(app.DOWNLOAD_PROGRESS.request_cancel())
            app.DOWNLOAD_PROGRESS.raise_if_cancelled()

        self.client.fetch_image.side_effect = cancel_fetch
        app.RUN_CONTINUOUSLY = True
        with patch.object(app, "sleep_until_next_cycle", return_value=False), \
             patch.object(app, "save_latest_image") as save_image:
            app.main([], configuration_loaded=True)

        save_image.assert_not_called()
        self.assertEqual(app.get_current_image_path(), existing)
        self.assertEqual(app.IMAGE_STATUS["error"], "")
        self.assertTrue(app.DOWNLOAD_PROGRESS.snapshot()["cancelled"])

    def test_signature_distinguishes_source_area_product_size_time_and_url(self):
        baseline = app.noaa_frame_signature(self.frame)
        for field, value in (("area", "continental"), ("product", "13"), ("resolution", "678x678")):
            with self.subTest(field=field):
                old = app.SOURCE_PROFILES["goes_east"][field]
                app.SOURCE_PROFILES["goes_east"][field] = value
                self.assertNotEqual(app.noaa_frame_signature(self.frame), baseline)
                app.SOURCE_PROFILES["goes_east"][field] = old
        app.IMAGE_SOURCE = "goes_west"
        self.assertNotEqual(app.noaa_frame_signature(self.frame), baseline)
        app.IMAGE_SOURCE = "goes_east"
        for field, value in (("timestamp", "2026-09-11T12:10:00Z"), ("url", self.frame["url"] + "?updated")):
            with self.subTest(field=field):
                frame = dict(self.frame, **{field: value})
                self.assertNotEqual(app.noaa_frame_signature(frame), baseline)

    def test_unchanged_frame_skips_second_download(self):
        app.RUN_CONTINUOUSLY = True
        with patch.object(app, "sleep_until_next_cycle", side_effect=[True, False]):
            app.main([], configuration_loaded=True)
        self.assertEqual(self.client.latest.call_count, 2)
        self.client.fetch_image.assert_called_once()
        self.assert_installed_png()

    def test_matching_frame_reuses_latest_after_runtime_restart(self):
        app.main(["--once"], configuration_loaded=True)
        installed = app.get_current_image_path()
        app.set_current_image_path(None)

        app.main(["--once"], configuration_loaded=True)

        self.assertEqual(self.client.latest.call_count, 2)
        self.client.fetch_image.assert_called_once()
        self.assertEqual(app.get_current_image_path(), installed)

    def test_matching_frame_migrates_legacy_latest_state_without_download(self):
        app.ensure_directories()
        signature = app.image_cache_configuration_key(app.capture_loaded_configuration())
        installed = app.save_latest_image(
            self.make_png({}, (32, 18)),
            signature,
            (32, 18),
            source_time=self.frame["timestamp"],
        )
        state_path = app._latest_state_path()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["version"] = 1
        state.pop("source_hash", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        app.main(["--once"], configuration_loaded=True)

        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_not_called()
        self.assertEqual(app.get_current_image_path(), installed)
        migrated = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["source_hash"], app.signature_digest(
            app.noaa_frame_signature(self.frame)
        ))

    def test_force_refresh_downloads_same_frame_without_duplicate_history(self):
        app.RUN_CONTINUOUSLY = True
        app.ENABLE_HISTORY = True
        cycles = iter((True, False))
        def next_cycle(*args, **kwargs):
            more = next(cycles)
            if more:
                app.FORCE_UPDATE_EVENT.set()
            return more
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main([], configuration_loaded=True)
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assert_installed_png()
        self.assertEqual(list(app.HISTORY_DIR.glob("*.png")), [])

    def test_failed_frame_is_retried_before_signature_is_committed(self):
        app.RUN_CONTINUOUSLY = True
        png = self.make_png(self.frame, (32, 18))
        self.client.fetch_image.side_effect = [RuntimeError("temporary failure"), png]
        with patch.object(app, "sleep_until_next_cycle", side_effect=[True, False]):
            app.main([], configuration_loaded=True)
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assert_installed_png()
        self.assertEqual(app.IMAGE_STATUS["timestamp"], self.frame["timestamp"])
        self.assertEqual(app.IMAGE_STATUS["error"], "")

    def test_older_source_timestamp_does_not_replace_newer_installed_frame(self):
        app.RUN_CONTINUOUSLY = True
        older = dict(self.frame, timestamp="2026-09-11T11:50:00Z")
        self.client.latest.side_effect = [self.frame, older]
        with patch.object(app, "sleep_until_next_cycle", side_effect=[True, False]):
            app.main([], configuration_loaded=True)
        self.client.fetch_image.assert_called_once()
        self.assert_installed_png()
        self.assertEqual(app.IMAGE_STATUS["timestamp"], self.frame["timestamp"])
        self.assertIn("older image", app.IMAGE_STATUS["error"])

    def test_fractional_second_advance_is_newer_than_exact_second(self):
        app.RUN_CONTINUOUSLY = True
        advanced = dict(self.frame, timestamp="2026-09-11T12:00:00.100000Z")
        self.client.latest.side_effect = [self.frame, advanced]
        with patch.object(app, "sleep_until_next_cycle", side_effect=[True, False]):
            app.main([], configuration_loaded=True)
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assert_installed_png()
        self.assertEqual(app.IMAGE_STATUS["timestamp"], advanced["timestamp"])
        self.assertEqual(app.IMAGE_STATUS["error"], "")

    def test_source_serialization_adds_tables_to_old_toml_and_preserves_wms(self):
        existing = (
            '# Keep this comment.\n[service]\nendpoint = "https://example.invalid/wms"\n'
            '\n[[layers]]\nkind = "wms"\nname = "existing:layer"\nenabled = true\n'
        )
        profiles = deepcopy(app.SOURCE_PROFILES)
        profiles["solar"].update(product="Fe094", resolution="600x600")
        updated = app.replace_source_configuration(existing, "solar", profiles)
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["source"]["provider"], "solar")
        self.assertEqual(parsed["sources"], profiles)
        self.assertEqual(parsed["service"]["endpoint"], "https://example.invalid/wms")
        self.assertEqual(parsed["layers"][0]["name"], "existing:layer")
        self.assertIn("# Keep this comment.", updated)
        repeated = app.replace_source_configuration(updated, "solar", profiles)
        self.assertEqual(tomllib.loads(repeated), parsed)
        updates = app.source_configuration_updates("solar", profiles)
        self.assertEqual(dict(((section, key), value) for section, key, value in updates)[
            ("sources.solar", "product")
        ], "Fe094")

    def test_eumetsat_gap_fill_uses_older_passes_then_latest(self):
        app.IMAGE_SOURCE = "eumetsat"
        layer_name = "copernicus:sentinel3a_olci_l1_rgb_fullres"
        app.SOURCE_PROFILES["eumetsat"].update(
            theme="weather_monitoring",
            satellite="Sentinel-3A",
            mission="Sentinel-3",
            product_type="RGB Composites",
            layer=layer_name,
            orbit_type="LEO",
            fill_gaps=True,
            gap_fill_lookback_hours=12,
        )
        metadata = {
            "dimensions": {"time": {
                "default": "2026-09-20T06:15:00Z",
                "values": (
                    "2020-02-17T03:01:00.000Z/"
                    "2026-09-20T06:15:00.000Z/PT1H41M"
                ),
            }},
        }
        resolved = [{
            "kind": "wms", "name": layer_name, "style": "", "opacity": 1.0,
            "time": "2026-09-20T06:15:00Z", "metadata": metadata,
        }]

        mode, requests = app.build_render_plan(
            resolved, app.PROJECTIONS["Geographic"], "-90,-180,90,180", 32, 18
        )

        self.assertEqual(mode, "local")
        times = [parse_qs(urlparse(item["url"]).query)["time"][0]
                 for item in requests]
        self.assertEqual(times[-1], "2026-09-20T06:15:00Z")
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), 8)
        self.assertTrue(all(item["role"] == "gap_fill" for item in requests))
        self.assertTrue(all(
            parse_qs(urlparse(item["url"]).query)["transparent"] == ["true"]
            for item in requests
        ))

    def test_eumetsat_accumulated_layer_cannot_enable_gap_fill(self):
        profile = app.normalize_eumetsat_profile({
            "theme": "weather_monitoring",
            "satellite": "Sentinel-3 (A + B)",
            "mission": "Sentinel-3",
            "product_type": "RGB Composites",
            "layer": "copernicus:daily_sentinel3ab_olci_l1_rgb_fulres",
            "orbit_type": "LEO",
            "fill_gaps": True,
            "gap_fill_lookback_hours": 24,
        })
        self.assertFalse(profile["fill_gaps"])

    def test_eumetsat_gap_fill_fetches_latest_first_and_fills_only_transparency(self):
        requests = [
            {"label": "oldest", "role": "gap_fill"},
            {"label": "previous", "role": "gap_fill"},
            {"label": "latest", "role": "gap_fill"},
        ]
        latest = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
        latest.putpixel((1, 0), (0, 0, 255, 255))
        previous = Image.new("RGBA", (2, 1), (255, 0, 0, 255))
        oldest = Image.new("RGBA", (2, 1), (0, 255, 0, 255))
        images = {"latest": latest, "previous": previous, "oldest": oldest}
        order = []

        def layer(request, _width, _height):
            order.append(request["label"])
            return images[request["label"]].copy(), 10

        app.BACKGROUND_COLOR = "#000000"
        with patch.object(app, "download_rendered_layer", side_effect=layer):
            data, downloaded = app.compose_rendered_layers(requests, 2, 1)

        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(result.getpixel((1, 0)), (0, 0, 255))
        self.assertEqual(order, ["latest", "previous"])
        self.assertEqual(downloaded, 20)

    def test_eumetsat_gap_fill_skips_failed_optional_older_pass(self):
        requests = [
            {"label": "oldest", "role": "gap_fill"},
            {"label": "failed", "role": "gap_fill"},
            {"label": "latest", "role": "gap_fill"},
        ]
        latest = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        oldest = Image.new("RGBA", (1, 1), (0, 255, 0, 255))
        order = []

        def layer(request, _width, _height):
            order.append(request["label"])
            if request["label"] == "failed":
                raise RuntimeError("temporary server error")
            image = latest if request["label"] == "latest" else oldest
            return image.copy(), 10

        app.BACKGROUND_COLOR = "#000000"
        with patch.object(app, "download_rendered_layer", side_effect=layer):
            data, downloaded = app.compose_rendered_layers(requests, 1, 1)

        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.getpixel((0, 0)), (0, 255, 0))
        self.assertEqual(order, ["latest", "failed", "oldest"])
        self.assertEqual(downloaded, 20)

    def test_backup_roundtrip_retains_source_profiles(self):
        profiles = deepcopy(app.SOURCE_PROFILES)
        profiles["goes_west"].update(product="13", resolution="10848x10848")
        original = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        updated = app.replace_source_configuration(original, "goes_west", profiles)
        app.ACTIVE_CONFIG_PATH.write_text(updated, encoding="utf-8", newline="")
        with patch.object(app, "is_windows_startup_enabled", return_value=False):
            payload = app.create_settings_backup_payload()
        restored, restored_profiles, startup = app.parse_settings_backup_payload(payload)
        self.assertFalse(startup)
        self.assertEqual(restored_profiles, app.IMAGE_PROFILE_LIBRARY)
        self.assertEqual(restored, updated)
        self.assertEqual(payload["settings"]["sources"], profiles)
        app.IMAGE_SOURCE = "solar"
        app.load_configuration(app.ACTIVE_CONFIG_PATH)
        self.assertEqual(app.IMAGE_SOURCE, "goes_west")
        self.assertEqual(app.SOURCE_PROFILES, profiles)


if __name__ == "__main__":
    unittest.main()
