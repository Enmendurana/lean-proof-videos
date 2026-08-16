"""Length-aware cinematic pacing for proof actions."""

from __future__ import annotations

import math
from dataclasses import dataclass


# Preserve the old 0.22-second readability reference while allowing an
# explicitly requested maximum pace three times as fast.
MIN_VISIBLE_ACTION_SECONDS = 0.22 / 3.0
DEFAULT_VISIBLE_GLYPHS_PER_SECOND = 48.0
# The global pace control may now reach three times the former 2-glyph/frame
# ceiling.  At 30 FPS this raises the accepted --write-speed maximum from
# 60 to 180 while the separate action floor still keeps every proof move
# visible for at least three frames.
MAX_VISIBLE_GLYPHS_PER_FRAME = 6.0
SLOW_TYPING_WINDOW_SECONDS = 10.0
SLOW_TYPING_SPEED_RATIO = 0.4
CINEMATIC_ENDPOINT_ACTION_SECONDS = 2.0 / 3.0
# Hold the opening and closing cadence completely still before entering the
# continuous ramp.  This is measured in proof-action time, independently of
# the one-second initial-board write and the final QED hold.
CINEMATIC_ENDPOINT_HOLD_SECONDS = 10.0
CINEMATIC_EDGE_WINDOW_SECONDS = 10.0


def minimum_visible_action_frames(fps: int) -> int:
    """Smallest readable proof action, expressed in whole video frames."""

    return max(2, math.ceil(MIN_VISIBLE_ACTION_SECONDS * fps))


def maximum_visible_write_speed(fps: int) -> float:
    """Fastest chalk writing that still leaves every proof step visible."""

    return max(1.0, fps * MAX_VISIBLE_GLYPHS_PER_FRAME)


@dataclass(frozen=True)
class ProofPacing:
    durations: tuple[int, ...]
    phases: tuple[str, ...]

    @property
    def total_frames(self) -> int:
        return sum(self.durations)


@dataclass(frozen=True)
class ProofTypingPacing:
    """Frame-exact movement and writing driven by one global envelope."""

    initial_frames: int
    durations: tuple[int, ...]
    phases: tuple[str, ...]
    move_ends: tuple[float, ...]
    write_starts: tuple[float, ...]
    write_ends: tuple[float, ...]
    speeds: tuple[float, ...]

    @property
    def action_frames(self) -> int:
        return self.initial_frames + sum(self.durations)


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _smootherstep(value: float) -> float:
    """C2-continuous easing with zero velocity and acceleration at both ends."""

    value = max(0.0, min(1.0, value))
    return value**3 * (value * (value * 6.0 - 15.0) + 10.0)


def _ramped_speed(
    distance_from_edge: float,
    *,
    fps: int,
    maximum_speed: float,
    enabled: bool,
) -> float:
    """Speed after moving ``distance_from_edge`` into a cinematic end."""

    if not enabled:
        return maximum_speed
    window = SLOW_TYPING_WINDOW_SECONDS * fps
    factor = SLOW_TYPING_SPEED_RATIO + (
        1.0 - SLOW_TYPING_SPEED_RATIO
    ) * _smoothstep(distance_from_edge / window)
    return max(1.0, maximum_speed * factor)


