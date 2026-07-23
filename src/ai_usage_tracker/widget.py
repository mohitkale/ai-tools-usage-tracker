from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import importlib
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any, Collection, Mapping, Sequence

from .fixtures import all_snapshots
from .model import ProviderSnapshot
from .providers.antigravity import read_antigravity_usage
from .providers.claude import MAX_STATUS_PAYLOAD_BYTES, parse_status_payload
from .providers.claude_setup import (
    ClaudeSetupError,
    claude_status_line_state,
    install_claude_status_line,
    widget_capture_argv,
)
from .providers.codex import read_rate_limits, resolve_codex_executable
from .providers.cursor import read_cursor_usage
from .providers.devin import read_devin_usage
from .providers.github_copilot import (
    GitHubCopilotProbeError,
    read_copilot_cli_usage,
    safe_error_guidance,
    safe_error_status,
)
from .storage import SnapshotStore
from .widget_settings import SUPPORTED_PROVIDERS, WidgetSettings, WidgetSettingsStore


PROVIDER_ORDER = (
    "cursor",
    "claude",
    "codex",
    "devin",
    "antigravity",
    "github_copilot",
)
PROVIDER_NAMES = {
    "cursor": "Cursor",
    "claude": "Claude",
    "codex": "Codex",
    "github_copilot": "GitHub Copilot",
    "devin": "Devin",
    "antigravity": "Antigravity",
}
PROVIDER_DESCRIPTIONS = {
    "cursor": "Reads one exact Cursor session record and sends it only to Cursor's usage RPC.",
    "claude": "Reads only the normalized local status snapshot; no credential or network access.",
    "codex": "Starts the official local Codex process; Codex keeps control of its own login.",
    "github_copilot": "Reads only aggregate AI-credit totals from Copilot CLI's undocumented local event database; credentials and prompts are excluded.",
    "devin": "Reads only Devin's undocumented normalized plan cache; authentication records are excluded.",
    "antigravity": "Reads only Antigravity's undocumented model-credit cache; OAuth state is excluded.",
}
PROVIDER_SUMMARIES = {
    "cursor": "Live billing-cycle usage",
    "claude": "Plan limits or session context",
    "codex": "Rolling usage windows",
    "github_copilot": "Local Copilot CLI AI-credit usage",
    "devin": "Quota and included usage",
    "antigravity": "Available AI credits",
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
    detail: str | None = None


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
    if unit == "ai_credits" and used is not None:
        return f"{used:,.2f} used locally"
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
    status_text = (
        "Live API"
        if provider_id == "cursor"
        else "Local"
        if provider_id == "github_copilot"
        else "Live"
    )
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
        "Off",
    )


def planned_display(provider_id: str) -> ProviderDisplay:
    return ProviderDisplay(
        provider_id,
        PROVIDER_NAMES[provider_id],
        "planned",
        "Planned",
    )


def loading_display(
    provider_id: str,
    status_text: str = "Refreshing…",
    detail: str | None = None,
) -> ProviderDisplay:
    return ProviderDisplay(
        provider_id,
        PROVIDER_NAMES[provider_id],
        "loading",
        status_text,
        detail=detail,
    )


def error_display(
    provider_id: str,
    detail: str | None = None,
    status_text: str = "Needs attention",
) -> ProviderDisplay:
    return ProviderDisplay(
        provider_id,
        PROVIDER_NAMES[provider_id],
        "error",
        status_text,
        detail=detail,
    )


