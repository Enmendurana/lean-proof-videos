from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os

from ortools.sat.python import cp_model


class TransitionRole(str, Enum):
    """The only physical operations allowed in a strict proof transition."""

    PRESERVE = "preserve"
    COPY = "copy"
    REWRITE = "rewrite"
    CREATE = "create"
    DELETE = "delete"


@dataclass(frozen=True)
class TokenPair:
    source: int
    target: int


@dataclass(frozen=True)
class TransitionCandidate:
    """One indivisible, proof-backed AST correspondence.

    A candidate is a hyperedge rather than an isolated character pair.  If a
    complete application is selected, all of its rendered tokens are selected
    together.  This prevents a solver from moving ``f`` while recreating its
    parentheses and argument.
    """

    candidate_id: str
    source_node_id: str
    target_node_id: str
    role: TransitionRole
    reason: str
    pairs: tuple[TokenPair, ...]
    certified: bool
    exact_composite: bool
    source_kind: str = ""
    target_kind: str = ""

    @property
    def source_indices(self) -> frozenset[int]:
        return frozenset(pair.source for pair in self.pairs)

    @property
    def target_indices(self) -> frozenset[int]:
        return frozenset(pair.target for pair in self.pairs)


@dataclass(frozen=True)
class TransitionPlan:
    source_count: int
    target_count: int
    selected: tuple[TransitionCandidate, ...]
    created_targets: tuple[int, ...]
    deleted_sources: tuple[int, ...]
    valid: bool
    errors: tuple[str, ...] = ()
    rejected_candidates: tuple[str, ...] = ()

    @property
    def pairs(self) -> tuple[tuple[int, int], ...]:
        return tuple(
            sorted(
                (pair.source, pair.target)
                for candidate in self.selected
                for pair in candidate.pairs
            )
        )


def _candidate_weight(candidate: TransitionCandidate) -> int:
    """Prefer large proof objects, then the nearest certified occurrence."""

    size = len(candidate.pairs)
    # Convex size rewards make one complete expression more valuable than a
    # partition into its children.  Continuity from the immediately preceding
    # conclusion then beats a same-looking copy from an older context row.
    structural = size**3 * 10_000
    role_bonus = {
        TransitionRole.PRESERVE: 3_000,
        TransitionRole.REWRITE: 2_000,
        TransitionRole.COPY: 1_000,
        TransitionRole.CREATE: 0,
        TransitionRole.DELETE: 0,
    }[candidate.role]
    exact_bonus = 100_000 if candidate.exact_composite else 0
    # Geometry is only a tie-breaker *after* Lean provenance, role and
    # composite size have agreed.  It disambiguates several occurrences of the
    # very same logical free variable without ever making an unrelated equal
    # glyph eligible.
    movement_cost = sum(abs(pair.source - pair.target) for pair in candidate.pairs)
    return structural + exact_bonus + role_bonus - movement_cost


def _candidate_errors(
    candidate: TransitionCandidate,
    source_count: int,
    target_count: int,
) -> list[str]:
    errors: list[str] = []
    if not candidate.certified:
        errors.append(f"{candidate.candidate_id}: correspondence is not Lean-certified")
    if candidate.role not in {
        TransitionRole.PRESERVE,
        TransitionRole.COPY,
        TransitionRole.REWRITE,
    }:
        errors.append(f"{candidate.candidate_id}: invalid mapped role {candidate.role}")
    if not candidate.pairs:
        errors.append(f"{candidate.candidate_id}: empty correspondence")
    source_indices = [pair.source for pair in candidate.pairs]
    target_indices = [pair.target for pair in candidate.pairs]
    if len(source_indices) != len(set(source_indices)):
        errors.append(f"{candidate.candidate_id}: repeats a source token internally")
    if len(target_indices) != len(set(target_indices)):
        errors.append(f"{candidate.candidate_id}: repeats a target token internally")
    if any(index < 0 or index >= source_count for index in source_indices):
        errors.append(f"{candidate.candidate_id}: source token is outside the board")
    if any(index < 0 or index >= target_count for index in target_indices):
        errors.append(f"{candidate.candidate_id}: target token is outside the board")
    if (
        (candidate.source_kind == "app" or candidate.target_kind == "app")
        and not candidate.exact_composite
        and candidate.reason not in {
            "verified-structural-shell",
            "verified-premise-branch-shell",
            # Lean can encode a numeral/coercion as an application even when
            # its semantic renderer exposes a single atomic token.  This edge
            # is emitted only for a unique checked fingerprint in an immediate
            # theorem premise and result, so it is a whole surviving subterm,
            # not a partial glyph match inside an application.
            "verified-direct-premise-subexpression",
        }
    ):
        errors.append(
            f"{candidate.candidate_id}: partial function application is forbidden"
        )
    return errors


