from __future__ import annotations

import datetime as _dt
import os
import secrets
import textwrap
import time
import math
import asyncio
from dataclasses import dataclass

import psutil
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.widgets import Button, Checkbox, DataTable, Input, Label, Select, Static

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
        self.query_one("#export_name", Input).value = "session_export"
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
    """Left panel: context & configuration inputs."""

    def compose(self) -> ComposeResult:
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


class TracePanel(Static):
    """Center bottom: decision tree-ish trace (flattened, colored deltas)."""

    def update_from(self, ctx: UserContext, *, scoring: ScoringResult, verdict: Verdict) -> None:
        if bool(getattr(self.app, "_busy_visible", False)):
            self._render_skeleton()
            return
        score_overrides: dict[str, float] = dict(getattr(self.app, "_anim_current_scores", {}) or {})
        flash_keys = set(getattr(self.app, "_active_flash_keys", lambda: set())())

        view_mode = str(getattr(self.app, "view_mode", "default") or "default")
        compact = view_mode == "compact"

        def render_method_trace(method_value: str, score: float, is_win: bool) -> Text:
            lines = scoring.reasons.get(method_value, [])
            base = "5.0"
            deltas: list[str] = []
            cap_line: str | None = None

            for line in lines:
                if line.startswith("Base score"):
                    base = line.split(":", 1)[1].strip()
                    continue
                if line.startswith("Capped at"):
                    cap_line = line
                    continue
                deltas.append(line)

            out = Text()
            title_style = "bold #00FF41" if is_win else "bold #A0A0A0"
            out.append(_method_short(method_value), style=title_style)
            out.append("  ", style="dim")
            key = f"score:{method_value}"
            flash = key in flash_keys
            score_style = ("bold #00FF41" if is_win else "bold")
            if flash:
                score_style = (score_style + " " + _flash_style(True)).strip()
            out.append(f"{score:.1f}", style=score_style)
            out.append("  ", style="dim")
            out.append("(SELECTED)" if is_win else "(REJECTED)", style=("bold #00FF41" if is_win else "bold #FF1F7E"))
            out.append("\n")
            out.append(f"┗━━ BASE {base}\n", style="bold #00D9FF")

            for d in deltas:
                left, right = d.split(":", 1)
                left = left.strip()
                right = right.strip()
                style = "green" if left.startswith("+") else "red" if left.startswith("-") else "yellow"
                arrow = "↑" if left.startswith("+") else "↓" if left.startswith("-") else "·"
                out.append("   ┣━━ ")
                out.append(f"{arrow} {left}", style=f"bold {style}")
                out.append(" ")
                out.append(right)
                out.append("\n")

            out.append("   ┗━━ FINAL ", style="bold #FFFFFF")
            final_style = ("bold #00FF41" if is_win else "bold #FFFFFF")
            if flash:
                final_style = (final_style + " " + _flash_style(True)).strip()
            out.append(f"{score:.1f}", style=final_style)
            out.append("\n")
            if cap_line:
                out.append("      ")
                out.append(cap_line, style="dim")
                out.append("\n")
            return out

        t = Text()
        # Show the full scoring trace for every option (ranked), not just the winner.
        ranked = verdict.ranked
        if compact:
            ranked = [(verdict.recommended, scoring.scores[verdict.recommended])]

        for i, (method, score) in enumerate(ranked):
            if i:
                t.append("\n")
                t.append(Text("─" * max(10, (self.size.width or 60) - 2) + "\n", style="dim"))
                t.append("\n")
            shown = float(score_overrides.get(method.value, score))
            t.append(render_method_trace(method.value, shown, method == verdict.recommended))

        self.update(t)

    def _render_skeleton(self) -> None:
        phase = int(getattr(self.app, "_pulse_phase", 0) or 0)
        w = max(20, self.size.width or 60)
        # A few stable placeholder blocks with shimmer.
        t = Text()
        t.append("LOADING TRACE\n", style="bold dim")
        for _i, pct in enumerate((82.0, 68.0, 76.0, 54.0, 64.0), start=1):
            bar = _bar_shimmer(pct, max(10, min(40, w - 6)), phase)
            t.append("  ", style="dim")
            t.append(bar, style="dim #00D9FF")
            t.append("\n")
        self.update(t)


