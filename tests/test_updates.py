"""Release checks and the startup update notice."""

import io
import json
import os
from pathlib import Path
import tempfile
import threading
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import marblescape_download as app


class _Response(io.BytesIO):
    def __init__(self, value):
        payload = json.dumps(value).encode("utf-8")
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


class UpdateTests(unittest.TestCase):
    def test_new_public_release_is_shown_until_that_version_is_skipped(self):
        payload = {
            "tag_name": "v1.3.1",
            "html_url": app.PROJECT_URL + "/releases/tag/v1.3.1",
        }
        with patch.object(app, "urlopen", return_value=_Response(payload)):
            release = app.check_github_update("1.3.0")
        self.assertTrue(app.should_show_update_notification(release))
        self.assertFalse(app.should_show_update_notification(release, "1.3.1"))
        self.assertFalse(app.should_show_update_notification(release, "v1.3.1"))
        self.assertTrue(app.should_show_update_notification(release, "v1.3.0"))
        self.assertEqual(release["url"], payload["html_url"])

    def test_tag_without_a_published_release_does_not_open_a_notice(self):
        release = {
            "latest": "v1.3.1",
            "update_available": True,
            "url": app.PROJECT_URL + "/releases",
        }
        self.assertFalse(app.should_show_update_notification(release))

    def test_skip_setting_can_be_added_to_an_existing_configuration(self):
        original = "[windows]\nposition = \"fit\"\n"
        updated = app.replace_toml_section_value(
            app.ensure_updates_configuration_section(original),
            "updates", "skipped_version", "v1.3.1",
        )
        self.assertEqual(tomllib.loads(updated)["updates"]["skipped_version"],
                         "v1.3.1")


@unittest.skipUnless(os.name == "nt", "Windows tray update notice")
class UpdateNoticeIntegrationTests(unittest.TestCase):
    def test_startup_notice_skips_and_persists_a_new_release(self):
        import tkinter as tk
        from tkinter import ttk
        import pystray

        previous = app.capture_loaded_configuration()
        event = threading.Event()
        error = []
        original_tk = tk.Tk
        original_thread = threading.Thread

        def thread_factory(*args, **kwargs):
            if kwargs.get("name") == "MarbleScapeStartupUpdate":
                return SimpleNamespace(start=kwargs["target"])
            return original_thread(*args, **kwargs)

        class FakeIcon:
            def __init__(self, *args, menu, **kwargs):
                self.menu = menu

            def run(self, setup):
                setup(self)
                if not event.wait(8):
                    raise AssertionError("Startup update notice did not open")

            def stop(self):
                pass

        def hidden_tk():
            root = original_tk()
            root.withdraw()
            root.deiconify = lambda: None
            root.attributes = lambda *args: None

            def inspect_notice():
                try:
                    buttons = [widget for widget in root.winfo_children()[0].winfo_children()
                               if isinstance(widget, ttk.Button)]
                    skip = next(widget for widget in buttons
                                if widget.cget("text") == "Skip this version")
                    self.assertTrue(any(widget.cget("text") == "Open GitHub"
                                        for widget in buttons))
                    skip.invoke()
                except Exception as exc:
                    error.append(exc)
                finally:
                    event.set()

            root.mainloop = inspect_notice
            return root

        release = {"current": "1.3.0", "latest": "v1.3.1",
                   "update_available": True,
                   "url": app.PROJECT_URL + "/releases/tag/v1.3.1"}
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(app.DEFAULT_CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8"),
                              encoding="utf-8")
            try:
                with patch.object(pystray, "Icon", FakeIcon), \
                     patch.object(tk, "Tk", side_effect=hidden_tk), \
                     patch.object(app, "run_application", return_value=0), \
                     patch.object(app, "warm_public_catalogues"), \
                     patch.object(app, "create_windows_tray_image", return_value=None), \
                     patch.object(app, "apply_tk_window_icon"), \
                     patch.object(app.threading, "Thread", side_effect=thread_factory), \
                     patch.object(app, "check_github_update", return_value=release), \
                     patch.object(app, "log"):
                    self.assertEqual(app.run_with_windows_tray(["--config", str(config)]), 0)
                self.assertEqual(error, [])
                saved = tomllib.loads(config.read_text(encoding="utf-8"))
                self.assertEqual(saved["updates"]["skipped_version"], "v1.3.1")
            finally:
                app.restore_loaded_configuration(previous)
                app.APPLICATION_STOP_EVENT.clear()


if __name__ == "__main__":
    unittest.main()