def solve_transition_plan(
    source_count: int,
    target_count: int,
    candidates: tuple[TransitionCandidate, ...],
) -> TransitionPlan:
    """Select a globally consistent set of certified AST hyperedges.

    Invalid candidates are conservatively discarded.  Unmatched source and
    target tokens become DELETE and CREATE operations; this is always safer
    than inventing a visual identity.
    """

    rejected_errors: list[str] = []
    admissible: list[TransitionCandidate] = []
    for candidate in candidates:
        errors = _candidate_errors(candidate, source_count, target_count)
        if errors:
            rejected_errors.extend(errors)
        else:
            admissible.append(candidate)

    model = cp_model.CpModel()
    variables = [model.new_bool_var(f"edge_{index}") for index in range(len(admissible))]
    source_sets = tuple(candidate.source_indices for candidate in admissible)
    target_sets = tuple(candidate.target_indices for candidate in admissible)
    target_owners: dict[int, list] = {}
    source_owners: dict[int, list] = {}
    for index, candidate in enumerate(admissible):
        for target in target_sets[index]:
            target_owners.setdefault(target, []).append(variables[index])
        if candidate.role != TransitionRole.COPY:
            # COPY is non-consuming: a persistent hypothesis may remain on
            # the board and provide a certified clone to the new conclusion.
            for source in source_sets[index]:
                source_owners.setdefault(source, []).append(variables[index])
    for owners in target_owners.values():
        if len(owners) > 1:
            model.add_at_most_one(owners)
    for owners in source_owners.values():
        if len(owners) > 1:
            model.add_at_most_one(owners)

    if variables:
        model.maximize(
            sum(
                _candidate_weight(candidate) * variables[index]
                for index, candidate in enumerate(admissible)
            )
        )

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    # Animation optimality must never hold a verified proof hostage.  A large
    # normalization step can expose thousands of overlapping certified AST
    # hyperedges.  CP-SAT may keep proving optimality long after it has found
    # a usable plan; after this deadline UNKNOWN safely becomes CREATE/DELETE.
    configured_deadline = os.environ.get("LEAN_PROOF_SOLVER_SECONDS", "0.25")
    try:
        deadline = float(configured_deadline)
    except ValueError:
        deadline = 0.25
    solver.parameters.max_time_in_seconds = max(0.01, min(deadline, 5.0))
    status = solver.solve(model)
    selected = (
        tuple(
            candidate
            for index, candidate in enumerate(admissible)
            if solver.boolean_value(variables[index])
        )
        if status in {cp_model.OPTIMAL, cp_model.FEASIBLE}
        else ()
    )
    # CP-SAT may return a good FEASIBLE plan at the short animation deadline
    # without having switched on every independent positive singleton. Close
    # the plan greedily with certified, non-conflicting candidates. This is
    # especially important for relation/operator shell tokens next to a large
    # substitution: leaving them out would make unchanged syntax blink merely
    # because the global optimum proof timed out.
    if selected:
        selected_list = list(selected)
        selected_ids = {candidate.candidate_id for candidate in selected_list}
        used_targets = {
            pair.target for candidate in selected_list for pair in candidate.pairs
        }
        consumed_sources = {
            pair.source
            for candidate in selected_list
            if candidate.role != TransitionRole.COPY
            for pair in candidate.pairs
        }
        indexed_candidates = sorted(
            enumerate(admissible),
            key=lambda item: (_candidate_weight(item[1]), item[1].candidate_id),
            reverse=True,
        )
        for candidate_index, candidate in indexed_candidates:
            if candidate.candidate_id in selected_ids:
                continue
            if target_sets[candidate_index] & used_targets:
                continue
            if (
                candidate.role != TransitionRole.COPY
                and source_sets[candidate_index] & consumed_sources
            ):
                continue
            selected_list.append(candidate)
            selected_ids.add(candidate.candidate_id)
            used_targets.update(target_sets[candidate_index])
            if candidate.role != TransitionRole.COPY:
                consumed_sources.update(source_sets[candidate_index])
        selected = tuple(selected_list)

    used_source = {
        pair.source for candidate in selected for pair in candidate.pairs
    }
    used_target = {
        pair.target for candidate in selected for pair in candidate.pairs
    }
    plan = TransitionPlan(
        source_count=source_count,
        target_count=target_count,
        selected=selected,
        created_targets=tuple(
            index for index in range(target_count) if index not in used_target
        ),
        deleted_sources=tuple(
            index for index in range(source_count) if index not in used_source
        ),
        valid=status in {cp_model.OPTIMAL, cp_model.FEASIBLE},
        errors=(),
        rejected_candidates=tuple(rejected_errors),
    )
    return validate_transition_plan(plan)


def validate_transition_plan(plan: TransitionPlan) -> TransitionPlan:
    """Independently audit the solver result before Manim sees it."""

    errors = list(plan.errors)
    target_owners: dict[int, str] = {}
    source_owners: dict[int, str] = {}
    for candidate in plan.selected:
        errors.extend(
            _candidate_errors(candidate, plan.source_count, plan.target_count)
        )
        for pair in candidate.pairs:
            previous = target_owners.setdefault(pair.target, candidate.candidate_id)
            if previous != candidate.candidate_id:
                errors.append(
                    f"target token {pair.target} has two origins: "
                    f"{previous} and {candidate.candidate_id}"
                )
            if candidate.role != TransitionRole.COPY:
                previous = source_owners.setdefault(pair.source, candidate.candidate_id)
                if previous != candidate.candidate_id:
                    errors.append(
                        f"source token {pair.source} has two destinations: "
                        f"{previous} and {candidate.candidate_id}"
                    )

    expected_created = tuple(
        index for index in range(plan.target_count) if index not in target_owners
    )
    if plan.created_targets != expected_created:
        errors.append("CREATE coverage does not equal the unmapped target tokens")
    used_source = {
        pair.source for candidate in plan.selected for pair in candidate.pairs
    }
    expected_deleted = tuple(
        index for index in range(plan.source_count) if index not in used_source
    )
    if plan.deleted_sources != expected_deleted:
        errors.append("DELETE coverage does not equal the unmapped source tokens")

    fatal_errors = tuple(dict.fromkeys(errors))
    return TransitionPlan(
        source_count=plan.source_count,
        target_count=plan.target_count,
        selected=plan.selected if not fatal_errors else (),
        created_targets=(
            plan.created_targets if not fatal_errors else tuple(range(plan.target_count))
        ),
        deleted_sources=(
            plan.deleted_sources if not fatal_errors else tuple(range(plan.source_count))
        ),
        valid=plan.valid and not fatal_errors,
        errors=fatal_errors,
        rejected_candidates=plan.rejected_candidates,
    )
