"""Visible GNOME desktop window, independent of optional tray indicator.

Window has read-only OFF/MONITOR controls and operator-clicked Keep Awake.
Automatic suspend remains disarmed. GI/GTK is imported only for the GUI.
No automatic power, scheduled-job or external service changes occur.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .receiver import read_monitor_health
from .settings import ModeConfigError, read_mode, set_mode
from .mode_gate import resolve_mode
from .keepawake import KeepAwakeError, KeepAwakeManager
from .tray import backend_probe


def _minimal_desktop_env() -> dict[str, str]:
    """Forward session display data, not tokens or unrelated credentials."""
    names = (
        "HOME", "PATH", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE",
        "GDK_BACKEND", "LANG", "LC_ALL", "TZ",
    )
    env = {key: os.environ[key] for key in names if key in os.environ}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _launch_tray() -> bool:
    """Explicit click only. Start exactly the existing ISC tray entrypoint."""
    if backend_probe():
        return False
    try:
        subprocess.Popen(
            [sys.executable, "-B", "-m", "isc", "tray"],
            env=_minimal_desktop_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


class DesktopWindow:
    """Simple always-visible launcher experience when GNOME tray is absent."""

    def __init__(self, Gtk, GLib):
        self.Gtk = Gtk
        self.GLib = GLib
        self._awake = KeepAwakeManager()
        self.window = Gtk.Window(title="Intelligent Sleep Controller")
        self.window.set_default_size(650, 365)
        self.window.set_border_width(20)
        self.window.connect("destroy", self._close_window)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.window.add(content)

        heading = Gtk.Label(label="Intelligent Sleep Controller")
        heading.set_xalign(0)
        content.pack_start(heading, False, False, 0)

        intro = Gtk.Label(
            label="Local monitoring and manual controls. "
                  "Automatic suspension is not implemented."
        )
        intro.set_xalign(0)
        intro.set_line_wrap(True)
        content.pack_start(intro, False, False, 0)

        self.status_label = Gtk.Label(label="Reading local monitoring state…")
        self.status_label.set_xalign(0)
        self.status_label.set_line_wrap(True)
        self.status_label.set_selectable(False)
        content.pack_start(self.status_label, True, True, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(buttons, False, False, 0)
        for label, callback in (
            ("OFF", lambda *_: self.set_monitor_mode("off")),
            ("MONITOR", lambda *_: self.set_monitor_mode("monitor")),
            ("Refresh", lambda *_: self.refresh()),
            ("Start Tray Icon", lambda *_: self.start_tray()),
            ("Close", lambda *_: self.window.destroy()),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            buttons.pack_start(button, False, False, 0)

        self.awake_label = Gtk.Label(label="Keep Awake: inactive")
        self.awake_label.set_xalign(0)
        content.pack_start(self.awake_label, False, False, 0)
        awake_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(awake_buttons, False, False, 0)
        for label, callback in (
            ("Keep Awake 30 min", lambda *_: self.start_keep_awake(30)),
            ("Keep Awake until cancelled", lambda *_: self.start_keep_awake(None)),
            ("Cancel Keep Awake", lambda *_: self.cancel_keep_awake()),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", callback)
            awake_buttons.pack_start(button, False, False, 0)
        self.refresh()
        GLib.timeout_add_seconds(5, self.refresh)
        self.window.show_all()

    def _message(self, title: str, text: str, *, error: bool = False) -> None:
        dialog = self.Gtk.MessageDialog(
            transient_for=self.window,
            flags=self.Gtk.DialogFlags.MODAL,
            message_type=(
                self.Gtk.MessageType.ERROR if error
                else self.Gtk.MessageType.INFO
            ),
            buttons=self.Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(text)
        try:
            dialog.run()
        finally:
            dialog.destroy()

    def set_monitor_mode(self, mode: str) -> None:
        if mode not in ("off", "monitor"):
            return
        try:
            set_mode(mode)
        except ModeConfigError:
            self._message(
                "Preference unchanged",
                "Local settings are unavailable or unsafe.", error=True,
            )
        self.refresh()

    def start_keep_awake(self, minutes: int | None) -> None:
        """Explicit click only; use the existing verified native inhibitor."""
        if minutes not in (30, None):
            return
        try:
            state = self._awake.start(minutes)
            if not state.requested:
                self._message(
                    "Keep Awake unavailable",
                    "Linux did not verify the inhibitor.", error=True,
                )
        except KeepAwakeError:
            self._message(
                "Keep Awake unavailable",
                "The native Linux inhibitor could not be verified.", error=True,
            )
        self.refresh()

    def cancel_keep_awake(self) -> None:
        """Cancel only this window's lease; never a tray-owned inhibitor."""
        try:
            self._awake.cancel()
        except KeepAwakeError:
            self._message(
                "Keep Awake cancellation unverified",
                "The Linux inhibitor may still be active.", error=True,
            )
        self.refresh()

    def _close_window(self, *_args) -> None:
        """Release the window-owned inhibitor on normal GUI shutdown."""
        try:
            self._awake.close()
        except KeepAwakeError:
            print("ISC Keep Awake cancellation unverified.")
        self.Gtk.main_quit()

    def start_tray(self) -> None:
        issues = backend_probe()
        if issues:
            self._message(
                "Tray icon unavailable",
                "The desktop tray backend needs attention: "
                + ", ".join(issues) + ".\n"
                "GNOME Shell also requires AppIndicator support to display "
                "the icon. The main application window remains usable.",
                error=True,
            )
            return
        if not _launch_tray():
            self._message(
                "Tray did not start",
                "Could not start the optional tray process.", error=True,
            )
            return
        self._message(
            "Tray launch requested",
            "ISC attempted to launch its existing tray. If the icon is not "
            "visible, check GNOME AppIndicator support or run isc tray-check. "
            "The window remains available.",
        )

    def refresh(self) -> bool:
        try:
            effective = resolve_mode(read_mode())
            mode = (
                effective.mode.upper() if effective.configuration_valid
                else "OFF (invalid settings)"
            )
        except ModeConfigError:
            mode = "OFF (invalid settings)"
        health = read_monitor_health()
        if health is None:
            detail = "Monitor receiver: unavailable or not responding."
        else:
            detail = (
                f"Receiver: {health.accepted} accepted / "
                f"{health.rejected} rejected hook events.\n"
                f"Observed active sessions: {health.observed_active}; "
                f"unknown sessions: {health.observed_unknown}.\n"
            )
            if health.process_roots is None:
                detail += "Background process census: UNKNOWN."
            else:
                detail += (
                    f"Partial process census: {health.process_roots} roots, "
                    f"{health.process_descendants} visible children; "
                    f"age {health.process_sample_age_seconds}s."
                )
        awake = getattr(self, "_awake", None)
        awake_label = getattr(self, "awake_label", None)
        if awake is not None and awake_label is not None:
            state = awake.status()
            if not state.requested:
                awake_text = "Keep Awake: inactive"
            elif state.remaining_seconds is None:
                awake_text = "Keep Awake: until cancelled (window must stay open)"
            else:
                awake_text = (
                    "Keep Awake: active ("
                    + str((state.remaining_seconds + 59) // 60)
                    + " min remaining)"
                )
            awake_label.set_text(awake_text)
        self.status_label.set_text(
            f"Operating preference: {mode}\n"
            f"{detail}\n"
            "Workload coverage: NOT VERIFIED. No automatic suspend."
        )
        return True


def run_window() -> int:
    """Open the real application window from GNOME Show Apps."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("ISC desktop window unavailable: graphical_session_unavailable")
        return 2
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, GLib
        initialized = Gtk.init_check()
        if not (initialized[0] if isinstance(initialized, tuple) else initialized):
            print("ISC desktop window unavailable: cannot initialize GTK display.")
            return 2
        DesktopWindow(Gtk, GLib)
        Gtk.main()
        return 0
    except (ImportError, ValueError):
        print("ISC desktop window unavailable: python3-gi_or_gtk3_unavailable")
        return 2
    except (OSError, RuntimeError):
        print("ISC desktop window unavailable: GUI backend error.")
        return 2
