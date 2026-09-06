"""Typed loader for the calibration profile.

`calibration/novatech_profile.json` holds **fitted parameters, never dataset
rows** — the pattern `DATA_SOURCES.md` recommends for licence cleanliness and
determinism. This module is the only thing that reads it, so the shape is
validated once, here, rather than being indexed into ad hoc by the generator.

Everything is an integer: minor units, basis points or days. That is not
pedantry — `backend/seed/**` is scanned by the same no-float CI rule as
`backend/finance/**`, because the layer that populates the money columns can
corrupt them exactly as easily as the layer that computes on them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.finance.cadence import DepositSchedule, PayrollFrequency, ServiceFrequency
from backend.finance.forecast import AGING_BUCKETS, CollectionCurve, LagComponent
from backend.finance.money import Money

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / "calibration" / "novatech_profile.json"

BPS = 10_000


class ProfileError(ValueError):
    """The calibration profile is missing a parameter or is internally wrong."""


def _require(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping:
        raise ProfileError(f"calibration profile is missing {path}.{key}")
    return mapping[key]


def _int(mapping: dict[str, Any], key: str, path: str) -> int:
    value = _require(mapping, key, path)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProfileError(f"{path}.{key} must be an integer, got {type(value).__name__}")
    return value


def _int_map(mapping: dict[str, Any], key: str, path: str) -> dict[str, int]:
    raw = _require(mapping, key, path)
    if not isinstance(raw, dict):
        raise ProfileError(f"{path}.{key} must be an object")
    return {name: _int(raw, name, f"{path}.{key}") for name in raw}


@dataclass(frozen=True, slots=True)
class Profile:
    """The calibration profile, validated and addressable."""

    raw: dict[str, Any]

    @property
    def currency(self) -> str:
        code = _require(self.raw, "currency", "profile")
        if not isinstance(code, str):
            raise ProfileError("profile.currency must be a string")
        return code

    @property
    def version(self) -> int:
        return _int(self.raw, "version", "profile")

    def section(self, name: str) -> dict[str, Any]:
        value = _require(self.raw, name, "profile")
        if not isinstance(value, dict):
            raise ProfileError(f"profile.{name} must be an object")
        return value

    def money(self, section: str, key: str) -> Money:
        return Money.of(_int(self.section(section), key, section), self.currency)

    def count(self, section: str, key: str) -> int:
        return _int(self.section(section), key, section)

    # -- derived views -------------------------------------------------- #

    def collection_curve(self) -> CollectionCurve:
        """The fitted AR behaviour, in the shape the forecast engine consumes."""
        ar = self.section("ar")
        raw_mixture = _require(ar, "lag_mixture", "ar")
        mixture: dict[str, tuple[LagComponent, ...]] = {}
        for segment, components in raw_mixture.items():
            if not isinstance(components, list) or not components:
                raise ProfileError(f"ar.lag_mixture.{segment} must be a non-empty list")
            mixture[segment] = tuple(
                LagComponent(
                    weight_bps=_int(component, "weight_bps", f"ar.lag_mixture.{segment}"),
                    lag_days=_int(component, "lag_days", f"ar.lag_mixture.{segment}"),
                )
                for component in components
            )
        probabilities = _int_map(ar, "collection_probability_bps", "ar")
        missing = set(AGING_BUCKETS) - set(probabilities)
        if missing:
            raise ProfileError(f"ar.collection_probability_bps is missing {sorted(missing)}")
        # `CollectionCurve` re-validates the weights, so a mixture that does not
        # sum to 10000 bps fails here rather than silently losing cash.
        return CollectionCurve(
            lag_mixture=mixture,
            collection_probability_bps=probabilities,
            dispute_haircut_bps=_int(ar, "dispute_haircut_bps", "ar"),
        )

    def segment_weights_bps(self) -> dict[str, int]:
        weights = _int_map(self.section("ar"), "segment_weights_bps", "ar")
        if sum(weights.values()) != BPS:
            raise ProfileError("ar.segment_weights_bps must sum to 10000")
        return weights

    def segment_invoice_minor(self) -> dict[str, int]:
        return _int_map(self.section("ar"), "segment_invoice_minor", "ar")

    def segment_terms_days(self) -> dict[str, int]:
        return _int_map(self.section("ar"), "segment_terms_days", "ar")

    def payroll_frequency(self) -> PayrollFrequency:
        return PayrollFrequency(_require(self.section("payroll"), "frequency", "payroll"))

    def deposit_schedule(self) -> DepositSchedule:
        return DepositSchedule(_require(self.section("payroll"), "deposit_schedule", "payroll"))

    def debt_service_frequency(self) -> ServiceFrequency:
        return ServiceFrequency(_require(self.section("debt"), "service_frequency", "debt"))

    def category_error_bps(self) -> dict[str, int]:
        return _int_map(self.section("history"), "category_error_bps", "history")

    def horizon_error_multiplier_bps(self) -> dict[int, int]:
        raw = _int_map(self.section("history"), "horizon_error_multiplier_bps", "history")
        return {int(horizon): value for horizon, value in raw.items()}

    def covenants(self) -> dict[str, int]:
        debt = self.section("debt")
        raw = _require(debt, "covenants", "debt")
        if not isinstance(raw, dict):
            raise ProfileError("debt.covenants must be an object")
        return {name: _int(raw, name, "debt.covenants") for name in raw}


def load_profile(path: Path | None = None) -> Profile:
    """Read and validate the profile. Eager: a bad profile fails at load."""
    profile_path = path or DEFAULT_PROFILE_PATH
    try:
        text = profile_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"cannot read calibration profile at {profile_path}") from exc
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ProfileError("calibration profile must be a JSON object")
    profile = Profile(raw=loaded)
    # Touch the derived views so a malformed profile is rejected at load time
    # rather than three thousand rows into a seed run.
    profile.collection_curve()
    profile.segment_weights_bps()
    profile.payroll_frequency()
    profile.deposit_schedule()
    profile.debt_service_frequency()
    profile.covenants()
    return profile


__all__ = ["DEFAULT_PROFILE_PATH", "Profile", "ProfileError", "load_profile"]
