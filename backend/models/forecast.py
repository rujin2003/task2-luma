from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CHAR, BigInteger, CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from backend.models.base import (
    Bitemporal,
    Sourced,
    TenantOwned,
    money_pair,
    new_id,
    non_negative,
    one_of,
    valid_currency,
)
from backend.models.vocab import FORECAST_CATEGORIES, VARIANCE_KINDS

HORIZON_WEEKS = 13


class ForecastVersion(TenantOwned, SQLModel, table=True):
    """Published forecast. Immutable once published — next week's baseline.

    `data_recorded_through` is the bitemporal watermark: the `recorded_at` cutoff
    of the facts this version was built from. Backtesting without it silently
    re-runs history against data that arrived later, which makes the accuracy
    numbers in Phase 3 flattering and wrong.
    """

    __tablename__ = "forecast_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "version_label", name="uq_forecast_versions_label"
        ),
        valid_currency("currency", "forecast_versions"),
        CheckConstraint("horizon_weeks > 0", name="ck_forecast_versions_horizon"),
        CheckConstraint(
            "published = FALSE OR (published_at IS NOT NULL AND published_by IS NOT NULL)",
            name="ck_forecast_versions_published_fields",
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    version_label: str = Field(max_length=64)
    as_of: datetime
    data_recorded_through: datetime
    week_ending: date = Field(index=True)
    first_week_start: date
    horizon_weeks: int = HORIZON_WEEKS
    published: bool = False
    published_at: datetime | None = None
    published_by: str | None = Field(default=None, max_length=64)
    opening_cash_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)


class Assumption(TenantOwned, SQLModel, table=True):
    """A citable forecast assumption.

    Assumptions are rows, not free text on a line, because the
    `stale_forecast_assumption` anomaly and the Evidence Explorer both need to
    point at one and ask when it was last reviewed.
    """

    __tablename__ = "assumptions"
    __table_args__ = (
        UniqueConstraint("version_id", "category", "key", name="uq_assumptions_key"),
        one_of("category", FORECAST_CATEGORIES, "assumptions"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    version_id: str = Field(foreign_key="forecast_versions.id", index=True)
    category: str = Field(max_length=64, index=True)
    key: str = Field(max_length=64)
    statement: str = Field(max_length=512)
    basis: str = Field(max_length=256)
    as_of: date
    review_due: date | None = None


class ForecastLine(TenantOwned, Sourced, SQLModel, table=True):
    """One category × week cell. Signed: positive is cash in."""

    __tablename__ = "forecast_lines"
    __table_args__ = (
        UniqueConstraint("version_id", "category", "week_start", name="uq_forecast_lines_cell"),
        valid_currency("currency", "forecast_lines"),
        one_of("category", FORECAST_CATEGORIES, "forecast_lines"),
        CheckConstraint("week_end > week_start", name="ck_forecast_lines_week_order"),
        CheckConstraint("week_index >= 1", name="ck_forecast_lines_week_index"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    version_id: str = Field(foreign_key="forecast_versions.id", index=True)
    assumption_id: str | None = Field(default=None, foreign_key="assumptions.id", index=True)
    category: str = Field(max_length=64, index=True)
    week_index: int
    week_start: date = Field(index=True)
    week_end: date
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    source: str = Field(max_length=64)
    assumption: str = Field(max_length=512)
    method: str = Field(max_length=64)
    as_of: datetime


class VarianceItem(TenantOwned, SQLModel, table=True):
    __tablename__ = "variance_items"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "kind", "category", "week_ending", name="uq_variance_items_cell"
        ),
        valid_currency("currency", "variance_items"),
        one_of("kind", VARIANCE_KINDS, "variance_items"),
        one_of("category", FORECAST_CATEGORIES, "variance_items"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    version_id: str = Field(foreign_key="forecast_versions.id", index=True)
    kind: str = Field(max_length=32)
    category: str = Field(max_length=64)
    week_ending: date | None = None
    forecast_minor: int = Field(sa_type=BigInteger)
    actual_minor: int | None = Field(default=None, sa_type=BigInteger)
    prior_forecast_minor: int | None = Field(default=None, sa_type=BigInteger)
    variance_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(sa_type=CHAR(3), max_length=3)
    material: bool = False
    explanation: str | None = Field(default=None, max_length=1024)


class AccuracyStat(TenantOwned, SQLModel, table=True):
    """MAPE by category × horizon. Basis points, never a float.

    This replaces model confidence throughout the product, so it is stored as a
    scaled integer for the same reason money is: a percentage held as a float is
    a rounding difference waiting to be argued about.
    """

    __tablename__ = "accuracy_stats"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "category",
            "horizon_weeks",
            "window_weeks",
            "as_of",
            name="uq_accuracy_stats_cell",
        ),
        one_of("category", FORECAST_CATEGORIES, "accuracy_stats"),
        non_negative("mape_bps", "accuracy_stats"),
        CheckConstraint("horizon_weeks >= 1", name="ck_accuracy_stats_horizon"),
        CheckConstraint("window_weeks >= 1", name="ck_accuracy_stats_window"),
        CheckConstraint("n >= 1", name="ck_accuracy_stats_n"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    company_id: str = Field(foreign_key="companies.id", index=True)
    category: str = Field(max_length=64, index=True)
    horizon_weeks: int
    window_weeks: int
    mape_bps: int
    n: int
    as_of: date = Field(index=True)


class Override(TenantOwned, Bitemporal, SQLModel, table=True):
    """A human adjustment to an assumption, with author and reason."""

    __tablename__ = "overrides"
    __table_args__ = (
        money_pair("amount_minor", "currency", "overrides"),
        valid_currency("currency", "overrides"),
        one_of("category", FORECAST_CATEGORIES, "overrides"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    version_id: str = Field(foreign_key="forecast_versions.id", index=True)
    assumption_id: str | None = Field(default=None, foreign_key="assumptions.id")
    category: str = Field(max_length=64)
    assumption: str = Field(max_length=512)
    reason: str = Field(max_length=1024)
    author: str = Field(max_length=64)
    amount_minor: int | None = Field(default=None, sa_type=BigInteger)
    currency: str | None = Field(default=None, sa_type=CHAR(3), max_length=3)
    expires_at: datetime | None = None
