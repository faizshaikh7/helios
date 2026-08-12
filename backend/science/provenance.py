"""Trust tiers and provenance records.

Every number this system returns carries where it came from. Provenance attaches to *values*,
not to answers: one sentence routinely mixes a measured TLE epoch, a derived orbital period, and
a predicted pass time, so labelling a whole response "high confidence" says nothing useful.

The rule that makes the taxonomy mean something is **monotonic inheritance** -- a derived value
carries the weakest tier among its inputs. Exact arithmetic over a Predicted input is still
Predicted.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Tier(str, Enum):
    """How much weight a value can bear.

    Ordered weakest-last so `min` over the declaration order yields the weakest tier.
    """

    OBSERVED = "observed"
    """Measured data, as published by its source. A TLE's epoch; a station's coordinates."""

    DERIVED = "derived"
    """Computed from observed inputs via accepted physics. An orbital period from a mean motion."""

    PREDICTED = "predicted"
    """Estimated by propagating a model forward. Every future pass time."""

    SPECULATIVE = "speculative"
    """Insufficient evidence. Nothing should reach a user at this tier without saying so."""


# Strength order, strongest first. Used to resolve inheritance.
_TIER_STRENGTH: dict[Tier, int] = {
    Tier.OBSERVED: 0,
    Tier.DERIVED: 1,
    Tier.PREDICTED: 2,
    Tier.SPECULATIVE: 3,
}


def weakest(*tiers: Tier) -> Tier:
    """Return the weakest of the given tiers.

    This is the whole of monotonic inheritance: a value computed from several inputs can be no
    more trustworthy than its least trustworthy input, regardless of how exact the arithmetic
    between them was.

    Args:
        *tiers: Tiers of the inputs a value was computed from.

    Returns:
        The weakest tier supplied, or ``DERIVED`` if none were.
    """
    if not tiers:
        return Tier.DERIVED
    return max(tiers, key=lambda t: _TIER_STRENGTH[t])


class Receipt(BaseModel):
    """Everything needed to reproduce or audit a single value.

    A number without a receipt is indistinguishable from a number a language model recalled,
    which is precisely the failure this project exists to avoid.
    """

    tool: str = Field(description="Which tool produced the value.")
    inputs: dict[str, Any] = Field(default_factory=dict, description="Exact parameters used.")
    frame: str | None = Field(default=None, description="Reference frame, e.g. TEME, ITRF.")
    time_scale: str | None = Field(default=None, description="Time scale, e.g. UTC, TT, TAI.")
    dataset: dict[str, Any] | None = Field(
        default=None, description="Source dataset and its epoch or fetch time."
    )
    equation: str | None = Field(default=None, description="Relation or model applied.")
    uncertainty: dict[str, Any] | None = Field(
        default=None, description="Interval or stated error, never a bare confidence percentage."
    )
    notes: str | None = Field(default=None, description="Caveats a reader needs to judge this.")


class Value(BaseModel):
    """A single quantity with its unit, trust tier, and receipt.

    No bare floats cross an API boundary: a number without a unit is an invitation to the
    unit-confusion class of bug, and a number without a frame is an invitation to the
    reference-frame class.
    """

    value: float | str
    unit: str = Field(description="Unit symbol, or 'none' for dimensionless or textual values.")
    tier: Tier
    receipt: Receipt

    @classmethod
    def derived_from(
        cls,
        value: float | str,
        unit: str,
        receipt: Receipt,
        *inputs: Value,
    ) -> Value:
        """Build a value whose tier is inherited from the values it was computed from.

        Prefer this over constructing a ``Value`` with an explicit tier whenever the quantity is
        computed from other values -- it is what keeps inheritance monotonic without every call
        site having to remember the rule.

        Args:
            value: The computed quantity.
            unit: Unit symbol.
            receipt: Provenance for this computation.
            *inputs: Values this was computed from.

        Returns:
            A value carrying the weakest tier among ``inputs``.
        """
        return cls(
            value=value,
            unit=unit,
            tier=weakest(*(item.tier for item in inputs)),
            receipt=receipt,
        )
