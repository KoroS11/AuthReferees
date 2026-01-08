from __future__ import annotations

import datetime as _dt
import os
import secrets
import textwrap
import time
import math
import asyncio
from dataclasses import dataclass
from typing import Optional

import psutil
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static, Tree

from referee.comparison import ComparisonRow, build_comparison_rows
from referee.models import (
    ApplicationType,
    AuthMethod,
    BackendArchitecture,
    ExpectedUsers,
    SecuritySensitivity,
    SocialLoginRequired,
    TeamExperience,
    UserContext,
)
from referee.scoring import ScoringResult, score_options
from referee.verdict import Verdict, build_verdict


@dataclass(frozen=True)
class Choice:
    label: str
    value: str


def _now_text() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _bar(value: float, width: int = 10) -> str:
    value = max(0.0, min(100.0, float(value)))
    filled = int(round((value / 100.0) * width))
    return "█" * filled + "░" * (width - filled)


def _bar_shimmer(value: float, width: int, phase: int) -> str:
    """Progress bar with a simple moving shimmer over the filled portion."""
    value = max(0.0, min(100.0, float(value)))
    fill = (value / 100.0) * width
    out: list[str] = []
    shimmer_pos = int(round((phase % max(1, width)) ))
    for i in range(width):
        if i + 1 <= int(fill):
            out.append("▓" if i == shimmer_pos else "█")
        elif i < fill:
            out.append("▒")
        else:
            out.append("░")
    return "".join(out)


def _sparkle(phase: int) -> str:
    return ["✦", "✧", "✦", " "][phase % 4]


def _ease_in_out(p: float) -> float:
    p = 0.0 if p < 0.0 else 1.0 if p > 1.0 else p
    return p * p * (3.0 - 2.0 * p)


def _flash_style(active: bool) -> str:
    return "black on #FFB000" if active else ""


def _session_id() -> str:
    return secrets.token_hex(3).upper()


def _method_short(method_value: str) -> str:
    return {
        "Session-based Authentication": "Sessions",
        "JWT-based Authentication": "JWT",
        "OAuth 2.0": "OAuth 2.0",
        "Firebase Authentication": "Firebase",
    }.get(method_value, method_value)


