"""Optional GTK3/AppIndicator tray for Intelligent Sleep Controller.

No GI dependency is imported by the CLI unless the tray is launched.
Launching the tray never installs a service or changes OS power settings.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from .manual_power import assess_manual_suspend, request_dialog_suspend
from .receiver import discover_monitor_socket
from .settings import ModeConfigError, read_mode, set_mode


@dataclass(frozen=True)
class TraySnapshot:
    mode: str
    configured: bool
    valid: bool
    receiver_advertised: bool


def tray_snapshot() -> TraySnapshot:
    try:
        current = read_mode()
        mode, configured, valid = current.mode, current.configured, True
    except ModeConfigError:
        mode, configured, valid = "off", False, False
    return TraySnapshot(
        mode, configured, valid, discover_monitor_socket() is not None,
    )


def backend_probe() -> tuple[str, ...]:
    """Check optional GUI bindings without launching GTK or changing files."""
    issues: list[str] = []
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        issues.append("graphical_session_unavailable")
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401
    except (ImportError, ValueError):
        issues.append("python3-gi_or_gtk3_unavailable")
        return tuple(issues)
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3  # noqa: F401
    except (ImportError, ValueError):
        try:
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3  # noqa: F401
        except (ImportError, ValueError):
            issues.append("appindicator_binding_unavailable")
    return tuple(issues)


def _gtk_dependencies():
    import gi
    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as Indicator
    except (ImportError, ValueError):
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as Indicator
    from gi.repository import Gtk, GLib
    return Gtk, GLib, Indicator


class TrayController:
    """User-invoked menu; does not start a background ISC receiver."""

    def __init__(self, Gtk, GLib, Indicator):
        self.Gtk = Gtk
        self._refreshing = True
        self.indicator = Indicator.Indicator.new(
            "intelligent-sleep-controller",
            "system-suspend-symbolic",
            Indicator.IndicatorCategory.SYSTEM_SERVICES,
        )
        self.indicator.set_status(Indicator.IndicatorStatus.ACTIVE)
        self.menu = Gtk.Menu()
        self.title = self._label("ISC — OFF")
        self.receiver_label = self._label("Receiver: not advertised")
        self._separator()
        self.off = Gtk.RadioMenuItem.new_with_label(None, "OFF — no automatic suspend")
        self.monitor = Gtk.RadioMenuItem.new_with_label_from_widget(
            self.off, "MONITOR — observe only",
        )
        self.menu.append(self.off)
        self.menu.append(self.monitor)
        self.off.connect("toggled", self._set_mode, "off")
        self.monitor.connect("toggled", self._set_mode, "monitor")
        self._disabled("ON — unavailable")
        self._separator()
        self._item("Sleep / Suspend…", self._suspend)
        self._disabled("Hibernate — planned")
        self._disabled("Hybrid Sleep — planned")
        self._separator()
        self._disabled("Keep Awake — planned")
        self._item("Status…", self._status)
        self._separator()
        self._item("Quit ISC tray", lambda _item: Gtk.main_quit())
        self.menu.show_all()
        self.indicator.set_menu(self.menu)
        self._refreshing = False
        self.refresh()
        GLib.timeout_add_seconds(3, self.refresh)

    def _label(self, label):
        item = self.Gtk.MenuItem.new_with_label(label)
        item.set_sensitive(False)
        self.menu.append(item)
        return item

    def _disabled(self, label):
        return self._label(label)

    def _separator(self):
        self.menu.append(self.Gtk.SeparatorMenuItem())

    def _item(self, label, callback):
        item = self.Gtk.MenuItem.new_with_label(label)
        item.connect("activate", callback)
        self.menu.append(item)
        return item

    def _notice(self, title, detail, *, error=False):
        Gtk = self.Gtk
        dialog = Gtk.MessageDialog(
            transient_for=None, flags=0,
            message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK, text=title,
        )
        dialog.format_secondary_text(detail)
        try:
            dialog.run()
        finally:
            dialog.destroy()

    def _set_mode(self, item, selected):
        if self._refreshing or not item.get_active():
            return
        try:
            set_mode(selected)
        except ModeConfigError:
            self._notice(
                "ISC preference unchanged",
                "Local configuration is unavailable or unsafe.", error=True,
            )
        self.refresh()

    def refresh(self):
        state = tray_snapshot()
        self._refreshing = True
        try:
            mode = state.mode.upper() if state.valid else "OFF (invalid config)"
            self.title.set_label("Intelligent Sleep Controller — " + mode)
            self.off.set_active(state.mode == "off")
            self.monitor.set_active(state.mode == "monitor")
            self.receiver_label.set_label(
                "Receiver: socket advertised (unverified)"
                if state.receiver_advertised else "Receiver: not advertised"
            )
        finally:
            self._refreshing = False
        return True

    def _status(self, _item):
        state = tray_snapshot()
        mode = state.mode.upper() if state.valid else "OFF (invalid config)"
        self._notice(
            "Intelligent Sleep Controller",
            f"Mode preference: {mode}\n"
            f"Receiver socket: {'advertised' if state.receiver_advertised else 'not advertised'}\n"
            "The tray does not start a monitoring service.\n"
            "Automatic suspension is not implemented.",
        )

    def _suspend(self, _item):
        assessment = assess_manual_suspend()
        if not assessment.request_possible:
            self._notice(
                "Suspend refused",
                "Safety preflight blocked the request: "
                + ", ".join(assessment.blockers), error=True,
            )
            return
        Gtk = self.Gtk
        dialog = Gtk.Dialog(
            title="Suspend this computer?", transient_for=None,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Suspend", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        explanation = Gtk.Label(
            label="Save your work. ISC cannot verify every background task, "
                  "and your display may fail to resume.\n"
                  "Type SUSPEND to confirm immediate manual sleep."
        )
        explanation.set_line_wrap(True)
        explanation.set_max_width_chars(58)
        content.add(explanation)
        phrase = Gtk.Entry()
        phrase.set_placeholder_text("Type SUSPEND")
        content.add(phrase)
        content.show_all()
        try:
            accepted = dialog.run() == Gtk.ResponseType.OK
            typed = phrase.get_text() if accepted else ""
        finally:
            dialog.destroy()
        if not accepted:
            return
        result = request_dialog_suspend(confirmed=True, typed=typed)
        if not result.submitted:
            self._notice(
                "No suspend request made", result.reason, error=True,
            )


def run_tray() -> int:
    """Explicit foreground GUI. No startup registration or root privileges."""
    issues = backend_probe()
    if issues:
        print("ISC tray unavailable: " + ", ".join(issues))
        print("ISC command-line controls are unaffected.")
        return 2
    try:
        Gtk, GLib, Indicator = _gtk_dependencies()
        initialized = Gtk.init_check()
        if not (initialized[0] if isinstance(initialized, tuple) else initialized):
            print("ISC tray unavailable: no GTK display.")
            return 2
        controller = TrayController(Gtk, GLib, Indicator)
        Gtk.main()
        return 0
    except (OSError, ImportError, ValueError, RuntimeError):
        print("ISC tray unavailable: GUI backend error.")
        return 2
