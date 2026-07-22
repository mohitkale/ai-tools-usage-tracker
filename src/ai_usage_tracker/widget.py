from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import importlib
from pathlib import Path
import queue
import sys
import threading
from typing import Any, Mapping, Sequence

from .model import ProviderSnapshot
from .providers.codex import read_rate_limits, resolve_codex_executable
from .providers.cursor import read_cursor_usage
from .storage import SnapshotStore
from .widget_settings import WidgetSettings, WidgetSettingsStore


PROVIDER_ORDER = ("claude", "codex", "cursor")
PROVIDER_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
}
PROVIDER_DESCRIPTIONS = {
    "claude": "Reads only the normalized local status snapshot; no credential or network access.",
    "codex": "Starts the official local Codex process; Codex keeps control of its own login.",
    "cursor": "Reads one exact Cursor session record and sends it only to Cursor's usage RPC.",
}


class WidgetRuntimeError(RuntimeError):
    pass


def load_tk_modules() -> tuple[Any, Any, Any]:
    """Load Tk, including Homebrew's intentionally isolated macOS Tk module."""
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk

        return tk, messagebox, ttk
    except (ImportError, ModuleNotFoundError) as original:
        if sys.platform != "darwin":
            raise WidgetRuntimeError(
                "Tk support is unavailable in this Python installation. "
                "Use a packaged build or install the matching Python Tk package."
            ) from original

        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        candidates = (
            Path(f"/usr/local/opt/python-tk@{version}/libexec"),
            Path(f"/opt/homebrew/opt/python-tk@{version}/libexec"),
        )
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            formula = f"python-tk@{version}"
            if len(resolved.parents) < 3 or resolved.parents[1].name != formula:
                continue
            if not any(resolved.glob("_tkinter.*")):
                continue
            sys.path.insert(0, str(resolved))
            sys.modules.pop("tkinter", None)
            sys.modules.pop("_tkinter", None)
            importlib.invalidate_caches()
            try:
                import tkinter as tk
                from tkinter import messagebox, ttk

                return tk, messagebox, ttk
            except (ImportError, ModuleNotFoundError):
                continue
        raise WidgetRuntimeError(
            "Tk support is unavailable in this Python installation. "
            "Use a packaged build or install the matching Python Tk package."
        ) from original


@dataclass(frozen=True, slots=True)
class WindowDisplay:
    label: str
    used_percent: float | None
    amount_text: str
    reset_text: str | None


@dataclass(frozen=True, slots=True)
class ProviderDisplay:
    provider_id: str
    display_name: str
    status: str
    status_text: str
    windows: tuple[WindowDisplay, ...] = ()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _format_money(cents: float) -> str:
    return f"${cents / 100:,.2f}"