class RightPanel(Static):
    """Right: costs + breaks + losers."""

    def _boxed(self, title: str, body_lines: list[str], *, icon: str, color: str) -> Text:
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
        for raw in body_lines:
            wrapped = textwrap.wrap(raw, width=inner - 4) or [""]
            for wline in wrapped:
                line = f"{icon} {wline}".ljust(inner)[:inner]
                out.append("║" + line + "║\n", style=color)
        out.append(bot + "\n", style=f"bold {color}")
        return out

    def update_from(self, ctx: UserContext, *, verdict: Verdict) -> None:
        if bool(getattr(self.app, "_busy_visible", False)):
            self._render_skeleton()
            return

        t = Text()
        t.append(self._boxed("COSTS", verdict.tradeoffs[:3], icon="⚠", color="#FFB000"))
        t.append("\n")
        t.append(self._boxed("BREAKS", verdict.break_conditions[:3], icon="⛔", color="#FF1F7E"))

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
        t.append(self._boxed("REFERENCES", refs, icon="ℹ", color="#00D9FF"))

        t.append("\nLOSERS\n", style="bold red")
        for method, _score in verdict.ranked:
            if method == verdict.recommended:
                continue
            bullets = verdict.rejections.get(method, [])[:1]
            reason = bullets[0] if bullets else "Lower fit"
            t.append(f"{_method_short(method.value)}: ", style="bold red")
            t.append(reason, style="red")
            t.append("\n")

        self.update(t)

    def _render_skeleton(self) -> None:
        phase = int(getattr(self.app, "_pulse_phase", 0) or 0)
        # Keep the same boxed style, but use placeholder lines.
        def ph(width: int, pct: float) -> str:
            return _bar_shimmer(pct, width, phase)

        t = Text()
        t.append(self._boxed("COSTS", ["loading…", ph(18, 72.0), ph(18, 58.0)], icon="⚠", color="#FFB000"))
        t.append("\n")
        t.append(self._boxed("BREAKS", ["loading…", ph(18, 66.0), ph(18, 52.0)], icon="⛔", color="#FF1F7E"))
        t.append("\n")
        t.append(self._boxed("REFERENCES", ["loading…", ph(18, 62.0)], icon="ℹ", color="#00D9FF"))
        t.append("\nLOSERS\n", style="bold red")
        t.append("loading…\n", style="dim")
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

    _typing_target: str = ""
    _typing_i: int = 0
    _typing_timer = None

    def compose(self) -> ComposeResult:
        yield Label("λ", id="prompt")
        yield Input(placeholder="commands: r(reset)  q(quit)  ?(help)", id="cmd")
        yield Label("", id="status")

    def on_mount(self) -> None:
        self.query_one("#cmd", Input).focus()
        self._render_status()

    def _render_status(self) -> None:
        # Render with a simple typing cursor while animating.
        shown = self.status
        if self._typing_target and self._typing_i < len(self._typing_target):
            shown = self._typing_target[: self._typing_i] + "▌"
        self.query_one("#status", Label).update(Text(shown, style="dim"))

    def set_status(self, s: str) -> None:
        self._typing_target = s
        self._typing_i = 0

        # Restart typing timer.
        if self._typing_timer is not None:
            try:
                self._typing_timer.stop()
            except Exception:
                pass

        def tick() -> None:
            if self._typing_i >= len(self._typing_target):
                self.status = self._typing_target
                if self._typing_timer is not None:
                    try:
                        self._typing_timer.stop()
                    except Exception:
                        pass
                self._render_status()
                return
            self._typing_i += 1
            self._render_status()

        # Fast enough to feel snappy.
        self._typing_timer = self.set_interval(0.02, tick)
        self._render_status()
        self._render_status()