class ProviderCollector:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.snapshot_store = SnapshotStore(data_dir)

    def collect(self, provider_id: str) -> ProviderDisplay:
        try:
            if provider_id == "claude":
                snapshot = self.snapshot_store.load("claude")
                if snapshot is None:
                    hook_state = claude_status_line_state(widget_capture_argv())
                    if hook_state == "installed":
                        return ProviderDisplay(
                            "claude", "Claude", "no_data", "Claude Code only"
                        )
                    if hook_state == "different":
                        return ProviderDisplay(
                            "claude", "Claude", "no_data", "Existing hook"
                        )
                    return ProviderDisplay(
                        "claude", "Claude", "no_data", "Code setup required"
                    )
                return display_from_snapshot(snapshot)
            if provider_id == "codex":
                executable = resolve_codex_executable()
                return display_from_snapshot(read_rate_limits(executable))
            if provider_id == "cursor":
                return display_from_snapshot(read_cursor_usage())
            if provider_id == "github_copilot":
                return display_from_snapshot(read_copilot_cli_usage())
            if provider_id == "devin":
                return display_from_snapshot(read_devin_usage())
            if provider_id == "antigravity":
                return display_from_snapshot(read_antigravity_usage())
        except GitHubCopilotProbeError as exc:
            return error_display(
                provider_id,
                safe_error_guidance(exc),
                safe_error_status(exc),
            )
        except Exception:
            # UI errors are intentionally generic. Provider exceptions can contain
            # local paths or unreviewed payload fragments and are never displayed.
            return error_display(provider_id)
        return error_display(provider_id)


class DemoProviderCollector:
    """Returns synthetic snapshots without touching provider resources."""

    def __init__(self) -> None:
        collected_at = datetime.now(UTC)
        self.displays = {
            snapshot.provider_id: display_from_snapshot(snapshot)
            for snapshot in all_snapshots(collected_at)
        }

    def collect(self, provider_id: str) -> ProviderDisplay:
        try:
            return self.displays[provider_id]
        except KeyError:
            return error_display(provider_id)


class DemoSettingsStore:
    """Keeps demo preferences in memory so the demo never writes local state."""

    def __init__(self) -> None:
        self.settings = WidgetSettings(
            enabled_providers=frozenset({"cursor", "claude", "codex"}),
            refresh_minutes=5,
            always_on_top=False,
        )

    def load(self) -> WidgetSettings:
        return self.settings

    def save(self, settings: WidgetSettings) -> None:
        self.settings = settings


