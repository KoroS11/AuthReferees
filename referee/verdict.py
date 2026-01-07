from __future__ import annotations

from dataclasses import dataclass

from referee.models import AuthMethod, SocialLoginRequired, UserContext
from referee.scoring import ScoringResult


@dataclass(frozen=True)
class Verdict:
    recommended: AuthMethod
    ranked: list[tuple[AuthMethod, float]]
    confidence_percent: int
    why_fits: list[str]
    tradeoffs: list[str]
    rejections: dict[AuthMethod, list[str]]
    tie_break_applied: bool
    tie_break_summary: str | None
    break_conditions: list[str]


# Tie-breakers (lower is better)
_IMPLEMENTATION_COMPLEXITY: dict[AuthMethod, int] = {
    AuthMethod.SESSIONS: 1,
    AuthMethod.JWT: 2,
    AuthMethod.FIREBASE: 2,
    AuthMethod.OAUTH2: 3,
}

_LONG_TERM_RISK: dict[AuthMethod, int] = {
    AuthMethod.SESSIONS: 2,
    AuthMethod.JWT: 3,
    AuthMethod.OAUTH2: 2,
    AuthMethod.FIREBASE: 4,
}


def _break_ties(candidates: list[AuthMethod]) -> AuthMethod:
    candidates_sorted = sorted(
        candidates,
        key=lambda m: (_IMPLEMENTATION_COMPLEXITY[m], _LONG_TERM_RISK[m]),
    )
    return candidates_sorted[0]


def build_verdict(ctx: UserContext, scoring: ScoringResult) -> Verdict:
    ranked = sorted(scoring.scores.items(), key=lambda kv: kv[1], reverse=True)
    top_score = ranked[0][1]
    top_methods = [m for (m, s) in ranked if s == top_score]
    recommended = _break_ties(top_methods)
    tie_break_applied = len(top_methods) > 1

    tie_break_summary: str | None = None
    if tie_break_applied:
        others = [m for m in top_methods if m != recommended]
        label = {
            AuthMethod.SESSIONS: "Sessions",
            AuthMethod.JWT: "JWT",
            AuthMethod.OAUTH2: "OAuth 2.0",
            AuthMethod.FIREBASE: "Firebase",
        }
        other_short = ", ".join([label[m] for m in others])
        tie_break_summary = (
            f"Tie-break: {label[recommended]} over {other_short} "
            f"(complexity→risk)."
        )

    confidence = int(round((top_score / 10.0) * 100))

    why_fits = _build_why_fits(recommended, scoring, tie_break_applied=tie_break_applied)
    tradeoffs = _build_tradeoffs(recommended, ctx)
    rejections = _build_rejections(recommended, ctx)
    break_conditions = _build_break_conditions(recommended, ctx)

    return Verdict(
        recommended=recommended,
        ranked=ranked,
        confidence_percent=confidence,
        why_fits=why_fits,
        tradeoffs=tradeoffs,
        rejections=rejections,
        tie_break_applied=tie_break_applied,
        tie_break_summary=tie_break_summary,
        break_conditions=break_conditions,
    )


def _build_why_fits(
    recommended: AuthMethod,
    scoring: ScoringResult,
    *,
    tie_break_applied: bool,
) -> list[str]:
    # Take up to 5 strongest adjustments (by absolute value), excluding base.
    items: list[tuple[float, str]] = []
    for line in scoring.reasons.get(recommended, []):
        if line.startswith("Base score"):
            continue
        if line.startswith("Capped at"):
            continue
        # Parse the numeric prefix like "+2.0:" or "-2.0:"
        try:
            prefix = line.split(":", 1)[0]
            delta = float(prefix)
        except Exception:
            delta = 0.0
        reason = line.split(": ", 1)[1] if ": " in line else line
        items.append((abs(delta), reason))

    items.sort(key=lambda x: x[0], reverse=True)
    bullets = [r for _, r in items[:4]]

    if tie_break_applied and len(bullets) < 4:
        bullets.append("Tie-break applied: lowest implementation complexity, then lowest long-term risk")

    # Ensure 4–5 bullets.
    fallbacks = [
        "Overall best match across your constraints",
        "Lowest conflict with your architecture and client type",
        "Operational overhead is proportional to your needs",
        "Keeps implementation complexity aligned with team capability",
    ]
    for fb in fallbacks:
        if len(bullets) >= 4:
            break
        if fb not in bullets:
            bullets.append(fb)
    return bullets[:4]


