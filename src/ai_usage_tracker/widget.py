from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import importlib
from pathlib import Path
import queue
import sys
import threading
from typing import Any, Mapping, Sequence

from .model import ProviderSnapshot
from .providers.antigravity import read_antigravity_usage
from .providers.claude import MAX_STATUS_PAYLOAD_BYTES, parse_status_payload
from .providers.claude_setup import (
    ClaudeSetupError,
    install_claude_status_line,
    widget_capture_argv,
)
from .providers.codex import read_rate_limits, resolve_codex_executable
from .providers.cursor import read_cursor_usage
from .providers.devin import read_devin_usage
from .providers.github_copilot import read_copilot_usage
from .storage import SnapshotStore
from .widget_settings import SUPPORTED_PROVIDERS, WidgetSettings, WidgetSettingsStore


PROVIDER_ORDER = (
    "cursor",
    "claude",
    "codex",
    "github_copilot",
    "devin",
    "antigravity",
)
PROVIDER_NAMES = {
    "cursor": "Cursor",
    "claude": "Claude Code",
    "codex": "Codex",
    "github_copilot": "GitHub Copilot",
    "devin": "Devin",
    "antigravity": "Antigravity",
}
PROVIDER_DESCRIPTIONS = {
    "cursor": "Reads one exact Cursor session record and sends it only to Cursor's usage RPC.",
    "claude": "Reads only the normalized local status snapshot; no credential or network access.",
    "codex": "Starts the official local Codex process; Codex keeps control of its own login.",
    "github_copilot": "Starts the official GitHub CLI and requests only Copilot premium-request totals.",
    "devin": "Reads only Devin's normalized cached plan record; authentication records are excluded.",
    "antigravity": "Reads only Antigravity's cached model-credit record; OAuth state is excluded.",
}
PROVIDER_SUMMARIES = {
    "cursor": "Monthly included usage and billing reset",
    "claude": "5-hour and 7-day limits from the status hook",
    "codex": "Rolling rate-limit windows from the local app",
    "github_copilot": "Monthly premium-request usage through your signed-in GitHub CLI",
    "devin": "Daily, weekly, and included usage from Devin's local plan cache",
    "antigravity": "Available AI credits from Antigravity's local cache",
}
PROVIDER_COLORS = {
    "cursor": "#7C8CFF",
    "claude": "#D97757",
    "codex": "#26B98A",
    "github_copilot": "#A78BFA",
    "devin": "#4F9CF9",
    "antigravity": "#F2B84B",
}
PROVIDER_MARKS = {
    "cursor": "C",
    "claude": "A",
    "codex": "O",
    "github_copilot": "GH",
    "devin": "D",
    "antigravity": "AG",
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
    remaining = _number(window.get("remaining"))
    unit = window.get("unit")
    if unit == "currency_cents" and used is not None and limit is not None:
        return f"{_format_money(used)} of {_format_money(limit)}"
    if unit == "currency_cents" and used is not None:
        if percent is not None:
            return f"{_format_money(used)} · {percent:.0f}% used"
        return f"{_format_money(used)} used"
    if used is not None and limit is not None:
        return f"{used:g} of {limit:g}"
    if used is not None:
        return f"{used:g} used"
    if remaining is not None:
        return f"{remaining:,.0f} remaining"
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
    status_text = "Live"
    collected_at = document.get("collected_at")
    if isinstance(collected_at, str):
        try:
            parsed_collected_at = datetime.fromisoformat(
                collected_at.replace("Z", "+00:00")
            )
        except ValueError:
            parsed_collected_at = None
        if (
            parsed_collected_at is not None
            and parsed_collected_at.tzinfo is not None
            and datetime.now(UTC) - parsed_collected_at.astimezone(UTC)
            > timedelta(minutes=30)
        ):
            status_text = "Cached"
    return ProviderDisplay(
        provider_id,
        display_name,
        "available",
        status_text,
        tuple(windows),
    )


def disabled_display(provider_id: str) -> ProviderDisplay:
    return ProviderDisplay(
        provider_id,
        PROVIDER_NAMES[provider_id],
        "ready",
        "Ready",
    )


def planned_display(provider_id: str) -> ProviderDisplay:
    return ProviderDisplay(
        provider_id,
        PROVIDER_NAMES[provider_id],
        "planned",
        "Planned",
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
        "Needs attention",
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
                        "claude", "Claude Code", "no_data", "Setup required"
                    )
                return display_from_snapshot(snapshot)
            if provider_id == "codex":
                executable = resolve_codex_executable()
                return display_from_snapshot(read_rate_limits(executable))
            if provider_id == "cursor":
                return display_from_snapshot(read_cursor_usage())
            if provider_id == "github_copilot":
                return display_from_snapshot(read_copilot_usage())
            if provider_id == "devin":
                return display_from_snapshot(read_devin_usage())
            if provider_id == "antigravity":
                return display_from_snapshot(read_antigravity_usage())
        except Exception:
            # UI errors are intentionally generic. Provider exceptions can contain
            # local paths or unreviewed payload fragments and are never displayed.
            return error_display(provider_id)
        return error_display(provider_id)


class UsageWidget:
    BG = "#090D18"
    SURFACE = "#101725"
    CARD = "#131B2B"
    CARD_HOVER = "#182237"
    CARD_BORDER = "#222E45"
    TEXT = "#F4F7FC"
    MUTED = "#8D9AB0"
    FAINT = "#5F6C82"
    ACCENT = "#8391FF"
    GREEN = "#4BD6A2"
    AMBER = "#F2B84B"
    TRACK = "#263147"

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
            provider_id: (
                disabled_display(provider_id)
                if provider_id in SUPPORTED_PROVIDERS
                else planned_display(provider_id)
            )
            for provider_id in PROVIDER_ORDER
        }
        self.updated_text = tk.StringVar(value="All provider access is opt-in")

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
        self.root.geometry("420x650")
        self.root.minsize(420, 560)

    def _build_layout(self) -> None:
        header = self.tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=18, pady=(16, 12))
        brand = self.tk.Canvas(
            header,
            width=34,
            height=34,
            bg=self.BG,
            highlightthickness=0,
            borderwidth=0,
        )
        brand.pack(side="left", padx=(0, 10))
        brand.create_oval(2, 2, 32, 32, fill=self.ACCENT, outline="")
        brand.create_text(17, 17, text="AI", fill="#FFFFFF", font=("TkDefaultFont", 9, "bold"))
        title_group = self.tk.Frame(header, bg=self.BG)
        title_group.pack(side="left")
        self.tk.Label(
            title_group,
            text="Usage Monitor",
            bg=self.BG,
            fg=self.TEXT,
            font=("TkDefaultFont", 15, "bold"),
        ).pack(anchor="w")
        self.tk.Label(
            title_group,
            textvariable=self.updated_text,
            bg=self.BG,
            fg=self.MUTED,
            font=("TkDefaultFont", 8),
        ).pack(anchor="w", pady=(1, 0))
        controls = self.tk.Frame(header, bg=self.BG)
        controls.pack(side="right")
        self._button(controls, "↻", self.refresh_all, compact=True).pack(
            side="left", padx=(0, 6)
        )
        self._button(controls, "Manage", self.open_settings).pack(side="left")

        viewport = self.tk.Frame(self.root, bg=self.BG)
        viewport.pack(fill="both", expand=True, padx=14)
        self.cards_canvas = self.tk.Canvas(
            viewport,
            bg=self.BG,
            highlightthickness=0,
            borderwidth=0,
        )
        self.cards_canvas.pack(fill="both", expand=True)
        self.cards = self.tk.Frame(self.cards_canvas, bg=self.BG)
        self.cards_window = self.cards_canvas.create_window(
            (0, 0), window=self.cards, anchor="nw"
        )
        self.cards.bind(
            "<Configure>",
            lambda _event: self.cards_canvas.configure(
                scrollregion=self.cards_canvas.bbox("all")
            ),
        )
        self.cards_canvas.bind(
            "<Configure>",
            lambda event: self.cards_canvas.itemconfigure(
                self.cards_window, width=event.width
            ),
        )
        self.root.bind(
            "<MouseWheel>",
            lambda event: self.cards_canvas.yview_scroll(
                -1 if event.delta > 0 else 1, "units"
            ),
        )
        self.root.bind(
            "<Button-4>", lambda _event: self.cards_canvas.yview_scroll(-1, "units")
        )
        self.root.bind(
            "<Button-5>", lambda _event: self.cards_canvas.yview_scroll(1, "units")
        )

        self.tk.Label(
            self.root,
            text="ON DEVICE  ·  NO TELEMETRY",
            bg=self.BG,
            fg=self.FAINT,
            font=("TkDefaultFont", 7, "bold"),
        ).pack(pady=(7, 11))

    def _button(
        self,
        parent: Any,
        text: str,
        command: Any,
        *,
        accent: bool = False,
        compact: bool = False,
    ) -> Any:
        background = self.ACCENT if accent else self.SURFACE
        foreground = "#FFFFFF" if accent else self.TEXT
        button = self.tk.Label(
            parent,
            text=text,
            bg=background,
            fg=foreground,
            font=("TkDefaultFont", 8 if compact else 9, "bold"),
            padx=9 if compact else 11,
            pady=6,
            cursor="hand2",
            highlightthickness=0,
            takefocus=True,
        )
        button.bind("<Button-1>", lambda _event: command())
        button.bind("<Return>", lambda _event: command())
        button.bind("<space>", lambda _event: command())
        hover = self.CARD_HOVER if not accent else "#6979EE"
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=background))
        return button

    def _render_cards(self) -> None:
        for child in self.cards.winfo_children():
            child.destroy()
        for provider_id in PROVIDER_ORDER:
            self._render_card(self.displays[provider_id])
        self.root.after_idle(
            lambda: self.cards_canvas.configure(
                scrollregion=self.cards_canvas.bbox("all")
            )
        )

    def _status_style(self, status: str) -> tuple[str, str]:
        if status == "available":
            return "#15372E", self.GREEN
        if status == "ready":
            return "#1D2844", self.ACCENT
        if status in {"error", "no_data"}:
            return "#3A2C17", self.AMBER
        return "#202838", self.MUTED

    def _card_detail(self, display: ProviderDisplay) -> str:
        if display.status == "planned":
            return PROVIDER_DESCRIPTIONS[display.provider_id]
        if display.status == "ready":
            return PROVIDER_SUMMARIES[display.provider_id]
        if display.status == "loading":
            return "Checking the latest usage…"
        if display.status == "error":
            return "Could not refresh. Your saved provider session was not changed."
        if display.status == "no_data" and display.provider_id == "claude":
            return "Enable the Claude status-line capture to begin receiving usage."
        if display.status == "no_data" and display.provider_id == "devin":
            return "Open Devin once to refresh its normalized local plan cache."
        if display.status == "no_data" and display.provider_id == "antigravity":
            return "Open Antigravity's usage/settings view to refresh its local cache."
        if display.status == "no_data":
            return "The provider returned no supported usage measurements."
        return PROVIDER_SUMMARIES[display.provider_id]

    def _render_card(self, display: ProviderDisplay) -> None:
        card = self.tk.Frame(
            self.cards,
            bg=self.CARD,
            highlightbackground=self.CARD_BORDER,
            highlightthickness=1,
        )
        card.pack(fill="x", pady=4)
        content = self.tk.Frame(card, bg=self.CARD)
        content.pack(fill="x", padx=12, pady=(10, 9))
        icon = self.tk.Canvas(
            content,
            width=32,
            height=32,
            bg=self.CARD,
            highlightthickness=0,
            borderwidth=0,
        )
        icon.pack(side="left", anchor="n", padx=(0, 10))
        provider_color = PROVIDER_COLORS[display.provider_id]
        icon.create_oval(1, 1, 31, 31, fill=provider_color, outline="")
        icon.create_text(
            16,
            16,
            text=PROVIDER_MARKS[display.provider_id],
            fill="#FFFFFF",
            font=("TkDefaultFont", 7, "bold"),
        )
        body = self.tk.Frame(content, bg=self.CARD)
        body.pack(side="left", fill="x", expand=True)
        heading = self.tk.Frame(body, bg=self.CARD)
        heading.pack(fill="x")
        self.tk.Label(
            heading,
            text=display.display_name,
            bg=self.CARD,
            fg=self.TEXT,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(side="left")
        status_bg, status_color = self._status_style(display.status)
        self.tk.Label(
            heading,
            text=display.status_text,
            bg=status_bg,
            fg=status_color,
            font=("TkDefaultFont", 7, "bold"),
            padx=7,
            pady=2,
        ).pack(side="right")

        if not display.windows:
            detail_row = self.tk.Frame(body, bg=self.CARD)
            detail_row.pack(fill="x", pady=(5, 0))
            self.tk.Label(
                detail_row,
                text=self._card_detail(display),
                bg=self.CARD,
                fg=self.MUTED,
                font=("TkDefaultFont", 8),
                justify="left",
                anchor="w",
                wraplength=255,
            ).pack(side="left", fill="x", expand=True)
            if display.status == "ready":
                self._button(
                    detail_row,
                    "Connect",
                    lambda provider_id=display.provider_id: self.connect_provider(
                        provider_id
                    ),
                    accent=True,
                    compact=True,
                ).pack(side="right", padx=(8, 0))
            elif display.status == "error":
                self._button(
                    detail_row,
                    "Retry",
                    self.refresh_all,
                    compact=True,
                ).pack(side="right", padx=(8, 0))
            elif display.status == "no_data" and display.provider_id == "claude":
                self._button(
                    detail_row,
                    "Configure",
                    self.configure_claude,
                    accent=True,
                    compact=True,
                ).pack(side="right", padx=(8, 0))
            return

        for index, window in enumerate(display.windows):
            row = self.tk.Frame(body, bg=self.CARD)
            row.pack(fill="x", pady=(7, 0 if index == len(display.windows) - 1 else 2))
            labels = self.tk.Frame(row, bg=self.CARD)
            labels.pack(fill="x")
            self.tk.Label(
                labels,
                text=window.label,
                bg=self.CARD,
                fg=self.MUTED,
                font=("TkDefaultFont", 7),
            ).pack(side="left")
            self.tk.Label(
                labels,
                text=window.amount_text,
                bg=self.CARD,
                fg=self.TEXT,
                font=("TkDefaultFont", 8, "bold"),
            ).pack(side="right")
            if window.used_percent is not None:
                bar = self.tk.Canvas(
                    row,
                    height=5,
                    bg=self.CARD,
                    highlightthickness=0,
                    borderwidth=0,
                )
                bar.pack(fill="x", pady=(4, 2))
                bar.update_idletasks()
                width = max(bar.winfo_width(), 285)
                bar.create_rectangle(0, 0, width, 5, fill=self.TRACK, outline="")
                fill = width * window.used_percent / 100
                color = self.AMBER if window.used_percent >= 90 else self.ACCENT
                bar.create_rectangle(0, 0, fill, 5, fill=color, outline="")
            if window.reset_text:
                self.tk.Label(
                    row,
                    text=window.reset_text,
                    bg=self.CARD,
                    fg=self.MUTED,
                    font=("TkDefaultFont", 7),
                ).pack(anchor="e")

    def refresh_all(self) -> None:
        if self.closed:
            return
        enabled = self.settings.enabled_providers
        for provider_id in PROVIDER_ORDER:
            if provider_id not in SUPPORTED_PROVIDERS:
                self.displays[provider_id] = planned_display(provider_id)
                continue
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

    def _save_settings(self, settings: WidgetSettings, parent: Any) -> bool:
        try:
            self.settings_store.save(settings)
        except (OSError, ValueError):
            self.messagebox.showerror(
                "Settings not saved",
                "The local settings file could not be written safely.",
                parent=parent,
            )
            return False
        self.settings = settings
        self.root.attributes("-topmost", settings.always_on_top)
        return True

    def connect_provider(self, provider_id: str) -> None:
        if provider_id not in SUPPORTED_PROVIDERS:
            return
        approved = self.messagebox.askyesno(
            f"Enable {PROVIDER_NAMES[provider_id]}?",
            PROVIDER_DESCRIPTIONS[provider_id]
            + "\n\nOnly normalized usage details appear in the widget. "
            "You can disable this provider at any time.",
            parent=self.root,
        )
        if not approved:
            return
        settings = WidgetSettings(
            enabled_providers=self.settings.enabled_providers | {provider_id},
            refresh_minutes=self.settings.refresh_minutes,
            always_on_top=self.settings.always_on_top,
        )
        if self._save_settings(settings, self.root):
            self.refresh_all()

    def configure_claude(self) -> None:
        approved = self.messagebox.askyesno(
            "Configure Claude Code?",
            "Add this app as Claude Code's status-line command?\n\n"
            "Claude will send its official status JSON to the local app after each "
            "response. The raw JSON is not retained; only normalized quota percentages "
            "and reset times are stored. Existing status-line settings will never be "
            "overwritten.",
            parent=self.root,
        )
        if not approved:
            return
        try:
            install_claude_status_line(widget_capture_argv())
        except (ClaudeSetupError, OSError, ValueError) as exc:
            self.messagebox.showerror(
                "Claude setup not changed",
                str(exc),
                parent=self.root,
            )
            return
        self.messagebox.showinfo(
            "Claude capture enabled",
            "Claude Code is configured. The card will update after Claude produces its "
            "next response with rate-limit data.",
            parent=self.root,
        )
        self.refresh_all()

    def open_settings(self) -> None:
        dialog = self.tk.Toplevel(self.root)
        dialog.title("AI Usage Settings")
        dialog.configure(bg=self.BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("440x700")

        self.tk.Label(
            dialog,
            text="Manage providers",
            bg=self.BG,
            fg=self.TEXT,
            font=("TkDefaultFont", 15, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 2))
        self.tk.Label(
            dialog,
            text="Each provider remains off until you explicitly enable it.",
            bg=self.BG,
            fg=self.MUTED,
            font=("TkDefaultFont", 8),
        ).pack(anchor="w", padx=20, pady=(0, 13))

        variables: dict[str, Any] = {}
        for provider_id in PROVIDER_ORDER:
            if provider_id not in SUPPORTED_PROVIDERS:
                continue
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
                font=("TkDefaultFont", 9, "bold"),
                anchor="w",
                highlightthickness=0,
            )
            checkbox.pack(fill="x", padx=10, pady=(7, 1))
            self.tk.Label(
                block,
                text=PROVIDER_DESCRIPTIONS[provider_id],
                wraplength=365,
                justify="left",
                bg=self.CARD,
                fg=self.MUTED,
                font=("TkDefaultFont", 7),
            ).pack(anchor="w", padx=13, pady=(0, 8))

        preferences = self.tk.Frame(dialog, bg=self.BG)
        preferences.pack(fill="x", padx=20, pady=(14, 0))
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
            if not self._save_settings(settings, dialog):
                return
            dialog.destroy()
            self.refresh_all()

        save_button = self._button(actions, "Save & refresh", save, accent=True)
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
            size = f"widget-rendered {root.winfo_width()}x{root.winfo_height()}"
            root.destroy()
            if sys.stdout is not None:
                print(size)

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
    parser.add_argument(
        "--claude-capture",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.claude_capture:
        try:
            payload = sys.stdin.buffer.read(MAX_STATUS_PAYLOAD_BYTES + 1)
            if len(payload) > MAX_STATUS_PAYLOAD_BYTES:
                raise ValueError("Claude status payload exceeds the size limit")
            snapshot = parse_status_payload(payload)
            SnapshotStore(Path(args.data_dir) if args.data_dir else None).save(snapshot)
            percentages = [
                f"{window.label}: {window.used_percent:.0f}%"
                for window in snapshot.windows
                if window.used_percent is not None
            ]
            print("Claude | " + " | ".join(percentages) if percentages else "Claude usage pending")
            return 0
        except (OSError, ValueError):
            print("Claude usage unavailable")
            return 2
    try:
        run_widget(
            Path(args.data_dir) if args.data_dir else None,
            smoke_test=args.smoke_test,
        )
    except WidgetRuntimeError as exc:
        print(f"AI Usage Tracker: {exc}")
        return 2
    return 0
