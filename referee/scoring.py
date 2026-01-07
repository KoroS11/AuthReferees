from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class ScoringResult:
    scores: dict[AuthMethod, float]
    reasons: dict[AuthMethod, list[str]]


BASE_SCORE = 5.0
MAX_SCORE = 10.0


def score_options(ctx: UserContext) -> ScoringResult:
    scores: dict[AuthMethod, float] = {m: BASE_SCORE for m in AuthMethod}
    reasons: dict[AuthMethod, list[str]] = {m: [f"Base score: {BASE_SCORE:.1f}"] for m in AuthMethod}

    def add(method: AuthMethod, delta: float, reason: str) -> None:
        scores[method] += delta
        sign = "+" if delta >= 0 else ""
        reasons[method].append(f"{sign}{delta:.1f}: {reason}")

    # Application Type
    if ctx.application_type == ApplicationType.WEB:
        add(AuthMethod.SESSIONS, 2.0, "Web application: sessions keep auth server-controlled with straightforward invalidation")
    if ctx.application_type == ApplicationType.API:
        add(AuthMethod.JWT, 2.0, "API-first: JWT enables stateless per-request authentication")
    if ctx.application_type == ApplicationType.MOBILE:
        add(AuthMethod.OAUTH2, 2.0, "Mobile-first: OAuth supports delegated identity flows suitable for native clients")
        add(AuthMethod.FIREBASE, 2.0, "Mobile-first: Firebase SDKs eliminate custom auth plumbing")

    # Scale
    if ctx.expected_users == ExpectedUsers.LT_1K:
        add(AuthMethod.SESSIONS, 2.0, "<1,000 users: sessions minimize operational overhead")
    if ctx.expected_users == ExpectedUsers.GTE_50K:
        add(AuthMethod.JWT, 2.0, "50,000+ users: JWT reduces shared-state pressure for horizontal scaling")
        add(AuthMethod.OAUTH2, 2.0, "50,000+ users: OAuth standardizes identity delegation across providers")

    # Security
    if ctx.security_sensitivity == SecuritySensitivity.HIGH:
        add(AuthMethod.OAUTH2, 2.0, "High sensitivity: OAuth supports mature access-control patterns and audited flows")
        add(AuthMethod.JWT, 2.0, "High sensitivity: JWT with strict claims/expiry supports strong controls")
    if ctx.security_sensitivity == SecuritySensitivity.LOW:
        add(AuthMethod.SESSIONS, 1.0, "Low sensitivity: sessions avoid token lifecycle surface area")

    # Backend
    if ctx.backend_architecture == BackendArchitecture.STATEFUL:
        add(AuthMethod.SESSIONS, 2.0, "Stateful backend: sessions align with server-managed state")
    if ctx.backend_architecture == BackendArchitecture.STATELESS:
        add(AuthMethod.JWT, 2.0, "Stateless backend: JWT preserves statelessness")

    # Team Level
    if ctx.team_experience == TeamExperience.BEGINNER:
        add(AuthMethod.SESSIONS, 2.0, "Beginner team: sessions reduce failure modes and debugging complexity")
        add(AuthMethod.FIREBASE, 2.0, "Beginner team: Firebase accelerates delivery with managed defaults")
    if ctx.team_experience == TeamExperience.ADVANCED:
        add(AuthMethod.JWT, 1.0, "Advanced team: can safely operate token rotation and claims")
        add(AuthMethod.OAUTH2, 1.0, "Advanced team: can safely operate OAuth flows and token lifecycles")

    # Social Login
    if ctx.social_login_required == SocialLoginRequired.YES:
        add(AuthMethod.OAUTH2, 3.0, "Social login required: OAuth is the standard provider integration model")
        add(AuthMethod.FIREBASE, 3.0, "Social login required: Firebase provides turn-key provider integrations")
    if ctx.social_login_required == SocialLoginRequired.NO:
        add(AuthMethod.OAUTH2, -2.0, "No social login: OAuth adds complexity without delegation needs")

    # Cap final scores so confidence never exceeds 100%.
    for method in list(scores.keys()):
        if scores[method] > MAX_SCORE:
            scores[method] = MAX_SCORE
            reasons[method].append(f"Capped at {MAX_SCORE:.1f}: confidence is limited to 100%")

    return ScoringResult(scores=scores, reasons=reasons)
