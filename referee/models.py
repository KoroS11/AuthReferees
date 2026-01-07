from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ApplicationType(str, Enum):
    WEB = "Web"
    MOBILE = "Mobile"
    API = "API"


class ExpectedUsers(str, Enum):
    LT_1K = "< 1,000"
    BTW_1K_50K = "1,000–50,000"
    GTE_50K = "50,000+"


class SecuritySensitivity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class BackendArchitecture(str, Enum):
    STATEFUL = "Stateful"
    STATELESS = "Stateless"


class TeamExperience(str, Enum):
    BEGINNER = "Beginner"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class SocialLoginRequired(str, Enum):
    YES = "Yes"
    NO = "No"


class AuthMethod(str, Enum):
    SESSIONS = "Session-based Authentication"
    JWT = "JWT-based Authentication"
    OAUTH2 = "OAuth 2.0"
    FIREBASE = "Firebase Authentication"


@dataclass(frozen=True)
class UserContext:
    application_type: ApplicationType
    expected_users: ExpectedUsers
    security_sensitivity: SecuritySensitivity
    backend_architecture: BackendArchitecture
    team_experience: TeamExperience
    social_login_required: SocialLoginRequired