class UsageWidget:
    BG = "#0A0E14"
    SURFACE = "#111721"
    CARD = "#141B26"
    CARD_HOVER = "#1A2330"
    CARD_BORDER = "#273140"
    TEXT = "#F2F5F8"
    MUTED = "#98A4B3"
    FAINT = "#667282"
    ACCENT = "#7D86F7"
    GREEN = "#54D6A0"
    AMBER = "#F3BC5B"
    RED = "#F17B82"
    TRACK = "#293344"
    DETAIL_WINDOW_WIDTH = 472
    COMPACT_WINDOW_WIDTH = 340
    MIN_WINDOW_HEIGHT = 170
    SCREEN_MARGIN = 80
    MANUAL_REFRESH_COOLDOWN_SECONDS = 15.0
    RETRY_FEEDBACK_DELAY_MS = 250

    def __init__(
        self,
        root: Any,
        tk: Any,
        ttk: Any,
        messagebox: Any,
        settings_store: WidgetSettingsStore,
        collector: ProviderCollector,
        *,
        demo_mode: bool = False,
    ) -> None:
        self.root = root
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.settings_store = settings_store
        self.collector = collector
        self.demo_mode = demo_mode
        self.font_family = (
            "SF Pro Text"
            if sys.platform == "darwin"
            else "Segoe UI"
            if sys.platform == "win32"
            else "DejaVu Sans"
        )
        try:
            self.settings = settings_store.load()
        except (OSError, ValueError):
            self.settings = WidgetSettings()
        self.results: queue.Queue[ProviderDisplay] = queue.Queue()
        self.in_progress: set[str] = set()
        self.closed = False
        self.refresh_job: str | None = None
        self.render_job: str | None = None
        self.last_refresh_started = 0.0
        self.compact_mode = False
        self.displays = {
            provider_id: (
                disabled_display(provider_id)
                if provider_id in SUPPORTED_PROVIDERS
                else planned_display(provider_id)
            )
            for provider_id in PROVIDER_ORDER
        }
        self.updated_text = tk.StringVar(value="Not updated")
        self.provider_count_text = tk.StringVar(value="0 providers")

        self._configure_window()
        self._build_layout()
        self._render_cards()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(120, self._poll_results)
        self.root.after(200, lambda: self.refresh_all(force=True))

    def _configure_window(self) -> None:
        self.root.title("AI Tools Usage Tracker")
        self.root.configure(bg=self.BG)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", self.settings.always_on_top)
        width = self._current_window_width()
        self.root.geometry(f"{width}x{self.MIN_WINDOW_HEIGHT}")
        self.root.minsize(width, self.MIN_WINDOW_HEIGHT)
        self.root.maxsize(
            width,
            max(
                self.MIN_WINDOW_HEIGHT,
                self.root.winfo_screenheight() - self.SCREEN_MARGIN,
            ),
        )
        if sys.platform == "darwin":
            try:
                self.root.tk.call(
                    "tk::unsupported::MacWindowStyle",
                    "style",
                    self.root._w,
                    "utility",
                    "closeBox collapseBox",
                )
            except self.tk.TclError:
                pass
            self.root.bind("<Command-m>", lambda _event: self.root.iconify())

    def _font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        return (self.font_family, size, weight)

    def _current_window_width(self) -> int:
        return (
            self.COMPACT_WINDOW_WIDTH
            if self.compact_mode
            else self.DETAIL_WINDOW_WIDTH
        )

    @staticmethod
    def _updated_time_text() -> str:
        local = datetime.now().astimezone()
        return f"Updated {local.strftime('%I:%M %p').lstrip('0')}"

    def _build_layout(self) -> None:
        header = self.tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=16, pady=(13, 10))
        title_group = self.tk.Frame(header, bg=self.BG)
        title_group.pack(side="left")
        self.title_label = self.tk.Label(
            title_group,
            text="AI Tools Usage Tracker",
            bg=self.BG,
            fg=self.TEXT,
            font=self._font(14, "bold"),
        )
        self.title_label.pack(anchor="w")
        controls = self.tk.Frame(header, bg=self.BG)
        controls.pack(side="right")
        self._button(
            controls,
            "↻",
            self.refresh_all,
            compact=True,
            icon=True,
        ).pack(side="right")
        self._button(
            controls,
            "⚙",
            self.open_settings,
            compact=True,
            icon=True,
        ).pack(side="right", padx=(0, 5))
        self.compact_button = self._button(
            controls,
            "−",
            self.toggle_compact_mode,
            compact=True,
            icon=True,
        )
        self.compact_button.pack(side="right", padx=(0, 5))
        self.updated_label = self.tk.Label(
            controls,
            textvariable=self.updated_text,
            bg=self.BG,
            fg=self.MUTED,
            font=self._font(7),
        )
        self.updated_label.pack(side="right", padx=(0, 9))

        self.tk.Frame(self.root, height=1, bg=self.CARD_BORDER).pack(fill="x")

        self.cards = self.tk.Frame(self.root, bg=self.BG)
        self.cards.pack(fill="x", padx=12, pady=(7, 0))

        footer = self.tk.Frame(self.root, bg=self.BG)
        footer.pack(fill="x", padx=16, pady=(6, 8))
        privacy_dot = self.tk.Canvas(
            footer,
            width=8,
            height=8,
            bg=self.BG,
            highlightthickness=0,
        )
        privacy_dot.pack(side="left", pady=2)
        privacy_dot.create_oval(1, 1, 7, 7, fill=self.GREEN, outline="")
        self.tk.Label(
            footer,
            text=(
                "Synthetic demo · no provider access"
                if self.demo_mode
                else "Local only · no telemetry"
            ),
            bg=self.BG,
            fg=self.FAINT,
            font=self._font(7),
        ).pack(side="left", padx=(5, 0))
        self.tk.Label(
            footer,
            textvariable=self.provider_count_text,
            bg=self.BG,
            fg=self.FAINT,
            font=self._font(7),
        ).pack(side="right")

    def _rounded_shape(
        self,
        canvas: Any,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        color: str,
        *,
        tag: str,
    ) -> None:
        radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
        canvas.create_rectangle(
            x1 + radius,
            y1,
            x2 - radius,
            y2,
            fill=color,
            outline="",
            tags=tag,
        )
        canvas.create_rectangle(
            x1,
            y1 + radius,
            x2,
            y2 - radius,
            fill=color,
            outline="",
            tags=tag,
        )
        for left, top in (
            (x1, y1),
            (x2 - 2 * radius, y1),
            (x1, y2 - 2 * radius),
            (x2 - 2 * radius, y2 - 2 * radius),
        ):
            canvas.create_oval(
                left,
                top,
                left + 2 * radius,
                top + 2 * radius,
                fill=color,
                outline="",
                tags=tag,
            )

    def _rounded_surface(
        self,
        canvas: Any,
        width: int,
        height: int,
        radius: int,
        fill: str,
        border: str,
        *,
        tag: str,
    ) -> None:
        canvas.delete(tag)
        self._rounded_shape(
            canvas, 0, 0, width, height, radius, border, tag=tag
        )
        self._rounded_shape(
            canvas,
            1,
            1,
            width - 1,
            height - 1,
            max(radius - 1, 1),
            fill,
            tag=tag,
        )
        canvas.tag_lower(tag)

    @staticmethod
    def _progress_fill_width(width: float, used_percent: float) -> float:
        safe_width = max(float(width), 0.0)
        safe_percent = min(max(float(used_percent), 0.0), 100.0)
        return safe_width * safe_percent / 100.0

    def _draw_progress_bar(
        self, canvas: Any, width: float, used_percent: float
    ) -> None:
        canvas.delete("progress")
        safe_width = max(float(width), 1.0)
        canvas.create_rectangle(
            0,
            0,
            safe_width,
            4,
            fill=self.TRACK,
            outline="",
            tags="progress",
        )
        fill_width = self._progress_fill_width(safe_width, used_percent)
        color = self.AMBER if used_percent >= 90 else self.ACCENT
        if fill_width > 0:
            canvas.create_rectangle(
                0,
                0,
                fill_width,
                4,
                fill=color,
                outline="",
                tags="progress",
            )

    def _button(
        self,
        parent: Any,
        text: str,
        command: Any,
        *,
        accent: bool = False,
        compact: bool = False,
        icon: bool = False,
    ) -> Any:
        background = self.ACCENT if accent else self.SURFACE
        foreground = "#FFFFFF" if accent else self.TEXT
        height = 30 if compact else 32
        width = 30 if icon else max(58, len(text) * 7 + (16 if compact else 20))
        try:
            parent_background = parent.cget("bg")
        except self.tk.TclError:
            parent_background = self.BG
        button = self.tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=parent_background,
            cursor="hand2",
            highlightthickness=0,
            borderwidth=0,
            takefocus=1,
        )
        border = self.ACCENT if accent else self.CARD_BORDER
        self._rounded_surface(
            button, width, height, 6, background, border, tag="button-bg"
        )
        button.create_text(
            width / 2,
            height / 2,
            text=text,
            fill=foreground,
            font=self._font(12 if icon else 8 if compact else 9, "bold"),
            tags="button-text",
        )
        button.bind("<Button-1>", lambda _event: command())
        button.bind("<Return>", lambda _event: command())
        button.bind("<space>", lambda _event: command())
        hover = self.CARD_HOVER if not accent else "#6979EE"
        button.bind(
            "<Enter>",
            lambda _event: self._rounded_surface(
                button, width, height, 6, hover, border, tag="button-bg"
            ),
        )
        button.bind(
            "<Leave>",
            lambda _event: self._rounded_surface(
                button, width, height, 6, background, border, tag="button-bg"
            ),
        )
        return button

    def _render_cards(self) -> None:
        for child in self.cards.winfo_children():
            child.destroy()
        visible_providers = self._visible_provider_ids(
            self.settings.enabled_providers
        )
        count = len(visible_providers)
        self.provider_count_text.set(
            f"{count} provider" if count == 1 else f"{count} providers"
        )
        if not visible_providers:
            self._render_empty_state()
        for provider_id in visible_providers:
            display = self.displays[provider_id]
            if self.compact_mode:
                self._render_compact_card(display)
            else:
                self._render_card(display)
        self.root.after_idle(self._fit_window_to_content)

    @staticmethod
    def _visible_provider_ids(
        enabled_providers: Collection[str],
    ) -> tuple[str, ...]:
        return tuple(
            provider_id
            for provider_id in PROVIDER_ORDER
            if provider_id in enabled_providers
        )

    def _render_empty_state(self) -> None:
        empty = self.tk.Frame(
            self.cards,
            bg=self.CARD,
            highlightbackground=self.CARD_BORDER,
            highlightthickness=1,
        )
        empty.pack(fill="x", pady=2)
        self.tk.Label(
            empty,
            text="No providers selected",
            bg=self.CARD,
            fg=self.TEXT,
            font=self._font(10, "bold"),
        ).pack(anchor="w", padx=14, pady=(11, 2))
        self.tk.Label(
            empty,
            text="Open Settings to choose which providers appear here.",
            bg=self.CARD,
            fg=self.MUTED,
            font=self._font(8),
        ).pack(anchor="w", padx=14, pady=(0, 11))

    def _fit_window_to_content(self) -> None:
        if self.closed:
            return
        self.root.update_idletasks()
        requested = max(self.root.winfo_reqheight(), self.MIN_WINDOW_HEIGHT)
        available = max(
            self.MIN_WINDOW_HEIGHT,
            self.root.winfo_screenheight() - self.SCREEN_MARGIN,
        )
        if requested > available and not self.compact_mode:
            self.compact_mode = True
            self._apply_display_mode()
            self._render_cards()
            return
        height = min(requested, available)
        width = self._current_window_width()
        if (
            self.root.winfo_width() != width
            or self.root.winfo_height() != height
        ):
            self.root.geometry(f"{width}x{height}")

    def toggle_compact_mode(self) -> None:
        self.compact_mode = not self.compact_mode
        self._apply_display_mode()
        self._render_cards()

    def _apply_display_mode(self) -> None:
        self.compact_button.itemconfigure(
            "button-text", text="+" if self.compact_mode else "−"
        )
        width = self._current_window_width()
        available_height = max(
            self.MIN_WINDOW_HEIGHT,
            self.root.winfo_screenheight() - self.SCREEN_MARGIN,
        )
        self.root.minsize(width, self.MIN_WINDOW_HEIGHT)
        self.root.maxsize(width, available_height)
        if self.compact_mode:
            self.title_label.configure(
                text="AI Usage",
                font=self._font(12, "bold"),
            )
            self.updated_label.pack_forget()
        else:
            self.title_label.configure(
                text="AI Tools Usage Tracker",
                font=self._font(14, "bold"),
            )
            self.updated_label.pack(side="right", padx=(0, 9))

    def _request_render(self) -> None:
        if self.closed or self.render_job is not None:
            return
        self.render_job = self.root.after(120, self._flush_render)

    def _flush_render(self) -> None:
        self.render_job = None
        if not self.closed:
            self._render_cards()

    def _status_style(self, status: str) -> tuple[str, str]:
        if status == "available":
            return "#18352D", self.GREEN
        if status == "ready":
            return "#202936", self.MUTED
        if status == "error":
            return "#3B2328", self.RED
        if status == "no_data":
            return "#3A2D1B", self.AMBER
        return "#202936", self.MUTED

    def _status_badge(self, parent: Any, text: str, status: str) -> Any:
        background, foreground = self._status_style(status)
        width = max(38, int(len(text) * 5.6) + 16)
        height = 22
        badge = self.tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=self.CARD,
            highlightthickness=0,
            borderwidth=0,
        )
        self._rounded_surface(
            badge,
            width,
            height,
            5,
            background,
            foreground,
            tag="badge-bg",
        )
        badge.create_text(
            width / 2,
            height / 2,
            text=text,
            fill=foreground,
            font=self._font(7, "bold"),
        )
        return badge

    def _card_detail(self, display: ProviderDisplay) -> str:
        if display.status == "planned":
            return PROVIDER_DESCRIPTIONS[display.provider_id]
        if display.status == "ready":
            return PROVIDER_SUMMARIES[display.provider_id]
        if display.status == "loading":
            return display.detail or "Checking the latest usage…"
        if display.status == "error":
            return display.detail or (
                "Could not refresh. Your saved provider session was not changed."
            )
        if display.status == "no_data" and display.provider_id == "claude":
            if display.status_text == "Code setup required":
                return "Enable the Claude Code status-line capture to begin receiving usage."
            if display.status_text == "Claude Code only":
                return (
                    "Claude Desktop chats do not expose usage through this source. "
                    "Only Claude Code status data can be captured safely."
                )
            if display.status_text == "Existing hook":
                return "Claude already has a different status line, so it was left unchanged."
            return "No plan limits were supplied. On the free tier, send another Claude Code prompt for session context."
        if display.status == "no_data" and display.provider_id == "devin":
            return "Open Devin once to refresh its normalized local plan cache."
        if display.status == "no_data" and display.provider_id == "antigravity":
            return "Open Antigravity's usage/settings view to refresh its local cache."
        if display.status == "no_data" and display.provider_id == "github_copilot":
            return "Send a prompt in Copilot CLI, then refresh to read its local AI-credit total."
        if display.status == "no_data":
            return "The provider returned no supported usage measurements."
        return PROVIDER_SUMMARIES[display.provider_id]

    @staticmethod
    def _compact_summary(display: ProviderDisplay) -> str:
        if not display.windows:
            return display.status_text
        window = display.windows[0]
        if window.used_percent is not None:
            return f"{max(100 - window.used_percent, 0):.0f}% left"
        return window.amount_text

    def _render_compact_card(self, display: ProviderDisplay) -> None:
        card = self.tk.Frame(
            self.cards,
            bg=self.CARD,
            highlightbackground=self.CARD_BORDER,
            highlightthickness=1,
        )
        card.pack(fill="x", pady=2)
        dot = self.tk.Canvas(
            card,
            width=10,
            height=10,
            bg=self.CARD,
            highlightthickness=0,
            borderwidth=0,
        )
        dot.pack(side="left", padx=(10, 8), pady=10)
        dot.create_oval(
            1,
            1,
            9,
            9,
            fill=PROVIDER_COLORS[display.provider_id],
            outline="",
        )
        self.tk.Label(
            card,
            text=display.display_name,
            bg=self.CARD,
            fg=self.TEXT,
            font=self._font(9, "bold"),
        ).pack(side="left")
        self.tk.Label(
            card,
            text=self._compact_summary(display),
            bg=self.CARD,
            fg=self.GREEN if display.windows else self.MUTED,
            font=self._font(8, "bold"),
        ).pack(side="right", padx=10)

    def _render_card(self, display: ProviderDisplay) -> None:
        card = self.tk.Frame(
            self.cards,
            bg=self.CARD,
            highlightbackground=self.CARD_BORDER,
            highlightthickness=1,
        )
        card.pack(fill="x", pady=2)
        rail = self.tk.Canvas(
            card,
            width=4,
            height=1,
            bg=self.CARD,
            highlightthickness=0,
            borderwidth=0,
        )
        rail.pack(side="left", fill="y", padx=(2, 0), pady=2)

        def draw_rail(event: Any) -> None:
            rail.delete("provider-rail")
            if event.height >= 6:
                self._rounded_shape(
                    rail,
                    0,
                    0,
                    4,
                    event.height,
                    2,
                    PROVIDER_COLORS[display.provider_id],
                    tag="provider-rail",
                )

        rail.bind("<Configure>", draw_rail)
        content = self.tk.Frame(card, bg=self.CARD)
        content.pack(side="left", fill="x", expand=True, padx=10, pady=7)
        icon = self.tk.Canvas(
            content,
            width=27,
            height=27,
            bg=self.CARD,
            highlightthickness=0,
            borderwidth=0,
        )
        icon.pack(side="left", anchor="n", padx=(0, 9))
        provider_color = PROVIDER_COLORS[display.provider_id]
        icon.create_oval(1, 1, 26, 26, fill=self.SURFACE, outline=provider_color)
        icon.create_text(
            13.5,
            13.5,
            text=PROVIDER_MARKS[display.provider_id],
            fill=provider_color,
            font=self._font(7, "bold"),
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
            font=self._font(10, "bold"),
        ).pack(side="left")
        self._status_badge(heading, display.status_text, display.status).pack(
            side="right"
        )

        if not display.windows:
            detail_row = self.tk.Frame(body, bg=self.CARD)
            detail_row.pack(fill="x", pady=(3, 0))
            has_action = (
                display.status in {"ready", "error"}
                or (
                    display.status == "no_data"
                    and display.provider_id == "claude"
                    and display.status_text == "Code setup required"
                )
            )
            self.tk.Label(
                detail_row,
                text=self._card_detail(display),
                bg=self.CARD,
                fg=self.MUTED,
                font=self._font(8),
                justify="left",
                anchor="w",
                wraplength=285 if has_action else 375,
            ).pack(side="left", fill="x", expand=True)
            if display.status == "ready":
                self._button(
                    detail_row,
                    "Enable",
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
                    lambda provider_id=display.provider_id: self.retry_provider(
                        provider_id
                    ),
                    compact=True,
                ).pack(side="right", padx=(8, 0))
            elif (
                display.status == "no_data"
                and display.provider_id == "claude"
                and display.status_text == "Code setup required"
            ):
                self._button(
                    detail_row,
                    "Configure",
                    self.configure_claude,
                    accent=True,
                    compact=True,
                ).pack(side="right", padx=(8, 0))
            return

        reset_texts: list[str] = []
        for window in display.windows:
            if window.reset_text and window.reset_text not in reset_texts:
                reset_texts.append(window.reset_text)

        for window in display.windows:
            row = self.tk.Frame(body, bg=self.CARD)
            row.pack(fill="x", pady=(5, 0))
            row.columnconfigure(1, weight=1)
            self.tk.Label(
                row,
                text=window.label,
                bg=self.CARD,
                fg=self.MUTED,
                font=self._font(7),
                anchor="w",
                width=13,
            ).grid(row=0, column=0, sticky="w")
            if window.used_percent is not None:
                bar = self.tk.Canvas(
                    row,
                    height=4,
                    bg=self.CARD,
                    highlightthickness=0,
                    borderwidth=0,
                )
                bar.grid(row=0, column=1, sticky="ew", padx=(6, 10))
                bar.bind(
                    "<Configure>",
                    lambda event, canvas=bar, percent=window.used_percent: (
                        self._draw_progress_bar(canvas, event.width, percent)
                    ),
                )
            self.tk.Label(
                row,
                text=window.amount_text,
                bg=self.CARD,
                fg=self.TEXT,
                font=self._font(8, "bold"),
                anchor="e",
            ).grid(row=0, column=2, sticky="e")

        if reset_texts:
            self.tk.Label(
                body,
                text=" · ".join(reset_texts),
                bg=self.CARD,
                fg=self.FAINT,
                font=self._font(7),
            ).pack(anchor="e", pady=(4, 0))

    def refresh_all(self, *, force: bool = False) -> None:
        if self.closed:
            return
        now = time.monotonic()
        if (
            not force
            and self.last_refresh_started
            and now - self.last_refresh_started
            < self.MANUAL_REFRESH_COOLDOWN_SECONDS
        ):
            return
        enabled = self.settings.enabled_providers
        started = False
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
            started = True
            if not self.displays[provider_id].windows:
                self.displays[provider_id] = loading_display(provider_id)
            self._launch_provider_collection(provider_id)
        if started:
            self.last_refresh_started = now
            self.updated_text.set("Updating…")
        self._request_render()
        self._schedule_refresh()

    def retry_provider(self, provider_id: str) -> None:
        if (
            self.closed
            or provider_id not in SUPPORTED_PROVIDERS
            or provider_id not in self.settings.enabled_providers
        ):
            return
        if provider_id in self.in_progress:
            self.updated_text.set(f"{PROVIDER_NAMES[provider_id]} is refreshing…")
            return
        self.in_progress.add(provider_id)
        self.displays[provider_id] = loading_display(provider_id)
        self.updated_text.set(f"Retrying {PROVIDER_NAMES[provider_id]}…")
        self._render_cards()
        self.root.update_idletasks()
        self.root.after(
            self.RETRY_FEEDBACK_DELAY_MS,
            lambda: self._launch_provider_collection(provider_id),
        )

    def _launch_provider_collection(self, provider_id: str) -> None:
        if self.closed or provider_id not in self.settings.enabled_providers:
            self.in_progress.discard(provider_id)
            return
        thread = threading.Thread(
            target=self._collect_in_background,
            args=(provider_id,),
            daemon=True,
            name=f"usage-{provider_id}",
        )
        thread.start()

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
            self.updated_text.set(self._updated_time_text())
            self._request_render()
        self.root.after(120, self._poll_results)

    def _schedule_refresh(self) -> None:
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
        milliseconds = self.settings.refresh_minutes * 60 * 1000
        self.refresh_job = self.root.after(
            milliseconds, lambda: self.refresh_all(force=True)
        )

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
            self.refresh_all(force=True)

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
        except (ClaudeSetupError, OSError, ValueError):
            self.messagebox.showerror(
                "Claude setup not changed",
                "Claude Code settings could not be updated safely. "
                "No existing status line was changed.",
                parent=self.root,
            )
            return
        self.messagebox.showinfo(
            "Claude capture enabled",
            "Claude Code is configured. The card will update after Claude produces its "
            "next response with rate-limit data.",
            parent=self.root,
        )
        self.refresh_all(force=True)

    def open_settings(self) -> None:
        dialog = self.tk.Toplevel(self.root)
        dialog.title("AI Tools Usage Tracker Settings")
        dialog.configure(bg=self.BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("472x680")

        self.tk.Label(
            dialog,
            text="Manage providers",
            bg=self.BG,
            fg=self.TEXT,
            font=self._font(14, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 2))
        self.tk.Label(
            dialog,
            text="Each provider remains off until you explicitly enable it.",
            bg=self.BG,
            fg=self.MUTED,
            font=self._font(8),
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
                font=self._font(9, "bold"),
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
                font=self._font(7),
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
            self.refresh_all(force=True)

        save_button = self._button(actions, "Save & refresh", save, accent=True)
        save_button.pack(side="right", padx=(0, 8))

    def close(self) -> None:
        self.closed = True
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
        if self.render_job is not None:
            self.root.after_cancel(self.render_job)
        self.root.destroy()


def run_widget(
    data_dir: Path | None = None,
    *,
    smoke_test: bool = False,
    demo_mode: bool = False,
) -> None:
    tk, messagebox, ttk = load_tk_modules()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise WidgetRuntimeError("The desktop display is unavailable.") from exc
    settings_store = DemoSettingsStore() if demo_mode else WidgetSettingsStore(data_dir)
    collector = DemoProviderCollector() if demo_mode else ProviderCollector(data_dir)
    widget = UsageWidget(
        root,
        tk,
        ttk,
        messagebox,
        settings_store,
        collector,
        demo_mode=demo_mode,
    )
    if smoke_test:
        def finish_smoke_test() -> None:
            root.update_idletasks()
            normal_size = f"{root.winfo_width()}x{root.winfo_height()}"
            widget.toggle_compact_mode()
            root.update_idletasks()
            compact_size = f"{root.winfo_width()}x{root.winfo_height()}"
            root.destroy()
            if sys.stdout is not None:
                print(
                    f"widget-rendered normal={normal_size} compact={compact_size}"
                )

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
        "--demo",
        action="store_true",
        help=(
            "show synthetic in-memory usage without reading provider files, "
            "starting provider processes, accessing the network, or saving settings"
        ),
    )
    parser.add_argument(
        "--claude-capture",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.demo and args.claude_capture:
        parser.error("--demo cannot be combined with --claude-capture")
    if args.demo and args.data_dir:
        parser.error("--demo keeps all state in memory and does not use --data-dir")
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
            demo_mode=args.demo,
        )
    except WidgetRuntimeError as exc:
        print(f"AI Usage Tracker: {exc}")
        return 2
    return 0
