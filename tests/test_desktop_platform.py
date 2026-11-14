"""Pure backend selection: no display power actions, subprocesses, or writes."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from isc.desktop_platform import detect_desktop_platform


class DesktopPlatformTests(unittest.TestCase):
    def test_gnome_wayland(self):
        for name in ("GNOME", "ubuntu:GNOME", "gnome:ubuntu"):
            with self.subTest(name=name):
                state = detect_desktop_platform({
                    "XDG_SESSION_TYPE": "wayland",
                    "XDG_CURRENT_DESKTOP": name,
                    "DISPLAY": ":0",
                })
                self.assertEqual(state.desktop, "GNOME")
                self.assertEqual(state.display_backend, "gnome-wayland")

    def test_kde_plasma_wayland(self):
        for name in ("KDE", "plasma", "KDE:PLASMA"):
            with self.subTest(name=name):
                state = detect_desktop_platform({
                    "XDG_SESSION_TYPE": "wayland",
                    "XDG_CURRENT_DESKTOP": name,
                    "DISPLAY": ":0",
                })
                self.assertEqual(state.desktop, "KDE Plasma")
                self.assertEqual(state.display_backend, "kde-wayland")

    def test_x11_native_is_desktop_independent(self):
        for name in ("GNOME", "KDE", "XFCE", "X-Cinnamon", "", "MATE"):
            with self.subTest(name=name):
                state = detect_desktop_platform({
                    "XDG_SESSION_TYPE": "x11",
                    "XDG_CURRENT_DESKTOP": name,
                })
                self.assertEqual(state.display_backend, "x11")

    def test_xwayland_display_does_not_override_wayland_session(self):
        state = detect_desktop_platform({
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "GNOME",
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
        })
        self.assertEqual(state.display_backend, "gnome-wayland")

    def test_unknown_wayland_compositors_are_not_falsely_supported(self):
        for name in ("sway", "Hyprland", "wlroots", "labwc", "", "XFCE"):
            with self.subTest(name=name):
                state = detect_desktop_platform({
                    "XDG_SESSION_TYPE": "wayland",
                    "XDG_CURRENT_DESKTOP": name,
                })
                self.assertEqual(state.display_backend, "unsupported")

    def test_conflicted_desktop_is_not_randomly_assigned(self):
        state = detect_desktop_platform({
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "GNOME:KDE",
        })
        self.assertEqual(state.desktop, "ambiguous")
        self.assertEqual(state.display_backend, "unsupported")

    def test_missing_session_type_does_not_assume_x11(self):
        state = detect_desktop_platform({
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_CURRENT_DESKTOP": "GNOME",
        })
        self.assertEqual(state.session, "unknown")
        self.assertEqual(state.display_backend, "unsupported")

    def test_fallback_desktop_name_only_when_current_desktop_missing(self):
        state = detect_desktop_platform({
            "XDG_SESSION_TYPE": "wayland",
            "XDG_SESSION_DESKTOP": "KDE",
        })
        self.assertEqual(state.display_backend, "kde-wayland")

    def test_malicious_desktop_strings_are_never_invoked_as_commands(self):
        state = detect_desktop_platform({
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "GNOME; echo surprise",
        })
        self.assertEqual(state.display_backend, "unsupported")


if __name__ == "__main__":
    unittest.main()
