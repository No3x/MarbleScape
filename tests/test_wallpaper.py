"""Wallpaper COM and runtime tests; no calls reach the real Windows desktop.

The enum contract follows Microsoft's DESKTOP_WALLPAPER_POSITION documentation:
https://learn.microsoft.com/windows/win32/api/shobjidl_core/ne-shobjidl_core-desktop_wallpaper_position
"""

from contextlib import ExitStack
from copy import deepcopy
import ctypes
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import marblescape_download as app


@unittest.skipUnless(os.name == "nt", "Windows COM wallpaper contract")
class WallpaperCOMTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.events = []
        self.method_signatures = []
        self.pointer = ctypes.c_void_p(12345)
        self.position_result = 0
        self.wallpaper_result = 0
        self.initialize_result = 0
        self.ole32 = SimpleNamespace(
            CoInitialize=Mock(side_effect=self.initialize),
            CoUninitialize=Mock(side_effect=lambda: self.events.append(("uninitialize",))),
        )
        self.stack.enter_context(patch.object(app.ctypes, "windll", SimpleNamespace(ole32=self.ole32)))
        self.stack.enter_context(patch.object(app, "create_desktop_wallpaper_interface", side_effect=self.create_interface))
        self.stack.enter_context(patch.object(app, "get_com_method", side_effect=self.get_method))
        self.stack.enter_context(patch.object(app, "list_windows_wallpaper_monitors", return_value=[]))
        self.stack.enter_context(patch.object(app, "log"))
        self.stack.enter_context(patch.object(app, "WINDOWS_WALLPAPER_POSITION", "fit"))

    def initialize(self, reserved):
        self.assertIsNone(reserved)
        self.events.append(("initialize",))
        return self.initialize_result

    def create_interface(self):
        self.events.append(("create",))
        return self.pointer

    def get_method(self, pointer, index, restype, *argtypes):
        self.assertIs(pointer, self.pointer)
        self.method_signatures.append((index, restype, argtypes))
        if index == 10:
            def position(interface, value):
                self.assertIs(interface, self.pointer)
                self.events.append(("position", value))
                return self.position_result
            return position
        if index == 3:
            def wallpaper(interface, monitor, filename):
                self.assertIs(interface, self.pointer)
                self.events.append(("wallpaper", monitor, filename))
                return self.wallpaper_result
            return wallpaper
        if index == 2:
            def release(interface):
                self.assertIs(interface, self.pointer)
                self.events.append(("release",))
                return 0
            return release
        self.fail(f"Unexpected COM vtable index: {index}")

    def test_six_windows_modes_set_position_before_wallpaper_and_release_com(self):
        # These values come from the Windows ABI, not from the app's mapping.
        expected_modes = {"center": 0, "tile": 1, "stretch": 2, "fit": 3, "fill": 4, "span": 5}
        self.assertEqual(app.WINDOWS_WALLPAPER_POSITIONS, expected_modes)
        image_path = Path("wallpaper test.png")
        for mode, value in expected_modes.items():
            with self.subTest(mode=mode):
                self.events.clear()
                self.method_signatures.clear()
                app.WINDOWS_WALLPAPER_POSITION = mode
                app.set_windows_wallpaper(image_path)
                self.assertEqual(self.events, [
                    ("initialize",), ("create",), ("position", value),
                    ("wallpaper", None, str(image_path.resolve())),
                    ("release",), ("uninitialize",),
                ])
                self.assertEqual(self.method_signatures, [
                    (3, ctypes.c_long, (ctypes.c_wchar_p, ctypes.c_wchar_p)),
                    (10, ctypes.c_long, (ctypes.c_int,)),
                    (2, ctypes.c_ulong, ()),
                ])

    def test_failed_position_does_not_set_image_and_still_releases_com(self):
        self.position_result = ctypes.c_long(0x80004005).value
        with self.assertRaisesRegex(OSError, "SetPosition.*80004005"):
            app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertEqual([event[0] for event in self.events],
                         ["initialize", "create", "position", "release", "uninitialize"])

    def test_failed_wallpaper_still_releases_com(self):
        self.wallpaper_result = ctypes.c_long(0x80004005).value
        with self.assertRaisesRegex(OSError, "SetWallpaper.*80004005"):
            app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertEqual([event[0] for event in self.events],
                         ["initialize", "create", "position", "wallpaper", "release", "uninitialize"])

    def test_s_false_initialization_and_position_are_successful_and_balanced(self):
        self.initialize_result = self.position_result = 1
        app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertEqual(self.ole32.CoUninitialize.call_count, 1)
        self.assertEqual([event[0] for event in self.events][-3:], ["wallpaper", "release", "uninitialize"])

    def test_failed_com_initialization_does_not_create_or_uninitialize_interface(self):
        self.initialize_result = ctypes.c_long(0x80010106).value
        with self.assertRaisesRegex(OSError, "CoInitialize.*80010106"):
            app.set_windows_wallpaper(Path("wallpaper.png"))
        self.assertEqual(self.events, [("initialize",)])
        self.ole32.CoUninitialize.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows wallpaper runtime integration")
class WallpaperRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.saved_configuration = app.capture_loaded_configuration()
        self.saved_image_status = deepcopy(app.IMAGE_STATUS)
        self.saved_rotation_status = deepcopy(app.ROTATION_STATUS)
        self.saved_rotation_deadline = app.NEXT_ROTATION_DEADLINE
        self.events = (app.APPLICATION_STOP_EVENT, app.CONFIGURATION_RELOAD_EVENT, app.FORCE_UPDATE_EVENT)
        self.saved_event_states = [event.is_set() for event in self.events]
        for event in self.events:
            event.clear()
        self.temporary = tempfile.TemporaryDirectory(prefix="marblescape-wallpaper-test-")
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "config.toml"
        config = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
        config = app.replace_source_configuration(config, "goes_east", app.DEFAULT_SOURCE_PROFILES)
        config = app.replace_toml_values(config, [
            ("output", "windows_root", self.root.as_posix()),
            ("output", "linux_root", self.root.as_posix()),
            ("output", "width", 32), ("output", "height", 18),
            ("output", "aspect_ratio", "16:9"),
            ("history", "enabled", False),
            ("service", "run_continuously", True),
            ("windows", "set_wallpaper", True),
            ("windows", "position", "fit"),
            ("windows", "pause_on_error", False),
        ])
        self.config_path.write_text(config, encoding="utf-8")
        buffer = io.BytesIO()
        with Image.new("RGB", (32, 18), (30, 70, 90)) as image:
            image.save(buffer, format="PNG")
        self.frame = {
            "url": "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/FD/GEOCOLOR/20262550000_GOES19-ABI-FD-GEOCOLOR-1808x1808.jpg",
            "timestamp": "2026-09-12T00:00:00Z", "expected_interval_seconds": 600,
        }
        self.client = SimpleNamespace(latest=Mock(return_value=self.frame), fetch_image=Mock(return_value=buffer.getvalue()))
        self.wallpaper_calls = []
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(
            app, "PROFILE_LIBRARY_PATH", self.root / "profiles.toml"
        ))
        self.stack.enter_context(patch.object(app, "get_noaa_client", return_value=self.client))
        self.stack.enter_context(patch.object(app, "download_capabilities", side_effect=AssertionError("Unexpected WMS request")))
        self.stack.enter_context(patch.object(app, "urlopen", side_effect=AssertionError("Unexpected network request")))
        self.stack.enter_context(patch.object(app, "log"))
        self.wallpaper = self.stack.enter_context(patch.object(app, "set_windows_wallpaper", side_effect=self.record_wallpaper))

    def tearDown(self):
        try:
            app.restore_loaded_configuration(self.saved_configuration)
            app.IMAGE_STATUS.clear()
            app.IMAGE_STATUS.update(self.saved_image_status)
            app.ROTATION_STATUS.clear()
            app.ROTATION_STATUS.update(self.saved_rotation_status)
            app.NEXT_ROTATION_DEADLINE = self.saved_rotation_deadline
            for event, was_set in zip(self.events, self.saved_event_states):
                event.set() if was_set else event.clear()
        finally:
            self.stack.close()
            self.temporary.cleanup()

    def record_wallpaper(self, path):
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(self.root))
        self.wallpaper_calls.append((path, app.WINDOWS_WALLPAPER_POSITION))

    def test_reloaded_position_reapplies_identical_image_path(self):
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "span")])
                self.config_path.write_text(config, encoding="utf-8")
                app.CONFIGURATION_RELOAD_EVENT.set()
                return True
            return False
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual([mode for _, mode in self.wallpaper_calls], ["fit", "span"])
        self.assertEqual(self.wallpaper_calls[0][0], self.wallpaper_calls[1][0])
        self.assertEqual(len(app.get_latest_image_files()), 1)
        self.assertEqual(app.WINDOWS_WALLPAPER_POSITION, "span")
        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_called_once()

    def test_position_change_applies_existing_image_even_when_noaa_is_offline(self):
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "span")])
                self.config_path.write_text(config, encoding="utf-8")
                self.client.fetch_image.side_effect = OSError("NOAA is offline")
                app.CONFIGURATION_RELOAD_EVENT.set()
                return True
            return False
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual([mode for _, mode in self.wallpaper_calls], ["fit", "span"])
        self.assertEqual(self.wallpaper_calls[0][0], self.wallpaper_calls[1][0])
        self.assertEqual(len(app.get_latest_image_files()), 1)
        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_called_once()
        self.assertEqual(app.IMAGE_STATUS["error"], "")

    def test_failed_position_change_retries_existing_image_while_noaa_stays_offline(self):
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "span")])
                self.config_path.write_text(config, encoding="utf-8")
                self.client.fetch_image.side_effect = OSError("NOAA is still offline")
                app.CONFIGURATION_RELOAD_EVENT.set()
            return cycles < 3
        def fail_first_span(path):
            self.record_wallpaper(path)
            if app.WINDOWS_WALLPAPER_POSITION == "span" and len(self.wallpaper_calls) == 2:
                raise OSError("Temporary Windows position failure")
        self.wallpaper.side_effect = fail_first_span
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual([mode for _, mode in self.wallpaper_calls], ["fit", "span", "span"])
        self.assertEqual(len({path for path, _ in self.wallpaper_calls}), 1)
        self.assertEqual(len(app.get_latest_image_files()), 1)
        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_called_once()
        self.assertEqual(app.IMAGE_STATUS["error"], "")

    def test_every_position_change_uses_local_image_without_checking_source(self):
        modes = iter(("center", "tile", "stretch", "fit", "fill", "span"))
        def next_cycle(*args, **kwargs):
            mode = next(modes, None)
            if mode is None:
                return False
            config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                            [("windows", "position", mode)])
            self.config_path.write_text(config, encoding="utf-8")
            app.CONFIGURATION_RELOAD_EVENT.set()
            return True
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual([mode for _, mode in self.wallpaper_calls],
                         ["fit", "center", "tile", "stretch", "fit", "fill", "span"])
        self.assertEqual(len({path for path, _ in self.wallpaper_calls}), 1)
        self.client.latest.assert_called_once()
        self.client.fetch_image.assert_called_once()

    def test_position_only_reload_preserves_next_regular_check_deadline(self):
        clock = [100.0]
        starts = []
        def next_cycle(start, *args, **kwargs):
            starts.append(start)
            if len(starts) == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "center")])
                self.config_path.write_text(config, encoding="utf-8")
                clock[0] = 110
                app.CONFIGURATION_RELOAD_EVENT.set()
            elif len(starts) == 2:
                self.assertEqual(app.NEXT_ROTATION_DEADLINE, 400)
                clock[0] = 399
            elif len(starts) == 3:
                clock[0] = 400
            return len(starts) < 4
        with patch.object(app.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual(starts, [100, 100, 100, 400])
        self.assertEqual(self.client.latest.call_count, 2)
        self.client.fetch_image.assert_called_once()

    def test_force_download_still_works_after_local_position_change(self):
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "span")])
                self.config_path.write_text(config, encoding="utf-8")
                app.CONFIGURATION_RELOAD_EVENT.set()
            elif cycles == 2:
                app.FORCE_UPDATE_EVENT.set()
            return cycles < 3
        with patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        self.assertEqual(self.client.fetch_image.call_count, 2)
        self.assertEqual(self.client.latest.call_count, 2)

    def test_eumetsat_position_change_does_not_rebuild_or_fetch_image(self):
        text = app.replace_source_configuration(self.config_path.read_text(encoding="utf-8"),
                                               "eumetsat", app.DEFAULT_SOURCE_PROFILES)
        self.config_path.write_text(text, encoding="utf-8")
        app.download_capabilities.side_effect = None
        app.download_capabilities.return_value = b"<Capabilities/>"
        path = self.root / "content" / "latest" / "marblescape_2026-09-12_00-00-00.png"
        def update(*args, **kwargs):
            path.write_bytes(self.client.fetch_image.return_value)
            return path, path, path.stat().st_size
        cycles = 0
        def next_cycle(*args, **kwargs):
            nonlocal cycles
            cycles += 1
            if cycles == 1:
                config = app.replace_toml_values(self.config_path.read_text(encoding="utf-8"),
                                                [("windows", "position", "tile")])
                self.config_path.write_text(config, encoding="utf-8")
                app.CONFIGURATION_RELOAD_EVENT.set()
                return True
            return False
        plan = (32, 18, 32, 18, 1.0, "", "", {}, [], "server", [], False, None)
        with patch.object(app, "parse_layers", return_value={}), \
             patch.object(app, "prepare_runtime_render_plan", return_value=plan) as prepare, \
             patch.object(app, "perform_update", side_effect=update) as fetch, \
             patch.object(app, "print_configuration"), \
             patch.object(app, "sleep_until_next_cycle", side_effect=next_cycle):
            app.main(["--config", str(self.config_path)])
        app.download_capabilities.assert_called_once()
        prepare.assert_called_once()
        fetch.assert_called_once()
        self.assertEqual([mode for _, mode in self.wallpaper_calls], ["fit", "tile"])

    def test_unchanged_frame_without_reload_does_not_reapply_wallpaper(self):
        with patch.object(app, "sleep_until_next_cycle", side_effect=[True, False]):
            app.main(["--config", str(self.config_path)])
        self.wallpaper.assert_called_once()
        self.client.fetch_image.assert_called_once()

    def test_transient_windows_failure_retries_existing_path_without_redownload(self):
        def fail_first(path):
            self.record_wallpaper(path)
            if len(self.wallpaper_calls) == 1:
                raise OSError("Temporary desktop API failure")
        self.wallpaper.side_effect = fail_first
        with patch.object(app, "sleep_until_next_cycle", side_effect=[True, False]):
            app.main(["--config", str(self.config_path)])
        self.assertEqual(len(self.wallpaper_calls), 2)
        self.assertEqual(self.wallpaper_calls[0][0], self.wallpaper_calls[1][0])
        self.client.fetch_image.assert_called_once()


if __name__ == "__main__":
    unittest.main()
