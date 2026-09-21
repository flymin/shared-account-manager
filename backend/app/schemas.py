from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .password_policy import MIN_LENGTH, MAX_LENGTH
from .plugins import default_mail_tool

NewPassword = Annotated[str, Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class LoginInput(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class PasswordInput(Input):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: NewPassword


class UserInput(Input):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.@-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    password: NewPassword
    role: Literal["admin", "user"] = "user"
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    claim_limit: int | None = Field(default=None, ge=1, le=1000)


class UserPatch(Input):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: Literal["admin", "user"] | None = None
    group_ids: list[str] | None = Field(default=None, max_length=100)
    claim_limit: int | None = Field(default=None, ge=1, le=1000)
    password: NewPassword | None = None


class GroupInput(Input):
    name: str = Field(min_length=1, max_length=100)
    user_ids: list[str] | None = Field(default=None, max_length=10000)


class SettingsInput(Input):
    user_claim_limit: int = Field(ge=1, le=1000)
    account_capacity: int = Field(ge=1, le=1000)
    observation_hours: int = Field(ge=1, le=8760)
    cooldown_hours: int = Field(ge=1, le=8760)
    session_days: int = Field(ge=1, le=90)
    quota_depleted_threshold: int = Field(default=5, ge=1, le=100, strict=True)
    email_code_timeout_minutes: int = Field(default=5, ge=1, le=30, strict=True)
    quota_reset_interval_days: int = Field(default=7, ge=1, le=365, strict=True)


class TimeInput(Input):
    @field_validator("expires_at", "reset_at", check_fields=False)
    @classmethod
    def aware(cls, v):
        if v is not None and v.tzinfo is None:
            raise ValueError("时间必须包含时区")
        return v


class ImportInput(TimeInput):
    text: str = Field(min_length=1, max_length=500000)
    tier: Literal["5x", "20x"]
    mail_tool: str | None = Field(
        default_factory=default_mail_tool, min_length=1, max_length=64
    )
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    user_ids: list[str] = Field(default_factory=list, max_length=1000)
    expires_at: datetime | None = None
    capacity: int | None = Field(default=None, ge=1, le=1000)
    quota_reset_interval_days: int | None = Field(
        default=None, ge=1, le=365, strict=True
    )


class AccountPatch(TimeInput):
    tier: Literal["5x", "20x"] | None = None
    mail_tool: str | None = Field(default=None, min_length=1, max_length=64)
    group_ids: list[str] | None = Field(default=None, max_length=100)
    user_ids: list[str] | None = Field(default=None, max_length=1000)
    expires_at: datetime | None = None
    capacity: int | None = Field(default=None, ge=1, le=1000)
    quota_reset_interval_days: int | None = Field(
        default=None, ge=1, le=365, strict=True
    )
    password: str | None = Field(default=None, min_length=1, max_length=256)
    auth_password: str | None = Field(default=None, min_length=1, max_length=256)


class ClaimInput(Input):
    account_id: str
    acknowledge_warning: bool = False


class ActivationInput(Input):
    enabled: bool = Field(strict=True)


class QuotaInput(TimeInput):
    quota: int = Field(ge=0, le=100)
    reset_at: datetime | None = None


class HealthInput(Input):
    action: Literal["report", "clear"]
    categories: list[Literal["at capacity", "降智"]] = Field(
        default_factory=list, max_length=2
    )
    note: str = Field(default="", max_length=2000)
    version: int = Field(ge=0)


class ReturnInput(TimeInput):
    kind: Literal["normal", "abnormal", "acknowledge"]
    quota: int | None = Field(default=None, ge=0, le=100)
    reset_at: datetime | None = None
    categories: list[Literal["at capacity", "降智"]] = Field(
        default_factory=list, max_length=2
    )
    note: str = Field(default="", max_length=2000)
    health_action: Literal["maintain", "clear"] | None = None
    health_version: int | None = None


class RevokeInput(Input):
    reason: str = Field(min_length=1, max_length=2000)
