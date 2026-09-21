import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Integer,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Index,
    CheckConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(10), default="user")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    claim_limit: Mapped[int | None] = mapped_column(Integer)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("role IN ('admin','user')"),
        CheckConstraint("claim_limit IS NULL OR claim_limit > 0"),
    )


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class UserGroup(Base):
    __tablename__ = "user_groups"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_encrypted: Mapped[str] = mapped_column(Text)
    auth_password_encrypted: Mapped[str] = mapped_column(Text)
    mail_tool: Mapped[str | None] = mapped_column(String(64))
    mail_config_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    tier: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int | None] = mapped_column(Integer)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    quota: Mapped[int | None] = mapped_column(Integer)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_reset_interval_days: Mapped[int | None] = mapped_column(Integer)
    quota_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_source: Mapped[str | None] = mapped_column(String(20))
    quota_version: Mapped[int] = mapped_column(Integer, default=0)
    health: Mapped[str] = mapped_column(String(30), default="normal")
    health_categories: Mapped[list] = mapped_column(JSONB, default=list)
    health_note: Mapped[str] = mapped_column(Text, default="")
    anomaly_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_version: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        CheckConstraint("tier IN ('5x','20x')"),
        CheckConstraint("capacity IS NULL OR capacity > 0"),
        CheckConstraint("quota IS NULL OR quota BETWEEN 0 AND 100"),
        CheckConstraint(
            "quota_reset_interval_days IS NULL OR quota_reset_interval_days BETWEEN 1 AND 365",
            name="accounts_quota_reset_interval_range",
        ),
        CheckConstraint("health IN ('normal','abnormal','possibly_recovered')"),
    )


class AccountGroup(Base):
    __tablename__ = "account_groups"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )


class AccountUser(Base):
    __tablename__ = "account_users"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    observed_health_version: Mapped[int | None] = mapped_column(Integer)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_kind: Mapped[str | None] = mapped_column(String(20))
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    return_kind: Mapped[str | None] = mapped_column(String(30))
    __table_args__ = (
        Index(
            "uq_open_claim",
            "user_id",
            "account_id",
            unique=True,
            postgresql_where=text("returned_at IS NULL"),
        ),
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id"), index=True
    )
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    target_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(50))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    automation_key: Mapped[str | None] = mapped_column(String(120), unique=True)


class Settings(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    user_claim_limit: Mapped[int] = mapped_column(Integer, default=1)
    account_capacity: Mapped[int] = mapped_column(Integer, default=1)
    observation_hours: Mapped[int] = mapped_column(Integer, default=4)
    cooldown_hours: Mapped[int] = mapped_column(Integer, default=24)
    session_days: Mapped[int] = mapped_column(Integer, default=7)
    email_code_timeout_minutes: Mapped[int] = mapped_column(
        Integer, default=5, server_default="5"
    )
    quota_depleted_threshold: Mapped[int] = mapped_column(
        Integer, default=5, server_default="5"
    )
    quota_reset_interval_days: Mapped[int] = mapped_column(Integer, default=7)
    __table_args__ = (
        CheckConstraint(
            "email_code_timeout_minutes BETWEEN 1 AND 30",
            name="settings_email_code_timeout_range",
        ),
        CheckConstraint(
            "quota_depleted_threshold BETWEEN 1 AND 100",
            name="settings_quota_depleted_threshold_range",
        ),
        CheckConstraint(
            "quota_reset_interval_days BETWEEN 1 AND 365",
            name="settings_quota_reset_interval_range",
        ),
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )


class LoginBudget(Base):
    __tablename__ = "login_budget"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("id = 1", name="login_budget_singleton"),)


class TwoFactor(Base):
    __tablename__ = "two_factors"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    # Empty after cancellation; retain the row to keep versions monotonic.
    uri_encrypted: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("kind IN ('service','mail')"),)


class EmailCodeRun(Base):
    __tablename__ = "email_code_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    # The initiating login session must remain valid, including after password reset.
    session_hash: Mapped[str] = mapped_column(String(64))
    mail_tool: Mapped[str] = mapped_column(String(64))
    tool_config_hash: Mapped[str] = mapped_column(String(64))
    # Provider identity is frozen for each run, including persisted candidates.
    mail_backend: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    next_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code_encrypted: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(50))
    __table_args__ = (
        Index(
            "uq_running_email_code_account",
            "account_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'reading')"),
        ),
        CheckConstraint(
            "status IN ('pending','reading','found','timed_out','cancelled','failed')"
        ),
    )
