"""Optional GTK3/AppIndicator tray for Intelligent Sleep Controller.

No GI dependency is imported by the CLI unless the tray is launched.
Launching the tray never installs a service or changes OS power settings.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import time

from .manual_power import assess_manual_suspend, request_dialog_suspend
from .receiver import discover_monitor_socket, read_monitor_health
from .settings import ModeConfigError, read_mode, set_mode
from .mode_gate import resolve_mode
from .tray_runtime import TrayMonitorProcess
from .tray_singleton import TrayAlreadyRunning, TrayLockUnavailable, single_tray_instance
from .keepawake import KeepAwakeError, KeepAwakeManager, PRESETS_MINUTES
from .native_controls import (
    NativeControlError, PROFILES, power_profiles, set_profile,
    display_support, display_off, open_power_settings,
)
from .native_sleep import assess_native_sleep, request_native_sleep
from .gnome_policy import (
    GnomePolicyError, change_gnome_policy, inspect_gnome_policy,
)


@dataclass(frozen=True)
class TraySnapshot:
    mode: str
    configured: bool
    valid: bool
    receiver_advertised: bool


def tray_snapshot() -> TraySnapshot:
    try:
        current = read_mode()
        effective = resolve_mode(current)
        mode, configured, valid = (
            effective.mode, current.configured, effective.configuration_valid,
        )
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
    """User-invoked menu supervising a local MONITOR-only child process."""

    def __init__(self, Gtk, GLib, Indicator):
        self.Gtk = Gtk
        self._refreshing = True
        self._monitor_process = TrayMonitorProcess()
        self._awake = KeepAwakeManager()
        self._monitor_state = "off"
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
        self.off = Gtk.RadioMenuItem.new_with_label(None, "OFF — ISC automatic sleep disabled")
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
        self._item("Hibernate…", lambda item: self._native_sleep(item, "hibernate"))
        self._item("Hybrid Sleep…", lambda item: self._native_sleep(item, "hybrid-sleep"))
        self._item("Turn Off Displays…", self._display_off)
        self._separator()
        self.profiles_item = Gtk.MenuItem.new_with_label("Power Profile")
        self.profiles_menu = Gtk.Menu()
        self.profiles_item.set_submenu(self.profiles_menu)
        # Build the submenu once. Removing/recreating its children in GTK's
        # "show" signal makes the AppIndicator submenu collapse immediately.
        self._profile_items = {}
        for profile, label in (
            ("power-saver", "Energy Saver"),
            ("balanced", "Balanced"),
            ("performance", "Performance"),
        ):
            item = Gtk.MenuItem.new_with_label(label)
            item.connect("activate", self._select_profile, profile)
            self.profiles_menu.append(item)
            self._profile_items[profile] = item
        self._profile_unavailable = Gtk.MenuItem.new_with_label(
            "OS power-profile service unavailable"
        )
        self._profile_unavailable.set_sensitive(False)
        self.profiles_menu.append(self._profile_unavailable)
        self.menu.append(self.profiles_item)
        self._item("Open System Power Settings…", self._open_system_power)
        self._item("Disable GNOME Auto Sleep…",
                   lambda item: self._gnome_policy_change("disable"))
        self._item("Restore GNOME Auto Sleep…",
                   lambda item: self._gnome_policy_change("restore"))
        self._separator()
        self.awake_menu_item = Gtk.MenuItem.new_with_label("Keep Awake")
        self.awake_submenu = Gtk.Menu()
        for minutes in PRESETS_MINUTES:
            item = Gtk.MenuItem.new_with_label(f"Keep Awake for {minutes} minutes")
            item.connect("activate", self._awake_start, minutes)
            self.awake_submenu.append(item)
        forever_item = Gtk.MenuItem.new_with_label("Keep Awake — Until Cancelled")
        forever_item.connect("activate", self._awake_start, None)
        self.awake_submenu.append(forever_item)
        self.awake_submenu.append(Gtk.SeparatorMenuItem())
        cancel_item = Gtk.MenuItem.new_with_label("Cancel Keep Awake")
        cancel_item.connect("activate", self._awake_cancel)
        self.awake_submenu.append(cancel_item)
        self.awake_menu_item.set_submenu(self.awake_submenu)
        self.menu.append(self.awake_menu_item)
        self.awake_label = self._label("Keep Awake: inactive")
        self._item("Status…", self._status)
        self._separator()
        self._item("Quit ISC tray", self._quit)
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
        self._monitor_state = self._monitor_process.reconcile(
            mode=state.mode, config_valid=state.valid,
        )
        self._refreshing = True
        try:
            mode = state.mode.upper() if state.valid else "OFF (invalid config)"
            self.title.set_label("Intelligent Sleep Controller — " + mode)
            self.off.set_active(state.mode == "off")
            self.monitor.set_active(state.mode == "monitor")
            state_labels = {
                "off": "Monitoring: stopped (OFF)",
                "running": "Monitoring: receiver active (not authoritative)",
                "starting": "Monitoring: starting",
                "external": "Monitoring: external receiver (not owned)",
                "failed": "Monitoring: unavailable; see CLI",
                "invalid_configuration": "Monitoring: OFF (unsafe configuration)",
            }
            self.receiver_label.set_label(
                state_labels.get(self._monitor_state, "Monitoring: unknown")
            )
            self._refresh_profiles()
            awake = self._awake.status()
            self.awake_label.set_label(
                (
                    "Keep Awake: until cancelled"
                    if awake.remaining_seconds is None
                    else f"Keep Awake: active ({(awake.remaining_seconds + 59) // 60} min remaining)"
                ) if awake.requested else "Keep Awake: inactive"
            )
        finally:
            self._refreshing = False
        return True

    def _refresh_profiles(self, *, force: bool = False):
        """Bound OS subprocess queries, and never rebuild a GTK submenu."""
        now = time.monotonic()
        if not force and now - getattr(self, "_profiles_checked_at", -1e12) < 15:
            return
        self._profiles_checked_at = now
        try:
            state = power_profiles()
        except NativeControlError:
            self._set_profile_parent_label("Power Profile: unavailable")
            for item in self._profile_items.values():
                self._set_profile_item_visible(item, False)
            self._set_profile_item_visible(self._profile_unavailable, True)
            return

        self._set_profile_parent_label("Power Profile: " + state.current)
        self._set_profile_item_visible(self._profile_unavailable, False)
        names = {
            "power-saver": "Energy Saver",
            "balanced": "Balanced",
            "performance": "Performance",
        }
        for profile, item in self._profile_items.items():
            self._set_profile_item_visible(item, profile in state.available)
            label = ("● " if state.current == profile else "   ") + names[profile]
            # Calling Gtk.set_label for unchanged text can trigger a relayout
            # while the pointer is entering an AppIndicator submenu.
            if item.get_label() != label:
                item.set_label(label)

    def _set_profile_parent_label(self, label):
        if self.profiles_item.get_label() != label:
            self.profiles_item.set_label(label)

    @staticmethod
    def _set_profile_item_visible(item, visible):
        if item.get_visible() != visible:
            item.set_visible(visible)

    def _select_profile(self, _item, profile):
        try:
            set_profile(profile)
        except NativeControlError as exc:
            self._notice(
                "Power profile unavailable", str(exc), error=True,
            )
        self._refresh_profiles(force=True)

    def _open_system_power(self, _item):
        try:
            open_power_settings()
        except NativeControlError as exc:
            self._notice("Unable to open GNOME Power settings", str(exc), error=True)

    def _gnome_policy_change(self, operation: str) -> None:
        """Confirmed GNOME policy change; independent of ISC OFF/MONITOR."""
        if operation not in ("disable", "restore"):
            return
        Gtk = self.Gtk
        text = (
            "Disable GNOME automatic idle sleep on AC and battery?"
            if operation == "disable"
            else "Restore ISC-saved GNOME automatic idle sleep settings?"
        )
        explanation = (
            "This writes GNOME's AC and battery idle-sleep timeouts. "
            "ISC records existing values privately for later restoration. "
            "It does not enable ISC automatic suspension."
            if operation == "disable"
            else "Only ISC-saved values can be restored. GNOME settings "
                 "changed by another program will not be overwritten."
        )
        prompt = Gtk.MessageDialog(
            transient_for=None, flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=text,
        )
        prompt.format_secondary_text(explanation)
        try:
            approved = prompt.run() == Gtk.ResponseType.YES
        finally:
            prompt.destroy()
        if not approved:
            return
        try:
            result = change_gnome_policy(operation, confirmed=True)
        except GnomePolicyError:
            self._notice(
                "GNOME automatic sleep settings not verified",
                "The requested operation was refused or incomplete. "
                "Open the ISC window or GNOME Power Settings to review "
                "the current values and restore status.",
                error=True,
            )
            return
        if result.conflict:
            self._notice(
                "GNOME power settings need review",
                "The requested operation was not completely verified. "
                "Review GNOME Power Settings before relying on it.",
                error=True,
            )

    def _display_off(self, _item):
        available, reason = display_support()
        if not available:
            self._notice(
                "Display-only sleep unavailable",
                reason + ". This version supports Xorg DPMS only.",
                error=True,
            )
            return
        Gtk = self.Gtk
        prompt = Gtk.MessageDialog(
            transient_for=None, flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="Turn off the monitor images?",
        )
        prompt.format_secondary_text(
            "Only the displays will enter low-power mode. "
            "The computer and background tasks keep running. "
            "Keyboard or mouse activity normally wakes the displays."
        )
        prompt.add_button("Cancel", Gtk.ResponseType.CANCEL)
        prompt.add_button("Turn Off Displays", Gtk.ResponseType.OK)
        try:
            accepted = prompt.run() == Gtk.ResponseType.OK
        finally:
            prompt.destroy()
        if accepted:
            try:
                display_off(confirm=True)
            except NativeControlError as exc:
                self._notice("Display-off request refused", str(exc), error=True)

    def _native_sleep(self, _item, mode):
        assessment = assess_native_sleep(mode)
        if not assessment.request_possible:
            self._notice(
                mode + " unavailable",
                "Linux currently reports: "
                + assessment.capability + ". "
                + ", ".join(assessment.blockers),
                error=True,
            )
            return
        Gtk = self.Gtk
        phrase = "HIBERNATE" if mode == "hibernate" else "HYBRID"
        prompt = Gtk.Dialog(
            title=mode + " this computer?",
            transient_for=None, flags=Gtk.DialogFlags.MODAL,
        )
        prompt.add_button("Cancel", Gtk.ResponseType.CANCEL)
        prompt.add_button("Proceed", Gtk.ResponseType.OK)
        label = Gtk.Label(
            label="Save all work first. Linux reports this mode is supported, "
                  "but resume reliability is not verified.\n"
                  "Type " + phrase + " to confirm this power action."
        )
        label.set_line_wrap(True)
        label.set_max_width_chars(58)
        prompt.get_content_area().add(label)
        phrase_entry = Gtk.Entry()
        phrase_entry.set_placeholder_text("Type " + phrase)
        prompt.get_content_area().add(phrase_entry)
        prompt.get_content_area().show_all()
        try:
            accepted = prompt.run() == Gtk.ResponseType.OK
            entered = phrase_entry.get_text() if accepted else ""
        finally:
            prompt.destroy()
        if accepted:
            result = request_native_sleep(mode, confirmed=True, typed=entered)
            if not result.submitted:
                self._notice(
                    "No power request made", result.reason, error=True,
                )

    def _awake_start(self, _item, minutes):
        try:
            outcome = self._awake.start(minutes)
            if not outcome.requested:
                self._notice(
                    "Keep Awake unavailable",
                    "No verified systemd inhibitor was acquired.",
                    error=True,
                )
        except KeepAwakeError as exc:
            self._notice(
                "Keep Awake unavailable",
                "The systemd inhibitor could not be verified: " + str(exc),
                error=True,
            )
        self.refresh()

    def _awake_cancel(self, _item):
        try:
            self._awake.cancel()
        except KeepAwakeError:
            self._notice(
                "Keep Awake cancellation unverified",
                "Use the systemd inhibitor status to verify that the lock was released.",
                error=True,
            )
        self.refresh()

    def _status(self, _item):
        self.refresh()
        state = tray_snapshot()
        mode = state.mode.upper() if state.valid else "OFF (invalid config)"
        try:
            current_profile = power_profiles().current
        except NativeControlError:
            current_profile = "unavailable"
        health = read_monitor_health()
        if health is None:
            monitoring_detail = "Receiver health: unavailable"
        else:
            age = (
                f"{health.last_event_age_seconds}s ago"
                if health.last_event_age_seconds is not None
                else "no events observed"
            )
            monitoring_detail = (
                f"Receiver health: {health.accepted} accepted / "
                f"{health.rejected} rejected events\n"
                f"Observed active: {health.observed_active}; "
                f"waiting-human: {health.observed_waiting_human}; "
                f"unknown: {health.observed_unknown}\n"
                f"Most recent event: {age}"
            )
        if health is not None and health.claude_code_observations is not None:
            monitoring_detail += (
                f"\nClaimed source events: Claude Code={health.claude_code_observations}, "
                f"Codex={health.codex_observations}; "
                f"synthetic probes={health.synthetic_probe_events}"
            )
        if health is not None and health.process_roots is not None:
            monitoring_detail += (
                f"\nPartial processes: roots={health.process_roots}, "
                f"children={health.process_descendants}, "
                f"visibility races={health.process_visibility_races}; "
                f"sample age={health.process_sample_age_seconds}s"
            )
        elif health is not None:
            monitoring_detail += "\nBackground process census: UNKNOWN"
        try:
            power = inspect_gnome_policy()
            if power.ac_timeout is None or power.battery_timeout is None:
                policy_line = "GNOME idle sleep: UNKNOWN"
            elif power.reason == "gnome_idle_disabled":
                policy_line = "GNOME idle sleep: disabled (AC and battery)"
            elif power.reason == "gnome_idle_enabled":
                policy_line = "GNOME idle sleep: enabled"
            else:
                policy_line = "GNOME idle sleep: conflicting or incomplete"
        except (GnomePolicyError, OSError, ValueError):
            policy_line = "GNOME idle sleep: UNKNOWN"
        self._notice(
            "Intelligent Sleep Controller",
            f"Mode preference: {mode}\n"
            f"{policy_line}\n"
            f"OS Power Profile: {current_profile}\n"
            f"Tray-managed monitor: {self._monitor_state}\n"
            f"Receiver socket: {'advertised' if state.receiver_advertised else 'not advertised'}\n"
            f"{monitoring_detail}\n"
            f"Keep Awake: {'until cancelled' if self._awake.status().requested and self._awake.status().remaining_seconds is None else 'active' if self._awake.status().requested else 'inactive'}\n"
            "Only privacy-limited lifecycle metadata is collected.\n"
            "Full workload coverage is not verified.\n"
            "Automatic suspension is not implemented.",
        )

    def _quit(self, _item):
        self.close()
        self.Gtk.main_quit()

    def close(self):
        try:
            self._awake.close()
        except KeepAwakeError:
            print("ISC Keep Awake release could not be verified.")
        self._monitor_process.close()

    def _suspend(self, _item):
        if self._awake.status().requested:
            self._notice(
                "Keep Awake is active",
                "Cancel Keep Awake before requesting manual suspend.",
                error=True,
            )
            return
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
        with single_tray_instance():
            Gtk, GLib, Indicator = _gtk_dependencies()
            initialized = Gtk.init_check()
            if not (initialized[0] if isinstance(initialized, tuple) else initialized):
                print("ISC tray unavailable: no GTK display.")
                return 2
            controller = TrayController(Gtk, GLib, Indicator)
            try:
                Gtk.main()
            finally:
                controller.close()
        return 0
    except TrayAlreadyRunning:
        print("ISC tray already running in this user session.")
        return 2
    except TrayLockUnavailable:
        print("ISC tray unavailable: cannot lock private runtime.")
        return 2
    except (OSError, ImportError, ValueError, RuntimeError):
        print("ISC tray unavailable: GUI backend error.")
        return 2
