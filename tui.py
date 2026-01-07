from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from rich.columns import Columns
from rich.console import Console
from rich.console import Group
from rich.layout import Layout
from rich.rule import Rule
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
from rich import box

from referee.models import AuthMethod, UserContext
from referee.verdict import Verdict


@dataclass(frozen=True)
class Choice:
    key: str
    label: str


class TUI:
    def __init__(self) -> None:
        self.console = Console(highlight=False)

    def _ellipsize(self, s: str, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        s = " ".join(s.split())
        if len(s) <= max_chars:
            return s
        if max_chars <= 1:
            return "…"
        return s[: max_chars - 1] + "…"

    def header(self) -> None:
        # Dashboard-style header: no big framing, just a title line + thin rule.
        line = Text.assemble(
            ("AUTHREFEREE ⚖️", "bold"),
            ("  ", ""),
            ("deterministic • rule-based • no ML", "dim"),
        )
        self.console.print(line)
        self.console.print(Rule(style="dim"))

    def ask_choice(self, question: str, choices: Sequence[Choice]) -> str:
        keys = [c.key for c in choices]
        help_text = " / ".join([f"{c.key}:{c.label}" for c in choices])
        while True:
            self.console.print(Text(question, style="bold"))
            self.console.print(Text(help_text, style="dim"))
            raw = Prompt.ask("Select", choices=keys, default=keys[0])
            raw = raw.strip()
            if raw in keys:
                return raw
            self.console.print(Text("Invalid selection. Try again.", style="red"))

    def build_user_context_block(self, ctx: UserContext):
        grid = Table.grid(padding=(0, 2))
        grid.add_column(justify="left", style="dim", no_wrap=True)
        grid.add_column(justify="left", style="bold")
        grid.add_row("APP", ctx.application_type.value)
        grid.add_row("USERS", ctx.expected_users.value)
        grid.add_row("SEC", ctx.security_sensitivity.value)
        grid.add_row("BACKEND", ctx.backend_architecture.value)
        grid.add_row("TEAM", ctx.team_experience.value)
        grid.add_row("SOCIAL", ctx.social_login_required.value)
        return Group(Text("USER CONTEXT", style="bold dim"), grid)

    def build_verdict_block(self, verdict: Verdict):
        recommended_label = _method_label(verdict.recommended)
        kv = Table.grid(padding=(0, 1))
        kv.add_column(justify="left", style="dim", no_wrap=True)
        kv.add_column(justify="left")
        kv.add_row("WIN", Text(recommended_label, style="bold green"))
        kv.add_row("CONF", Text(f"{verdict.confidence_percent}%", style="bold"))
        if verdict.tie_break_applied and verdict.tie_break_summary:
            kv.add_row("TIE", Text(verdict.tie_break_summary, style="dim"))

        why = Table.grid(padding=(0, 1))
        why.add_column(justify="left", style="dim", no_wrap=True)
        why.add_column(justify="left")
        # Cap FIT lines to keep the center column tight and scannable.
        fit_max = max(28, int(self.console.size.width * 0.42) - 8)
        for i, line in enumerate(verdict.why_fits[:2], start=1):
            why.add_row(f"FIT{i}", self._ellipsize(line, fit_max))

        return Group(
            Text("FINAL VERDICT", style="bold dim"),
            kv,
            Rule(style="dim"),
            why,
        )

    def build_tradeoffs_block(self, verdict: Verdict):
        grid = Table.grid(padding=(0, 1))
        grid.add_column(justify="left", style="yellow", no_wrap=True)
        grid.add_column(justify="left")
        cost_max = max(28, int(self.console.size.width * 0.34) - 8)
        for i, line in enumerate(verdict.tradeoffs[:3], start=1):
            grid.add_row(f"C{i}", Text(self._ellipsize(line, cost_max), style="yellow"))
        return Group(Text("COSTS", style="bold dim"), grid)

    def build_decision_trace_block(self, verdict: Verdict, trace_lines: list[str], *, final_score: float):
        # Required audit-friendly format:
        # DECISION TRACE (X)
        # Base score: 5.0
        # +2.0  Rule
        # -----
        # Final score: 10.0
        base = 5.0
        deltas: list[tuple[str, str]] = []
        capped_note: str | None = None

        for line in trace_lines:
            if line.startswith("Base score"):
                try:
                    base = float(line.split(":", 1)[1].strip())
                except Exception:
                    base = 5.0
                continue
            if line.startswith("Capped at"):
                capped_note = line
                continue
            if ":" in line:
                left, right = line.split(":", 1)
                deltas.append((left.strip(), right.strip()))

        t = Table(
            show_header=False,
            box=None,
            pad_edge=False,
            collapse_padding=True,
        )
        t.add_column("k", style="dim", no_wrap=True)
        trace_max = max(32, int(self.console.size.width * 0.40) - 8)
        t.add_column("v", overflow="ellipsis", no_wrap=True, max_width=trace_max)

        t.add_row("BASE", f"{base:.1f}")
        for delta, reason in deltas[:6]:
            t.add_row(delta, reason)
        t.add_row("----", "")
        t.add_row("FINAL", Text(f"{final_score:.1f}", style="bold"))
        if capped_note:
            t.add_row("CAP", Text(capped_note, style="dim"))

        return Group(Text(f"TRACE ({_method_label(verdict.recommended)})", style="bold dim"), t)

    def build_breaks_block(self, verdict: Verdict):
        grid = Table.grid(padding=(0, 1))
        grid.add_column(justify="left", style="red", no_wrap=True)
        grid.add_column(justify="left")
        break_max = max(28, int(self.console.size.width * 0.34) - 8)
        for i, line in enumerate(verdict.break_conditions[:3], start=1):
            grid.add_row(f"B{i}", Text(self._ellipsize(line, break_max), style="red"))
        return Group(Text("BREAKS", style="bold dim"), grid)

    def build_comparison_table(self, rows: list[tuple[AuthMethod, float, str]], *, recommended: AuthMethod) -> Table:
        # Adapt the "Primary Reason" width to the current terminal to avoid tall tables.
        # Keep it deterministic and minimal.
        # Dashboard table: borderless and compact so all 4 methods fit in a short strip.
        reason_max_width = max(18, min(60, self.console.width - 24))
        table = Table(
            show_lines=False,
            show_edge=False,
            header_style="bold dim",
            box=None,
            pad_edge=False,
            collapse_padding=True,
        )
        table.add_column("M", no_wrap=True)
        table.add_column("S", justify="right", no_wrap=True)
        table.add_column("FIT", no_wrap=True)
        table.add_column("WHY", overflow="ellipsis", no_wrap=True, max_width=reason_max_width)

        for method, score, primary_reason in rows:
            style = "green" if method == recommended else None
            table.add_row(
                Text(_method_label(method), style=("bold green" if method == recommended else "bold")),
                Text(f"{score:.1f}", style=("bold green" if method == recommended else "")),
                _fit_label(score),
                primary_reason,
                style=style,
            )
        return table

    def build_rejections_block(self, verdict: Verdict):
        max_reason_width = max(24, min(52, self.console.width // 3))
        t = Table(show_header=False, box=None, pad_edge=False, collapse_padding=True)
        t.add_column("LOSER", style="bold red", no_wrap=True)
        t.add_column("WHY", overflow="ellipsis", no_wrap=True, max_width=max_reason_width)
        for method, _score in verdict.ranked:
            if method == verdict.recommended:
                continue
            bullets = verdict.rejections.get(method, [])[:1]
            reason = bullets[0] if bullets else "Lower fit under current constraints"
            t.add_row(_method_label(method), Text(reason, style="red"))
        return Group(Text("LOSERS", style="bold dim"), t)

    def build_referee_note_block(self):
        return Text("note: deterministic • rule-weighted • explainable", style="dim")

    def render_verdict_screen(
        self,
        *,
        user_context_block,
        verdict_block,
        decision_trace_block,
        tradeoffs_block,
        breaks_block,
        comparison_table: Table,
        rejections_block,
        referee_note_block,
    ) -> None:
        self.console.clear()
        term_w = self.console.size.width
        term_h = self.console.size.height
        root = Layout(name="root")

        # Header (2 lines), 3-column body, bottom strip (comparison + note + shortcuts).
        # The bottom strip is sized to keep everything visible without scrolling.
        bottom_h = 11 if term_h >= 32 else 10
        root.split_column(
            Layout(name="header", size=2),
            Layout(name="body", ratio=1),
            Layout(name="bottom", size=bottom_h),
        )

        root["header"].update(Group(
            Text.assemble(("AUTHREFEREE ⚖️", "bold"), ("  ", ""), ("auth decision console", "dim")),
            Rule(style="dim"),
        ))

        root["body"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="center", ratio=2),
            Layout(name="right", ratio=2),
        )

        left = Group(user_context_block)
        center = Group(verdict_block, Rule(style="dim"), decision_trace_block)
        right = Group(tradeoffs_block, Rule(style="dim"), breaks_block, Rule(style="dim"), rejections_block)

        root["body"]["left"].update(left)
        root["body"]["center"].update(center)
        root["body"]["right"].update(right)

        root["bottom"].update(
            Group(
                Text("COMPARISON", style="bold dim"),
                comparison_table,
                referee_note_block,
                Text("r restart   q quit   ? help", style="dim"),
            )
        )

        self.console.print(root)


def _fit_label(score: float) -> str:
    if score >= 9.0:
        return "Excellent"
    if score >= 7.0:
        return "Good"
    if score >= 5.0:
        return "Acceptable"
    return "Poor"


def make_bullets(lines: Iterable[str], *, style: str | None = None) -> Text:
    text = Text()
    for i, line in enumerate(lines):
        if i:
            text.append("\n")
        text.append("• ")
        text.append(line)
    if style:
        text.stylize(style)
    return text


def _method_label(method: AuthMethod) -> str:
    return {
        AuthMethod.SESSIONS: "Sessions",
        AuthMethod.JWT: "JWT",
        AuthMethod.OAUTH2: "OAuth 2.0",
        AuthMethod.FIREBASE: "Firebase",
    }[method]