def _build_tradeoffs(recommended: AuthMethod, ctx: UserContext) -> list[str]:
    # Keep to 2–3 bullets, contextual and concrete.
    if recommended == AuthMethod.SESSIONS:
        tradeoffs = [
            "Scaling cost rises when you need shared session storage and sticky-session avoidance",
            "Session/cookie security hardens over time (CSRF, SameSite, rotation) as the system grows",
        ]
        return tradeoffs[:3]

    if recommended == AuthMethod.JWT:
        tradeoffs = [
            "Immediate revocation is not free; rotation, deny-lists, and incident response add operational load",
            "Bugs in token storage/expiry/claims fail open at scale and are expensive to unwind",
        ]
        return tradeoffs[:3]

    if recommended == AuthMethod.OAUTH2:
        tradeoffs = [
            "Operational surface area increases (providers, scopes, redirects, refresh tokens, callback security)",
            "Misconfiguration risk is real; hardening and audits become a recurring cost",
        ]
        return tradeoffs[:3]

    # Firebase
    tradeoffs = [
        "Migration cost increases sharply if auth rules outgrow Firebase’s policy and identity model",
        "Edge-case control is constrained; custom flows often require workarounds or additional services",
    ]
    return tradeoffs[:3]


def _build_break_conditions(recommended: AuthMethod, ctx: UserContext) -> list[str]:
    # Three concrete triggers that would change the recommendation.
    if recommended == AuthMethod.SESSIONS:
        return [
            "User base exceeds ~50k and horizontal scaling demands stateless services",
            "Clients shift to API-first/mobile-first where cookie sessions become a liability",
            "Security requirements move to fine-grained delegated access (third-party identity)",
        ]

    if recommended == AuthMethod.JWT:
        return [
            "Immediate per-user revocation and session invalidation become mandatory",
            "Security posture requires centralized policy decisions per request (beyond JWT claims)",
            "User authentication becomes provider-driven (social login / third-party delegation)",
        ]

    if recommended == AuthMethod.OAUTH2:
        return [
            "Social login is removed; first-party auth becomes the dominant requirement",
            "Operational complexity must be minimized due to team or compliance constraints",
            "You need strict server-side session invalidation semantics across services",
        ]

    # Firebase
    return [
        "User base exceeds ~50k and identity must integrate with custom platform services",
        "Custom identity policies become mandatory (tenant isolation, bespoke MFA, complex RBAC)",
        "Backend transitions to fully stateless microservices requiring centralized policy control",
    ]


def _build_rejections(recommended: AuthMethod, ctx: UserContext) -> dict[AuthMethod, list[str]]:
    # Exactly 2 bullets for each non-recommended option.
    rejections: dict[AuthMethod, list[str]] = {}

    def reject(method: AuthMethod, bullets: list[str]) -> None:
        if method != recommended:
            rejections[method] = bullets[:2]

    reject(AuthMethod.SESSIONS, _session_rejection(ctx))
    reject(AuthMethod.JWT, _jwt_rejection(ctx))
    reject(AuthMethod.OAUTH2, _oauth_rejection(ctx))
    reject(AuthMethod.FIREBASE, _firebase_rejection(ctx))

    return rejections


def _session_rejection(ctx: UserContext) -> list[str]:
    bullets: list[str] = []
    if ctx.backend_architecture.value == "Stateless":
        bullets.append("Your backend is stateless; server-side sessions reintroduce shared state")
    if ctx.application_type.value in ("API", "Mobile"):
        bullets.append("Token-first clients (API/mobile) typically fit better than cookie sessions")
    if not bullets:
        bullets.append("Sessions are less portable across services than token-based approaches")
    bullets.append("Scaling often requires shared session storage and careful cookie security")
    return bullets[:2]


def _jwt_rejection(ctx: UserContext) -> list[str]:
    bullets: list[str] = []
    if ctx.social_login_required == SocialLoginRequired.YES:
        bullets.append("Social login is required; JWT alone does not provide identity delegation")
    if ctx.application_type.value == "Web" and ctx.backend_architecture.value == "Stateful":
        bullets.append("For a stateful web app, sessions often achieve the same goals with less token overhead")
    if not bullets:
        bullets.append("Revocation/rotation and claim design add ongoing operational complexity")
    bullets.append("Security depends heavily on correct token storage, expiry, and audience handling")
    return bullets[:2]


def _oauth_rejection(ctx: UserContext) -> list[str]:
    if ctx.social_login_required == SocialLoginRequired.NO:
        return [
            "No social login requirement, so OAuth complexity is hard to justify",
            "Redirect/callback flows (and scope/consent handling) increase maintenance cost",
        ]

    if ctx.team_experience.value == "Beginner":
        return [
            "OAuth fits social login, but it is easy to misconfigure without experience",
            "Flow selection, token handling, and callback security add implementation risk",
        ]

    return [
        "OAuth is strong for delegated identity, but adds more moving parts than simpler methods",
        "You pay ongoing complexity (providers, scopes, refresh tokens) even when requirements are stable",
    ]


def _firebase_rejection(ctx: UserContext) -> list[str]:
    bullets: list[str] = []
    if ctx.application_type.value != "Mobile":
        bullets.append("Firebase Auth shines on mobile; for non-mobile apps it can be an unnecessary dependency")
    if ctx.team_experience.value == "Advanced":
        bullets.append("With an advanced team, you may prefer full control over identity and infrastructure")
    if not bullets:
        bullets.append("Vendor dependency and migration cost if requirements outgrow Firebase")
    bullets.append("Less flexibility for custom policies compared to fully managed in-house auth")
    return bullets[:2]
