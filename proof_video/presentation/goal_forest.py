"""Renderer-independent projection of Lean's live goal forest.

This module deliberately knows nothing about tactics, pixels, Manim, or
Remotion.  It turns the ordered live goals and their observed metavariable
lineage into immutable presentation records.  A renderer may later choose a
geometry for these records, but it must not reconstruct goal identity or
branch ancestry itself.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import TYPE_CHECKING, Any, Iterable

from proof_video.proof.state import GoalState, ProofState

if TYPE_CHECKING:
    from proof_video.proof.schema import Frame


def _stable_id(namespace: str, *parts: str) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{namespace}:{hashlib.sha256(payload).hexdigest()[:24]}"


def _card_id(lineage_id: str, goal_id: str) -> str:
    identity = f"lineage:{lineage_id}" if lineage_id else f"goal:{goal_id}"
    return _stable_id("goal-card", identity)


def _unobserved_parent_id(goal_id: str) -> str:
    """Return a deterministic ancestry anchor when no prior card is available."""

    return _stable_id("goal-origin", goal_id)


@dataclass(frozen=True)
class GoalCard:
    """One live proof obligation in logical, not geometric, layout space."""

    stable_id: str
    goal_id: str
    lineage_id: str
    parent_card_ids: tuple[str, ...]
    root_card_ids: tuple[str, ...]
    depth: int
    order: int
    sibling_order: int
    branch_kind: str = ""
    branch_index: int | None = None
    focus_rank: int | None = None
    is_active: bool = False
    incoming_relation: str = "root"

    @property
    def is_focused(self) -> bool:
        return self.focus_rank is not None


@dataclass(frozen=True)
class GoalForestLayout:
    """Deterministic logical layout for one immutable proof-state frontier."""

    layout_id: str
    state_fingerprint: str
    cards: tuple[GoalCard, ...]
    root_card_ids: tuple[str, ...]
    focus_card_ids: tuple[str, ...]
    active_card_id: str | None
    introduced_card_ids: tuple[str, ...] = ()
    retired_card_ids: tuple[str, ...] = ()
    closed_card_ids: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def card(self, stable_id: str) -> GoalCard | None:
        return next((item for item in self.cards if item.stable_id == stable_id), None)

    def card_for_goal(self, goal_id: str) -> GoalCard | None:
        return next((item for item in self.cards if item.goal_id == goal_id), None)

    def card_for_lineage(self, lineage_id: str) -> GoalCard | None:
        return next(
            (item for item in self.cards if item.lineage_id == lineage_id), None
        )


@dataclass(frozen=True)
class _GoalView:
    goal_id: str
    lineage_id: str
    parent_goal_id: str | None
    branch_kind: str
    branch_index: int | None


@dataclass(frozen=True)
class _FrontierView:
    goals: tuple[_GoalView, ...]
    focus: tuple[str, ...]
    fingerprint: str
    lineage_edges: tuple[Any, ...]


def _goal_view(goal: GoalState | Any) -> _GoalView:
    return _GoalView(
        goal_id=str(goal.goal_id),
        lineage_id=str(goal.lineage_id),
        parent_goal_id=goal.parent_goal_id,
        branch_kind=str(goal.branch_kind),
        branch_index=goal.branch_index,
    )


def _fallback_fingerprint(goals: tuple[_GoalView, ...], focus: tuple[str, ...]) -> str:
    payload = {
        "goals": [
            {
                "goal": item.goal_id,
                "lineage": item.lineage_id,
                "parent": item.parent_goal_id,
                "branch": item.branch_kind,
                "index": item.branch_index,
            }
            for item in goals
        ],
        "focus": list(focus),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _frontier(value: ProofState | Frame) -> _FrontierView:
    if isinstance(value, ProofState):
        goals = tuple(_goal_view(goal) for goal in value.goals)
        return _FrontierView(goals, value.focus, value.fingerprint, ())

    state = value.proof_state
    if state is not None:
        goals = tuple(_goal_view(goal) for goal in state.goals)
        focus = state.focus
        fingerprint = state.fingerprint
    else:
        goals = tuple(_goal_view(goal) for goal in value.goals)
        focus = tuple(goal.goal_id for goal in value.focus_goals)
        fingerprint = _fallback_fingerprint(goals, focus)
    return _FrontierView(goals, focus, fingerprint, tuple(value.goal_lineage))


def _relation_value(edge: Any) -> str:
    relation = getattr(edge, "relation", "")
    return str(getattr(relation, "value", relation))


def _lineage_parents(goal_id: str, edges: Iterable[Any]) -> tuple[tuple[str, ...], str]:
    """Return genuine branch/join parents, excluding ordinary 1-to-1 evolution."""

    candidates: list[tuple[tuple[str, ...], str]] = []
    for edge in edges:
        sources = tuple(str(item) for item in edge.source_goal_ids)
        targets = tuple(str(item) for item in edge.target_goal_ids)
        if goal_id not in targets or not sources:
            continue
        relation = _relation_value(edge)
        if len(sources) > 1:
            candidates.append((sources, "merge"))
        elif len(targets) > 1:
            candidates.append((sources, "split"))
        elif relation in {"split", "copy", "merge"}:
            candidates.append((sources, relation))
    if not candidates:
        return (), ""
    unique = tuple(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise ValueError(
            f"goal {goal_id} has conflicting incoming structural lineage: {unique!r}"
        )
    return unique[0]


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def build_goal_forest_layout(
    value: ProofState | Frame,
    *,
    previous: GoalForestLayout | None = None,
) -> GoalForestLayout:
    """Project a proof frontier into stable logical goal cards.

    Passing the immediately preceding layout lets a newly-created child refer
    to its consumed parent by stable lineage card ID.  Persistent goals inherit
    their ancestry even though Lean no longer keeps the consumed parent live.
    The result contains no renderer coordinates and is deterministic under an
    identical state, lineage relation, and previous layout.
    """

    frontier = _frontier(value)
    previous_cards = previous.cards if previous is not None else ()
    previous_by_goal = {item.goal_id: item for item in previous_cards}
    previous_by_lineage = {
        item.lineage_id: item for item in previous_cards if item.lineage_id
    }

    identities = [item.lineage_id or f"goal:{item.goal_id}" for item in frontier.goals]
    if len(identities) != len(set(identities)):
        raise ValueError("live goals do not have unique presentation lineages")

    stable_ids = {
        item.goal_id: (
            previous_by_lineage[item.lineage_id].stable_id
            if item.lineage_id and item.lineage_id in previous_by_lineage
            else previous_by_goal[item.goal_id].stable_id
            if item.goal_id in previous_by_goal
            else _card_id(item.lineage_id, item.goal_id)
        )
        for item in frontier.goals
    }
    current_by_goal = {item.goal_id: item for item in frontier.goals}
    focus_rank = {goal_id: rank for rank, goal_id in enumerate(frontier.focus)}
    preliminary: list[GoalCard] = []

    for order, goal in enumerate(frontier.goals):
        prior = (
            previous_by_lineage.get(goal.lineage_id)
            if goal.lineage_id
            else previous_by_goal.get(goal.goal_id)
        )
        lineage_parents, structural_relation = _lineage_parents(
            goal.goal_id, frontier.lineage_edges
        )
        parent_goal_ids = _ordered_unique(
            (
                *((goal.parent_goal_id,) if goal.parent_goal_id else ()),
                *lineage_parents,
            )
        )
        # A new metavariable ID may continue the same lineage after an
        # ordinary rewrite. Its ``parent_goal_id`` is proof provenance, not a
        # new branch level. Only an explicit split/copy/merge edge (or a new
        # lineage) turns that predecessor into a parent card.
        if (
            prior is not None
            and prior.stable_id == stable_ids[goal.goal_id]
            and not lineage_parents
        ):
            parent_goal_ids = ()

        if not parent_goal_ids and prior is not None:
            parent_card_ids = prior.parent_card_ids
            root_card_ids = prior.root_card_ids
            depth = prior.depth
            incoming_relation = prior.incoming_relation
        elif parent_goal_ids:
            parent_cards: list[GoalCard] = []
            parent_card_ids_list: list[str] = []
            for parent_goal_id in parent_goal_ids:
                parent = previous_by_goal.get(parent_goal_id)
                if parent is None and parent_goal_id in current_by_goal:
                    parent_lineage = current_by_goal[parent_goal_id].lineage_id
                    parent = previous_by_lineage.get(parent_lineage)
                if parent is not None:
                    parent_cards.append(parent)
                    parent_card_ids_list.append(parent.stable_id)
                else:
                    parent_card_ids_list.append(_unobserved_parent_id(parent_goal_id))
            parent_card_ids = _ordered_unique(parent_card_ids_list)
            roots: list[str] = []
            for parent_id in parent_card_ids:
                parent = next(
                    (item for item in parent_cards if item.stable_id == parent_id), None
                )
                roots.extend(
                    parent.root_card_ids if parent is not None else (parent_id,)
                )
            root_card_ids = _ordered_unique(roots)
            depth = max((item.depth for item in parent_cards), default=0) + 1
            incoming_relation = structural_relation or "branch"
        else:
            parent_card_ids = ()
            root_card_ids = (stable_ids[goal.goal_id],)
            depth = 0
            incoming_relation = "root"

        rank = focus_rank.get(goal.goal_id)
        preliminary.append(
            GoalCard(
                stable_id=stable_ids[goal.goal_id],
                goal_id=goal.goal_id,
                lineage_id=goal.lineage_id,
                parent_card_ids=parent_card_ids,
                root_card_ids=root_card_ids,
                depth=depth,
                order=order,
                sibling_order=0,
                branch_kind=goal.branch_kind,
                branch_index=goal.branch_index,
                focus_rank=rank,
                is_active=rank == 0,
                incoming_relation=incoming_relation,
            )
        )

    siblings: dict[tuple[str, ...], list[GoalCard]] = {}
    for card in preliminary:
        siblings.setdefault(card.parent_card_ids, []).append(card)
    sibling_ranks: dict[str, int] = {}
    for group in siblings.values():
        ordered = sorted(
            group,
            key=lambda item: (
                item.branch_index is None,
                item.branch_index if item.branch_index is not None else 0,
                item.order,
                item.stable_id,
            ),
        )
        sibling_ranks.update(
            {item.stable_id: rank for rank, item in enumerate(ordered)}
        )
    cards = tuple(
        replace(item, sibling_order=sibling_ranks[item.stable_id])
        for item in preliminary
    )

    current_ids = {item.stable_id for item in cards}
    previous_ids = {item.stable_id for item in previous_cards}
    introduced = tuple(
        item.stable_id for item in cards if item.stable_id not in previous_ids
    )
    retired = tuple(
        item.stable_id for item in previous_cards if item.stable_id not in current_ids
    )
    closed_goal_ids = {
        str(goal_id)
        for edge in frontier.lineage_edges
        if not tuple(edge.target_goal_ids)
        for goal_id in edge.source_goal_ids
    }
    closed = tuple(
        item.stable_id for item in previous_cards if item.goal_id in closed_goal_ids
    )
    focus_card_ids = tuple(
        stable_ids[goal_id] for goal_id in frontier.focus if goal_id in stable_ids
    )
    roots = _ordered_unique(root for card in cards for root in card.root_card_ids)
    layout_identity = json.dumps(
        {
            "state": frontier.fingerprint,
            "cards": [
                {
                    "id": item.stable_id,
                    "goal": item.goal_id,
                    "parents": item.parent_card_ids,
                    "roots": item.root_card_ids,
                    "depth": item.depth,
                    "order": item.order,
                    "sibling": item.sibling_order,
                    "branch": item.branch_kind,
                    "branchIndex": item.branch_index,
                    "focus": item.focus_rank,
                    "active": item.is_active,
                    "relation": item.incoming_relation,
                }
                for item in cards
            ],
            "introduced": introduced,
            "retired": retired,
            "closed": closed,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    layout = GoalForestLayout(
        layout_id=_stable_id("goal-forest", layout_identity),
        state_fingerprint=frontier.fingerprint,
        cards=cards,
        root_card_ids=roots,
        focus_card_ids=focus_card_ids,
        active_card_id=focus_card_ids[0] if focus_card_ids else None,
        introduced_card_ids=introduced,
        retired_card_ids=retired,
        closed_card_ids=closed,
    )
    errors = validate_goal_forest_layout(layout)
    if errors:
        raise ValueError("invalid goal-forest layout: " + "; ".join(errors))
    return layout


def build_goal_forest_timeline(
    values: Iterable[ProofState | Frame],
) -> tuple[GoalForestLayout, ...]:
    """Project an ordered proof run while preserving consumed ancestry.

    Renderers must not build a layout only from their local preview/chunk
    window: doing so gives a branch a different parent (and therefore a
    different visual identity) in a tail preview than in the full movie.  This
    helper makes the sequential dependency explicit and keeps that policy in
    the renderer-independent presentation layer.
    """

    result: list[GoalForestLayout] = []
    previous: GoalForestLayout | None = None
    for value in values:
        current = build_goal_forest_layout(value, previous=previous)
        result.append(current)
        previous = current
    return tuple(result)


def validate_goal_forest_layout(layout: GoalForestLayout) -> tuple[str, ...]:
    errors: list[str] = []
    card_ids = [item.stable_id for item in layout.cards]
    goal_ids = [item.goal_id for item in layout.cards]
    if len(card_ids) != len(set(card_ids)):
        errors.append("duplicate stable goal-card IDs")
    if len(goal_ids) != len(set(goal_ids)):
        errors.append("duplicate live goal IDs")
    if tuple(item.order for item in layout.cards) != tuple(range(len(layout.cards))):
        errors.append("goal-card order is not contiguous")
    known = set(card_ids)
    if any(item not in known for item in layout.focus_card_ids):
        errors.append("focus references a non-live goal card")
    expected_active = layout.focus_card_ids[0] if layout.focus_card_ids else None
    if layout.active_card_id != expected_active:
        errors.append("active card is not the first focused card")
    if not set(layout.closed_card_ids).issubset(set(layout.retired_card_ids)):
        errors.append("closed cards are not retired")
    if not set(layout.introduced_card_ids).issubset(known):
        errors.append("introduced cards are not live")
    if set(layout.retired_card_ids) & known:
        errors.append("retired cards are still live")
    expected_roots = _ordered_unique(
        root for card in layout.cards for root in card.root_card_ids
    )
    if layout.root_card_ids != expected_roots:
        errors.append("forest roots do not match card ancestry")
    for card in layout.cards:
        if card.depth < 0:
            errors.append(f"{card.stable_id}: negative branch depth")
        if card.stable_id in card.parent_card_ids:
            errors.append(f"{card.stable_id}: goal card is its own parent")
        if not card.root_card_ids:
            errors.append(f"{card.stable_id}: no forest root")
        expected_rank = (
            layout.focus_card_ids.index(card.stable_id)
            if card.stable_id in layout.focus_card_ids
            else None
        )
        if card.focus_rank != expected_rank:
            errors.append(f"{card.stable_id}: inconsistent focus rank")
        if card.is_active != (card.stable_id == layout.active_card_id):
            errors.append(f"{card.stable_id}: inconsistent active-focus flag")
    sibling_groups: dict[tuple[str, ...], list[int]] = {}
    for card in layout.cards:
        sibling_groups.setdefault(card.parent_card_ids, []).append(card.sibling_order)
    for parent_ids, ranks in sibling_groups.items():
        if sorted(ranks) != list(range(len(ranks))):
            errors.append(f"siblings below {parent_ids!r} have non-contiguous order")
    return tuple(errors)


__all__ = [
    "GoalCard",
    "GoalForestLayout",
    "build_goal_forest_layout",
    "build_goal_forest_timeline",
    "validate_goal_forest_layout",
]
