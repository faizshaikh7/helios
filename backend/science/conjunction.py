"""Two-object conjunction screening from public two-line element sets.

This module answers the screening question -- "when are these two propagated objects closest?"
-- without pretending to answer the risk question.  A defensible collision probability needs
state covariances and hard-body radii, neither of which a public TLE supplies.  The distinction
matches operational practice: screening identifies an encounter for further analysis; it does
not authorize a manoeuvre.

Both objects are propagated with SGP4 in its native TEME frame.  A coarse scan finds every local
minimum in the requested interval and a bounded golden-section search refines each candidate.
The minimizer is ordinary numerical plumbing; the orbit model remains the validated ``sgp4``
implementation exposed through Skyfield.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from skyfield.sgp4lib import TEME

from science.catalog import TLE
from science.orbit import TIMESCALE, build_satellite, dataset_record, position_uncertainty
from science.provenance import Receipt

FRAME_TEME = "TEME (true equator, mean equinox -- SGP4's native frame)"
REFINEMENT_TOLERANCE_S = 0.001
MAX_REFINEMENT_ITERATIONS = 80


@dataclass(frozen=True)
class Encounter:
    """The closest approach found inside one screening interval.

    Attributes:
        tca_utc: Time of closest approach in UTC.
        miss_distance_km: Euclidean separation at TCA, kilometres in TEME.
        relative_speed_km_s: Relative inertial speed at TCA, kilometres per second.
        relative_position_rtn_km: Secondary minus primary position in the primary RTN frame.
        relative_velocity_rtn_km_s: Secondary minus primary velocity in the primary RTN frame.
        local_minima_screened: Number of interior candidate approaches refined.
    """

    tca_utc: datetime
    miss_distance_km: float
    relative_speed_km_s: float
    relative_position_rtn_km: tuple[float, float, float]
    relative_velocity_rtn_km_s: tuple[float, float, float]
    local_minima_screened: int


def _state_teme(tle: TLE, when: datetime) -> tuple[np.ndarray, np.ndarray]:
    """Propagate one object and return TEME position and velocity in km and km/s.

    Args:
        tle: Element set to propagate.
        when: UTC instant to evaluate.

    Returns:
        Position in kilometres and velocity in kilometres per second.
    """
    state = build_satellite(tle).at(TIMESCALE.from_datetime(when))
    distance, velocity = state.frame_xyz_and_velocity(TEME)
    return np.asarray(distance.km, dtype=float), np.asarray(velocity.km_per_s, dtype=float)


def _separation_squared_at(
    primary: TLE, secondary: TLE, start: datetime, offset_s: float
) -> float:
    """Return squared TEME separation at an offset from the screen start.

    Squared distance has the same minimum as distance and avoids a square root during every
    refinement evaluation.

    Args:
        primary: Primary object's element set.
        secondary: Secondary object's element set.
        start: UTC beginning of the screening interval.
        offset_s: Seconds after ``start``.

    Returns:
        Squared separation in square kilometres.
    """
    when = start + timedelta(seconds=offset_s)
    primary_position, _ = _state_teme(primary, when)
    secondary_position, _ = _state_teme(secondary, when)
    delta = secondary_position - primary_position
    return float(np.dot(delta, delta))


def _bounded_minimum(
    function: Callable[[float], float], lower: float, upper: float
) -> tuple[float, float]:
    """Minimize a continuous scalar function on a closed interval.

    Golden-section search needs no derivative and cannot leave its bracket.  Both boundaries are
    checked explicitly because the closest approach can legitimately sit exactly at the start or
    end of a user-supplied interval.

    Args:
        function: Scalar objective.
        lower: Inclusive lower bound.
        upper: Inclusive upper bound.

    Returns:
        Minimizing argument and objective value.
    """
    if upper <= lower:
        value = function(lower)
        return lower, value

    inverse_phi = (math.sqrt(5.0) - 1.0) / 2.0
    left = lower
    right = upper
    c = right - inverse_phi * (right - left)
    d = left + inverse_phi * (right - left)
    value_c = function(c)
    value_d = function(d)

    for _ in range(MAX_REFINEMENT_ITERATIONS):
        if right - left <= REFINEMENT_TOLERANCE_S:
            break
        if value_c <= value_d:
            right, d, value_d = d, c, value_c
            c = right - inverse_phi * (right - left)
            value_c = function(c)
        else:
            left, c, value_c = c, d, value_d
            d = left + inverse_phi * (right - left)
            value_d = function(d)

    candidates = [
        (lower, function(lower)),
        (upper, function(upper)),
        (c, value_c),
        (d, value_d),
    ]
    return min(candidates, key=lambda item: item[1])


def _coarse_separations(
    primary: TLE,
    secondary: TLE,
    start: datetime,
    duration_s: float,
    step_seconds: int,
) -> tuple[list[float], np.ndarray]:
    """Vectorize the coarse TEME scan that brackets candidate closest approaches.

    Args:
        primary: Primary object's element set.
        secondary: Secondary object's element set.
        start: UTC beginning of the screening interval.
        duration_s: Screening duration in seconds.
        step_seconds: Maximum spacing between coarse samples in seconds.

    Returns:
        Sample offsets in seconds and squared separations in square kilometres.

    Raises:
        ValueError: If propagation produces a non-finite state.
    """
    sample_count = max(2, math.ceil(duration_s / step_seconds) + 1)
    offsets = [min(index * float(step_seconds), duration_s) for index in range(sample_count)]
    offsets[-1] = duration_s
    moments = [start + timedelta(seconds=offset) for offset in offsets]
    times = TIMESCALE.from_datetimes(moments)

    primary_distance, _ = build_satellite(primary).at(times).frame_xyz_and_velocity(TEME)
    secondary_distance, _ = build_satellite(secondary).at(times).frame_xyz_and_velocity(TEME)
    relative = np.asarray(secondary_distance.km - primary_distance.km, dtype=float)
    squared = np.sum(relative * relative, axis=0)

    if not np.all(np.isfinite(squared)):
        raise ValueError("SGP4 could not produce a finite state throughout the screening interval.")

    return offsets, squared


def _rtn_components(
    primary_position_km: np.ndarray,
    primary_velocity_km_s: np.ndarray,
    relative_vector: np.ndarray,
) -> tuple[float, float, float]:
    """Project a relative vector into the primary object's radial/transverse/normal frame.

    Args:
        primary_position_km: Primary TEME position in kilometres.
        primary_velocity_km_s: Primary TEME velocity in kilometres per second.
        relative_vector: A position or velocity difference to project.

    Returns:
        Radial, in-track (transverse), and cross-track (normal) components in the input unit.

    Raises:
        ValueError: If the primary state cannot define a valid RTN basis.
    """
    radial_norm = float(np.linalg.norm(primary_position_km))
    angular_momentum = np.cross(primary_position_km, primary_velocity_km_s)
    normal_norm = float(np.linalg.norm(angular_momentum))
    if radial_norm == 0.0 or normal_norm == 0.0:
        raise ValueError("Primary state cannot define an RTN encounter frame.")

    radial = primary_position_km / radial_norm
    normal = angular_momentum / normal_norm
    transverse = np.cross(normal, radial)
    return tuple(float(np.dot(relative_vector, axis)) for axis in (radial, transverse, normal))


def screen(
    primary: TLE,
    secondary: TLE,
    start: datetime,
    *,
    duration_hours: float = 24.0,
    step_seconds: int = 60,
) -> Encounter:
    """Find the global closest approach between two TLE-propagated objects in an interval.

    Every interior local minimum identified by the coarse scan is refined, then compared with
    both interval boundaries.  A coarse step is therefore a bracketing control, not the precision
    of the reported TCA.

    Args:
        primary: Primary object's element set.
        secondary: Secondary object's element set.
        start: UTC beginning of the screening interval.
        duration_hours: Length of the interval in hours.
        step_seconds: Maximum coarse sample spacing in seconds.

    Returns:
        Closest-approach geometry in TEME and primary-centred RTN.

    Raises:
        ValueError: If the two catalog identifiers are equal or propagation is invalid.
    """
    if primary.norad_id == secondary.norad_id:
        raise ValueError("Primary and secondary must be different catalog objects.")

    duration_s = duration_hours * 3600.0
    offsets, squared = _coarse_separations(
        primary, secondary, start, duration_s, step_seconds
    )
    # The optimizer expects a scalar function; close over the immutable screen inputs so every
    # candidate is evaluated with exactly the same objects and epoch.
    def objective(offset: float) -> float:
        """Evaluate squared separation for one refinement offset."""
        return _separation_squared_at(primary, secondary, start, offset)

    candidates: list[tuple[float, float]] = [
        (offsets[0], float(squared[0])),
        (offsets[-1], float(squared[-1])),
    ]
    local_minima = 0
    for index in range(1, len(offsets) - 1):
        if squared[index] <= squared[index - 1] and squared[index] <= squared[index + 1]:
            candidates.append(
                _bounded_minimum(objective, offsets[index - 1], offsets[index + 1])
            )
            local_minima += 1

    best_offset, best_squared = min(candidates, key=lambda item: item[1])
    tca = start + timedelta(seconds=best_offset)
    primary_position, primary_velocity = _state_teme(primary, tca)
    secondary_position, secondary_velocity = _state_teme(secondary, tca)
    relative_position = secondary_position - primary_position
    relative_velocity = secondary_velocity - primary_velocity

    return Encounter(
        tca_utc=tca,
        miss_distance_km=math.sqrt(max(0.0, best_squared)),
        relative_speed_km_s=float(np.linalg.norm(relative_velocity)),
        relative_position_rtn_km=_rtn_components(
            primary_position, primary_velocity, relative_position
        ),
        relative_velocity_rtn_km_s=_rtn_components(
            primary_position, primary_velocity, relative_velocity
        ),
        local_minima_screened=local_minima,
    )


def receipt(
    primary: TLE,
    secondary: TLE,
    start: datetime,
    duration_hours: float,
    step_seconds: int,
    tca: datetime,
) -> Receipt:
    """Build the shared audit record for one conjunction screen.

    Args:
        primary: Primary object's element set.
        secondary: Secondary object's element set.
        start: UTC beginning of the screening interval.
        duration_hours: Length of the interval in hours.
        step_seconds: Coarse bracketing step in seconds.
        tca: Refined time of closest approach.

    Returns:
        Provenance, frame, method, and honest uncertainty limits.
    """
    return Receipt(
        tool="conjunction_screen",
        inputs={
            "primary_norad_id": primary.norad_id,
            "secondary_norad_id": secondary.norad_id,
            "start_utc": start.isoformat(),
            "duration_hours": duration_hours,
            "coarse_step_seconds": step_seconds,
            "refinement_tolerance_seconds": REFINEMENT_TOLERANCE_S,
        },
        frame=FRAME_TEME,
        time_scale="UTC",
        dataset={"primary": dataset_record(primary), "secondary": dataset_record(secondary)},
        equation=(
            "SGP4(TLE) for both objects; global coarse scan; bounded refinement of every local "
            "minimum; Euclidean relative state; primary-centred RTN projection"
        ),
        uncertainty={
            "primary_position": position_uncertainty(primary, tca),
            "secondary_position": position_uncertainty(secondary, tca),
            "interpretation": (
                "Independent rule-of-thumb position errors, not a joint covariance and not a "
                "collision-probability confidence interval."
            ),
        },
        notes=(
            "Screening result only. Public TLEs omit state covariance and hard-body radius, so "
            "collision probability is unavailable. Validate a close approach with operator-grade "
            "orbit determination before any operational decision."
        ),
    )
