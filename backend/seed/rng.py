"""Deterministic sampling. One seed produces a byte-identical dataset.

Two properties matter, and neither is what a bare `random.Random(seed)` gives
you.

**Streams.** Every generation step draws from its own stream, derived from
`(seed, stream_name)` via BLAKE2b. Without this, inserting one extra draw
anywhere — a new vendor, an extra invoice — shifts every subsequent draw in the
whole dataset, so an unrelated change rewrites every primary key and the
"identical dumps" test becomes noise. With it, changing the AR generator leaves
the payroll stream untouched.

**Integers only.** Randomness is used solely to *sample from empirically
observed distributions* (`PHASES.md` Phase 2), never to invent a number, and the
result is always an integer count, basis-point weight or minor-unit amount. The
no-float CI rule covers `backend/seed/**` for exactly this reason.

Python's built-in `hash()` is salted per process and cannot be used here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from random import Random
from typing import TypeVar

from backend.finance.money import Money

T = TypeVar("T")

BPS = 10_000


class SamplingError(ValueError):
    """A distribution was malformed or empty."""


def derive_seed(seed: int, stream: str) -> int:
    """A stable sub-seed for one named stream."""
    digest = hashlib.blake2b(f"{seed}|{stream}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


class Stream:
    """One named, independent source of deterministic draws."""

    def __init__(self, seed: int, name: str) -> None:
        self.name = name
        self._random = Random(derive_seed(seed, name))

    def integer(self, low: int, high: int) -> int:
        """Uniform integer in `[low, high]`."""
        if high < low:
            raise SamplingError(f"{self.name}: empty integer range [{low}, {high}]")
        return self._random.randint(low, high)

    def bps(self) -> int:
        """A uniform draw in `[0, 9999]`, the unit every weighted choice uses."""
        return self._random.randrange(BPS)

    def weighted(self, options: Sequence[tuple[T, int]]) -> T:
        """Pick from `(value, weight_bps)` pairs.

        Weights are relative; they need not sum to 10000. Zero-weight options are
        reachable only if every option is zero-weight, which is an error.
        """
        if not options:
            raise SamplingError(f"{self.name}: weighted choice over an empty sequence")
        total = sum(weight for _, weight in options)
        if total <= 0:
            raise SamplingError(f"{self.name}: weighted choice with non-positive total weight")
        target = self._random.randrange(total)
        cursor = 0
        for value, weight in options:
            cursor += weight
            if target < cursor:
                return value
        return options[-1][0]  # pragma: no cover - unreachable while total > 0

    def happens(self, probability_bps: int) -> bool:
        """True with probability `probability_bps / 10000`."""
        if not 0 <= probability_bps <= BPS:
            raise SamplingError(f"{self.name}: probability {probability_bps} outside 0..10000")
        return self.bps() < probability_bps

    def jitter(self, amount: Money, spread_bps: int) -> Money:
        """Scale `amount` by a factor drawn uniformly from ±`spread_bps`.

        Used to give generated invoices and costs a realistic spread around the
        fitted central value without inventing the central value itself.
        """
        if spread_bps < 0:
            raise SamplingError(f"{self.name}: jitter spread must be non-negative")
        factor = BPS + self.integer(-spread_bps, spread_bps)
        return Money.of(amount.amount * factor // BPS, amount.currency)

    def jitter_int(self, value: int, spread_bps: int) -> int:
        if spread_bps < 0:
            raise SamplingError(f"{self.name}: jitter spread must be non-negative")
        return value * (BPS + self.integer(-spread_bps, spread_bps)) // BPS

    def pick(self, items: Sequence[T]) -> T:
        if not items:
            raise SamplingError(f"{self.name}: pick from an empty sequence")
        return items[self.integer(0, len(items) - 1)]

    def shuffled(self, items: Sequence[T]) -> list[T]:
        copy = list(items)
        self._random.shuffle(copy)
        return copy


class Streams:
    """Named streams for one seed, created on first use."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._streams: dict[str, Stream] = {}

    def __call__(self, name: str) -> Stream:
        if name not in self._streams:
            self._streams[name] = Stream(self.seed, name)
        return self._streams[name]


def pareto_weights_bps(count: int, head_count: int, head_share_bps: int) -> tuple[int, ...]:
    """Spend weights following the observed supplier concentration curve.

    `head_count` vendors take `head_share_bps` of spend and the tail splits the
    rest, with both halves decaying geometrically rather than being flat — a flat
    tail is what makes a generated vendor list look generated.

    Criticality is deliberately *not* derived from these weights. The most
    dangerous supplier to delay is often a small one (`DATA_SOURCES.md` §5), and
    that is the whole reason the Supplier Risk agent has something real to say.
    """
    if count < 1:
        raise SamplingError("pareto_weights_bps needs at least one member")
    head_count = max(1, min(head_count, count))
    if not 0 < head_share_bps <= BPS:
        raise SamplingError("head_share_bps must be within 1..10000")
    tail_count = count - head_count

    def decay(size: int, budget: int) -> list[int]:
        if size == 0:
            return []
        # Geometric ranks, then allocate the budget by them so the parts sum to
        # the budget exactly.
        ranks = [max(1, 1000 * 100 // (100 + 45 * index)) for index in range(size)]
        total = sum(ranks)
        parts = [budget * rank // total for rank in ranks]
        parts[0] += budget - sum(parts)
        return parts

    head = decay(head_count, head_share_bps)
    tail = decay(tail_count, BPS - head_share_bps)
    return tuple(head + tail)


__all__ = [
    "BPS",
    "SamplingError",
    "Stream",
    "Streams",
    "derive_seed",
    "pareto_weights_bps",
]
