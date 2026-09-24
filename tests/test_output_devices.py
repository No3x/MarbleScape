import ctypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

import marblescape_download as app


class OutputDeviceTests(unittest.TestCase):
    def test_monitor_positions_survive_toml_escaping(self):
        monitor_id = r"\\?\DISPLAY#TEST123#1"
        positions = {monitor_id: "none"}
        updated = app.replace_toml_section_value(
            "[windows]\nposition = \"fit\"\n", "windows", "monitor_positions",
            json.dumps(positions),
        )
        self.assertEqual(json.loads(tomllib.loads(updated)["windows"]["monitor_positions"]),
                         positions)

    def test_monitor_output_settings_survive_toml_escaping(self):
        monitor_id = r"\\?\DISPLAY#TEST123#1"
        settings = {monitor_id: {"width": 1200, "height": 0,
                                 "aspect_ratio": "5:7", "render_scale": 1.5,
                                 "background_color": "#102030"}}
        updated = app.replace_toml_section_value(
            "[windows]\nposition = \"fit\"\n", "windows", "monitor_output_settings",
            json.dumps(settings),
        )
        loaded = tomllib.loads(updated)["windows"]["monitor_output_settings"]
        self.assertEqual(app.normalize_monitor_output_settings(
            loaded, {"width": 2560, "height": 0, "aspect_ratio": "16:9",
                     "render_scale": "auto", "background_color": "#000000"}),
            settings)

    def test_monitor_output_settings_load_without_attached_display(self):
        saved = app.capture_loaded_configuration()
        monitor_id = r"\\?\DISPLAY#DETACHED#1"
        settings = {monitor_id: {"width": 1200, "height": 0,
                                 "aspect_ratio": "5:7", "render_scale": 1.5,
                                 "background_color": "#102030"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.toml"
            config = app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
            config = app.replace_toml_section_value(
                config, "windows", "monitor_output_settings", json.dumps(settings)
            )
            path.write_text(config, encoding="utf-8")
            try:
                app.load_configuration(path)
                self.assertEqual(app.WINDOWS_WALLPAPER_MONITOR_OUTPUTS, settings)
            finally:
                app.restore_loaded_configuration(saved)

    def test_span_uses_the_correct_part_of_a_shared_image(self):
        source = Image.new("RGB", (4, 2), "red")
        source.paste("blue", (2, 0, 4, 2))
        rectangles = [(0, 0, 2, 2), (2, 0, 4, 2)]
        first = app.compose_monitor_wallpaper(source, "span", rectangles[0],
                                              rectangles, (0, 0, 0))
        second = app.compose_monitor_wallpaper(source, "span", rectangles[1],
                                               rectangles, (0, 0, 0))
        self.assertEqual(first.getpixel((1, 1)), (255, 0, 0))
        self.assertEqual(second.getpixel((1, 1)), (0, 0, 255))

    def test_fit_preserves_aspect_ratio_and_background(self):
        source = Image.new("RGB", (2, 2), "red")
        result = app.compose_monitor_wallpaper(source, "fit", (0, 0, 4, 2),
                                               [(0, 0, 4, 2)], (1, 2, 3))
        self.assertEqual(result.getpixel((0, 0)), (1, 2, 3))
        self.assertEqual(result.getpixel((1, 0)), (255, 0, 0))

    def test_none_has_a_clear_display_label_and_keeps_its_config_value(self):
        self.assertEqual(app.WALLPAPER_POSITION_LABELS["none"],
                         "Do not update (keep current wallpaper)")
        self.assertIn("none", app.WALLPAPER_POSITION_CHOICES)

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_previous_wallpaper_is_saved_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.png"
            next_image = root / "next.png"
            Image.new("RGB", (2, 2), "red").save(original)
            Image.new("RGB", (2, 2), "blue").save(next_image)
            original_bytes = original.read_bytes()
            path_buffer = ctypes.create_unicode_buffer(str(original))
            reads = []

            def method(_desktop, index, _restype, *_argtypes):
                if index == 4:
                    def get_wallpaper(_interface, monitor_id, output):
                        reads.append(monitor_id)
                        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = \
                            ctypes.addressof(path_buffer)
                        return 0
                    return get_wallpaper
                raise AssertionError(index)

            with patch.object(app, "CONTENT_DIR", root), \
                 patch.object(app, "with_windows_com", side_effect=lambda action: action()), \
                 patch.object(app, "create_desktop_wallpaper_interface", return_value=123), \
                 patch.object(app, "get_com_method", side_effect=method), \
                 patch.object(app, "release_com_pointer"), \
                 patch.object(app.ctypes, "windll", SimpleNamespace(
                     ole32=SimpleNamespace(CoTaskMemFree=Mock()))):
                monitor = {"id": "DISPLAY-A", "rect": (0, 0, 2, 2)}
                app.capture_previous_wallpapers([monitor], next_image)
                backup = app.previous_wallpaper_backup("DISPLAY-A")
                self.assertIsNotNone(backup)
                self.assertEqual(backup.read_bytes(), original_bytes)
                Image.new("RGB", (2, 2), "green").save(original)
                app.capture_previous_wallpapers([monitor], next_image)
                self.assertEqual(backup.read_bytes(), original_bytes)
            self.assertEqual(reads, ["DISPLAY-A"])

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_current_marblescape_wallpaper_is_not_saved_as_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "marblescape_2026-09-24_000000.png"
            Image.new("RGB", (2, 2), "red").save(current)
            path_buffer = ctypes.create_unicode_buffer(str(current))

            def method(_desktop, index, _restype, *_argtypes):
                if index == 4:
                    def get_wallpaper(_interface, _monitor_id, output):
                        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = \
                            ctypes.addressof(path_buffer)
                        return 0
                    return get_wallpaper
                raise AssertionError(index)

            with patch.object(app, "CONTENT_DIR", root), \
                 patch.object(app, "with_windows_com", side_effect=lambda action: action()), \
                 patch.object(app, "create_desktop_wallpaper_interface", return_value=123), \
                 patch.object(app, "get_com_method", side_effect=method), \
                 patch.object(app, "release_com_pointer"), \
                 patch.object(app.ctypes, "windll", SimpleNamespace(
                     ole32=SimpleNamespace(CoTaskMemFree=Mock()))):
                app.capture_previous_wallpapers(
                    [{"id": "DISPLAY-A", "rect": (0, 0, 2, 2)}], current
                )
                self.assertIsNone(app.previous_wallpaper_backup("DISPLAY-A"))

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_solid_color_is_saved_for_one_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def method(_desktop, index, _restype, *_argtypes):
                if index == 4:
                    return lambda _interface, _monitor_id, _output: 0
                if index == 9:
                    def get_color(_interface, output):
                        ctypes.cast(output, ctypes.POINTER(ctypes.c_uint32))[0] = 0x00302010
                        return 0
                    return get_color
                raise AssertionError(index)

            with patch.object(app, "CONTENT_DIR", root), \
                 patch.object(app, "with_windows_com", side_effect=lambda action: action()), \
                 patch.object(app, "create_desktop_wallpaper_interface", return_value=123), \
                 patch.object(app, "get_com_method", side_effect=method), \
                 patch.object(app, "release_com_pointer"), \
                 patch.object(app.ctypes, "windll", SimpleNamespace(
                     ole32=SimpleNamespace(CoTaskMemFree=Mock()))):
                app.capture_previous_wallpapers(
                    [{"id": "DISPLAY-A", "rect": (0, 0, 3, 2)}], root / "new.png"
                )
                backup = app.previous_wallpaper_backup("DISPLAY-A")
                with Image.open(backup) as picture:
                    self.assertEqual(picture.size, (3, 2))
                    self.assertEqual(picture.getpixel((0, 0)), (16, 32, 48))

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_restore_previous_wallpaper_targets_one_display_and_saves_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monitor_id = "DISPLAY-A"
            backup_dir = root / "previous_wallpapers"
            backup_dir.mkdir()
            backup = backup_dir / (
                hashlib.sha256(monitor_id.encode("utf-8")).hexdigest() + ".png"
            )
            Image.new("RGB", (2, 2), "red").save(backup)
            settings = {"DISPLAY-B": "fill"}
            calls = []

            def method(_desktop, index, _restype, *_argtypes):
                if index == 3:
                    return lambda _interface, target, image: calls.append((target, image)) or 0
                raise AssertionError(index)

            updates = []
            def write_config(transform):
                text = transform('[windows]\nposition = "fit"\n')
                updates.append(json.loads(tomllib.loads(text)["windows"]["monitor_positions"]))
                return True

            with patch.object(app, "CONTENT_DIR", root), \
                 patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS", settings), \
                 patch.object(app, "list_windows_wallpaper_monitors", return_value=[
                     {"id": monitor_id, "rect": (0, 0, 2, 2)}
                 ]), \
                 patch.object(app, "update_active_configuration", side_effect=write_config), \
                 patch.object(app, "with_windows_com", side_effect=lambda action: action()), \
                 patch.object(app, "create_desktop_wallpaper_interface", return_value=123), \
                 patch.object(app, "get_com_method", side_effect=method), \
                 patch.object(app, "release_com_pointer"):
                app.restore_previous_wallpaper(monitor_id)
                self.assertEqual(app.WINDOWS_WALLPAPER_MONITOR_POSITIONS,
                                 {"DISPLAY-B": "fill", monitor_id: "none"})
            self.assertEqual(updates, [{"DISPLAY-B": "fill", monitor_id: "none"}])
            self.assertEqual(calls, [(monitor_id, str(backup.resolve()))])

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_none_for_all_monitors_does_not_change_wallpaper(self):
        with patch.object(app, "WINDOWS_WALLPAPER_POSITION", "none"), \
             patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS", {}), \
             patch.object(app, "create_desktop_wallpaper_interface") as create:
            app.set_windows_wallpaper(Path("unused.png"))
        create.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_unknown_displays_do_not_override_a_skipped_monitor(self):
        with patch.object(app, "WINDOWS_WALLPAPER_POSITION", "fit"), \
             patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS", {"A": "none"}), \
             patch.object(app, "list_windows_wallpaper_monitors", return_value=[]), \
             patch.object(app, "create_desktop_wallpaper_interface") as create:
            with self.assertRaisesRegex(RuntimeError, "No connected display"):
                app.set_windows_wallpaper(Path("unused.png"))
        create.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_monitor_enumeration_excludes_disconnected_displays(self):
        buffers = [ctypes.create_unicode_buffer("DISPLAY-A"),
                   ctypes.create_unicode_buffer("DISPLAY-B"),
                   ctypes.create_unicode_buffer("DISPLAY-C")]

        def method(_desktop, index, _restype, *_argtypes):
            if index == 6:
                def get_count(_interface, count):
                    ctypes.cast(count, ctypes.POINTER(ctypes.c_uint))[0] = 3
                    return 0
                return get_count
            if index == 5:
                def get_id(_interface, monitor_index, output):
                    ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = \
                        ctypes.addressof(buffers[monitor_index])
                    return 0
                return get_id
            if index == 7:
                def get_rect(_interface, monitor_id, output):
                    if monitor_id == "DISPLAY-B":
                        return 1
                    if monitor_id == "DISPLAY-C":
                        return ctypes.c_long(0x80004005).value
                    rectangle = ctypes.cast(output, ctypes.POINTER(app.WindowsRect))
                    rectangle[0] = app.WindowsRect(0, 0, 1920, 1080)
                    return 0
                return get_rect
            raise AssertionError(index)

        free = Mock()
        with patch.object(app, "with_windows_com", side_effect=lambda action: action()), \
             patch.object(app, "create_desktop_wallpaper_interface", return_value=123), \
             patch.object(app, "get_com_method", side_effect=method), \
             patch.object(app, "release_com_pointer"), \
             patch.object(app.ctypes, "windll",
                          SimpleNamespace(ole32=SimpleNamespace(CoTaskMemFree=free))):
            self.assertEqual(app.list_windows_wallpaper_monitors(), [
                {"id": "DISPLAY-A", "rect": (0, 0, 1920, 1080)}
            ])
        self.assertEqual(free.call_count, 3)

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_none_skips_one_monitor_and_other_monitor_gets_composed_image(self):
        events = []

        def method(_interface, index, _restype, *_argtypes):
            if index == 11:
                def get_position(_desktop, position):
                    ctypes.cast(position, ctypes.POINTER(ctypes.c_int))[0] = 3
                    return 0
                return get_position
            if index == 10:
                return lambda _desktop, value: events.append(("position", value)) or 0
            if index == 3:
                return lambda _desktop, monitor, path: events.append(
                    ("wallpaper", monitor, path)
                ) or 0
            raise AssertionError(index)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "source.png"
            Image.new("RGB", (4, 2), "red").save(image_path)
            monitors = [
                {"id": "A", "rect": (0, 0, 2, 2)},
                {"id": "B", "rect": (2, 0, 4, 2)},
            ]
            with patch.object(app, "WINDOWS_WALLPAPER_POSITION", "fit"), \
                 patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS",
                              {"A": "none", "B": "fill"}), \
                 patch.object(app, "CONTENT_DIR", root), \
                 patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors), \
                 patch.object(app, "capture_previous_wallpapers"), \
                 patch.object(app, "with_windows_com", side_effect=lambda action: action()), \
                 patch.object(app, "create_desktop_wallpaper_interface", return_value=123), \
                 patch.object(app, "get_com_method", side_effect=method), \
                 patch.object(app, "release_com_pointer"), \
                 patch.object(app, "log"):
                app.set_windows_wallpaper(image_path)
            self.assertEqual(events[0], ("position", 3))
            self.assertEqual(len(events), 2)
            self.assertEqual(events[1][1], "B")
            with Image.open(events[1][2]) as output:
                self.assertEqual(output.size, (2, 2))

    @unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
    def test_detached_monitor_settings_are_kept_and_attached_monitor_is_rendered(self):
        events = []

        def method(_interface, index, _restype, *_argtypes):
            if index == 10:
                return lambda _desktop, value: events.append(("position", value)) or 0
            if index == 3:
                return lambda _desktop, monitor, path: events.append(
                    ("wallpaper", monitor, path)
                ) or 0
            raise AssertionError(index)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "source.png"
            Image.new("RGB", (4, 2), "red").save(image_path)
            settings = {
                "A": {"width": 3, "height": 0, "aspect_ratio": "1:1",
                      "render_scale": "auto", "background_color": "#102030"},
                "B": {"width": 2, "height": 0, "aspect_ratio": "1:1",
                      "render_scale": "auto", "background_color": "#000000"},
            }
            with patch.object(app, "WINDOWS_WALLPAPER_POSITION", "center"), \
                 patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS", {}), \
                 patch.object(app, "WINDOWS_WALLPAPER_MONITOR_OUTPUTS", settings), \
                 patch.object(app, "WIDTH", 4), \
                 patch.object(app, "HEIGHT", 2), \
                 patch.object(app, "ASPECT_RATIO", "2:1"), \
                 patch.object(app, "CONTENT_DIR", root), \
                 patch.object(app, "list_windows_wallpaper_monitors", return_value=[
                     {"id": "A", "rect": (0, 0, 5, 5)},
                 ]), \
                 patch.object(app, "capture_previous_wallpapers"), \
                 patch.object(app, "with_windows_com", side_effect=lambda action: action()), \
                 patch.object(app, "create_desktop_wallpaper_interface", return_value=123), \
                 patch.object(app, "get_com_method", side_effect=method), \
                 patch.object(app, "release_com_pointer"), \
                 patch.object(app, "log"):
                app.set_windows_wallpaper(image_path)
            self.assertEqual(events[1][1], "A")
            self.assertEqual(len(events), 2)
            with Image.open(events[1][2]) as output:
                self.assertEqual(output.size, (5, 5))
                self.assertEqual(output.getpixel((0, 0)), (16, 32, 48))
                self.assertEqual(output.getpixel((2, 2)), (255, 0, 0))
            self.assertIn("B", settings)

    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_single_instance_rejects_an_existing_mutex(self):
        create = Mock(return_value=123)
        close = Mock(return_value=1)
        kernel = type("Kernel", (), {"CreateMutexW": create, "CloseHandle": close})()
        with patch.object(app.ctypes, "WinDLL", return_value=kernel), \
             patch.object(app.ctypes, "get_last_error", return_value=183), \
             patch.object(app, "WINDOWS_SINGLE_INSTANCE_HANDLE", None):
            self.assertFalse(app.acquire_windows_single_instance())
        close.assert_called_once_with(123)

    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_single_instance_rejects_a_mutex_owned_by_another_user(self):
        create = Mock(return_value=None)
        kernel = SimpleNamespace(CreateMutexW=create, CloseHandle=Mock())
        with patch.object(app.ctypes, "WinDLL", return_value=kernel), \
             patch.object(app.ctypes, "get_last_error", return_value=5):
            self.assertFalse(app.acquire_windows_single_instance())

    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_restart_waits_until_the_previous_instance_exits(self):
        create = Mock(side_effect=[123, 456])
        close = Mock(return_value=1)
        kernel = SimpleNamespace(CreateMutexW=create, CloseHandle=close)
        with patch.object(app.ctypes, "WinDLL", return_value=kernel), \
             patch.object(app.ctypes, "get_last_error", side_effect=[183, 0]), \
             patch.object(app.time, "monotonic", return_value=0), \
             patch.object(app.time, "sleep"), \
             patch.object(app, "WINDOWS_SINGLE_INSTANCE_HANDLE", None):
            self.assertTrue(app.acquire_windows_single_instance(wait_seconds=1))
            self.assertEqual(app.WINDOWS_SINGLE_INSTANCE_HANDLE, 456)
        close.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
