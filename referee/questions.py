from __future__ import annotations

from referee.models import (
    ApplicationType,
    BackendArchitecture,
    ExpectedUsers,
    SecuritySensitivity,
    SocialLoginRequired,
    TeamExperience,
    UserContext,
)
from tui import Choice, TUI


def collect_user_context(tui: TUI) -> UserContext:
    # Ask in the exact required order.

    app_type_key = tui.ask_choice(
        "Application type",
        [
            Choice("1", ApplicationType.WEB.value),
            Choice("2", ApplicationType.MOBILE.value),
            Choice("3", ApplicationType.API.value),
        ],
    )
    application_type = {
        "1": ApplicationType.WEB,
        "2": ApplicationType.MOBILE,
        "3": ApplicationType.API,
    }[app_type_key]

    users_key = tui.ask_choice(
        "Expected number of users",
        [
            Choice("1", ExpectedUsers.LT_1K.value),
            Choice("2", ExpectedUsers.BTW_1K_50K.value),
            Choice("3", ExpectedUsers.GTE_50K.value),
        ],
    )
    expected_users = {
        "1": ExpectedUsers.LT_1K,
        "2": ExpectedUsers.BTW_1K_50K,
        "3": ExpectedUsers.GTE_50K,
    }[users_key]

    sec_key = tui.ask_choice(
        "Security sensitivity",
        [
            Choice("1", SecuritySensitivity.LOW.value),
            Choice("2", SecuritySensitivity.MEDIUM.value),
            Choice("3", SecuritySensitivity.HIGH.value),
        ],
    )
    security_sensitivity = {
        "1": SecuritySensitivity.LOW,
        "2": SecuritySensitivity.MEDIUM,
        "3": SecuritySensitivity.HIGH,
    }[sec_key]

    backend_key = tui.ask_choice(
        "Backend architecture",
        [
            Choice("1", BackendArchitecture.STATEFUL.value),
            Choice("2", BackendArchitecture.STATELESS.value),
        ],
    )
    backend_architecture = {
        "1": BackendArchitecture.STATEFUL,
        "2": BackendArchitecture.STATELESS,
    }[backend_key]

    team_key = tui.ask_choice(
        "Team experience",
        [
            Choice("1", TeamExperience.BEGINNER.value),
            Choice("2", TeamExperience.INTERMEDIATE.value),
            Choice("3", TeamExperience.ADVANCED.value),
        ],
    )
    team_experience = {
        "1": TeamExperience.BEGINNER,
        "2": TeamExperience.INTERMEDIATE,
        "3": TeamExperience.ADVANCED,
    }[team_key]

    social_key = tui.ask_choice(
        "Social login required",
        [
            Choice("1", SocialLoginRequired.YES.value),
            Choice("2", SocialLoginRequired.NO.value),
        ],
    )
    social_login_required = {
        "1": SocialLoginRequired.YES,
        "2": SocialLoginRequired.NO,
    }[social_key]

    return UserContext(
        application_type=application_type,
        expected_users=expected_users,
        security_sensitivity=security_sensitivity,
        backend_architecture=backend_architecture,
        team_experience=team_experience,
        social_login_required=social_login_required,
    )