def proof_typing_pacing(
    initial_units: int,
    created_units: tuple[int, ...] | list[int],
    *,
    fps: int,
    maximum_speed: float,
    slow_opening: bool,
    slow_closing: bool,
) -> ProofTypingPacing:
    """Reproduce the established length-aware handwriting choreography.

    New notation controls handwriting time. Preserved semantic expressions
    glide on a separate clock, so movement is never charged as typed text.
    Both clocks share the smooth ten-second opening/closing envelope.
    """

    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    if maximum_speed <= 0:
        raise ValueError("maximum_speed must be greater than zero")
    units = tuple(max(0, int(value)) for value in created_units)
    minimum_frames = minimum_visible_action_frames(fps)
    lead_frames = max(1, round(0.08 * fps))
    settle_frames = max(1, round(0.04 * fps))

    def requirements(value: int, speed: float) -> tuple[int, int, int]:
        speed_factor = max(SLOW_TYPING_SPEED_RATIO, speed / maximum_speed)
        movement = math.ceil(minimum_frames / speed_factor)
        writing = math.ceil(value * fps / speed) if value else 0
        return (
            max(movement, lead_frames + writing + settle_frames),
            movement,
            writing,
        )

    def edge_schedule(value: int, distance: int, enabled: bool):
        fastest = requirements(value, maximum_speed)[0]
        slowest_speed = max(1.0, maximum_speed * SLOW_TYPING_SPEED_RATIO)
        slowest = requirements(value, slowest_speed)[0]
        low, high = fastest, slowest
        while low < high:
            candidate = (low + high) // 2
            speed = _ramped_speed(
                distance + candidate / 2,
                fps=fps,
                maximum_speed=maximum_speed,
                enabled=enabled,
            )
            required = requirements(value, speed)[0]
            if candidate >= required:
                high = candidate
            else:
                low = candidate + 1
        duration = low
        speed = _ramped_speed(
            distance + duration / 2,
            fps=fps,
            maximum_speed=maximum_speed,
            enabled=enabled,
        )
        _required, movement, writing = requirements(value, speed)
        return duration, speed, movement, writing

    fastest_initial = max(1, math.ceil(initial_units * fps / maximum_speed))
    slowest_initial = max(
        1,
        math.ceil(
            initial_units
            * fps
            / max(1.0, maximum_speed * SLOW_TYPING_SPEED_RATIO)
        ),
    )
    low, high = fastest_initial, slowest_initial
    while low < high:
        candidate = (low + high) // 2
        speed = _ramped_speed(
            candidate / 2,
            fps=fps,
            maximum_speed=maximum_speed,
            enabled=slow_opening,
        )
        required = max(1, math.ceil(initial_units * fps / speed))
        if candidate >= required:
            high = candidate
        else:
            low = candidate + 1
    initial_frames = low

    opening: list[tuple[int, float, int, int]] = []
    elapsed = initial_frames
    for value in units:
        scheduled = edge_schedule(value, elapsed, slow_opening)
        opening.append(scheduled)
        elapsed += scheduled[0]

    closing_reversed: list[tuple[int, float, int, int]] = []
    remaining = 0
    for value in reversed(units):
        scheduled = edge_schedule(value, remaining, slow_closing)
        closing_reversed.append(scheduled)
        remaining += scheduled[0]
    closing = list(reversed(closing_reversed))

    durations: list[int] = []
    phases: list[str] = []
    speeds: list[float] = []
    movement_frames: list[int] = []
    write_frames: list[int] = []
    for value, opening_item, closing_item in zip(
        units, opening, closing, strict=True
    ):
        opening_speed = opening_item[1]
        closing_speed = closing_item[1]
        speed = min(opening_speed, closing_speed)
        duration, movement, writing = requirements(value, speed)
        durations.append(duration)
        speeds.append(speed)
        movement_frames.append(movement)
        write_frames.append(writing)
        if speed >= maximum_speed - 1e-9:
            phases.append("cruise")
        elif opening_speed <= closing_speed:
            phases.append("opening")
        else:
            phases.append("closing")

    move_ends: list[float] = []
    write_starts: list[float] = []
    write_ends: list[float] = []
    for duration, movement, writing in zip(
        durations, movement_frames, write_frames, strict=True
    ):
        move_ends.append(min(1.0, movement / duration))
        if writing <= 0:
            write_starts.append(0.5)
            write_ends.append(0.5)
            continue
        spare = max(0, duration - writing)
        lead = min(lead_frames, spare // 2)
        lead += max(0, spare - lead_frames - settle_frames) // 2
        write_starts.append(lead / duration)
        write_ends.append((lead + writing) / duration)

    return ProofTypingPacing(
        initial_frames=initial_frames,
        durations=tuple(durations),
        phases=tuple(phases),
        move_ends=tuple(move_ends),
        write_starts=tuple(write_starts),
        write_ends=tuple(write_ends),
        speeds=tuple(speeds),
    )


def _edge_speed_curve(
    count: int,
    *,
    fps: int,
    cruise_frames: int,
    reverse: bool,
) -> tuple[float, ...]:
    """Integrate a fixed endpoint plateau followed by a smooth speed ramp."""

    if count <= 0:
        return ()
    endpoint_seconds = CINEMATIC_ENDPOINT_ACTION_SECONDS
    cruise_seconds = cruise_frames / fps
    endpoint_rate = 1.0 / endpoint_seconds
    cruise_rate = 1.0 / cruise_seconds
    edge_seconds = CINEMATIC_ENDPOINT_HOLD_SECONDS + CINEMATIC_EDGE_WINDOW_SECONDS
    elapsed = 0.0
    opening: list[float] = []
    for _index in range(count):
        if elapsed >= edge_seconds:
            break
        if elapsed < CINEMATIC_ENDPOINT_HOLD_SECONDS:
            duration = endpoint_seconds
        else:
            duration = opening[-1] / fps
            for _ in range(8):
                midpoint = elapsed + duration / 2.0
                ramp_elapsed = midpoint - CINEMATIC_ENDPOINT_HOLD_SECONDS
                rate = endpoint_rate + (
                    cruise_rate - endpoint_rate
                ) * _smootherstep(
                    ramp_elapsed / CINEMATIC_EDGE_WINDOW_SECONDS
                )
                duration = 1.0 / rate
        opening.append(duration * fps)
        elapsed += duration
    result = tuple(opening)
    return tuple(reversed(result)) if reverse else result


def cinematic_edge_action_count(*, fps: int, cruise_frames: int) -> int:
    """Number of complete proof actions touched by one cinematic edge."""

    return len(
        _edge_speed_curve(
            max(
                64,
                math.ceil(
                    (
                        CINEMATIC_ENDPOINT_HOLD_SECONDS
                        + CINEMATIC_EDGE_WINDOW_SECONDS
                    )
                    * fps
                ),
            ),
            fps=fps,
            cruise_frames=cruise_frames,
            reverse=False,
        )
    )


def _quantize_monotone_edge(
    exact: tuple[float, ...], *, increasing: bool
) -> tuple[int, ...]:
    """Preserve total rounded time without alternating one-frame jitter."""

    result = [max(2, round(value)) for value in exact]
    remaining = round(sum(exact)) - sum(result)
    locked = len(result) - 1 if increasing else 0
    while remaining:
        step = 1 if remaining > 0 else -1
        order = sorted(
            range(len(result)),
            key=lambda index: (exact[index] - result[index]) * step,
            reverse=True,
        )
        changed = False
        for index in order:
            if index == locked:
                continue
            candidate = result[index] + step
            if candidate < 2:
                continue
            if increasing:
                monotone = (
                    (index == 0 or result[index - 1] <= candidate)
                    and (index == len(result) - 1 or candidate <= result[index + 1])
                )
            else:
                monotone = (
                    (index == 0 or result[index - 1] >= candidate)
                    and (index == len(result) - 1 or candidate >= result[index + 1])
                )
            if not monotone:
                continue
            result[index] = candidate
            remaining -= step
            changed = True
            break
        if not changed:
            break
    return tuple(result)


def proof_action_pacing(
    transition_count: int,
    *,
    fps: int,
    slow_opening: bool,
    slow_closing: bool,
    frame_budget: int | None = None,
    cruise_frames: int | None = None,
) -> ProofPacing:
    """Return a continuously changing duration for every proof action.

    The first and last ten seconds of proof actions hold the same fixed
    real-time speed regardless of the configured cruise pace. The adjacent
    edge actions connect those plateaus to the middle over real elapsed time.
    Frame-rounding error is redistributed without breaking monotonicity, and
    formula length never affects duration.
    """

    if transition_count <= 0:
        return ProofPacing((), ())
    fast = max(2, cruise_frames or minimum_visible_action_frames(fps))
    durations = [fast] * transition_count
    phases = ["cruise"] * transition_count
    edge_count = cinematic_edge_action_count(fps=fps, cruise_frames=fast)
    opening_count = min(edge_count, transition_count) if slow_opening else 0
    closing_count = min(edge_count, transition_count) if slow_closing else 0
    endpoint_frames = max(2, round(CINEMATIC_ENDPOINT_ACTION_SECONDS * fps))
    merge_edges = max if endpoint_frames >= fast else min
    if slow_opening:
        opening = _edge_speed_curve(
            opening_count,
            fps=fps,
            cruise_frames=fast,
            reverse=False,
        )
        opening_frames = _quantize_monotone_edge(opening, increasing=False)
        for index, action_frames in enumerate(opening_frames):
            durations[index] = action_frames
            phases[index] = "opening"

    if slow_closing:
        closing = _edge_speed_curve(
            closing_count,
            fps=fps,
            cruise_frames=fast,
            reverse=True,
        )
        closing_frames = _quantize_monotone_edge(closing, increasing=True)
        for offset, action_frames in enumerate(closing_frames):
            index = transition_count - closing_count + offset
            if phases[index] == "opening":
                durations[index] = merge_edges(durations[index], action_frames)
            else:
                durations[index] = action_frames
            phases[index] = "closing"
    if slow_opening:
        durations[0] = endpoint_frames
    if slow_closing:
        durations[-1] = endpoint_frames

    if frame_budget is not None and sum(durations) > frame_budget:
        # Protect the cinematic ends for as long as possible. Very large
        # Proofs first compress their middle glide, but never below the
        # readability floor. A time limit must not turn a checked inference
        # into a one-frame flash.
        readability_floor = minimum_visible_action_frames(fps)
        order = sorted(
            range(transition_count),
            key=lambda index: (
                phases[index] in {"opening", "closing"},
                durations[index],
            ),
        )
        excess = sum(durations) - frame_budget
        for index in order:
            if excess <= 0:
                break
            reduction = min(excess, durations[index] - readability_floor)
            durations[index] -= reduction
            excess -= reduction
        if excess > 0:
            raise ValueError(
                "The duration ceiling is too short to display every proof action "
                f"for at least {readability_floor} frames at {fps} FPS."
            )

    return ProofPacing(tuple(durations), tuple(phases))
