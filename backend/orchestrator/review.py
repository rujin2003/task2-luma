"""Review, challenge and publish -- the two steps of the Monday cycle that stay human.

Steps 8 and 9 of `WORKFLOW.md` section 4 are deliberately not automated, and this module is
where that decision is made real rather than merely stated:

* **An `Override` is recorded, not applied.** The analyst does not edit an assumption; they
  record that they replaced it, with a reason. An overridden assumption is *better* data
  than a generated one, so it is kept as a first-class object and fed back into accuracy
  tracking rather than dissolved into the number it changed.
* **Publishing is maker-checker.** The person who prepared the cycle cannot be the person
  who publishes it. That is SOX-relevant and it is enforced in code here, not left to a
  convention about who clicks which button.
* **The close calendar binds.** Accountants work in periods. A version whose week ending
  falls inside a closed period is not publishable and an override dated into one is not
  recordable, because a system that silently posts across a cut-off is worse than one that
  refuses to.

Nothing in here talks to a model. Judgement is the human's; recording it faithfully is
ours.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.contracts.approvals import ApprovalRole, Override, SegregationOfDutiesError


class ClosedPeriodError(RuntimeError):
    """An action would post into a period the controller has already closed."""


class ClosePeriod(BaseModel):
    """One accounting period and whether the controller has shut it."""

    model_config = ConfigDict(frozen=True)

    label: Annotated[str, Field(min_length=1)]
    starts_on: date
    ends_on: date
    closed: bool = False

    @model_validator(mode="after")
    def _period_runs_forwards(self) -> Self:
        if self.ends_on < self.starts_on:
            raise ValueError(f"{self.label} ends before it starts")
        return self

    def contains(self, when: date) -> bool:
        return self.starts_on <= when <= self.ends_on


class CloseCalendar(BaseModel):
    """The periods this company reports on. Empty means nothing is closed yet."""

    model_config = ConfigDict(frozen=True)

    periods: list[ClosePeriod] = Field(default_factory=list)

    def period_for(self, when: date) -> ClosePeriod | None:
        return next((period for period in self.periods if period.contains(when)), None)

    def is_closed(self, when: date) -> bool:
        period = self.period_for(when)
        return period is not None and period.closed

    def assert_open(self, when: date, *, what: str) -> None:
        period = self.period_for(when)
        if period is not None and period.closed:
            raise ClosedPeriodError(
                f"{what} falls in {period.label}, which closed on {period.ends_on}; "
                "post it to the current period instead"
            )


class PublishedVersion(BaseModel):
    """A locked forecast version. Next week compares against this one, not a draft."""

    model_config = ConfigDict(frozen=True)

    version_id: Annotated[str, Field(min_length=1)]
    week_ending: date
    prepared_by: Annotated[str, Field(min_length=1)]
    published_by: Annotated[str, Field(min_length=1)]
    published_by_role: ApprovalRole
    published_at: datetime
    override_ids: list[str] = Field(default_factory=list)
    note: Annotated[str, Field(max_length=400)] = ""


class ReviewLedger:
    """Overrides recorded this cycle, and the gate that publishes the version.

    Held in memory for the same reason `RunStore` is: the durable home for these rows is
    the `Override` and `ForecastVersion` tables; duplicating that schema here would put
    two sources of truth behind the same rows.
    """

    # Publishing locks a version, so the roles that may do it are the accountable ones in
    # the delegation matrix -- an analyst prepares the cycle, they do not sign it off.
    PUBLISHER_ROLES = frozenset({ApprovalRole.TREASURER, ApprovalRole.CFO})

    def __init__(
        self,
        *,
        prepared_by: str,
        calendar: CloseCalendar | None = None,
    ) -> None:
        self.prepared_by = prepared_by
        self.calendar = calendar or CloseCalendar()
        self._overrides: list[Override] = []
        self._published: PublishedVersion | None = None

    # --- step 8: review and challenge ------------------------------------------------

    def record_override(self, override: Override, *, effective_on: date | None = None) -> Override:
        """Record a human replacing a generated assumption. Never silently applied."""
        if self._published is not None:
            raise RuntimeError(
                f"version {self._published.version_id} is published; "
                "an override after publication belongs to next week's cycle"
            )
        when = effective_on or override.created_at.date()
        self.calendar.assert_open(when, what=f"override of {override.target_ref}")
        self._overrides.append(override)
        return override

    @property
    def overrides(self) -> list[Override]:
        return list(self._overrides)

    def overrides_for(self, target_ref: str) -> list[Override]:
        return [override for override in self._overrides if override.target_ref == target_ref]

    # --- step 9: publish -------------------------------------------------------------

    def publish(
        self,
        *,
        version_id: str,
        week_ending: date,
        published_by: str,
        published_by_role: ApprovalRole,
        note: str = "",
        now: datetime | None = None,
    ) -> PublishedVersion:
        """Lock the version. Preparer is not publisher, and the period must be open."""
        if self._published is not None:
            raise RuntimeError(f"version {self._published.version_id} is already published")
        if published_by.strip().lower() == self.prepared_by.strip().lower():
            raise SegregationOfDutiesError(
                f"{published_by} prepared this cycle and cannot also publish it"
            )
        if published_by_role not in self.PUBLISHER_ROLES:
            raise SegregationOfDutiesError(
                f"{published_by_role.value} may not publish a forecast version; "
                f"publication is {' or '.join(sorted(r.value for r in self.PUBLISHER_ROLES))}"
            )
        self.calendar.assert_open(week_ending, what=f"forecast version {version_id}")

        self._published = PublishedVersion(
            version_id=version_id,
            week_ending=week_ending,
            prepared_by=self.prepared_by,
            published_by=published_by,
            published_by_role=published_by_role,
            published_at=now or datetime.now(UTC),
            override_ids=[override.override_id for override in self._overrides],
            note=note,
        )
        return self._published

    @property
    def published(self) -> PublishedVersion | None:
        return self._published