class AuthRefereeTextual(App):
    CSS_PATH = "textual_app.tcss"

    live_mode: reactive[bool] = reactive(False)
    live_countdown: reactive[int] = reactive(0)
    theme_name: reactive[str] = reactive("cyber")
    view_mode: reactive[str] = reactive("default")

    _intro_done: bool = False
    _live_pulse: int = 0
    _pulse_phase: int = 0

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
                with VerticalScroll(id="trace_scroll"):
                    yield TracePanel(id="trace")
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

        # Apply initial theme/view.
        self._apply_theme()
        self._apply_view_mode()

        # Run staged intro.
        await self._run_intro()
        self._recompute()

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
        for pid in ("#left", "#center", "#right_wrap", "#compare"):
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
        w.update(Text(f"{ch} {self._busy_label}", style="bold #FFB000"))
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
        msg = "OAUTH"
        cur = ""
        for ch in msg:
            cur += ch
            boot.update(Text(cur, style="bold #00D9FF"))
            await asyncio.sleep(0.04)
        await asyncio.sleep(0.12)
        boot.remove_class("show")
        boot.update("")

        # Header slides down.
        top.styles.offset = (0, -2)
        top.styles.opacity = 0
        await top.animate("opacity", 1.0, duration=0.20, easing="in_out_cubic")
        await top.animate("offset", (0, 0), duration=0.22, easing="in_out_cubic")

        # Scanline
        await scan.animate("opacity", 1.0, duration=0.18, easing="in_out_cubic")

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

        await left.animate("opacity", 1.0, duration=0.22, easing="in_out_cubic")
        await left.animate("offset", (0, 0), duration=0.25, easing="in_out_cubic")
        await center.animate("opacity", 1.0, duration=0.22, easing="in_out_cubic")
        await center.animate("offset", (0, 0), duration=0.25, easing="in_out_cubic")
        await right.animate("opacity", 1.0, duration=0.22, easing="in_out_cubic")
        await right.animate("offset", (0, 0), duration=0.25, easing="in_out_cubic")

        # Comparison fades in.
        await compare_title.animate("opacity", 1.0, duration=0.18, easing="in_out_cubic")
        await compare.animate("opacity", 1.0, duration=0.18, easing="in_out_cubic")
        await bar.animate("opacity", 1.0, duration=0.18, easing="in_out_cubic")

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
        def update_pair(scroll_id: str, top_id: str, bot_id: str) -> None:
            try:
                s = self.query_one(scroll_id, VerticalScroll)
                top = self.query_one(top_id, Static)
                bot = self.query_one(bot_id, Static)
            except Exception:
                return

            y = int(getattr(s, "scroll_y", 0) or 0)
            max_y = int(getattr(s, "max_scroll_y", 0) or 0)
            above = max(0, y)
            below = max(0, max_y - y)

            top.update(Text(f"▲▲▲ {above} above ▲▲▲" if above else "", style="dim #00D9FF"))
            bot.update(Text(f"▼▼▼ {below} below ▼▼▼" if below else "", style="dim #00D9FF"))

        update_pair("#trace_scroll", "#trace_up", "#trace_down")
        update_pair("#right_scroll", "#right_up", "#right_down")

    def _ctx_from_controls(self) -> UserContext:
        app = self.query_one("#app", Select).value
        users = self.query_one("#users", Select).value
        sec = self.query_one("#sec", Select).value
        backend = self.query_one("#backend", Select).value
        team = self.query_one("#team", Select).value
        social = self.query_one("#social", Select).value

        # Map values back to enums using their .value strings.
        return UserContext(
            application_type=ApplicationType(app),
            expected_users=ExpectedUsers(users),
            security_sensitivity=SecuritySensitivity(sec),
            backend_architecture=BackendArchitecture(backend),
            team_experience=TeamExperience(team),
            social_login_required=SocialLoginRequired(social),
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
        self.query_one("#trace", TracePanel).update_from(ctx, scoring=scoring, verdict=verdict)
        self.query_one("#right", RightPanel).update_from(ctx, verdict=verdict)
        self.query_one("#compare", ComparisonTable).update_from(ctx, scoring=scoring, verdict=verdict, rows=rows)

    @on(Select.Changed)
    def _on_select_changed(self, _event: Select.Changed) -> None:
        self._busy_begin("recomputing…")
        try:
            self._recompute()
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
        self.query_one("#bar", CommandBar).set_status("view: compact")

    def action_view_detailed(self) -> None:
        self.view_mode = "detailed"
        self._apply_view_mode()
        self.query_one("#bar", CommandBar).set_status("view: detailed")

    def action_view_focus(self) -> None:
        self.view_mode = "focus"
        self._apply_view_mode()
        self.query_one("#bar", CommandBar).set_status("view: focus")

    def action_view_default(self) -> None:
        self.view_mode = "default"
        self._apply_view_mode()
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