def _ellipsize(s: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    s = " ".join(s.split())
    if len(s) <= max_chars:
        return s
    if max_chars <= 1:
        return "…"
    return s[: max_chars - 1] + "…"


def _wrap_text_lines(text: str, max_width: int, indent: int = 0, first_line_prefix: str = "", continuation_prefix: str = "") -> list[str]:
    """
    Wrap text to multiple lines with proper indentation.
    
    Args:
        text: The text to wrap
        max_width: Maximum width including any prefixes/indentation
        indent: Number of spaces for continuation line indentation
        first_line_prefix: Prefix for first line (like "├─ ")
        continuation_prefix: Prefix for wrapped lines (like "│  ")
    
    Returns:
        List of lines with proper wrapping
    """
    if not text or max_width <= 0:
        return [""]
    
    # Normalize whitespace
    text = " ".join(text.split())
    
    # Calculate available width for first line and continuation lines
    first_width = max_width - len(first_line_prefix)
    cont_width = max_width - len(continuation_prefix) - indent
    
    if first_width <= 0:
        first_width = 10
    if cont_width <= 0:
        cont_width = 10
    
    lines: list[str] = []
    remaining = text
    is_first = True
    
    while remaining:
        width = first_width if is_first else cont_width
        
        if len(remaining) <= width:
            # Fits on this line
            if is_first:
                lines.append(first_line_prefix + remaining)
            else:
                lines.append(continuation_prefix + " " * indent + remaining)
            break
        
        # Find break point (prefer space, then hyphenate)
        break_pos = remaining.rfind(" ", 0, width)
        if break_pos <= 0:
            # No space found, break at width with hyphen
            break_pos = width - 1
            chunk = remaining[:break_pos] + "-"
            remaining = remaining[break_pos:]
        else:
            chunk = remaining[:break_pos]
            remaining = remaining[break_pos + 1:]  # Skip the space
        
        if is_first:
            lines.append(first_line_prefix + chunk)
        else:
            lines.append(continuation_prefix + " " * indent + chunk)
        
        is_first = False
    
    return lines if lines else [""]


@dataclass
class _TypeSession:
    key: str
    target: str
    selector: str | None
    style: str
    speed_s: float
    blink: bool
    i: int = 0
    done: bool = False
    next_at: float = 0.0


class _Typewriter:
    """Single typewriter controller for consistent Phase-2 typing UX."""

    def __init__(self, app: "AuthRefereeTextual") -> None:
        self.app = app
        self.sessions: dict[str, _TypeSession] = {}

    def start(
        self,
        key: str,
        text: str,
        *,
        selector: str | None,
        style: str = "dim",
        speed_s: float = 0.02,
        blink: bool = True,
    ) -> None:
        now = time.monotonic()
        text = text or ""
        prev = self.sessions.get(key)
        if prev is not None and prev.target == text and prev.selector == selector:
            return
        self.sessions[key] = _TypeSession(
            key=key,
            target=text,
            selector=selector,
            style=style,
            speed_s=max(0.005, float(speed_s)),
            blink=bool(blink),
            i=0,
            done=(len(text) == 0),
            next_at=now,
        )
        self._push(key)

    def is_done(self, key: str) -> bool:
        s = self.sessions.get(key)
        return True if s is None else bool(s.done)

    def get_display(self, key: str) -> str:
        s = self.sessions.get(key)
        if s is None:
            return ""
        shown = s.target
        if not s.done:
            shown = s.target[: s.i]
            return shown + "▌"
        if s.blink:
            phase = int(getattr(self.app, "_pulse_phase", 0) or 0)
            return shown + ("▌" if phase % 2 == 0 else " ")
        return shown

    def tick(self) -> None:
        now = time.monotonic()
        any_dirty = False
        for key, s in list(self.sessions.items()):
            if s.done:
                # Still refresh for blink if bound to a widget.
                if s.selector:
                    any_dirty = True
                continue
            if now < s.next_at:
                continue
            s.i += 1
            if s.i >= len(s.target):
                s.i = len(s.target)
                s.done = True
            s.next_at = now + s.speed_s
            any_dirty = True
            self._push(key)

        if any_dirty:
            # Ensure blink updates are visible.
            for key in list(self.sessions.keys()):
                self._push(key)

    def _push(self, key: str) -> None:
        s = self.sessions.get(key)
        if s is None or not s.selector:
            return
        try:
            w = self.app.query_one(s.selector)
        except Exception:
            return
        # Widget may be Label or Static; both accept rich Text.
        try:
            w.update(Text(self.get_display(key), style=s.style))
        except Exception:
            pass


class TopBar(Static):
    """Header bar: ASCII logo + session/timestamp + system stats."""

    session: reactive[str] = reactive(_session_id())
    now: reactive[str] = reactive(_now_text())
    cpu: reactive[float] = reactive(0.0)
    mem: reactive[float] = reactive(0.0)
    net_kbps: reactive[float] = reactive(0.0)

    _last_net_bytes: int | None = None
    _pulse: reactive[int] = reactive(0)

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        self.now = _now_text()
        self._pulse = (self._pulse + 1) % 4
        # psutil.cpu_percent() needs one call to prime; it is ok to call continuously.
        self.cpu = psutil.cpu_percent(interval=None)
        self.mem = psutil.virtual_memory().percent
        try:
            counters = psutil.net_io_counters()
            total = int(counters.bytes_sent + counters.bytes_recv)
            if self._last_net_bytes is None:
                self._last_net_bytes = total
                self.net_kbps = 0.0
            else:
                delta = max(0, total - self._last_net_bytes)
                self._last_net_bytes = total
                # tick is ~1s
                self.net_kbps = delta / 1024.0
        except Exception:
            self.net_kbps = 0.0
        self.refresh()

    def _align3(self, left: Text, mid: Text, right: Text, width: int) -> Text:
        out = Text()
        out.append(left)
        out.append("  ")

        left_len = len(out.plain)
        right_len = len(right.plain)
        mid_len = len(mid.plain)

        mid_start = max(left_len, (width - right_len - mid_len) // 2)
        if mid_start > left_len:
            out.append(" " * (mid_start - left_len))
        out.append(mid)

        cur = len(out.plain)
        pad = max(1, width - right_len - cur)
        out.append(" " * pad)
        out.append(right)
        return out

    def render(self) -> Text:
        width = self.size.width or 80

        # Compact cyber logo (2-line) + pulsing clock + mini bars.
        logo1 = Text("█▀█ ▄▀█ █░█ ▀█▀", style="bold #00D9FF")
        logo2 = Text("█▄█ █▀█ █▄█ ░█░  AUTHREFEREE", style="bold #00D9FF")

        sparkle = _sparkle(self._pulse)
        sess = Text(f"┏ {self.session} ┓", style="bold")

        # Pulse the time separator a bit.
        now = _dt.datetime.now()
        sep = ":" if self._pulse % 2 == 0 else "·"
        time_str = now.strftime(f"%Y-%m-%d %H{sep}%M{sep}%S")
        mid2 = Text(f"{sparkle} {time_str}", style="dim")

        def metric(name: str, pct: float) -> Text:
            color = "#00FF41" if pct < 50 else "#FFB000" if pct < 80 else "#FF1F7E"
            t = Text(f"{name} {pct:>3.0f}% {_bar(pct, 8)}", style=f"bold {color}")
            return t

        cpu = metric("CPU", self.cpu)
        mem = metric("MEM", self.mem)
        net = Text(f"NET {self.net_kbps:>4.0f}KB/s", style="bold #A0A0A0")
        stats = Text.assemble(cpu, Text("  "), mem, Text("  "), net)

        live_mode = bool(getattr(self.app, "live_mode", False))
        live_countdown = int(getattr(self.app, "live_countdown", 0) or 0)
        live_pulse = int(getattr(self.app, "_live_pulse", 0) or 0)
        mode_text = (
            Text(f"{'●' if live_pulse % 2 == 0 else '◌'} LIVE [⟳ {live_countdown}s]", style="bold #00FF41")
            if live_mode
            else Text("Status: IDLE", style="dim")
        )

        line1 = self._align3(logo1, sess, stats, width)
        line2 = self._align3(logo2, mid2, mode_text, width)
        line1.append("\n")
        line1.append(line2)
        return line1


class HelpScreen(ModalScreen[None]):
    """Help overlay with keyboard map."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(self._help_text(), id="help")

    def _help_text(self) -> Text:
        t = Text()
        t.append("OAUTH AUTHREFEREE — HELP\n", style="bold #00D9FF")
        t.append("\nNAVIGATION\n", style="bold")
        t.append("  Tab / Shift+Tab  Focus next/prev panel\n", style="dim")
        t.append("  Arrow keys        Navigate & scroll\n", style="dim")
        t.append("  PgUp/PgDn          Page scroll\n", style="dim")
        t.append("  1/2/3/0            Compact/Detailed/Focus/Default view\n", style="dim")
        t.append("\nACTIONS\n", style="bold")
        t.append("  R   Reset\n", style="dim")
        t.append("  L   Toggle live mode (5s refresh)\n", style="dim")
        t.append("  T   Cycle theme\n", style="dim")
        t.append("  /   Search\n", style="dim")
        t.append("  E   Export\n", style="dim")
        t.append("  Q   Quit\n", style="dim")
        t.append("\nMISC\n", style="bold")
        t.append("  ?   Show help\n", style="dim")
        t.append("  Esc Close overlay\n", style="dim")
        return t

    def action_close(self) -> None:
        self.app.pop_screen()


class SearchScreen(ModalScreen[None]):
    """Global search overlay (simple substring match across sections)."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("SEARCH", classes="section")
        yield Input(placeholder="type to search (e.g. jwt, session, owasp)", id="search_q")
        yield Static("", id="search_results")

    def on_mount(self) -> None:
        self.query_one("#search_q", Input).focus()
        self._update_results("")

    @on(Input.Changed, "#search_q")
    def _on_change(self, event: Input.Changed) -> None:
        self._update_results(event.value or "")

    def _update_results(self, q: str) -> None:
        qn = (q or "").strip().lower()
        sections = getattr(self.app, "build_search_sections", lambda: {})()
        t = Text()
        if not qn:
            t.append("Type to search across TRACE / COSTS / BREAKS / REFERENCES / COMPARISON.", style="dim")
            self.query_one("#search_results", Static).update(t)
            return

        total = 0
        for name, content in sections.items():
            hay = content.lower()
            count = hay.count(qn)
            total += count
            color = "#00FF41" if count else "#A0A0A0"
            t.append(f"{name}: {count} match(es)\n", style=f"bold {color}" if count else "dim")
        t.append(f"\nTOTAL: {total}\n", style="bold")
        self.query_one("#search_results", Static).update(t)

    def action_close(self) -> None:
        self.app.pop_screen()


class ExportScreen(ModalScreen[None]):
    """Export current decision to a file."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("EXPORT", classes="section")
        yield Label("Format", classes="k")
        yield Select(
            options=[
                ("Plain Text (.txt)", "txt"),
                ("Markdown (.md)", "md"),
                ("JSON (.json)", "json"),
            ],
            value="md",
            id="export_fmt",
        )
        yield Label("Include", classes="k")
        yield Checkbox("Context", True, id="inc_ctx")
        yield Checkbox("Verdict", True, id="inc_verdict")
        yield Checkbox("Trace", True, id="inc_trace")
        yield Checkbox("Costs/Breaks/Losers", True, id="inc_risk")
        yield Checkbox("Comparison", True, id="inc_compare")
        yield Checkbox("System metadata (session/time)", False, id="inc_meta")
        yield Label("Filename", classes="k")
        yield Input(placeholder="session_export", id="export_name")
        with Horizontal():
            yield Button("Export", id="do_export", variant="primary")
            yield Button("Cancel", id="cancel_export")

    def on_mount(self) -> None:
        # Auto-generate filename with session ID and timestamp
        try:
            session = str(getattr(self.app.query_one("#top"), "session", "export") or "export")
        except Exception:
            session = "export"
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        auto_name = f"authreferee_{session}_{ts}"
        self.query_one("#export_name", Input).value = auto_name
        self.query_one("#export_name", Input).focus()

    @on(Button.Pressed, "#cancel_export")
    def _cancel(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#do_export")
    def _export(self) -> None:
        fmt = self.query_one("#export_fmt", Select).value or "md"
        name = (self.query_one("#export_name", Input).value or "session_export").strip() or "session_export"

        include = {
            "ctx": self.query_one("#inc_ctx", Checkbox).value,
            "verdict": self.query_one("#inc_verdict", Checkbox).value,
            "trace": self.query_one("#inc_trace", Checkbox).value,
            "risk": self.query_one("#inc_risk", Checkbox).value,
            "compare": self.query_one("#inc_compare", Checkbox).value,
            "meta": self.query_one("#inc_meta", Checkbox).value,
        }

        ok = getattr(self.app, "export_current", lambda *_args, **_kw: False)(fmt, name, include)
        self.app.pop_screen()
        bar = self.app.query_one("#bar", CommandBar)
        bar.set_status("exported" if ok else "export failed")


class Scanline(Static):
    """A thin animated separator line."""

    phase: reactive[int] = reactive(0)

    def on_mount(self) -> None:
        self.set_interval(0.15, self._tick)

    def _tick(self) -> None:
        self.phase = (self.phase + 1) % 8
        self.refresh()

    def render(self) -> Text:
        width = self.size.width or 80
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧"
        ch = spinner[self.phase]
        bar = ("═" * max(0, width - 6))
        t = Text(f"{ch} {bar} {ch}")
        t.stylize("cyan")
        return t


class ContextPanel(Static):
    """Left panel: context & configuration inputs with scrollbar."""

    def compose(self) -> ComposeResult:
        # Scroll indicator at top
        yield Static("", id="left_scroll_up")
        with VerticalScroll(id="left_scroll"):
            yield Label("CONTEXT", classes="section")

            yield Label("⚡ APP", classes="k")
            yield Select(
                options=[
                    ("Web", ApplicationType.WEB.value),
                    ("Mobile", ApplicationType.MOBILE.value),
                    ("API", ApplicationType.API.value),
                ],
                id="app",
                value=ApplicationType.WEB.value,
            )

            yield Label("◉ USERS", classes="k")
            yield Select(
                options=[
                    ("< 1,000", ExpectedUsers.LT_1K.value),
                    ("1,000–50,000", ExpectedUsers.BTW_1K_50K.value),
                    ("50,000+", ExpectedUsers.GTE_50K.value),
                ],
                id="users",
                value=ExpectedUsers.BTW_1K_50K.value,
            )
            yield Static("", id="users_gauge")

            yield Label("⚿ SEC", classes="k")
            yield Select(
                options=[
                    ("Low", SecuritySensitivity.LOW.value),
                    ("Medium", SecuritySensitivity.MEDIUM.value),
                    ("High", SecuritySensitivity.HIGH.value),
                ],
                id="sec",
                value=SecuritySensitivity.MEDIUM.value,
            )

            yield Label("⚙ BACKEND", classes="k")
            yield Select(
                options=[
                    ("Stateful", BackendArchitecture.STATEFUL.value),
                    ("Stateless", BackendArchitecture.STATELESS.value),
                ],
                id="backend",
                value=BackendArchitecture.STATELESS.value,
            )

            yield Label("◆ TEAM", classes="k")
            yield Select(
                options=[
                    ("Beginner", TeamExperience.BEGINNER.value),
                    ("Intermediate", TeamExperience.INTERMEDIATE.value),
                    ("Advanced", TeamExperience.ADVANCED.value),
                ],
                id="team",
                value=TeamExperience.INTERMEDIATE.value,
            )

            yield Label("◇ SOCIAL", classes="k")
            yield Select(
                options=[
                    ("Yes", SocialLoginRequired.YES.value),
                    ("No", SocialLoginRequired.NO.value),
                ],
                id="social",
                value=SocialLoginRequired.NO.value,
            )
        # Scroll indicator at bottom
        yield Static("", id="left_scroll_down")


class VerdictPanel(Static):
    """Center top: winner + confidence + fit lines."""

    def update_from(self, ctx: UserContext, *, verdict: Verdict) -> None:
        # Skeleton while busy overlay is visible (only after 200ms threshold).
        if bool(getattr(self.app, "_busy_visible", False)):
            self._render_skeleton(verdict)
            return

        win = verdict.recommended.value
        win_short = _method_short(win)
        # Animated confidence (app may provide an interpolated value).
        conf_display = float(
            getattr(self.app, "_anim_current_conf", verdict.confidence_percent) or verdict.confidence_percent
        )
        conf = int(round(conf_display))

        flash_keys = set(getattr(self.app, "_active_flash_keys", lambda: set())())
        flash_winner = "winner" in flash_keys
        flash_conf = "conf" in flash_keys

        band = "#00FF41" if conf >= 80 else "#FFB000" if conf >= 60 else "#FF1F7E"
        level = "HIGH" if conf >= 80 else "MED" if conf >= 60 else "LOW"

        phase = int(getattr(self.app, "_pulse_phase", 0) or 0)
        meter = _bar_shimmer(conf_display, 18, phase)
        sparkle = _sparkle(conf // 10)

        # Dramatic banner (compact to fit).
        w = max(28, self.size.width or 28)
        inner = max(10, w - 2)
        top = "╔" + ("═" * inner) + "╗"
        bot = "╚" + ("═" * inner) + "╝"

        t = Text()
        t.append(top + "\n", style=f"bold {band}")
        line1 = f"{sparkle}  WINNER: {win_short}"
        t.append(
            "║" + line1.ljust(inner)[:inner] + "║\n",
            style=(f"bold {band} " + _flash_style(flash_winner)).strip(),
        )
        line2 = f"CONF {conf:>3d}% {meter} [{level}]"
        t.append(
            "║" + line2.ljust(inner)[:inner] + "║\n",
            style=(f"bold {band} " + _flash_style(flash_conf)).strip(),
        )
        if verdict.tie_break_applied and verdict.tie_break_summary:
            tb = ("TIE " + verdict.tie_break_summary)
            t.append("║" + tb.ljust(inner)[:inner] + "║\n", style="dim")
        t.append(bot + "\n", style=f"bold {band}")

        for i, line in enumerate(verdict.why_fits[:2], start=1):
            t.append(f"FIT{i} ", style="bold dim")
            t.append(line)
            t.append("\n")

        self.update(t)

    def _render_skeleton(self, verdict: Verdict) -> None:
        phase = int(getattr(self.app, "_pulse_phase", 0) or 0)
        # Keep geometry identical to the real banner to avoid layout shift.
        w = max(28, self.size.width or 28)
        inner = max(10, w - 2)
        top = "╔" + ("═" * inner) + "╗"
        bot = "╚" + ("═" * inner) + "╝"

        t = Text()
        t.append(top + "\n", style="dim #00D9FF")
        line1 = "✧  WINNER: " + "—" * min(10, max(4, inner - 12))
        t.append("║" + line1.ljust(inner)[:inner] + "║\n", style="dim")
        meter = _bar_shimmer(70.0, 18, phase)
        line2 = f"CONF  --% {meter} [---]"
        t.append("║" + line2.ljust(inner)[:inner] + "║\n", style="dim")
        if verdict.tie_break_applied and verdict.tie_break_summary:
            tb = "TIE " + "—" * min(18, max(6, inner - 4))
            t.append("║" + tb.ljust(inner)[:inner] + "║\n", style="dim")
        t.append(bot + "\n", style="dim #00D9FF")

        for i in range(1, 3):
            t.append(f"FIT{i} ", style="bold dim")
            t.append(_ellipsize(" ".join(["—" * 6] * 6), max(10, (self.size.width or 40) - 6)), style="dim")
            t.append("\n")

        self.update(t)


class TraceTree(Tree[None]):
    """Center bottom: expandable decision trace (Tree) with compact summaries."""

    def __init__(self, label: str = "TRACE", **kwargs) -> None:
        super().__init__(label, **kwargs)

    BINDINGS = [
        Binding("space", "toggle_node", "Toggle"),
        Binding("enter", "toggle_node", "Toggle"),
        Binding("a", "toggle_expand_all", "Expand/Collapse all"),
    ]

    def on_mount(self) -> None:
        self.show_root = False
        self.guide_depth = 4

    def _get_primary_reason(self, scoring: ScoringResult, method) -> str:
        """Get the most significant reason for a method's score."""
        reasons = scoring.reasons.get(method, [])
        # Find the strongest delta (highest absolute value)
        best_delta = 0.0
        best_reason = "Base score"
        for line in reasons:
            if line.startswith("Base score") or line.startswith("Capped at"):
                continue
            if ":" in line:
                try:
                    delta_str = line.split(":", 1)[0].strip()
                    delta = float(delta_str)
                    if abs(delta) > abs(best_delta):
                        best_delta = delta
                        best_reason = line.split(":", 1)[1].strip()
                except (ValueError, IndexError):
                    pass
        return best_reason

    def _get_enable_hint(self, method, ctx: UserContext) -> str:
        """Get a hint for what would need to change to enable this method."""
        method_short = _method_short(method.value)
        
        if method == AuthMethod.SESSIONS:
            if ctx.backend_architecture.value == "Stateless":
                return "Change backend to Stateful"
            if ctx.application_type.value in ("API", "Mobile"):
                return "Better suited for Web apps"
            return "Already optimal for your config"
        
        if method == AuthMethod.JWT:
            if ctx.backend_architecture.value == "Stateful":
                return "Change backend to Stateless"
            if ctx.social_login_required == SocialLoginRequired.YES:
                return "Doesn't handle social login directly"
            return "Already optimal for your config"
        
        if method == AuthMethod.OAUTH2:
            if ctx.social_login_required == SocialLoginRequired.NO:
                return "Enable Social Login requirement"
            return "Already optimal for your config"
        
        if method == AuthMethod.FIREBASE:
            if ctx.application_type.value != "Mobile":
                return "Best suited for Mobile apps"
            if ctx.team_experience.value == "Advanced":
                return "May prefer more control than Firebase offers"
            return "Already optimal for your config"
        
        return "Review scoring factors"

    def update_from(self, ctx: UserContext, *, scoring: ScoringResult, verdict: Verdict) -> None:
        if bool(getattr(self.app, "_busy_visible", False)):
            self._render_skeleton()
            return

        score_overrides: dict[str, float] = dict(getattr(self.app, "_anim_current_scores", {}) or {})
        flash_keys = set(getattr(self.app, "_active_flash_keys", lambda: set())())
        open_set: set[str] = getattr(self.app, "_trace_open", set()) or set()

        view_mode = str(getattr(self.app, "view_mode", "default") or "default")
        compact_mode = view_mode == "compact"

        root = self.root
        root.remove_children()

        ranked = verdict.ranked
        if compact_mode:
            ranked = [(verdict.recommended, scoring.scores[verdict.recommended])]

        win_score = float(score_overrides.get(verdict.recommended.value, scoring.scores[verdict.recommended]))
        
        # Calculate available width for text wrapping
        # Tree widget has some built-in indentation, so we calculate usable width
        tree_width = max(30, (self.size.width or 50) - 8)  # Account for tree indent and padding

        for method, score in ranked:
            method_value = method.value
            method_short = _method_short(method_value)
            shown = float(score_overrides.get(method_value, score))
            is_win = method == verdict.recommended
            is_expanded = (method_value in open_set) if open_set else is_win

            key = f"score:{method_value}"
            flash = key in flash_keys

            # Get the primary reason for collapsed one-line summary
            primary_reason = self._get_primary_reason(scoring, method)
            primary_reason_short = _ellipsize(primary_reason, min(35, tree_width - 25))

            # Status icon and colors
            status_icon = "✓" if is_win else "✗"
            status_text = "SELECTED" if is_win else "REJECTED"
            title_style = "bold #00FF41" if is_win else "bold #FF1F7E"
            score_style = "bold #00FF41" if is_win else "bold #A0A0A0"
            if flash:
                score_style = (score_style + " " + _flash_style(True)).strip()

            # Build compact header with one-line summary
            # Format: ▼/▶ ✓/✗ Name  Score (STATUS) - Reason
            header = Text()
            expand_icon = "▼" if is_expanded else "▶"
            header.append(f"{expand_icon} ", style="bold #00D9FF")
            header.append(f"{status_icon} ", style=title_style)
            header.append(f"{method_short:<10}", style=title_style)
            header.append(f"{shown:>4.1f}", style=score_style)
            header.append(f" ({status_text})", style=title_style)
            
            # Add delta from winner for non-winners
            if not is_win:
                delta = shown - win_score
                delta_style = "#FFB000" if delta == 0 else "#FF1F7E"
                header.append(f" {delta:+.1f}", style=f"bold {delta_style}")
            
            # Add one-line reason summary (visible when collapsed)
            if not is_expanded:
                header.append(" — ", style="dim")
                header.append(primary_reason_short, style="dim")

            node = root.add(header, data=method_value, expand=is_expanded)

            # Detailed breakdown (only visible when expanded)
            lines = scoring.reasons.get(method, [])
            base = "5.0"
            deltas: list[tuple[str, str]] = []
            cap_line: str | None = None
            
            for line in lines:
                if line.startswith("Base score"):
                    base = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("Capped at"):
                    cap_line = line
                    continue
                if ":" in line:
                    delta_part, reason_part = line.split(":", 1)
                    deltas.append((delta_part.strip(), reason_part.strip()))
                else:
                    deltas.append(("", line))

            # ── BASE ──
            base_row = Text()
            base_row.append("  ├─ ", style="dim #00D9FF")
            base_row.append("BASE ", style="bold #00D9FF")
            base_row.append(base, style="bold #FFFFFF")
            node.add_leaf(base_row)

            # ── DELTAS with text wrapping ──
            # Calculate usable width for reason text (after tree symbols and delta value)
            reason_width = max(20, tree_width - 15)  # 15 chars for "  ├─ ▲ +2.00  "
            
            for i, (delta_str, reason) in enumerate(deltas):
                is_last_delta = (i == len(deltas) - 1) and cap_line is None
                prefix = "  └─ " if is_last_delta else "  ├─ "
                cont_prefix = "  │     " if not is_last_delta else "        "

                if not delta_str:
                    # No delta value, just reason text
                    wrapped = _wrap_text_lines(reason, reason_width + 8, indent=0, first_line_prefix="", continuation_prefix="")
                    for j, chunk in enumerate(wrapped):
                        row = Text()
                        if j == 0:
                            row.append(prefix, style="dim")
                        else:
                            row.append(cont_prefix, style="dim")
                        row.append(chunk, style="")
                        node.add_leaf(row)
                    continue

                is_positive = delta_str.startswith("+")
                is_negative = delta_str.startswith("-")
                delta_color = "#00FF41" if is_positive else "#FF1F7E" if is_negative else "#FFB000"
                arrow = "▲" if is_positive else "▼" if is_negative else "●"

                # Wrap the reason text
                wrapped = _wrap_text_lines(reason, reason_width, indent=0, first_line_prefix="", continuation_prefix="")
                
                for j, chunk in enumerate(wrapped):
                    row = Text()
                    if j == 0:
                        # First line with delta value
                        row.append(prefix, style="dim")
                        row.append(f"{arrow} ", style=f"bold {delta_color}")
                        row.append(f"{delta_str:>5}", style=f"bold {delta_color}")
                        row.append("  ", style="dim")
                        row.append(chunk, style="")
                    else:
                        # Continuation line - indent to align with text start
                        row.append(cont_prefix, style="dim")
                        row.append("       ", style="")  # Align with text after delta
                        row.append(chunk, style="")
                    node.add_leaf(row)

            # ── FINAL ──
            has_more_after_final = cap_line is not None or not is_win
            final_prefix = "  ├─ " if has_more_after_final else "  └─ "
            final_row = Text()
            final_row.append(final_prefix, style="dim")
            final_style = "#00FF41" if is_win else "#FFFFFF"
            if flash:
                final_style = "#FFB000"
            final_row.append("══ FINAL ", style="bold")
            final_row.append(f"{shown:.1f}", style=f"bold {final_style}")
            final_row.append(" ══", style="bold")
            node.add_leaf(final_row)

            # ── CAP NOTE with wrapping ──
            if cap_line:
                has_hint = not is_win
                cap_prefix = "  ├─ " if has_hint else "  └─ "
                cap_cont = "  │     " if has_hint else "        "
                
                wrapped = _wrap_text_lines(cap_line, reason_width + 5, indent=0, first_line_prefix="", continuation_prefix="")
                for j, chunk in enumerate(wrapped):
                    cap_row = Text()
                    if j == 0:
                        cap_row.append(cap_prefix, style="dim")
                        cap_row.append("⚠ ", style="bold #FFB000")
                        cap_row.append(chunk, style="dim #FFB000")
                    else:
                        cap_row.append(cap_cont, style="dim")
                        cap_row.append("  ", style="")  # Align with text after icon
                        cap_row.append(chunk, style="dim #FFB000")
                    node.add_leaf(cap_row)

            # ── ENABLE HINT for rejected methods with wrapping ──
            if not is_win:
                hint = self._get_enable_hint(method, ctx)
                hint_text = f"To enable: {hint}"
                wrapped = _wrap_text_lines(hint_text, reason_width + 5, indent=0, first_line_prefix="", continuation_prefix="")
                
                for j, chunk in enumerate(wrapped):
                    hint_row = Text()
                    if j == 0:
                        hint_row.append("  └─ ", style="dim")
                        hint_row.append("💡 ", style="bold #BD00FF")
                        hint_row.append(chunk, style="#BD00FF")
                    else:
                        hint_row.append("        ", style="dim")
                        hint_row.append("  ", style="")  # Align with text
                        hint_row.append(chunk, style="#BD00FF")
                    node.add_leaf(hint_row)

        if not ranked:
            root.add_leaf(Text("(no trace)", style="dim"))

    def _render_skeleton(self) -> None:
        phase = int(getattr(self.app, "_pulse_phase", 0) or 0)
        root = self.root
        root.remove_children()

        for i, pct in enumerate((78.0, 66.0, 72.0, 58.0), start=1):
            hdr = Text(f"  METHOD {i}", style="dim")
            node = root.add(hdr, expand=(i == 1))
            # Skeleton matches the detailed format
            bar = _bar_shimmer(pct, 16, phase)
            base_row = Text()
            base_row.append("├─ ", style="dim #00D9FF")
            base_row.append("BASE ", style="dim #00D9FF")
            base_row.append("5.0", style="dim")
            node.add_leaf(base_row)

            delta_row = Text()
            delta_row.append("├─ ", style="dim")
            delta_row.append("▲ ", style="dim #00FF41")
            delta_row.append(f"+-.--  loading {bar}", style="dim")
            node.add_leaf(delta_row)

            final_row = Text()
            final_row.append("└─ ", style="dim")
            final_row.append("═══ FINAL ", style="dim")
            final_row.append("--.--", style="dim")
            final_row.append(" ═══", style="dim")
            node.add_leaf(final_row)

    @on(Tree.NodeExpanded)
    def _on_node_expanded(self, event: Tree.NodeExpanded) -> None:
        data = getattr(event.node, "data", None)
        if isinstance(data, str) and data:
            self.app._trace_open.add(data)

    @on(Tree.NodeCollapsed)
    def _on_node_collapsed(self, event: Tree.NodeCollapsed) -> None:
        data = getattr(event.node, "data", None)
        if isinstance(data, str) and data:
            self.app._trace_open.discard(data)

    def action_toggle_expand_all(self) -> None:
        """Toggle expand/collapse all method nodes."""
        open_set = self.app._trace_open
        # If any are open, collapse all; otherwise expand all
        all_keys: set[str] = set()
        for node in self.root.children:
            data = getattr(node, "data", None)
            if isinstance(data, str) and data:
                all_keys.add(data)

        if open_set & all_keys:  # Some are open -> collapse all
            for node in self.root.children:
                try:
                    node.collapse()
                except Exception:
                    pass
            self.app._trace_open = set()
        else:  # None open -> expand all
            for node in self.root.children:
                try:
                    node.expand()
                except Exception:
                    pass
                data = getattr(node, "data", None)
                if isinstance(data, str) and data:
                    open_set.add(data)
            self.app._trace_open = open_set


class TraceViewport(Widget):
    """TRACE renderer with strict wrapping + vertical-only scrolling.

    This avoids Tree label truncation and allows drawing a manual scrollbar
    inside the TRACE area.
    """

    can_focus = True

    BINDINGS = [
        Binding("up", "scroll_up", "Up"),
        Binding("down", "scroll_down", "Down"),
        Binding("pageup", "page_up", "PgUp"),
        Binding("pagedown", "page_down", "PgDn"),
        Binding("home", "scroll_top", "Top"),
        Binding("end", "scroll_bottom", "Bottom"),
        Binding("a", "toggle_expand_all", "Expand/Collapse all"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._lines: list[tuple[str, str]] = []
        self._scroll_y: int = 0
        self._line_method_headers: dict[int, str] = {}

    @property
    def scroll_y(self) -> int:
        return int(self._scroll_y)

    @property
    def max_scroll_y(self) -> int:
        h = int(self.size.height or 0)
        total = len(self._lines)
        return max(0, total - max(1, h))

    def _clamp_scroll(self) -> None:
        self._scroll_y = max(0, min(self._scroll_y, self.max_scroll_y))

    def action_scroll_up(self) -> None:
        self._scroll_y -= 1
        self._clamp_scroll()
        self.refresh()

    def action_scroll_down(self) -> None:
        self._scroll_y += 1
        self._clamp_scroll()
        self.refresh()

    def action_page_up(self) -> None:
        self._scroll_y -= max(1, int(self.size.height or 1) - 1)
        self._clamp_scroll()
        self.refresh()

    def action_page_down(self) -> None:
        self._scroll_y += max(1, int(self.size.height or 1) - 1)
        self._clamp_scroll()
        self.refresh()

    def action_scroll_top(self) -> None:
        self._scroll_y = 0
        self.refresh()

    def action_scroll_bottom(self) -> None:
        self._scroll_y = self.max_scroll_y
        self.refresh()

    def action_toggle_expand_all(self) -> None:
        open_set: set[str] = set(getattr(self.app, "_trace_open", set()) or set())
        all_keys: set[str] = set()
        for _method_value, _style in self._method_keys_in_last_render():
            all_keys.add(_method_value)

        if open_set & all_keys:
            self.app._trace_open = set()
        else:
            self.app._trace_open = set(all_keys)
        self.refresh()

    def _method_keys_in_last_render(self) -> list[tuple[str, str]]:
        # We don't need styles here; keep a stable ordered list of method keys.
        ordered: list[tuple[str, str]] = []
        seen: set[str] = set()
        for _, method_value in sorted((k, v) for (k, v) in self._line_method_headers.items()):
            if method_value not in seen:
                seen.add(method_value)
                ordered.append((method_value, ""))
        return ordered

    def _get_primary_reason(self, scoring: ScoringResult, method) -> str:
        reasons = scoring.reasons.get(method, [])
        best_delta = 0.0
        best_reason = "Base score"
        for line in reasons:
            if line.startswith("Base score") or line.startswith("Capped at"):
                continue
            if ":" in line:
                try:
                    delta_str = line.split(":", 1)[0].strip()
                    delta = float(delta_str)
                    if abs(delta) > abs(best_delta):
                        best_delta = delta
                        best_reason = line.split(":", 1)[1].strip()
                except (ValueError, IndexError):
                    pass
        return best_reason

    def _get_enable_hint(self, method, ctx: UserContext) -> str:
        if method == AuthMethod.SESSIONS:
            if ctx.backend_architecture.value == "Stateless":
                return "Change backend to Stateful"
            if ctx.application_type.value in ("API", "Mobile"):
                return "Better suited for Web apps"
            return "Already optimal for your config"

        if method == AuthMethod.JWT:
            if ctx.backend_architecture.value == "Stateful":
                return "Change backend to Stateless"
            if ctx.social_login_required == SocialLoginRequired.YES:
                return "Doesn't handle social login directly"
            return "Already optimal for your config"

        if method == AuthMethod.OAUTH2:
            if ctx.social_login_required == SocialLoginRequired.NO:
                return "Enable Social Login requirement"
            return "Already optimal for your config"

        if method == AuthMethod.FIREBASE:
            if ctx.application_type.value != "Mobile":
                return "Best suited for Mobile apps"
            if ctx.team_experience.value == "Advanced":
                return "May prefer more control than Firebase offers"
            return "Already optimal for your config"

        return "Review scoring factors"

    def _add_wrapped(
        self,
        out: list[tuple[str, str]],
        text: str,
        *,
        max_width: int,
        first_prefix: str,
        continuation_prefix: str,
        style: str,
    ) -> None:
        for line in _wrap_text_lines(
            text,
            max_width,
            indent=0,
            first_line_prefix=first_prefix,
            continuation_prefix=continuation_prefix,
        ):
            out.append((line, style))

    def _build_recommendations_lines(self, ctx: UserContext, verdict: Verdict, *, content_width: int) -> list[tuple[str, str]]:
        box_width = max(20, int(content_width))
        inner_width = max(10, box_width - 4)
        lines: list[tuple[str, str]] = []

        title = " RECOMMENDATIONS "
        side_len = max(0, (box_width - len(title) - 2) // 2)
        top_border = "┌" + ("─" * side_len) + title + ("─" * (box_width - 2 - side_len - len(title))) + "┐"
        bot_border = "└" + ("─" * (box_width - 2)) + "┘"
        lines.append((top_border, "bold #BD00FF"))

        winner = verdict.recommended
        winner_short = _method_short(winner.value)

        caveats: list[str] = []
        if winner == AuthMethod.SESSIONS:
            if ctx.expected_users.value == "50,000+":
                caveats.append("Consider Redis/Memcached for session storage at scale")
            if ctx.backend_architecture.value == "Stateless":
                caveats.append("Requires sticky sessions or shared session store")
            caveats.append("Implement CSRF protection and secure cookie settings")
        elif winner == AuthMethod.JWT:
            caveats.append("Implement token refresh and rotation strategy")
            if ctx.security_sensitivity.value == "High":
                caveats.append("Add token blacklisting for immediate revocation")
            caveats.append("Store tokens securely (httpOnly cookies for web)")
        elif winner == AuthMethod.OAUTH2:
            caveats.append("Set up proper redirect URI validation")
            if ctx.team_experience.value == "Beginner":
                caveats.append("Consider using a well-tested OAuth library")
            caveats.append("Implement state parameter to prevent CSRF")
        elif winner == AuthMethod.FIREBASE:
            if ctx.expected_users.value == "50,000+":
                caveats.append("Review Firebase pricing for high-volume usage")
            caveats.append("Plan migration strategy if you outgrow Firebase")

        caveats = caveats[:2]

        def box_row(text: str, style: str = "") -> None:
            wrapped = _wrap_text_lines(text, inner_width, indent=0, first_line_prefix="", continuation_prefix="")
            for chunk in wrapped:
                padded = chunk.ljust(inner_width)
                lines.append((f"│ {padded} │", style))

        if caveats:
            box_row(f"Using {winner_short}:", "bold #00FF41")
            for c in caveats:
                box_row(f"→ {c}", "")
        else:
            box_row(f"Using {winner_short}:", "bold #00FF41")
            box_row("→ No special caveats detected", "dim")

        # Alternatives (max 2)
        alts: list[tuple[str, str]] = []
        for method, _score in verdict.ranked:
            if method == verdict.recommended:
                continue
            method_short = _method_short(method.value)
            hint = self._get_enable_hint(method, ctx)
            alts.append((method_short, hint))
        alts = alts[:2]

        if alts:
            box_row("", "")
            box_row("Rejected options (how to enable):", "bold #FF1F7E")
            for short, hint in alts:
                box_row(f"→ {short}: To enable this: {hint}", "")

        lines.append((bot_border, "bold #BD00FF"))
        return lines

    def update_from(self, ctx: UserContext, *, scoring: ScoringResult, verdict: Verdict) -> None:
        if bool(getattr(self.app, "_busy_visible", False)):
            self._lines = [("loading…", "dim")]
            self._scroll_y = 0
            self.refresh()
            return

        score_overrides: dict[str, float] = dict(getattr(self.app, "_anim_current_scores", {}) or {})
        flash_keys = set(getattr(self.app, "_active_flash_keys", lambda: set())())
        open_set: set[str] = getattr(self.app, "_trace_open", set()) or set()

        view_mode = str(getattr(self.app, "view_mode", "default") or "default")
        compact_mode = view_mode == "compact"

        # Reserve 1 column for the manual scrollbar.
        content_width = max(24, (self.size.width or 60) - 1)

        ranked = verdict.ranked
        if compact_mode:
            ranked = [(verdict.recommended, scoring.scores[verdict.recommended])]

        win_score = float(score_overrides.get(verdict.recommended.value, scoring.scores[verdict.recommended]))

        out: list[tuple[str, str]] = []
        self._line_method_headers = {}

        for method, score in ranked:
            method_value = method.value
            method_short = _method_short(method_value)
            shown = float(score_overrides.get(method_value, score))
            is_win = method == verdict.recommended
            is_expanded = (method_value in open_set) if open_set else is_win

            key = f"score:{method_value}"
            flash = key in flash_keys

            expand_icon = "▼" if is_expanded else "▶"
            status_icon = "✓" if is_win else "✗"
            status_text = "SELECTED" if is_win else "REJECTED"

            header = f"{expand_icon} {status_icon} {method_short:<10} {shown:>4.1f} ({status_text})"
            if not is_win:
                delta = shown - win_score
                header += f" {delta:+.1f}"

            header_style = "bold #00FF41" if is_win else "bold #FF1F7E"
            if flash:
                header_style = (header_style + " " + _flash_style(True)).strip()

            header_lines = _wrap_text_lines(header, content_width, indent=0, first_line_prefix="", continuation_prefix="│ ")
            for i, hl in enumerate(header_lines):
                if i == 0:
                    self._line_method_headers[len(out)] = method_value
                out.append((hl, header_style))

            primary_reason = self._get_primary_reason(scoring, method)
            if not is_expanded:
                self._add_wrapped(
                    out,
                    primary_reason,
                    max_width=content_width,
                    first_prefix="│  — ",
                    continuation_prefix="│    ",
                    style="dim",
                )
                out.append(("", ""))
                continue

            # Expanded: show BASE/DELTAS/FINAL + enable hint for rejected.
            lines = scoring.reasons.get(method, [])
            base = "5.0"
            deltas: list[tuple[str, str]] = []
            cap_line: str | None = None

            for line in lines:
                if line.startswith("Base score"):
                    base = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("Capped at"):
                    cap_line = line
                    continue
                if ":" in line:
                    delta_part, reason_part = line.split(":", 1)
                    deltas.append((delta_part.strip(), reason_part.strip()))
                else:
                    deltas.append(("", line.strip()))

            out.append((f"│  ├─ BASE {base}", "dim #00D9FF"))

            for i, (delta_str, reason) in enumerate(deltas):
                is_last_delta = (i == len(deltas) - 1)
                branch = "└─" if is_last_delta and cap_line is None and is_win else "├─"
                if delta_str:
                    is_positive = delta_str.startswith("+")
                    is_negative = delta_str.startswith("-")
                    arrow = "▲" if is_positive else "▼" if is_negative else "●"
                    prefix = f"│  {branch} {arrow} {delta_str:>5}  "
                else:
                    prefix = f"│  {branch} "

                cont_prefix = "│  │" + (" " * max(0, len(prefix) - len("│  │")))
                self._add_wrapped(
                    out,
                    reason,
                    max_width=content_width,
                    first_prefix=prefix,
                    continuation_prefix=cont_prefix,
                    style="",
                )

            has_more_after_final = cap_line is not None or not is_win
            final_branch = "├─" if has_more_after_final else "└─"
            out.append((f"│  {final_branch} FINAL {shown:.1f}", "bold #00FF41" if is_win else "bold #A0A0A0"))

            if cap_line is not None:
                cap_text = cap_line.split(":", 1)[1].strip() if ":" in cap_line else cap_line
                cap_branch = "├─" if not is_win else "└─"
                self._add_wrapped(
                    out,
                    f"CAPPED AT: {cap_text}",
                    max_width=content_width,
                    first_prefix=f"│  {cap_branch} ",
                    continuation_prefix="│      ",
                    style="dim #FFB000",
                )

            if not is_win:
                hint = self._get_enable_hint(method, ctx)
                self._add_wrapped(
                    out,
                    f"To enable this: {hint}",
                    max_width=content_width,
                    first_prefix="│  └─ ",
                    continuation_prefix="│     ",
                    style="dim",
                )

            out.append(("", ""))

        # Recommendations box beneath trace.
        rec_lines = self._build_recommendations_lines(ctx, verdict, content_width=content_width)
        out.extend(rec_lines)

        # Safety pass: if any line still exceeds the viewport width, wrap it
        # instead of truncating. This keeps TRACE strictly vertical-scroll only.
        normalized: list[tuple[str, str]] = []
        for line, style in out:
            if not line:
                normalized.append(("", style))
                continue
            if len(line) <= content_width:
                normalized.append((line, style))
                continue
            wrapped = _wrap_text_lines(line, content_width, indent=0, first_line_prefix="", continuation_prefix="│ ")
            for wline in wrapped:
                normalized.append((wline, style))

        self._lines = normalized
        self._clamp_scroll()
        self.refresh()

    def on_mouse_scroll_up(self, _event) -> None:
        self.action_scroll_up()

    def on_mouse_scroll_down(self, _event) -> None:
        self.action_scroll_down()

    def on_click(self, event) -> None:
        # Toggle expansion when clicking a header line.
        y = int(getattr(event, "y", 0) or 0)
        idx = self._scroll_y + y
        method_value = self._line_method_headers.get(idx)
        if not method_value:
            return
        open_set: set[str] = set(getattr(self.app, "_trace_open", set()) or set())
        if method_value in open_set:
            open_set.discard(method_value)
        else:
            open_set.add(method_value)
        self.app._trace_open = open_set
        self.refresh()

    def render(self) -> Text:
        w = int(self.size.width or 1)
        h = int(self.size.height or 1)
        content_width = max(1, w - 1)

        total = len(self._lines)
        self._clamp_scroll()
        start = self._scroll_y
        end = min(total, start + h)

        # Scrollbar thumb math (proportional).
        if total <= h:
            thumb_start = 0
            thumb_len = 0
        else:
            thumb_len = max(1, int(round((h * h) / max(1, total))))
            thumb_len = min(h, thumb_len)
            max_pos = max(1, h - thumb_len)
            thumb_start = int(round((start * max_pos) / max(1, total - h)))

        t = Text()
        for i in range(h):
            abs_i = start + i
            if abs_i < end:
                line, style = self._lines[abs_i]
            else:
                line, style = ("", "")

            # Ensure vertical-only view: content is hard-wrapped upstream.
            padded = (line or "")
            if len(padded) < content_width:
                padded = padded.ljust(content_width)

            # Track uses ║ when scrollable, │ when not.
            if total <= h:
                bar = "│"
                bar_style = "dim #00D9FF"
            else:
                in_thumb = thumb_start <= i < (thumb_start + thumb_len)
                bar = "█" if in_thumb else "║"
                bar_style = "bold #00D9FF" if in_thumb else "dim #00D9FF"

            t.append(padded, style=style)
            t.append(bar, style=bar_style)
            if i != h - 1:
                t.append("\n")
        return t


class RecommendationsPanel(Static):
    """Smart recommendations based on current selection and rejected alternatives."""

    def _get_winner_caveats(self, winner: AuthMethod, ctx: UserContext) -> list[str]:
        """Get implementation caveats for the selected method."""
        caveats = []
        
        if winner == AuthMethod.SESSIONS:
            if ctx.expected_users.value == "50,000+":
                caveats.append("Consider Redis/Memcached for session storage at scale")
            if ctx.backend_architecture.value == "Stateless":
                caveats.append("Requires sticky sessions or shared session store")
            caveats.append("Implement CSRF protection and secure cookie settings")
        
        elif winner == AuthMethod.JWT:
            caveats.append("Implement token refresh and rotation strategy")
            if ctx.security_sensitivity.value == "High":
                caveats.append("Add token blacklisting for immediate revocation")
            caveats.append("Store tokens securely (httpOnly cookies for web)")
        
        elif winner == AuthMethod.OAUTH2:
            caveats.append("Set up proper redirect URI validation")
            if ctx.team_experience.value == "Beginner":
                caveats.append("Consider using a well-tested OAuth library")
            caveats.append("Implement state parameter to prevent CSRF")
        
        elif winner == AuthMethod.FIREBASE:
            if ctx.expected_users.value == "50,000+":
                caveats.append("Review Firebase pricing for high-volume usage")
            caveats.append("Plan migration strategy if you outgrow Firebase")
        
        return caveats[:2]  # Max 2 caveats

    def _get_alternatives(self, verdict: Verdict, ctx: UserContext) -> list[tuple[str, str]]:
        """Get alternatives with what needs to change."""
        alternatives = []
        
        for method, score in verdict.ranked:
            if method == verdict.recommended:
                continue
            
            method_short = _method_short(method.value)
            
            # Determine what would need to change
            if method == AuthMethod.JWT:
                if ctx.backend_architecture.value == "Stateful":
                    hint = "if you switch to Stateless backend"
                elif ctx.social_login_required == SocialLoginRequired.YES:
                    hint = "combine with OAuth for social login"
                else:
                    hint = "viable alternative, lower tie-break score"
            
            elif method == AuthMethod.SESSIONS:
                if ctx.backend_architecture.value == "Stateless":
                    hint = "if you switch to Stateful backend"
                elif ctx.application_type.value in ("API", "Mobile"):
                    hint = "better suited for Web applications"
                else:
                    hint = "viable alternative, consider complexity"
            
            elif method == AuthMethod.OAUTH2:
                if ctx.social_login_required == SocialLoginRequired.NO:
                    hint = "if you need Social Login in future"
                else:
                    hint = "viable for delegated identity"
            
            elif method == AuthMethod.FIREBASE:
                if ctx.application_type.value != "Mobile":
                    hint = "best suited for Mobile apps"
                else:
                    hint = "good for rapid prototyping"
            else:
                hint = "review scoring factors"
            
            alternatives.append((method_short, hint))
        
        return alternatives[:2]  # Max 2 alternatives

    def _wrap_box_line(self, text: str, inner_width: int, style: str = "") -> list[Text]:
        """Wrap text inside a box with │ borders on both sides."""
        lines: list[Text] = []
        wrapped = _wrap_text_lines(text, inner_width, indent=0, first_line_prefix="", continuation_prefix="")
        for i, chunk in enumerate(wrapped):
            row = Text()
            padded = chunk.ljust(inner_width)[:inner_width]
            row.append("│ ", style="bold #BD00FF")
            row.append(padded, style=style)
            row.append(" │", style="bold #BD00FF")
            lines.append(row)
        return lines

    def update_from(self, ctx: UserContext, *, verdict: Verdict) -> None:
        if bool(getattr(self.app, "_busy_visible", False)):
            self._render_skeleton()
            return

        # Calculate box width based on available space
        box_width = max(40, (self.size.width or 45) - 2)
        inner_width = box_width - 4  # Account for "│ " on left and " │" on right
        
        winner = verdict.recommended
        winner_short = _method_short(winner.value)
        
        t = Text()
        
        # Top border with title
        title = " RECOMMENDATIONS "
        side_len = max(0, (box_width - len(title) - 2) // 2)
        top_border = "┌" + ("─" * side_len) + title + ("─" * (box_width - 2 - side_len - len(title))) + "┐"
        t.append(top_border + "\n", style="bold #BD00FF")
        
        # Winner caveats section
        caveats = self._get_winner_caveats(winner, ctx)
        if caveats:
            # Section header
            header_text = f"Using {winner_short}:"
            t.append("│ ", style="bold #BD00FF")
            t.append(header_text.ljust(inner_width)[:inner_width], style="bold #00FF41")
            t.append(" │\n", style="bold #BD00FF")
            
            for caveat in caveats:
                # Wrap caveat text with arrow prefix
                arrow_prefix = "→ "
                caveat_text = arrow_prefix + caveat
                wrapped = _wrap_text_lines(caveat_text, inner_width, indent=2, first_line_prefix="", continuation_prefix="")
                for line_chunk in wrapped:
                    t.append("│ ", style="bold #BD00FF")
                    t.append(line_chunk.ljust(inner_width)[:inner_width], style="#00FF41")
                    t.append(" │\n", style="bold #BD00FF")
        
        # Empty line separator
        t.append("│ ", style="bold #BD00FF")
        t.append(" " * inner_width, style="")
        t.append(" │\n", style="bold #BD00FF")
        
        # Alternatives section
        alternatives = self._get_alternatives(verdict, ctx)
        if alternatives:
            # Section header
            t.append("│ ", style="bold #BD00FF")
            t.append("Alternatives:".ljust(inner_width)[:inner_width], style="bold #FFB000")
            t.append(" │\n", style="bold #BD00FF")
            
            for alt_name, alt_hint in alternatives:
                alt_text = f"→ {alt_name}: {alt_hint}"
                wrapped = _wrap_text_lines(alt_text, inner_width, indent=2, first_line_prefix="", continuation_prefix="")
                for line_chunk in wrapped:
                    t.append("│ ", style="bold #BD00FF")
                    t.append(line_chunk.ljust(inner_width)[:inner_width], style="#FFB000")
                    t.append(" │\n", style="bold #BD00FF")
        
        # Bottom border
        bottom_border = "└" + ("─" * (box_width - 2)) + "┘"
        t.append(bottom_border, style="bold #BD00FF")
        
        self.update(t)

    def _render_skeleton(self) -> None:
        phase = int(getattr(self.app, "_pulse_phase", 0) or 0)
        bar = _bar_shimmer(65.0, 16, phase)
        
        box_width = max(40, (self.size.width or 45) - 2)
        inner_width = box_width - 4
        
        t = Text()
        
        # Top border
        title = " RECOMMENDATIONS "
        side_len = max(0, (box_width - len(title) - 2) // 2)
        top_border = "┌" + ("─" * side_len) + title + ("─" * (box_width - 2 - side_len - len(title))) + "┐"
        t.append(top_border + "\n", style="dim #BD00FF")
        
        # Loading content
        loading_line = f"loading {bar}"
        t.append("│ ", style="dim #BD00FF")
        t.append(loading_line.ljust(inner_width)[:inner_width], style="dim")
        t.append(" │\n", style="dim #BD00FF")
        
        # Bottom border
        bottom_border = "└" + ("─" * (box_width - 2)) + "┘"
        t.append(bottom_border, style="dim #BD00FF")
        
        self.update(t)


class RightPanel(Static):
    """Right: costs + breaks + losers."""

    def _boxed(
        self,
        title: str,
        body_lines: list[str],
        *,
        icon: str,
        color: str,
        changed_indices: set[int] | None = None,
    ) -> Text:
        max_width = self.size.width or 36
        width = max(24, min(48, max_width))
        inner = max(10, width - 2)

        # Exact-width borders to prevent any wrapping.
        top_inner = "═" * inner
        title_txt = f" {title} "
        start = max(0, (inner - len(title_txt)) // 2)
        top_inner = top_inner[:start] + title_txt + top_inner[start + len(title_txt) :]
        top = "╔" + top_inner + "╗"
        bot = "╚" + ("═" * inner) + "╝"

        out = Text()
        out.append(top + "\n", style=f"bold {color}")
        changed = changed_indices or set()

        # Keep pointer spacing consistent across sections.
        # This is the total prefix width inside the box (bullet + spaces).
        pointer_pad = 2  # e.g. "▣ " / "▶ "
        wrap_width = max(1, inner - pointer_pad)
        for idx, raw in enumerate(body_lines):
            wrapped = textwrap.wrap(raw, width=wrap_width) or [""]
            bullet = "▶" if idx in changed else icon
            for j, wline in enumerate(wrapped):
                # Only show the bullet on the first wrapped line; continuation
                # lines align under the text for readability.
                prefix = (bullet + " " * (pointer_pad - 1)) if j == 0 else (" " * pointer_pad)
                line = f"{prefix}{wline}".ljust(inner)[:inner]
                out.append("║" + line + "║\n", style=color)
        out.append(bot + "\n", style=f"bold {color}")
        return out

    def update_from(self, ctx: UserContext, *, verdict: Verdict) -> None:
        if bool(getattr(self.app, "_busy_visible", False)):
            self._render_skeleton()
            return

        # Track per-section changes so we can vary the pointer when config changes.
        if not hasattr(self, "_prev_sections"):
            self._prev_sections = {"costs": [], "breaks": [], "refs": [], "losers": []}

        t = Text()

        costs = verdict.tradeoffs[:3]
        prev_costs: list[str] = self._prev_sections.get("costs", [])
        costs_changed = {i for i, v in enumerate(costs) if i >= len(prev_costs) or prev_costs[i] != v}
        self._prev_sections["costs"] = list(costs)
        t.append(self._boxed("COSTS", costs, icon="▣", color="#FFB000", changed_indices=costs_changed))
        t.append("\n")

        breaks = verdict.break_conditions[:3]
        prev_breaks: list[str] = self._prev_sections.get("breaks", [])
        breaks_changed = {i for i, v in enumerate(breaks) if i >= len(prev_breaks) or prev_breaks[i] != v}
        self._prev_sections["breaks"] = list(breaks)
        t.append(self._boxed("BREAKS", breaks, icon="▣", color="#FF1F7E", changed_indices=breaks_changed))

        # External references that back the general security/architecture statements.
        # Keep them compact so they remain readable in the narrow right panel.
        t.append("\n")
        refs = [
            "RFC 6749 — OAuth 2.0 (rfc-editor.org/rfc6749)",
            "RFC 6750 — Bearer Tokens (rfc-editor.org/rfc6750)",
            "OWASP — Session Management (owasp.org/Session_Management_Cheat_Sheet)",
            "OWASP — JWT guidance (owasp.org/JWT_Cheat_Sheet)",
            "NIST 800-63B — Digital Identity (nist.gov/800-63b)",
            "Firebase Auth docs (firebase.google.com/docs/auth)",
        ]
        prev_refs: list[str] = self._prev_sections.get("refs", [])
        refs_changed = {i for i, v in enumerate(refs) if i >= len(prev_refs) or prev_refs[i] != v}
        self._prev_sections["refs"] = list(refs)
        t.append(self._boxed("REFERENCES", refs, icon="▣", color="#00D9FF", changed_indices=refs_changed))

        losers: list[str] = []
        for method, _score in verdict.ranked:
            if method == verdict.recommended:
                continue
            bullets = verdict.rejections.get(method, [])[:1]
            reason = bullets[0] if bullets else "Lower fit"
            losers.append(f"{_method_short(method.value)}: {reason}")

        if not losers:
            losers = ["No rejected options"]

        losers = losers[:3]
        prev_losers: list[str] = self._prev_sections.get("losers", [])
        losers_changed = {i for i, v in enumerate(losers) if i >= len(prev_losers) or prev_losers[i] != v}
        self._prev_sections["losers"] = list(losers)

        t.append("\n")
        # Use a pleasant purple so LOSERS isn't visually identical to BREAKS.
        # Use a pixel-style bullet for LOSERS points (no emoji icons).
        t.append(self._boxed("LOSERS", losers, icon="■", color="#BD93F9", changed_indices=losers_changed))

        self.update(t)

    def _render_skeleton(self) -> None:
        phase = int(getattr(self.app, "_pulse_phase", 0) or 0)
        # Keep the same boxed style, but use placeholder lines.
        def ph(width: int, pct: float) -> str:
            return _bar_shimmer(pct, width, phase)

        t = Text()
        t.append(self._boxed("COSTS", ["loading…", ph(18, 72.0), ph(18, 58.0)], icon="▣", color="#FFB000"))
        t.append("\n")
        t.append(self._boxed("BREAKS", ["loading…", ph(18, 66.0), ph(18, 52.0)], icon="▣", color="#FF1F7E"))
        t.append("\n")
        t.append(self._boxed("REFERENCES", ["loading…", ph(18, 62.0)], icon="▣", color="#00D9FF"))
        t.append("\n")
        t.append(self._boxed("LOSERS", ["loading…", ph(18, 54.0)], icon="■", color="#BD93F9"))
        self.update(t)


class ComparisonTable(DataTable):
    """Bottom: aligned comparison table."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.show_cursor = False
        # Fixed-width columns keep the table stable and prevent "content all over".
        # Remaining space is allocated to WHY.
        self.add_column("M", width=10)
        self.add_column("S", width=5)
        self.add_column("Δ", width=5)
        self.add_column("FIT", width=5)
        self.add_column("WHY")
        self.zebra_stripes = True

    def update_from(self, ctx: UserContext, *, scoring: ScoringResult, verdict: Verdict, rows: list[ComparisonRow]) -> None:
        if bool(getattr(self.app, "_busy_visible", False)):
            self._render_skeleton()
            return

        score_overrides: dict[str, float] = dict(getattr(self.app, "_anim_current_scores", {}) or {})
        flash_keys = set(getattr(self.app, "_active_flash_keys", lambda: set())())

        self.clear()
        # Keep WHY strictly single-line by pre-ellipsizing to available width.
        width = self.size.width or 80
        # Approximate available chars for WHY given fixed columns and separators.
        why_max = max(18, width - (10 + 5 + 5 + 5) - 14)
        win_actual = scoring.scores[verdict.recommended]
        win_score = float(score_overrides.get(verdict.recommended.value, win_actual))
        for r in rows:
            fit = "✓✓" if r.score >= 9 else "✓" if r.score >= 7 else "○" if r.score >= 5 else "·"
            label = _method_short(r.method.value)
            is_win = r.method == verdict.recommended
            shown_score = float(score_overrides.get(r.method.value, r.score))
            delta = shown_score - win_score
            marker = "▶" if is_win else " "
            m_cell = Text(_ellipsize(f"{marker}{label}", 10), style=("bold green" if is_win else "bold"))
            s_key = f"score:{r.method.value}"
            s_flash = s_key in flash_keys
            s_style = ("bold green" if is_win else "")
            if s_flash:
                s_style = (s_style + " " + _flash_style(True)).strip() or _flash_style(True)
            s_cell = Text(f"{shown_score:.1f}", style=s_style)
            d_style = "green" if delta == 0 else "red" if delta < 0 else "green"
            d_cell_style = ("bold green" if is_win else d_style)
            if s_flash:
                d_cell_style = (d_cell_style + " " + _flash_style(True)).strip()
            d_cell = Text(f"{delta:+.1f}", style=d_cell_style)
            fit_color = "#00FF41" if r.score >= 9 else "#FFB000" if r.score >= 7 else "#A0A0A0"
            fit_cell = Text(fit, style=(f"bold {fit_color}" if is_win else f"{fit_color}"))
            why_cell = Text(_ellipsize(r.primary_reason, why_max))
            self.add_row(m_cell, s_cell, d_cell, fit_cell, why_cell)

    def _render_skeleton(self) -> None:
        self.clear()
        phase = int(getattr(self.app, "_pulse_phase", 0) or 0)
        # Stable row order and widths; no scoring calls.
        names = [
            "Sessions",
            "JWT",
            "OAuth 2.0",
            "Firebase",
        ]
        for i, name in enumerate(names):
            shimmer = _bar_shimmer(70.0 - (i * 6.0), 10, phase)
            m_cell = Text(_ellipsize(" " + name, 10), style="dim")
            s_cell = Text("--.-", style="dim")
            d_cell = Text("+0.0", style="dim")
            fit_cell = Text("·", style="dim")
            why_cell = Text(_ellipsize(f"loading {shimmer}", max(18, (self.size.width or 80) - 44)), style="dim")
            self.add_row(m_cell, s_cell, d_cell, fit_cell, why_cell)


class CommandBar(Static):
    """Bottom command line: minimal prompt + status."""

    status: reactive[str] = reactive("ready")

    def compose(self) -> ComposeResult:
        yield Label("λ", id="prompt")
        yield Input(placeholder="commands: r(reset)  q(quit)  ?(help)", id="cmd")
        yield Label("", id="status")

    def on_mount(self) -> None:
        self.query_one("#cmd", Input).focus()
        self.set_status(self.status)

    def set_status(self, s: str) -> None:
        self.status = s
        # Delegate typing to the shared app typewriter for consistency.
        try:
            getattr(self.app, "type_status")(s)
        except Exception:
            self.query_one("#status", Label).update(Text(s, style="dim"))


class AuthRefereeTextual(App):
    CSS_PATH = "textual_app.tcss"

    live_mode: reactive[bool] = reactive(False)
    live_countdown: reactive[int] = reactive(0)
    theme_name: reactive[str] = reactive("cyber")
    view_mode: reactive[str] = reactive("default")

    _intro_done: bool = False
    _live_pulse: int = 0
    _pulse_phase: int = 0

    _typewriter: _Typewriter | None = None
    _type_timer = None

    # Phase-2: busy/loading controller (show spinner only if work > 200ms).
    _busy_depth: int = 0
    _busy_label: str = "processing…"
    _busy_visible: bool = False
    _busy_show_timer = None
    _busy_hide_timer = None
    _busy_shown_at: float = 0.0

    _last_live_tick: float = 0.0

    _last_ctx: UserContext | None = None
    _last_scoring: ScoringResult | None = None
    _last_verdict: Verdict | None = None
    _last_rows: list[ComparisonRow] | None = None
    _snapshot: dict[str, object] = {}
    _flash_until: dict[str, float] = {}
    _trace_open: set[str] = set()  # Track which trace method nodes are expanded

    _anim_active: bool = False
    _anim_start: float = 0.0
    _anim_duration: float = 0.0
    _anim_from_conf: float = 0.0
    _anim_to_conf: float = 0.0
    _anim_current_conf: float = 0.0
    _anim_from_scores: dict[str, float] = {}
    _anim_to_scores: dict[str, float] = {}
    _anim_current_scores: dict[str, float] = {}

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reset", "Reset"),
        Binding("?", "help", "Help"),
        Binding("l", "toggle_live", "Live"),
        Binding("t", "cycle_theme", "Theme"),
        Binding("e", "export", "Export"),
        Binding("/", "search", "Search"),
        Binding("1", "view_compact", "Compact"),
        Binding("2", "view_detailed", "Detailed"),
        Binding("3", "view_focus", "Focus"),
        Binding("0", "view_default", "Default"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("", id="boot")
        yield Static("", id="busy")
        yield TopBar(id="top")
        yield Scanline(id="scan")
        with Horizontal(id="main"):
            yield ContextPanel(id="left")
            with Vertical(id="center"):
                yield Static("FINAL", classes="section")
                yield VerdictPanel(id="verdict")
                yield Static("TRACE", classes="section")
                yield Static("", id="trace_up")
                yield TraceViewport(id="trace_scroll")
                yield Static("", id="trace_down")
            with Vertical(id="right_wrap"):
                yield Static("", id="right_up")
                with VerticalScroll(id="right_scroll"):
                    yield RightPanel(id="right")
                yield Static("", id="right_down")
        yield Static("COMPARISON", id="compare_title", classes="section")
        yield ComparisonTable(id="compare")
        yield CommandBar(id="bar")

    async def on_mount(self) -> None:
        # Timers
        self.set_interval(1.0, self._tick_live)
        self.set_interval(0.06, self._tick_animations)
        self.set_interval(0.20, self._tick_scroll_indicators)
        self.set_interval(0.12, self._tick_pulse)

        # Shared typewriter (typing + blinking cursor).
        self._typewriter = _Typewriter(self)
        self._type_timer = self.set_interval(0.03, self._tick_typewriter)

        # Apply initial theme/view.
        self._apply_theme()
        self._apply_view_mode()

        # Run staged intro.
        await self._run_intro()
        self._recompute()

    async def _tween(self, widget, attribute: str, value, *, duration: float, easing: str = "in_out_cubic") -> None:
        """Animate a widget attribute safely.

        Textual 7.0.1's animation plumbing is currently unstable in this project
        (BoundAnimator call mismatch). For stability, do a small manual tween for
        the few attributes we use (opacity/offset) and fall back to applying the
        final value directly.
        """
        try:
            dur = max(0.0, float(duration))
            steps = 10 if dur >= 0.12 else 6
            sleep_s = dur / max(1, steps)

            # Read current value as start.
            start_val = getattr(widget.styles, attribute)

            def lerp(a: float, b: float, t: float) -> float:
                return a + (b - a) * t

            def ease(t: float) -> float:
                # Keep easing deterministic regardless of string.
                return _ease_in_out(t)

            # Only tween attributes we actually use.
            if attribute == "opacity":
                a = float(start_val if start_val is not None else 1.0)
                b = float(value)
                for i in range(1, steps + 1):
                    t = ease(i / steps)
                    widget.styles.opacity = lerp(a, b, t)
                    await asyncio.sleep(sleep_s)
                widget.styles.opacity = b
                return

            if attribute == "offset":
                sa = start_val if isinstance(start_val, tuple) else (0, 0)
                sb = value if isinstance(value, tuple) else (0, 0)
                ax, ay = int(sa[0]), int(sa[1])
                bx, by = int(sb[0]), int(sb[1])
                for i in range(1, steps + 1):
                    t = ease(i / steps)
                    widget.styles.offset = (int(round(lerp(ax, bx, t))), int(round(lerp(ay, by, t))))
                    await asyncio.sleep(sleep_s)
                widget.styles.offset = (bx, by)
                return

            # Unknown attribute: just apply.
            setattr(widget.styles, attribute, value)
        except Exception:
            # Never let eye-candy crash the app.
            try:
                setattr(widget.styles, attribute, value)
            except Exception:
                pass

    def _tick_typewriter(self) -> None:
        if self._typewriter is None:
            return
        self._typewriter.tick()

    def type_status(self, s: str) -> None:
        if self._typewriter is None:
            return
        # #status is inside CommandBar but id-unique in the app.
        self._typewriter.start("status", s, selector="#status", style="dim", speed_s=0.02, blink=True)

    def _type_busy_label(self, s: str) -> None:
        if self._typewriter is None:
            return
        # Busy label is rendered as part of the spinner line; no direct selector.
        self._typewriter.start("busy", s, selector=None, style="bold #FFB000", speed_s=0.02, blink=True)

    async def _type_boot(self, s: str) -> None:
        if self._typewriter is None:
            return
        self._typewriter.start("boot", s, selector="#boot", style="bold #00D9FF", speed_s=0.04, blink=True)
        # Wait until typing is complete.
        for _ in range(400):
            if self._typewriter.is_done("boot"):
                break
            await asyncio.sleep(0.01)

    def _tick_live(self) -> None:
        if not self.live_mode:
            self.live_countdown = 0
            return

        if self.live_countdown <= 0:
            self.live_countdown = 5
            self._busy_begin("refreshing…")
            try:
                self._recompute()
            finally:
                self._busy_end()
        else:
            self.live_countdown -= 1

        self._live_pulse = (self._live_pulse + 1) % 6

    def _tick_pulse(self) -> None:
        self._pulse_phase = (self._pulse_phase + 1) % 8
        # Pulse the focused panel border by toggling classes.
        for pid in ("#left", "#center", "#right_wrap", "#compare", "#bar"):
            try:
                self.query_one(pid).remove_class("pulse_a")
                self.query_one(pid).remove_class("pulse_b")
            except Exception:
                pass

        # Determine which high-level area currently has focus.
        focused = self.focused
        target_id: str | None = None
        try:
            if focused is not None:
                if focused.has_ancestor("#left"):
                    target_id = "#left"
                elif focused.has_ancestor("#center"):
                    target_id = "#center"
                elif focused.has_ancestor("#right_wrap"):
                    target_id = "#right_wrap"
                elif focused.has_ancestor("#compare"):
                    target_id = "#compare"
        except Exception:
            target_id = None

        if target_id:
            try:
                target = self.query_one(target_id)
                target.add_class("pulse_a" if self._pulse_phase % 2 == 0 else "pulse_b")
            except Exception:
                pass

        # Busy overlay spinner
        self._render_busy()

    def _busy_begin(self, label: str = "processing…") -> None:
        """Begin a busy section.

        Spinner appears only if the busy section lasts >200ms.
        Nested busy sections are supported.
        """
        self._busy_depth += 1
        self._busy_label = label
        self._type_busy_label(label)

        if self._busy_visible:
            return
        if self._busy_show_timer is not None:
            return

        def show() -> None:
            self._busy_show_timer = None
            if self._busy_depth <= 0:
                return
            self._busy_visible = True
            self._busy_shown_at = time.monotonic()
            self._render_busy()

        # Show only if still busy after 200ms.
        self._busy_show_timer = self.set_timer(0.20, show)

    def _busy_end(self) -> None:
        """End a busy section (paired with `_busy_begin`)."""
        if self._busy_depth > 0:
            self._busy_depth -= 1
        if self._busy_depth > 0:
            return

        # If we never showed, cancel the delayed show.
        if self._busy_show_timer is not None:
            try:
                self._busy_show_timer.stop()
            except Exception:
                pass
            self._busy_show_timer = None

        if not self._busy_visible:
            self._render_busy()
            return

        # Avoid flicker: once shown, keep it visible briefly.
        min_visible = 0.25
        shown_for = max(0.0, time.monotonic() - float(self._busy_shown_at or 0.0))
        delay = max(0.0, min_visible - shown_for)

        if self._busy_hide_timer is not None:
            try:
                self._busy_hide_timer.stop()
            except Exception:
                pass
            self._busy_hide_timer = None

        def hide() -> None:
            self._busy_hide_timer = None
            if self._busy_depth > 0:
                return
            self._busy_visible = False
            self._render_busy()

        self._busy_hide_timer = self.set_timer(delay, hide)

    def _render_busy(self) -> None:
        try:
            w = self.query_one("#busy", Static)
        except Exception:
            return

        if not self._busy_visible:
            w.update("")
            w.remove_class("show")
            return

        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        ch = frames[self._pulse_phase % len(frames)]
        label = self._busy_label
        if self._typewriter is not None:
            label = self._typewriter.get_display("busy") or label
        w.update(Text(f"{ch} {label}", style="bold #FFB000"))
        w.add_class("show")

    async def _run_intro(self) -> None:
        if self._intro_done:
            return

        boot = self.query_one("#boot", Static)
        top = self.query_one("#top")
        scan = self.query_one("#scan")
        main = self.query_one("#main")
        compare_title = self.query_one("#compare_title")
        compare = self.query_one("#compare")
        bar = self.query_one("#bar")

        # Start black: hide everything.
        for w in (top, scan, main, compare_title, compare, bar):
            w.styles.opacity = 0

        # Boot logo typing.
        boot.add_class("show")
        await self._type_boot("OAUTH")
        await asyncio.sleep(0.12)
        boot.remove_class("show")
        boot.update("")

        # Header slides down.
        top.styles.offset = (0, -2)
        top.styles.opacity = 0
        await self._tween(top, "opacity", 1.0, duration=0.20, easing="in_out_cubic")
        await self._tween(top, "offset", (0, 0), duration=0.22, easing="in_out_cubic")

        # Scanline
        await self._tween(scan, "opacity", 1.0, duration=0.18, easing="in_out_cubic")

        # Panels
        left = self.query_one("#left")
        center = self.query_one("#center")
        right = self.query_one("#right_wrap")
        left.styles.offset = (-20, 0)
        center.styles.offset = (0, 4)
        right.styles.offset = (20, 0)
        main.styles.opacity = 1
        left.styles.opacity = 0
        center.styles.opacity = 0
        right.styles.opacity = 0

        await self._tween(left, "opacity", 1.0, duration=0.22, easing="in_out_cubic")
        await self._tween(left, "offset", (0, 0), duration=0.25, easing="in_out_cubic")
        await self._tween(center, "opacity", 1.0, duration=0.22, easing="in_out_cubic")
        await self._tween(center, "offset", (0, 0), duration=0.25, easing="in_out_cubic")
        await self._tween(right, "opacity", 1.0, duration=0.22, easing="in_out_cubic")
        await self._tween(right, "offset", (0, 0), duration=0.25, easing="in_out_cubic")

        # Comparison fades in.
        await self._tween(compare_title, "opacity", 1.0, duration=0.18, easing="in_out_cubic")
        await self._tween(compare, "opacity", 1.0, duration=0.18, easing="in_out_cubic")
        await self._tween(bar, "opacity", 1.0, duration=0.18, easing="in_out_cubic")

        self._intro_done = True

    def _active_flash_keys(self) -> set[str]:
        now = time.monotonic()
        return {k for (k, until) in self._flash_until.items() if until > now}

    def _tick_animations(self) -> None:
        if not self._anim_active:
            return
        now = time.monotonic()
        if self._anim_duration <= 0:
            self._anim_active = False
            return

        p = (now - self._anim_start) / self._anim_duration
        if p >= 1.0:
            self._anim_current_conf = self._anim_to_conf
            self._anim_current_scores = dict(self._anim_to_scores)
            self._anim_active = False
            if self._last_ctx is not None:
                self._render(self._last_ctx)
            return

        k = _ease_in_out(p)
        self._anim_current_conf = self._anim_from_conf + (self._anim_to_conf - self._anim_from_conf) * k
        cur: dict[str, float] = {}
        for method_value, to_v in self._anim_to_scores.items():
            from_v = self._anim_from_scores.get(method_value, to_v)
            cur[method_value] = from_v + (to_v - from_v) * k
        self._anim_current_scores = cur
        if self._last_ctx is not None:
            self._render(self._last_ctx)

    def _tick_scroll_indicators(self) -> None:
        def update_pair(scroll_id: str, top_id: str, bot_id: str, compact: bool = False) -> None:
            try:
                s = self.query_one(scroll_id)
                top = self.query_one(top_id, Static)
                bot = self.query_one(bot_id, Static)
            except Exception:
                return

            y = int(getattr(s, "scroll_y", 0) or 0)
            max_y = int(getattr(s, "max_scroll_y", 0) or 0)
            above = max(0, y)
            below = max(0, max_y - y)

            if compact:
                # Compact indicators for left panel
                top.update(Text(f"▲ {above}" if above else "", style="dim #00D9FF"))
                bot.update(Text(f"▼ {below}" if below else "", style="dim #00D9FF"))
            else:
                top.update(Text(f"▲▲▲ {above} above ▲▲▲" if above else "", style="dim #00D9FF"))
                bot.update(Text(f"▼▼▼ {below} below ▼▼▼" if below else "", style="dim #00D9FF"))

        update_pair("#trace_scroll", "#trace_up", "#trace_down")
        update_pair("#right_scroll", "#right_up", "#right_down")
        update_pair("#left_scroll", "#left_scroll_up", "#left_scroll_down", compact=True)

    def _ctx_from_controls(self) -> UserContext:
        # NOTE: Textual Select may transiently report value=None/invalid during
        # misclicks / focus changes. Never crash the app for that; instead,
        # fall back to last known-good context (or defaults).
        last = self._last_ctx

        def safe_enum(enum_cls, raw, fallback):
            try:
                if raw is None:
                    raise ValueError("empty")
                return enum_cls(raw)
            except Exception:
                return fallback

        app_raw = self.query_one("#app", Select).value
        users_raw = self.query_one("#users", Select).value
        sec_raw = self.query_one("#sec", Select).value
        backend_raw = self.query_one("#backend", Select).value
        team_raw = self.query_one("#team", Select).value
        social_raw = self.query_one("#social", Select).value

        app = safe_enum(ApplicationType, app_raw, last.application_type if last else ApplicationType.WEB)
        users = safe_enum(ExpectedUsers, users_raw, last.expected_users if last else ExpectedUsers.BTW_1K_50K)
        sec = safe_enum(SecuritySensitivity, sec_raw, last.security_sensitivity if last else SecuritySensitivity.MEDIUM)
        backend = safe_enum(BackendArchitecture, backend_raw, last.backend_architecture if last else BackendArchitecture.STATELESS)
        team = safe_enum(TeamExperience, team_raw, last.team_experience if last else TeamExperience.INTERMEDIATE)
        social = safe_enum(SocialLoginRequired, social_raw, last.social_login_required if last else SocialLoginRequired.NO)

        return UserContext(
            application_type=app,
            expected_users=users,
            security_sensitivity=sec,
            backend_architecture=backend,
            team_experience=team,
            social_login_required=social,
        )

    def _recompute(self) -> None:
        ctx = self._ctx_from_controls()
        self._last_ctx = ctx
        # Context gauge (visual hierarchy hint).
        users_value = ctx.expected_users.value
        if users_value == ExpectedUsers.LT_1K.value:
            gauge = "[" + _bar(25, 10) + "] 1K"
            color = "#00FF41"
        elif users_value == ExpectedUsers.BTW_1K_50K.value:
            gauge = "[" + _bar(60, 10) + "] 50K"
            color = "#FFB000"
        else:
            gauge = "[" + _bar(95, 10) + "] 50K+"
            color = "#FF1F7E"
        self.query_one("#users_gauge", Static).update(Text(f"{users_value}  {gauge}", style=f"bold {color}"))

        # Compute once for diff/animation targets.
        scoring = score_options(ctx)
        verdict = build_verdict(ctx, scoring)
        rows = build_comparison_rows(scoring)

        # Cache model for rendering (root fix: avoid recomputing per panel).
        self._last_scoring = scoring
        self._last_verdict = verdict
        self._last_rows = rows

        now = time.monotonic()
        new_snapshot: dict[str, object] = {
            "winner": verdict.recommended.value,
            "conf": verdict.confidence_percent,
        }
        for method, score in verdict.ranked:
            new_snapshot[f"score:{method.value}"] = float(score)

        for k, v in new_snapshot.items():
            if k in self._snapshot and self._snapshot[k] != v:
                self._flash_until[k] = now + 0.6
        self._snapshot = new_snapshot

        # Start animation targets (confidence + method scores).
        to_conf = float(verdict.confidence_percent)
        from_conf = float(self._anim_current_conf or to_conf)
        to_scores = {m.value: float(s) for (m, s) in verdict.ranked}
        from_scores = dict(self._anim_current_scores) if self._anim_current_scores else dict(to_scores)

        max_delta = max([abs(to_conf - from_conf)] + [abs(to_scores[k] - from_scores.get(k, to_scores[k])) for k in to_scores])
        dur = 0.4 if max_delta <= 2.0 else 0.8 if max_delta >= 5.0 else 0.6

        self._anim_start = time.monotonic()
        self._anim_duration = dur
        self._anim_from_conf = from_conf
        self._anim_to_conf = to_conf
        self._anim_from_scores = from_scores
        self._anim_to_scores = to_scores
        self._anim_active = True

        self._render(ctx)

    def _render(self, ctx: UserContext) -> None:
        scoring = self._last_scoring or score_options(ctx)
        verdict = self._last_verdict or build_verdict(ctx, scoring)
        rows = self._last_rows or build_comparison_rows(scoring)

        # Panels pull animation/flash state from app.
        self.query_one("#verdict", VerdictPanel).update_from(ctx, verdict=verdict)
        self.query_one("#trace_scroll", TraceViewport).update_from(ctx, scoring=scoring, verdict=verdict)
        self.query_one("#right", RightPanel).update_from(ctx, verdict=verdict)
        self.query_one("#compare", ComparisonTable).update_from(ctx, scoring=scoring, verdict=verdict, rows=rows)

    @on(Select.Changed)
    def _on_select_changed(self, _event: Select.Changed) -> None:
        self._busy_begin("recomputing…")
        try:
            try:
                self._recompute()
            except Exception as e:
                # Prevent Textual's full-screen exception UI on transient input.
                try:
                    self.query_one("#bar", CommandBar).set_status(f"input glitch ignored: {type(e).__name__}")
                except Exception:
                    pass
                if self._last_ctx is not None:
                    try:
                        self._render(self._last_ctx)
                    except Exception:
                        pass
        finally:
            self._busy_end()

    @on(Input.Submitted)
    def _on_cmd(self, event: Input.Submitted) -> None:
        cmd = (event.value or "").strip().lower()
        bar = self.query_one("#bar", CommandBar)
        self.query_one("#cmd", Input).value = ""

        if cmd in {"q", "quit", "exit"}:
            self.exit()
            return
        if cmd in {"r", "reset"}:
            self.action_reset()
            bar.set_status("reset")
            return
        if cmd in {"?", "help"}:
            bar.set_status("help: q quit | r reset | change selects to live-update")
            return

        bar.set_status(f"unknown command: {cmd}")

    def action_reset(self) -> None:
        self.query_one("#app", Select).value = ApplicationType.WEB.value
        self.query_one("#users", Select).value = ExpectedUsers.BTW_1K_50K.value
        self.query_one("#sec", Select).value = SecuritySensitivity.MEDIUM.value
        self.query_one("#backend", Select).value = BackendArchitecture.STATELESS.value
        self.query_one("#team", Select).value = TeamExperience.INTERMEDIATE.value
        self.query_one("#social", Select).value = SocialLoginRequired.NO.value
        self._recompute()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_search(self) -> None:
        self.push_screen(SearchScreen())

    def action_export(self) -> None:
        self.push_screen(ExportScreen())

    def build_search_sections(self) -> dict[str, str]:
        """Build text buckets for search overlay (no internal filenames)."""
        ctx = self._ctx_from_controls()
        scoring = score_options(ctx)
        verdict = build_verdict(ctx, scoring)
        rows = build_comparison_rows(scoring)

        trace_lines: list[str] = []
        for method, _score in verdict.ranked:
            trace_lines.extend(scoring.reasons.get(method, []))

        compare_lines = [f"{_method_short(r.method.value)} {r.score:.1f} {r.primary_reason}" for r in rows]

        refs = [
            "RFC 6749 OAuth 2.0",
            "RFC 6750 Bearer Tokens",
            "OWASP Session Management",
            "OWASP JWT guidance",
            "NIST 800-63B Digital Identity",
            "Firebase Auth docs",
        ]

        return {
            "TRACE": "\n".join(trace_lines),
            "COSTS": "\n".join(verdict.tradeoffs),
            "BREAKS": "\n".join(verdict.break_conditions),
            "LOSERS": "\n".join([" ".join(v) for v in verdict.rejections.values()]),
            "REFERENCES": "\n".join(refs),
            "COMPARISON": "\n".join(compare_lines),
        }

    def export_current(self, fmt: str, name: str, include: dict[str, bool]) -> bool:
        """Export current view to exports/<name>.<ext>."""
        self._busy_begin("exporting…")
        try:
            ctx = self._ctx_from_controls()
            scoring = score_options(ctx)
            verdict = build_verdict(ctx, scoring)
            rows = build_comparison_rows(scoring)

            os.makedirs("exports", exist_ok=True)
            path = os.path.join("exports", f"{name}.{fmt}")

            now = _dt.datetime.now().isoformat(timespec="seconds")
            session = getattr(self.query_one("#top", TopBar), "session", "")

            if fmt == "json":
                import json

                payload: dict[str, object] = {
                    "context": {
                        "app": ctx.application_type.value,
                        "users": ctx.expected_users.value,
                        "sec": ctx.security_sensitivity.value,
                        "backend": ctx.backend_architecture.value,
                        "team": ctx.team_experience.value,
                        "social": ctx.social_login_required.value,
                    },
                    "verdict": {
                        "recommended": verdict.recommended.value,
                        "confidence": verdict.confidence_percent,
                        "tie_break": verdict.tie_break_summary,
                    },
                    "ranked": [(m.value, s) for (m, s) in verdict.ranked],
                    "comparison": [
                        {"method": r.method.value, "score": r.score, "why": r.primary_reason}
                        for r in rows
                    ],
                }
                if include.get("meta"):
                    payload["meta"] = {"session": str(session), "time": now}
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                return True

            lines: list[str] = []
            if include.get("meta"):
                lines.append(f"Session: {session}")
                lines.append(f"Time: {now}")
                lines.append("")

            if include.get("ctx"):
                lines.append("CONTEXT")
                lines.append(f"  APP:     {ctx.application_type.value}")
                lines.append(f"  USERS:   {ctx.expected_users.value}")
                lines.append(f"  SEC:     {ctx.security_sensitivity.value}")
                lines.append(f"  BACKEND: {ctx.backend_architecture.value}")
                lines.append(f"  TEAM:    {ctx.team_experience.value}")
                lines.append(f"  SOCIAL:  {ctx.social_login_required.value}")
                lines.append("")

            if include.get("verdict"):
                lines.append(f"VERDICT: {verdict.recommended.value}")
                lines.append(f"CONFIDENCE: {verdict.confidence_percent}%")
                if verdict.tie_break_summary:
                    lines.append(f"TIE-BREAK: {verdict.tie_break_summary}")
                lines.append("")

            if include.get("trace"):
                lines.append("TRACE")
                for method, _score in verdict.ranked:
                    lines.append(f"- {method.value}")
                    for r in scoring.reasons.get(method, []):
                        lines.append(f"  {r}")
                lines.append("")

            if include.get("risk"):
                lines.append("COSTS")
                for x in verdict.tradeoffs:
                    lines.append(f"- {x}")
                lines.append("\nBREAKS")
                for x in verdict.break_conditions:
                    lines.append(f"- {x}")
                lines.append("\nLOSERS")
                for m, _ in verdict.ranked:
                    if m == verdict.recommended:
                        continue
                    reason = (verdict.rejections.get(m) or ["Lower fit"])[0]
                    lines.append(f"- {m.value}: {reason}")
                lines.append("")

            if include.get("compare"):
                lines.append("COMPARISON")
                for r in rows:
                    lines.append(f"- {r.method.value}: {r.score:.1f} — {r.primary_reason}")
                lines.append("")

            if fmt == "md":
                md: list[str] = []
                md.append("# AuthReferee Report\n")
                md.append("```\n" + "\n".join(lines).strip() + "\n```\n")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(md))
                return True

            # txt
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines).strip() + "\n")
            return True
        except Exception:
            return False
        finally:
            self._busy_end()

    def action_toggle_live(self) -> None:
        self.live_mode = not self.live_mode
        self.live_countdown = 5 if self.live_mode else 0
        self.query_one("#bar", CommandBar).set_status("live: ON" if self.live_mode else "live: OFF")
        if self.live_mode:
            self._busy_begin("live mode…")
            self._busy_end()

    def action_cycle_theme(self) -> None:
        order = ["cyber", "matrix", "dracula"]
        try:
            idx = order.index(self.theme_name)
        except ValueError:
            idx = 0
        self.theme_name = order[(idx + 1) % len(order)]
        self._apply_theme()
        self.query_one("#bar", CommandBar).set_status(f"theme: {self.theme_name}")

    def _apply_theme(self) -> None:
        # Toggle CSS classes on Screen.
        screen = self.screen
        screen.remove_class("cyber")
        screen.remove_class("matrix")
        screen.remove_class("dracula")
        screen.add_class(self.theme_name)

    def action_view_compact(self) -> None:
        self.view_mode = "compact"
        self._apply_view_mode()
        # Collapse all traces in compact mode
        self._trace_open = set()
        self._recompute()
        self.query_one("#bar", CommandBar).set_status("view: compact (winner only)")

    def action_view_detailed(self) -> None:
        self.view_mode = "detailed"
        self._apply_view_mode()
        # Expand all traces in detailed mode
        if self._last_verdict:
            self._trace_open = {m.value for m, _ in self._last_verdict.ranked}
        self._recompute()
        self.query_one("#bar", CommandBar).set_status("view: detailed (all expanded)")

    def action_view_focus(self) -> None:
        self.view_mode = "focus"
        self._apply_view_mode()
        # Focus mode: expand winner trace only
        if self._last_verdict:
            self._trace_open = {self._last_verdict.recommended.value}
        self._recompute()
        self.query_one("#bar", CommandBar).set_status("view: focus (trace only)")

    def action_view_default(self) -> None:
        self.view_mode = "default"
        self._apply_view_mode()
        # Reset to winner-expanded
        if self._last_verdict:
            self._trace_open = {self._last_verdict.recommended.value}
        self._recompute()
        self.query_one("#bar", CommandBar).set_status("view: default")

    def _apply_view_mode(self) -> None:
        screen = self.screen
        screen.remove_class("view_default")
        screen.remove_class("view_compact")
        screen.remove_class("view_detailed")
        screen.remove_class("view_focus")
        screen.add_class(f"view_{self.view_mode}")


def run() -> None:
    # Prevent Textual from running inside certain non-interactive contexts.
    os.environ.setdefault("TERM", os.environ.get("TERM", "xterm-256color"))
    AuthRefereeTextual().run()


if __name__ == "__main__":
    run()