def _format_reset(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        reset = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if reset.tzinfo is None:
        return None
    local = reset.astimezone()
    return f"Resets {local.strftime('%b')} {local.day}, {local.strftime('%I:%M %p').lstrip('0')}"


def _format_amount(window: Mapping[str, Any], percent: float | None) -> str:
    used = _number(window.get("used"))
    limit = _number(window.get("limit"))
    unit = window.get("unit")
    if unit == "currency_cents" and used is not None and limit is not None:
        return f"{_format_money(used)} of {_format_money(limit)}"
    if used is not None and limit is not None:
        return f"{used:g} of {limit:g}"
    if percent is not None:
        return f"{percent:.0f}% used"
    return "Usage available"


def display_from_snapshot(snapshot: ProviderSnapshot | Mapping[str, Any]) -> ProviderDisplay:
    document = snapshot.to_dict() if isinstance(snapshot, ProviderSnapshot) else snapshot
    provider_id = document.get("provider_id")
    if provider_id not in PROVIDER_NAMES:
        raise ValueError("snapshot has an unsupported provider")
    display_name = PROVIDER_NAMES[provider_id]
    status = document.get("status")
    if status != "available":
        status_text = "Waiting for data" if status == "no_data" else "Unavailable"
        return ProviderDisplay(provider_id, display_name, str(status), status_text)
    raw_windows = document.get("windows")
    if not isinstance(raw_windows, (list, tuple)):
        raise ValueError("snapshot windows are invalid")
    windows: list[WindowDisplay] = []
    for raw_window in raw_windows:
        if not isinstance(raw_window, Mapping):
            continue
        label = raw_window.get("label")
        if not isinstance(label, str) or not label or len(label) > 80:
            continue
        percent = _number(raw_window.get("used_percent"))
        if percent is not None:
            percent = min(max(percent, 0), 100)
        windows.append(
            WindowDisplay(
                label=label,
                used_percent=percent,
                amount_text=_format_amount(raw_window, percent),
                reset_text=_format_reset(raw_window.get("resets_at")),
            )
        )
    if not windows:
        return ProviderDisplay(provider_id, display_name, "no_data", "Waiting for data")
    return ProviderDisplay(
        provider_id,
        display_name,
        "available",
        "Live",
        tuple(windows),
    )


def disabled_display(provider_id: str) -> ProviderDisplay:
    return ProviderDisplay(
        provider_id,
        PROVIDER_NAMES[provider_id],
        "disabled",
        "Not enabled",
    )


def loading_display(provider_id: str) -> ProviderDisplay:
    return ProviderDisplay(
        provider_id,
        PROVIDER_NAMES[provider_id],
        "loading",
        "Refreshing…",
    )


def error_display(provider_id: str) -> ProviderDisplay:
    return ProviderDisplay(
        provider_id,
        PROVIDER_NAMES[provider_id],
        "error",
        "Refresh failed",
    )


class ProviderCollector:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.snapshot_store = SnapshotStore(data_dir)

    def collect(self, provider_id: str) -> ProviderDisplay:
        try:
            if provider_id == "claude":
                snapshot = self.snapshot_store.load("claude")
                if snapshot is None:
                    return ProviderDisplay(
                        "claude", "Claude Code", "no_data", "Waiting for status snapshot"
                    )
                return display_from_snapshot(snapshot)
            if provider_id == "codex":
                executable = resolve_codex_executable()
                return display_from_snapshot(read_rate_limits(executable))
            if provider_id == "cursor":
                return display_from_snapshot(read_cursor_usage())
        except Exception:
            # UI errors are intentionally generic. Provider exceptions can contain
            # local paths or unreviewed payload fragments and are never displayed.
            return error_display(provider_id)
        return error_display(provider_id)


class UsageWidget:
    BG = "#0B1020"
    CARD = "#151C2F"
    CARD_BORDER = "#26324D"
    TEXT = "#F7F9FC"
    MUTED = "#94A3B8"
    ACCENT = "#7C8CFF"
    GREEN = "#41D19A"
    AMBER = "#F5B942"
    TRACK = "#27334C"

    def __init__(
        self,
        root: Any,
        tk: Any,
        ttk: Any,
        messagebox: Any,
        settings_store: WidgetSettingsStore,
        collector: ProviderCollector,
    ) -> None:
        self.root = root
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.settings_store = settings_store
        self.collector = collector
        try:
            self.settings = settings_store.load()
        except (OSError, ValueError):
            self.settings = WidgetSettings()
        self.results: queue.Queue[ProviderDisplay] = queue.Queue()
        self.in_progress: set[str] = set()
        self.closed = False
        self.refresh_job: str | None = None
        self.displays = {
            provider_id: disabled_display(provider_id) for provider_id in PROVIDER_ORDER
        }
        self.updated_text = tk.StringVar(value="Nothing accessed yet")

        self._configure_window()
        self._build_layout()
        self._render_cards()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(120, self._poll_results)
        self.root.after(200, self.refresh_all)

    def _configure_window(self) -> None:
        self.root.title("AI Usage Tracker")
        self.root.configure(bg=self.BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", self.settings.always_on_top)
        self.root.geometry("380x520")
        self.root.minsize(380, 420)

    def _build_layout(self) -> None:
        header = self.tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=18, pady=(16, 10))
        title_group = self.tk.Frame(header, bg=self.BG)
        title_group.pack(side="left")
        self.tk.Label(
            title_group,
            text="AI Usage",
            bg=self.BG,
            fg=self.TEXT,
            font=("TkDefaultFont", 18, "bold"),
        ).pack(anchor="w")
        self.tk.Label(
            title_group,
            textvariable=self.updated_text,
            bg=self.BG,
            fg=self.MUTED,
            font=("TkDefaultFont", 9),
        ).pack(anchor="w", pady=(2, 0))
        controls = self.tk.Frame(header, bg=self.BG)
        controls.pack(side="right")
        self._button(controls, "Refresh", self.refresh_all).pack(side="left", padx=(0, 6))
        self._button(controls, "Settings", self.open_settings).pack(side="left")

        self.cards = self.tk.Frame(self.root, bg=self.BG)
        self.cards.pack(fill="both", expand=True, padx=14)

        self.tk.Label(
            self.root,
            text="Local only  •  No telemetry",
            bg=self.BG,
            fg=self.MUTED,
            font=("TkDefaultFont", 8),
        ).pack(pady=(8, 12))

    def _button(self, parent: Any, text: str, command: Any) -> Any:
        return self.tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.CARD,
            fg=self.TEXT,
            activebackground=self.CARD_BORDER,
            activeforeground=self.TEXT,
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=6,
            cursor="hand2",
            highlightthickness=0,
        )

    def _render_cards(self) -> None:
        for child in self.cards.winfo_children():
            child.destroy()
        for provider_id in PROVIDER_ORDER:
            self._render_card(self.displays[provider_id])

    def _render_card(self, display: ProviderDisplay) -> None:
        card = self.tk.Frame(
            self.cards,
            bg=self.CARD,
            highlightbackground=self.CARD_BORDER,
            highlightthickness=1,
        )
        card.pack(fill="x", pady=5)
        heading = self.tk.Frame(card, bg=self.CARD)
        heading.pack(fill="x", padx=13, pady=(10, 6))
        self.tk.Label(
            heading,
            text=display.display_name,
            bg=self.CARD,
            fg=self.TEXT,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(side="left")
        status_color = self.GREEN if display.status == "available" else self.MUTED
        if display.status == "error":
            status_color = self.AMBER
        self.tk.Label(
            heading,
            text=display.status_text,
            bg=self.CARD,
            fg=status_color,
            font=("TkDefaultFont", 9),
        ).pack(side="right")

        if not display.windows:
            detail = "Open Settings to enable" if display.status == "disabled" else ""
            if detail:
                self.tk.Label(
                    card,
                    text=detail,
                    bg=self.CARD,
                    fg=self.MUTED,
                    font=("TkDefaultFont", 8),
                ).pack(anchor="w", padx=13, pady=(0, 10))
            else:
                self.tk.Frame(card, bg=self.CARD, height=5).pack()
            return

        for index, window in enumerate(display.windows):
            row = self.tk.Frame(card, bg=self.CARD)
            row.pack(fill="x", padx=13, pady=(0, 9 if index == len(display.windows) - 1 else 7))
            labels = self.tk.Frame(row, bg=self.CARD)
            labels.pack(fill="x")
            self.tk.Label(
                labels,
                text=window.label,
                bg=self.CARD,
                fg=self.MUTED,
                font=("TkDefaultFont", 8),
            ).pack(side="left")
            self.tk.Label(
                labels,
                text=window.amount_text,
                bg=self.CARD,
                fg=self.TEXT,
                font=("TkDefaultFont", 9, "bold"),
            ).pack(side="right")
            if window.used_percent is not None:
                bar = self.tk.Canvas(
                    row,
                    height=6,
                    bg=self.CARD,
                    highlightthickness=0,
                    borderwidth=0,
                )
                bar.pack(fill="x", pady=(5, 3))
                bar.update_idletasks()
                width = max(bar.winfo_width(), 320)
                bar.create_rectangle(0, 0, width, 6, fill=self.TRACK, outline="")
                fill = width * window.used_percent / 100
                color = self.AMBER if window.used_percent >= 90 else self.ACCENT
                bar.create_rectangle(0, 0, fill, 6, fill=color, outline="")
            if window.reset_text:
                self.tk.Label(
                    row,
                    text=window.reset_text,
                    bg=self.CARD,
                    fg=self.MUTED,
                    font=("TkDefaultFont", 8),
                ).pack(anchor="e")

    def refresh_all(self) -> None:
        if self.closed:
            return
        enabled = self.settings.enabled_providers
        for provider_id in PROVIDER_ORDER:
            if provider_id not in enabled:
                self.displays[provider_id] = disabled_display(provider_id)
                continue
            if provider_id in self.in_progress:
                continue
            self.in_progress.add(provider_id)
            self.displays[provider_id] = loading_display(provider_id)
            thread = threading.Thread(
                target=self._collect_in_background,
                args=(provider_id,),
                daemon=True,
                name=f"usage-{provider_id}",
            )
            thread.start()
        self._render_cards()
        self._schedule_refresh()

    def _collect_in_background(self, provider_id: str) -> None:
        self.results.put(self.collector.collect(provider_id))

    def _poll_results(self) -> None:
        if self.closed:
            return
        changed = False
        while True:
            try:
                display = self.results.get_nowait()
            except queue.Empty:
                break
            self.in_progress.discard(display.provider_id)
            if display.provider_id in self.settings.enabled_providers:
                self.displays[display.provider_id] = display
            changed = True
        if changed:
            self.updated_text.set("Updated just now")
            self._render_cards()
        self.root.after(120, self._poll_results)

    def _schedule_refresh(self) -> None:
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
        milliseconds = self.settings.refresh_minutes * 60 * 1000
        self.refresh_job = self.root.after(milliseconds, self.refresh_all)

    def open_settings(self) -> None:
        dialog = self.tk.Toplevel(self.root)
        dialog.title("AI Usage Settings")
        dialog.configure(bg=self.BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("430x475")

        self.tk.Label(
            dialog,
            text="Provider access",
            bg=self.BG,
            fg=self.TEXT,
            font=("TkDefaultFont", 16, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 3))
        self.tk.Label(
            dialog,
            text="Nothing is accessed until you enable it here.",
            bg=self.BG,
            fg=self.MUTED,
            font=("TkDefaultFont", 9),
        ).pack(anchor="w", padx=20, pady=(0, 12))

        variables: dict[str, Any] = {}
        for provider_id in PROVIDER_ORDER:
            block = self.tk.Frame(dialog, bg=self.CARD)
            block.pack(fill="x", padx=20, pady=4)
            variable = self.tk.BooleanVar(
                value=provider_id in self.settings.enabled_providers
            )
            variables[provider_id] = variable
            checkbox = self.tk.Checkbutton(
                block,
                text=PROVIDER_NAMES[provider_id],
                variable=variable,
                bg=self.CARD,
                fg=self.TEXT,
                activebackground=self.CARD,
                activeforeground=self.TEXT,
                selectcolor=self.CARD_BORDER,
                font=("TkDefaultFont", 10, "bold"),
                anchor="w",
                highlightthickness=0,
            )
            checkbox.pack(fill="x", padx=10, pady=(8, 2))
            self.tk.Label(
                block,
                text=PROVIDER_DESCRIPTIONS[provider_id],
                wraplength=365,
                justify="left",
                bg=self.CARD,
                fg=self.MUTED,
                font=("TkDefaultFont", 8),
            ).pack(anchor="w", padx=13, pady=(0, 9))

        preferences = self.tk.Frame(dialog, bg=self.BG)
        preferences.pack(fill="x", padx=20, pady=(12, 0))
        topmost = self.tk.BooleanVar(value=self.settings.always_on_top)
        self.tk.Checkbutton(
            preferences,
            text="Keep widget above other windows",
            variable=topmost,
            bg=self.BG,
            fg=self.TEXT,
            activebackground=self.BG,
            activeforeground=self.TEXT,
            selectcolor=self.CARD_BORDER,
            highlightthickness=0,
        ).pack(anchor="w")
        interval_row = self.tk.Frame(preferences, bg=self.BG)
        interval_row.pack(fill="x", pady=(7, 0))
        self.tk.Label(
            interval_row,
            text="Refresh every",
            bg=self.BG,
            fg=self.MUTED,
        ).pack(side="left")
        interval = self.tk.StringVar(value=str(self.settings.refresh_minutes))
        selector = self.ttk.Combobox(
            interval_row,
            width=4,
            state="readonly",
            textvariable=interval,
            values=("2", "5", "10", "15", "30"),
        )
        selector.pack(side="left", padx=(8, 5))
        self.tk.Label(
            interval_row, text="minutes", bg=self.BG, fg=self.MUTED
        ).pack(side="left")

        actions = self.tk.Frame(dialog, bg=self.BG)
        actions.pack(fill="x", padx=20, pady=(18, 15))
        self._button(actions, "Cancel", dialog.destroy).pack(side="right")

        def save() -> None:
            settings = WidgetSettings(
                enabled_providers=frozenset(
                    provider_id
                    for provider_id, variable in variables.items()
                    if variable.get()
                ),
                refresh_minutes=int(interval.get()),
                always_on_top=bool(topmost.get()),
            )
            try:
                self.settings_store.save(settings)
            except (OSError, ValueError):
                self.messagebox.showerror(
                    "Settings not saved",
                    "The local settings file could not be written safely.",
                    parent=dialog,
                )
                return
            self.settings = settings
            self.root.attributes("-topmost", settings.always_on_top)
            dialog.destroy()
            self.refresh_all()

        save_button = self._button(actions, "Save & refresh", save)
        save_button.configure(bg=self.ACCENT)
        save_button.pack(side="right", padx=(0, 8))

    def close(self) -> None:
        self.closed = True
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
        self.root.destroy()


def run_widget(data_dir: Path | None = None, *, smoke_test: bool = False) -> None:
    tk, messagebox, ttk = load_tk_modules()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise WidgetRuntimeError("The desktop display is unavailable.") from exc
    settings_store = WidgetSettingsStore(data_dir)
    collector = ProviderCollector(data_dir)
    UsageWidget(root, tk, ttk, messagebox, settings_store, collector)
    if smoke_test:
        def finish_smoke_test() -> None:
            root.update_idletasks()
            print(f"widget-rendered {root.winfo_width()}x{root.winfo_height()}")
            root.destroy()

        root.after(350, finish_smoke_test)
    root.mainloop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai-usage-widget",
        description="Local-only always-on-top AI usage widget",
    )
    parser.add_argument(
        "--data-dir",
        help="override the local app-data directory; intended for testing and portable use",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="render the widget once and exit without enabling providers",
    )
    args = parser.parse_args(argv)
    try:
        run_widget(
            Path(args.data_dir) if args.data_dir else None,
            smoke_test=args.smoke_test,
        )
    except WidgetRuntimeError as exc:
        print(f"AI Usage Tracker: {exc}")
        return 2
    return 0
