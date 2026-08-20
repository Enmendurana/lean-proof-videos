"""Semantic correspondence between canonical proof states.

Correspondence is a relation, not a permutation.  Hyperedges make copying,
splitting, merging, creation and removal explicit without forcing renderers to
guess from equal-looking glyphs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from proof_video.proof.state import (
    ExprOccurrence,
    Expression,
    GoalState,
    LocalDecl,
    ProofState,
    expression_path_sort_key,
)


class EntityKind(str, Enum):
    GOAL = "goal"
    LOCAL = "local"
    LOCAL_TYPE = "local-type"
    LOCAL_VALUE = "local-value"
    TARGET = "target"
    OCCURRENCE = "occurrence"


class MatchProvenance(str, Enum):
    LEAN_IDENTITY = "lean-identity"
    ALIAS = "alias"
    LEAN_DEFEQ = "lean-defeq"
    EXPLICIT = "explicit-semantic"
    TYPED_STRUCTURE = "typed-structure"
    SOURCE_CONTINUITY = "source-continuity"
    STRUCTURAL_TREE = "structural-tree"
    TEXT_FALLBACK = "text-fallback"
    CREATION = "creation"
    REMOVAL = "removal"


_PROVENANCE_PRIORITY = {
    MatchProvenance.LEAN_IDENTITY: 0,
    MatchProvenance.ALIAS: 1,
    MatchProvenance.LEAN_DEFEQ: 2,
    MatchProvenance.EXPLICIT: 3,
    MatchProvenance.TYPED_STRUCTURE: 4,
    MatchProvenance.SOURCE_CONTINUITY: 5,
    MatchProvenance.STRUCTURAL_TREE: 6,
    MatchProvenance.TEXT_FALLBACK: 7,
    MatchProvenance.CREATION: 8,
    MatchProvenance.REMOVAL: 8,
}


class RelationKind(str, Enum):
    PRESERVE = "preserve"
    REWRITE = "rewrite"
    COPY = "copy"
    SPLIT = "split"
    MERGE = "merge"
    CREATE = "create"
    REMOVE = "remove"


@dataclass(frozen=True, order=True)
class EntityRef:
    kind: EntityKind
    goal_id: str
    local_id: str = ""
    expression_role: str = ""
    occurrence_id: str = ""

    @property
    def key(self) -> str:
        return "/".join(
            (
                self.kind.value,
                self.goal_id,
                self.local_id,
                self.expression_role,
                self.occurrence_id,
            )
        )


@dataclass(frozen=True)
class CorrespondenceEdge:
    sources: tuple[EntityRef, ...]
    targets: tuple[EntityRef, ...]
    relation: RelationKind
    provenance: MatchProvenance
    evidence: tuple[str, ...] = ()
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.sources and not self.targets:
            raise ValueError("empty correspondence hyperedge")
        if not self.sources and self.relation != RelationKind.CREATE:
            raise ValueError("a 0→n edge must be creation")
        if not self.targets and self.relation != RelationKind.REMOVE:
            raise ValueError("an n→0 edge must be removal")
        if self.relation in {RelationKind.PRESERVE, RelationKind.REWRITE} and (
            len(self.sources) != 1 or len(self.targets) != 1
        ):
            raise ValueError(f"{self.relation.value} correspondence must be 1→1")
        if self.relation in {RelationKind.COPY, RelationKind.SPLIT} and (
            len(self.sources) != 1 or len(self.targets) < 2
        ):
            raise ValueError(f"{self.relation.value} correspondence must be 1→n")
        if self.relation == RelationKind.MERGE and (
            len(self.sources) < 2 or len(self.targets) != 1
        ):
            raise ValueError("merge correspondence must be n→1")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("correspondence edge repeats a source entity")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("correspondence edge repeats a target entity")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("correspondence confidence is outside [0, 1]")

    @property
    def sort_key(self) -> tuple:
        return (
            _PROVENANCE_PRIORITY[self.provenance],
            self.relation.value,
            tuple(item.key for item in self.sources),
            tuple(item.key for item in self.targets),
            self.evidence,
        )


@dataclass(frozen=True)
class Correspondence:
    edges: tuple[CorrespondenceEdge, ...] = ()

    def normalized(self) -> Correspondence:
        unique: dict[tuple, CorrespondenceEdge] = {}
        for edge in self.edges:
            sources = tuple(sorted(set(edge.sources)))
            targets = tuple(sorted(set(edge.targets)))
            relation = _relation_for_arity(sources, targets, edge.relation)
            normalized = CorrespondenceEdge(
                sources=sources,
                targets=targets,
                relation=relation,
                provenance=edge.provenance,
                evidence=tuple(dict.fromkeys(edge.evidence)),
                confidence=edge.confidence,
            )
            key = (
                sources,
                targets,
                relation,
                edge.provenance,
                normalized.evidence,
            )
            previous = unique.get(key)
            if previous is None or normalized.confidence > previous.confidence:
                unique[key] = normalized
        return Correspondence(
            tuple(sorted(unique.values(), key=lambda item: item.sort_key))
        )

    def sources_for(self, target: EntityRef) -> tuple[EntityRef, ...]:
        return tuple(
            source
            for edge in self.edges
            if target in edge.targets
            for source in edge.sources
        )

    def targets_for(self, source: EntityRef) -> tuple[EntityRef, ...]:
        return tuple(
            target
            for edge in self.edges
            if source in edge.sources
            for target in edge.targets
        )


@dataclass(frozen=True)
class ExplicitOccurrenceEdge:
    """Extractor-provided occurrence relation, before state-level scoping."""

    source_occurrence_id: str
    target_occurrence_id: str
    reason: str
    source_goal_id: str = ""
    target_goal_id: str = ""
    confidence: float = 1.0
    relation: RelationKind | None = None


@dataclass(frozen=True)
class ExplicitGoalEdge:
    """Lean metavariable lineage as a genuine goal hyperedge."""

    source_goal_ids: tuple[str, ...]
    target_goal_ids: tuple[str, ...]
    reason: str
    relation: RelationKind = RelationKind.PRESERVE
    confidence: float = 1.0


def entity_ref_from_json(value: dict) -> EntityRef:
    """Parse the stable project-owned reference exported by Lean ABI 5."""

    return EntityRef(
        kind=EntityKind(str(value["kind"])),
        goal_id=str(value["goalId"]),
        local_id=str(value.get("localId", "")),
        expression_role=str(value.get("expressionRole", "")),
        occurrence_id=str(value.get("occurrenceId", "")),
    )


def canonical_edge_from_json(value: dict) -> CorrespondenceEdge:
    """Parse one native n→m kernel/elaborator correspondence hyperedge."""

    raw_provenance = str(value.get("provenance", "explicit-semantic"))
    try:
        provenance = MatchProvenance(raw_provenance)
    except ValueError:
        provenance = MatchProvenance.EXPLICIT
    return CorrespondenceEdge(
        sources=tuple(entity_ref_from_json(item) for item in value.get("sources", ())),
        targets=tuple(entity_ref_from_json(item) for item in value.get("targets", ())),
        relation=RelationKind(str(value["relation"])),
        provenance=provenance,
        evidence=tuple(
            dict.fromkeys(
                (
                    *(str(item) for item in value.get("evidence", ())),
                    *(
                        (f"extractor-provenance:{raw_provenance}",)
                        if provenance is MatchProvenance.EXPLICIT
                        and raw_provenance != provenance.value
                        else ()
                    ),
                )
            )
        ),
        confidence=float(value.get("confidence", 1.0)),
    )


@dataclass(frozen=True)
class _ExpressionOwner:
    """One expression together with its semantic place in a goal.

    Occurrence matching is deliberately performed over *all* owners of a
    paired goal, not one rendered row at a time.  This is what makes an
    introduction, reversion, specialization or a multi-premise inference a
    genuine cross-row relation rather than a collection of unrelated fades.
    """

    goal: GoalState
    expression: Expression
    role: EntityKind
    expression_role: str
    local_id: str = ""

    @property
    def root_ref(self) -> EntityRef:
        return expression_ref(
            self.goal,
            self.role,
            self.expression_role,
            local_id=self.local_id,
        )

    def node_ref(self, node: ExprOccurrence) -> EntityRef:
        return occurrence_ref(
            self.goal,
            node,
            self.expression_role,
            local_id=self.local_id,
        )


def _relation_for_arity(
    sources: tuple[EntityRef, ...],
    targets: tuple[EntityRef, ...],
    preferred: RelationKind = RelationKind.PRESERVE,
) -> RelationKind:
    if not sources:
        return RelationKind.CREATE
    if not targets:
        return RelationKind.REMOVE
    if len(sources) == 1 and len(targets) > 1:
        return (
            RelationKind.COPY if preferred == RelationKind.COPY else RelationKind.SPLIT
        )
    if len(sources) > 1 and len(targets) == 1:
        return RelationKind.MERGE
    if len(sources) == len(targets) == 1:
        # COPY/SPLIT/MERGE are candidate explanations gathered before the
        # connected components are known.  A candidate that ends up with one
        # endpoint on each side is ordinary continuity, not a degenerate
        # hyperedge.  Keeping the n-ary label here would violate the relation
        # algebra and made old, renderer-independent traces impossible to
        # migrate when their only copy candidate had a single surviving use.
        if preferred is RelationKind.COPY:
            return RelationKind.PRESERVE
        if preferred in {RelationKind.SPLIT, RelationKind.MERGE}:
            return RelationKind.REWRITE
    return preferred


def goal_ref(goal: GoalState) -> EntityRef:
    return EntityRef(EntityKind.GOAL, goal.goal_id)


def local_ref(goal: GoalState, local: LocalDecl) -> EntityRef:
    return EntityRef(EntityKind.LOCAL, goal.goal_id, local.decl_id)


def expression_ref(
    goal: GoalState,
    role: EntityKind,
    expression_role: str,
    *,
    local_id: str = "",
) -> EntityRef:
    return EntityRef(role, goal.goal_id, local_id, expression_role)


def occurrence_ref(
    goal: GoalState,
    occurrence: ExprOccurrence,
    expression_role: str,
    *,
    local_id: str = "",
) -> EntityRef:
    return EntityRef(
        EntityKind.OCCURRENCE,
        goal.goal_id,
        local_id,
        expression_role,
        occurrence.occurrence_id,
    )


def _goal_pairs(
    before: ProofState,
    after: ProofState,
    explicit: tuple[ExplicitGoalEdge, ...],
) -> tuple[
    list[tuple[GoalState, GoalState, MatchProvenance]], list[CorrespondenceEdge]
]:
    pairs: list[tuple[GoalState, GoalState, MatchProvenance]] = []
    edges: list[CorrespondenceEdge] = []
    used_before: set[str] = set()
    used_after: set[str] = set()

    def consume(
        source: GoalState, target: GoalState, provenance: MatchProvenance
    ) -> None:
        used_before.add(source.goal_id)
        used_after.add(target.goal_id)
        pairs.append((source, target, provenance))
        edges.append(
            CorrespondenceEdge(
                (goal_ref(source),),
                (goal_ref(target),),
                RelationKind.PRESERVE,
                provenance,
            )
        )

    before_by_id = {goal.goal_id: goal for goal in before.goals}
    after_by_id = {goal.goal_id: goal for goal in after.goals}

    # Identity and lineage are stronger than imported step evidence.  In
    # particular, a stale compatibility edge must never consume a live goal
    # before the metavariable identity that Lean itself supplied.
    for source in before.goals:
        target = after_by_id.get(source.goal_id)
        if target is not None:
            consume(source, target, MatchProvenance.LEAN_IDENTITY)

    for source in before.goals:
        if source.goal_id in used_before or not source.lineage_id:
            continue
        targets = [
            target
            for target in after.goals
            if target.goal_id not in used_after
            and target.lineage_id == source.lineage_id
        ]
        if len(targets) == 1:
            consume(source, targets[0], MatchProvenance.ALIAS)

    # Explicit proof-assignment evidence is the next authority.  Scope the
    # edge to still-unmatched entities: emitting the original unfiltered IDs
    # here would create a second, competing relation for an entity already
    # fixed by exact identity above.
    for item in sorted(
        explicit,
        key=lambda edge: (
            edge.source_goal_ids,
            edge.target_goal_ids,
            edge.reason,
        ),
    ):
        sources = tuple(
            before_by_id[goal_id]
            for goal_id in item.source_goal_ids
            if goal_id in before_by_id and goal_id not in used_before
        )
        targets = tuple(
            after_by_id[goal_id]
            for goal_id in item.target_goal_ids
            if goal_id in after_by_id and goal_id not in used_after
        )
        if not sources and not targets:
            continue
        source_refs = tuple(goal_ref(goal) for goal in sources)
        target_refs = tuple(goal_ref(goal) for goal in targets)
        edges.append(
            CorrespondenceEdge(
                source_refs,
                target_refs,
                _relation_for_arity(source_refs, target_refs, item.relation),
                MatchProvenance.EXPLICIT,
                (item.reason,),
                item.confidence,
            )
        )
        used_before.update(goal.goal_id for goal in sources)
        used_after.update(goal.goal_id for goal in targets)
        if len(sources) == len(targets) == 1:
            pairs.append((sources[0], targets[0], MatchProvenance.EXPLICIT))

    # Lean's metavariable-parent relation is a genuine 1→n branch relation.
    for source in before.goals:
        if source.goal_id in used_before:
            continue
        children = [
            target
            for target in after.goals
            if target.goal_id not in used_after
            and target.parent_goal_id == source.goal_id
        ]
        if children:
            used_before.add(source.goal_id)
            used_after.update(item.goal_id for item in children)
            edges.append(
                CorrespondenceEdge(
                    (goal_ref(source),),
                    tuple(goal_ref(item) for item in children),
                    RelationKind.SPLIT,
                    MatchProvenance.LEAN_IDENTITY,
                    ("metavariable-parent",),
                )
            )

    # A merge is rare in tactic mode, but the relation supports it directly.
    for target in after.goals:
        if target.goal_id in used_after or target.parent_goal_id is None:
            continue
        parents = [
            source
            for source in before.goals
            if source.goal_id not in used_before
            and source.goal_id == target.parent_goal_id
        ]
        if len(parents) > 1:
            used_before.update(item.goal_id for item in parents)
            used_after.add(target.goal_id)
            edges.append(
                CorrespondenceEdge(
                    tuple(goal_ref(item) for item in parents),
                    (goal_ref(target),),
                    RelationKind.MERGE,
                    MatchProvenance.LEAN_IDENTITY,
                    ("metavariable-join",),
                )
            )

    for source in before.goals:
        if source.goal_id not in used_before:
            edges.append(
                CorrespondenceEdge(
                    (goal_ref(source),),
                    (),
                    RelationKind.REMOVE,
                    MatchProvenance.REMOVAL,
                )
            )
    for target in after.goals:
        if target.goal_id not in used_after:
            edges.append(
                CorrespondenceEdge(
                    (),
                    (goal_ref(target),),
                    RelationKind.CREATE,
                    MatchProvenance.CREATION,
                )
            )
    return pairs, edges


def _local_pairs(
    source_goal: GoalState, target_goal: GoalState
) -> tuple[
    list[tuple[LocalDecl, LocalDecl, MatchProvenance]], list[CorrespondenceEdge]
]:
    pairs: list[tuple[LocalDecl, LocalDecl, MatchProvenance]] = []
    edges: list[CorrespondenceEdge] = []
    used_source: set[str] = set()
    used_target: set[str] = set()

    def consume(
        source: LocalDecl, target: LocalDecl, provenance: MatchProvenance
    ) -> None:
        used_source.add(source.decl_id)
        used_target.add(target.decl_id)
        relation = RelationKind.PRESERVE if source == target else RelationKind.REWRITE
        pairs.append((source, target, provenance))
        edges.append(
            CorrespondenceEdge(
                (local_ref(source_goal, source),),
                (local_ref(target_goal, target),),
                relation,
                provenance,
            )
        )

    targets_by_id = {local.decl_id: local for local in target_goal.locals}
    for source in source_goal.locals:
        target = targets_by_id.get(source.decl_id)
        if target is not None:
            consume(source, target, MatchProvenance.LEAN_IDENTITY)

    for source in source_goal.locals:
        if source.decl_id in used_source:
            continue
        candidates = [
            target
            for target in target_goal.locals
            if target.decl_id not in used_target
            and (
                source.decl_id in target.aliases
                or target.decl_id in source.aliases
                or bool(set(source.aliases) & set(target.aliases))
            )
        ]
        if len(candidates) == 1:
            consume(source, candidates[0], MatchProvenance.ALIAS)

    # ``replace h := ...`` creates a fresh fvar whose checked value depends on
    # the old h.  This dependency is the semantic bridge; the reused user name
    # alone would not be sufficient.
    for source in source_goal.locals:
        if source.decl_id in used_source:
            continue
        candidates = [
            target
            for target in target_goal.locals
            if target.decl_id not in used_target
            and target.user_name == source.user_name
            and source.decl_id in target.dependencies
        ]
        if len(candidates) == 1:
            consume(source, candidates[0], MatchProvenance.EXPLICIT)

    for source in source_goal.locals:
        if source.decl_id in used_source or source.source_range is None:
            continue
        candidates = [
            target
            for target in target_goal.locals
            if target.decl_id not in used_target
            and target.source_range == source.source_range
        ]
        if len(candidates) == 1:
            consume(source, candidates[0], MatchProvenance.SOURCE_CONTINUITY)

    for source in source_goal.locals:
        if source.decl_id not in used_source:
            edges.append(
                CorrespondenceEdge(
                    (local_ref(source_goal, source),),
                    (),
                    RelationKind.REMOVE,
                    MatchProvenance.REMOVAL,
                )
            )
    for target in target_goal.locals:
        if target.decl_id not in used_target:
            edges.append(
                CorrespondenceEdge(
                    (),
                    (local_ref(target_goal, target),),
                    RelationKind.CREATE,
                    MatchProvenance.CREATION,
                )
            )
    return pairs, edges


def _rendered_occurrence(node: ExprOccurrence, expression: Expression) -> str:
    return "".join(expression.latex[span.start : span.end] for span in node.latex_spans)


def _goal_expression_owners(goal: GoalState) -> tuple[_ExpressionOwner, ...]:
    owners: list[_ExpressionOwner] = [
        _ExpressionOwner(goal, goal.target, EntityKind.TARGET, "target")
    ]
    for local in goal.locals:
        owners.append(
            _ExpressionOwner(
                goal,
                local.type_expr,
                EntityKind.LOCAL_TYPE,
                "local-type",
                local.decl_id,
            )
        )
        if local.value_expr is not None:
            owners.append(
                _ExpressionOwner(
                    goal,
                    local.value_expr,
                    EntityKind.LOCAL_VALUE,
                    "local-value",
                    local.decl_id,
                )
            )
    return tuple(owners)


def _paired_expression_owners(
    source_goal: GoalState,
    target_goal: GoalState,
    local_pairs: Iterable[tuple[LocalDecl, LocalDecl, MatchProvenance]],
) -> tuple[tuple[_ExpressionOwner, _ExpressionOwner], ...]:
    result: list[tuple[_ExpressionOwner, _ExpressionOwner]] = [
        (
            _ExpressionOwner(
                source_goal, source_goal.target, EntityKind.TARGET, "target"
            ),
            _ExpressionOwner(
                target_goal, target_goal.target, EntityKind.TARGET, "target"
            ),
        )
    ]
    for source, target, _provenance in local_pairs:
        result.append(
            (
                _ExpressionOwner(
                    source_goal,
                    source.type_expr,
                    EntityKind.LOCAL_TYPE,
                    "local-type",
                    source.decl_id,
                ),
                _ExpressionOwner(
                    target_goal,
                    target.type_expr,
                    EntityKind.LOCAL_TYPE,
                    "local-type",
                    target.decl_id,
                ),
            )
        )
        if source.value_expr is not None and target.value_expr is not None:
            result.append(
                (
                    _ExpressionOwner(
                        source_goal,
                        source.value_expr,
                        EntityKind.LOCAL_VALUE,
                        "local-value",
                        source.decl_id,
                    ),
                    _ExpressionOwner(
                        target_goal,
                        target.value_expr,
                        EntityKind.LOCAL_VALUE,
                        "local-value",
                        target.decl_id,
                    ),
                )
            )
    return tuple(result)


_OccurrenceMatch = tuple[
    _ExpressionOwner,
    ExprOccurrence,
    _ExpressionOwner,
    ExprOccurrence,
    MatchProvenance,
    str,
    float,
    RelationKind | None,
]


def _global_expression_edges(
    source_goal: GoalState,
    target_goal: GoalState,
    owner_pairs: tuple[tuple[_ExpressionOwner, _ExpressionOwner], ...],
    explicit: tuple[ExplicitOccurrenceEdge, ...],
) -> list[CorrespondenceEdge]:
    """Match occurrences over a whole paired goal.

    A formula row is a layout choice, not a semantic boundary.  This matcher
    therefore permits a certified subtree to move between target and context,
    or between two different hypotheses.  Ambiguous repeated atoms remain
    unmatched unless Lean identity, extractor evidence, or a matched parent
    disambiguates them.
    """

    source_owners = _goal_expression_owners(source_goal)
    target_owners = _goal_expression_owners(target_goal)
    matches: list[_OccurrenceMatch] = []
    used_source: set[EntityRef] = set()
    used_target: set[EntityRef] = set()

    def add(
        source_owner: _ExpressionOwner,
        source_node: ExprOccurrence,
        target_owner: _ExpressionOwner,
        target_node: ExprOccurrence,
        provenance: MatchProvenance,
        evidence: str,
        confidence: float = 1.0,
        relation: RelationKind | None = None,
        *,
        allow_reuse: bool = False,
    ) -> bool:
        source_ref = source_owner.node_ref(source_node)
        target_ref = target_owner.node_ref(target_node)
        if not allow_reuse and (source_ref in used_source or target_ref in used_target):
            return False
        candidate = (
            source_owner,
            source_node,
            target_owner,
            target_node,
            provenance,
            evidence,
            confidence,
            relation,
        )
        if candidate not in matches:
            matches.append(candidate)
        used_source.add(source_ref)
        used_target.add(target_ref)
        return True

    def catalog(
        owners: tuple[_ExpressionOwner, ...], occurrence_id: str
    ) -> list[tuple[_ExpressionOwner, ExprOccurrence]]:
        return [
            (owner, node)
            for owner in owners
            for node in owner.expression.occurrences
            if node.occurrence_id == occurrence_id
        ]

    def unique_pass(
        source_scope: Iterable[_ExpressionOwner],
        target_scope: Iterable[_ExpressionOwner],
        provenance: MatchProvenance,
        key,
        evidence: str,
    ) -> None:
        source_groups: dict[tuple, list[tuple[_ExpressionOwner, ExprOccurrence]]] = {}
        target_groups: dict[tuple, list[tuple[_ExpressionOwner, ExprOccurrence]]] = {}
        for owner in source_scope:
            for node in owner.expression.occurrences:
                if owner.node_ref(node) in used_source:
                    continue
                value = key(owner, node)
                if value is not None:
                    source_groups.setdefault(value, []).append((owner, node))
        for owner in target_scope:
            for node in owner.expression.occurrences:
                if owner.node_ref(node) in used_target:
                    continue
                value = key(owner, node)
                if value is not None:
                    target_groups.setdefault(value, []).append((owner, node))
        for value in sorted(source_groups.keys() & target_groups.keys(), key=repr):
            old = source_groups[value]
            new = target_groups[value]
            if len(old) == len(new) == 1:
                add(old[0][0], old[0][1], new[0][0], new[0][1], provenance, evidence)

    # First preserve position inside semantically paired expression owners.
    # The owner pair is part of the key, so an equal-looking atom in another
    # row cannot steal the match.
    for pair_index, (source_owner, target_owner) in enumerate(owner_pairs):
        pair_key = f"owner:{pair_index}"
        unique_pass(
            (source_owner,),
            (target_owner,),
            MatchProvenance.LEAN_IDENTITY,
            lambda _owner, node, pair_key=pair_key: (
                (
                    pair_key,
                    node.kind,
                    node.lean_identity,
                    node.path,
                    node.type_fingerprint,
                )
                if node.lean_identity
                else None
            ),
            "same-lean-identity-owner-and-path",
        )
        unique_pass(
            (source_owner,),
            (target_owner,),
            MatchProvenance.ALIAS,
            lambda _owner, node, pair_key=pair_key: (
                (
                    pair_key,
                    node.kind,
                    tuple(sorted(node.aliases)),
                    node.path,
                    node.type_fingerprint,
                )
                if node.aliases
                else None
            ),
            "same-elaborator-alias-owner-and-path",
        )
    # Next allow exact identity to cross row boundaries.  Uniqueness over the
    # *whole goal* is essential: repeated ``f``/``x`` glyphs are never paired
    # merely because one happens to be geometrically convenient.
    unique_pass(
        source_owners,
        target_owners,
        MatchProvenance.LEAN_IDENTITY,
        lambda _owner, node: (
            (
                node.kind,
                node.lean_identity,
                node.type_fingerprint,
            )
            if node.lean_identity
            else None
        ),
        "unique-lean-identity-across-goal",
    )

    # Alias sets need not be textually identical.  A reciprocal unique graph
    # gives a deterministic 1→1 match without turning a shared alias into a
    # permutation heuristic.
    old_unmatched = [
        (owner, node)
        for owner in source_owners
        for node in owner.expression.occurrences
        if owner.node_ref(node) not in used_source and node.aliases
    ]
    new_unmatched = [
        (owner, node)
        for owner in target_owners
        for node in owner.expression.occurrences
        if owner.node_ref(node) not in used_target and node.aliases
    ]
    alias_candidates: dict[
        EntityRef, list[tuple[_ExpressionOwner, ExprOccurrence]]
    ] = {}
    reverse_alias_candidates: dict[
        EntityRef, list[tuple[_ExpressionOwner, ExprOccurrence]]
    ] = {}
    for old_owner, old_node in old_unmatched:
        old_names = set(old_node.aliases) | (
            {old_node.lean_identity} if old_node.lean_identity else set()
        )
        for new_owner, new_node in new_unmatched:
            new_names = set(new_node.aliases) | (
                {new_node.lean_identity} if new_node.lean_identity else set()
            )
            if (
                old_node.kind == new_node.kind
                and old_node.type_fingerprint == new_node.type_fingerprint
                and old_names & new_names
            ):
                alias_candidates.setdefault(old_owner.node_ref(old_node), []).append(
                    (new_owner, new_node)
                )
                reverse_alias_candidates.setdefault(
                    new_owner.node_ref(new_node), []
                ).append((old_owner, old_node))
    for old_owner, old_node in old_unmatched:
        candidates = alias_candidates.get(old_owner.node_ref(old_node), ())
        if len(candidates) != 1:
            continue
        new_owner, new_node = candidates[0]
        if len(reverse_alias_candidates.get(new_owner.node_ref(new_node), ())) == 1:
            add(
                old_owner,
                old_node,
                new_owner,
                new_node,
                MatchProvenance.ALIAS,
                "reciprocal-unique-alias-across-goal",
            )

    # Only after exact identity and aliases have fixed their entities may
    # proof-step evidence claim the remaining occurrences.  The relation is
    # still checked against conflicting Lean identities for every relation,
    # including REWRITE: a rewrite justifies a mathematical change, not an
    # arbitrary permutation of equal-looking atoms.
    for item in sorted(
        explicit,
        key=lambda edge: (
            edge.source_occurrence_id != edge.target_occurrence_id,
            edge.source_occurrence_id,
            edge.target_occurrence_id,
            edge.reason,
            edge.confidence,
        ),
    ):
        old = catalog(source_owners, item.source_occurrence_id)
        new = catalog(target_owners, item.target_occurrence_id)
        if len(old) != 1 or len(new) != 1:
            continue
        old_owner, old_node = old[0]
        new_owner, new_node = new[0]
        old_ref = old_owner.node_ref(old_node)
        new_ref = new_owner.node_ref(new_node)
        reusable = item.relation in {
            RelationKind.COPY,
            RelationKind.SPLIT,
            RelationKind.MERGE,
        }
        if not reusable and (old_ref in used_source or new_ref in used_target):
            continue
        old_names = {old_node.lean_identity, *old_node.aliases} - {""}
        new_names = {new_node.lean_identity, *new_node.aliases} - {""}
        if (
            old_node.lean_identity
            and new_node.lean_identity
            and not old_names.intersection(new_names)
        ):
            continue
        if (
            item.relation in {None, RelationKind.PRESERVE}
            and old_node.fingerprint
            and new_node.fingerprint
            and old_node.fingerprint != new_node.fingerprint
        ):
            continue
        add(
            old_owner,
            old_node,
            new_owner,
            new_node,
            MatchProvenance.EXPLICIT,
            item.reason,
            item.confidence,
            item.relation,
            allow_reuse=reusable,
        )

    # Typed structure and source continuity are weaker than certified step
    # evidence.  First use them within already paired expression owners.
    for pair_index, (source_owner, target_owner) in enumerate(owner_pairs):
        pair_key = f"owner:{pair_index}"
        unique_pass(
            (source_owner,),
            (target_owner,),
            MatchProvenance.TYPED_STRUCTURE,
            lambda _owner, node, pair_key=pair_key: (
                (
                    pair_key,
                    node.kind,
                    node.fingerprint,
                    node.type_fingerprint,
                    node.path,
                )
                if node.fingerprint
                else None
            ),
            "same-typed-subtree-owner-and-path",
        )
        unique_pass(
            (source_owner,),
            (target_owner,),
            MatchProvenance.SOURCE_CONTINUITY,
            lambda _owner, node, pair_key=pair_key: (
                (pair_key, node.kind, node.source_range)
                if node.source_range is not None
                else None
            ),
            "same-source-range",
        )

    unique_pass(
        source_owners,
        target_owners,
        MatchProvenance.TYPED_STRUCTURE,
        lambda _owner, node: (
            (
                node.kind,
                node.fingerprint,
                node.type_fingerprint,
            )
            if node.fingerprint
            else None
        ),
        "unique-typed-subtree-across-goal",
    )
    unique_pass(
        source_owners,
        target_owners,
        MatchProvenance.SOURCE_CONTINUITY,
        lambda _owner, node: (
            (node.kind, node.source_range) if node.source_range is not None else None
        ),
        "unique-source-range-across-goal",
    )

    # Once a parent subtree has a certified 1→1 relation, its children can be
    # paired by typed child position.  This preserves whole applications such
    # as ``f(x)`` instead of moving only the glyph ``f``.
    while True:
        parent_map: dict[EntityRef, EntityRef] = {}
        for old_owner, old_node, new_owner, new_node, *_rest in matches:
            old_ref = old_owner.node_ref(old_node)
            new_ref = new_owner.node_ref(new_node)
            existing = parent_map.get(old_ref)
            if existing is None:
                parent_map[old_ref] = new_ref
            elif existing != new_ref:
                parent_map.pop(old_ref, None)
        before_count = len(matches)
        source_groups: dict[tuple, list[tuple[_ExpressionOwner, ExprOccurrence]]] = {}
        target_groups: dict[tuple, list[tuple[_ExpressionOwner, ExprOccurrence]]] = {}
        for owner in source_owners:
            by_id = {node.occurrence_id: node for node in owner.expression.occurrences}
            for node in owner.expression.occurrences:
                if owner.node_ref(node) in used_source or node.parent_id not in by_id:
                    continue
                mapped_parent = parent_map.get(owner.node_ref(by_id[node.parent_id]))
                if mapped_parent is None:
                    continue
                key = (
                    mapped_parent,
                    node.path[-1:] if node.path else (),
                    node.kind,
                    node.fingerprint,
                    node.type_fingerprint,
                    node.lean_identity,
                )
                source_groups.setdefault(key, []).append((owner, node))
        for owner in target_owners:
            by_id = {node.occurrence_id: node for node in owner.expression.occurrences}
            for node in owner.expression.occurrences:
                if owner.node_ref(node) in used_target or node.parent_id not in by_id:
                    continue
                parent_ref = owner.node_ref(by_id[node.parent_id])
                key = (
                    parent_ref,
                    node.path[-1:] if node.path else (),
                    node.kind,
                    node.fingerprint,
                    node.type_fingerprint,
                    node.lean_identity,
                )
                target_groups.setdefault(key, []).append((owner, node))
        for key in sorted(source_groups.keys() & target_groups.keys(), key=repr):
            old = source_groups[key]
            new = target_groups[key]
            if len(old) == len(new) == 1:
                add(
                    old[0][0],
                    old[0][1],
                    new[0][0],
                    new[0][1],
                    MatchProvenance.STRUCTURAL_TREE,
                    "typed-child-of-certified-parent",
                )
        if len(matches) == before_count:
            break

    # A semantically unchanged subtree may remain in its original owner and
    # also occur in a newly constructed row.  This is a genuine copy, not a
    # second consumption of the source.  The ordinary one-to-one passes above
    # intentionally reserve the source for its stationary occurrence; here we
    # add only globally unique, typed structural copies and let the connected
    # component below normalize ``preserve + copies`` into one 1→n
    # hyperedge.  Repeated equal-looking source subtrees remain ambiguous and
    # are never paired by this rule.
    source_copy_groups: dict[
        tuple[str, str, str, str], list[tuple[_ExpressionOwner, ExprOccurrence]]
    ] = {}
    target_copy_groups: dict[
        tuple[str, str, str, str], list[tuple[_ExpressionOwner, ExprOccurrence]]
    ] = {}

    def copy_key(node: ExprOccurrence) -> tuple[str, str, str, str] | None:
        if not node.fingerprint:
            return None
        return (
            node.kind,
            node.fingerprint,
            node.type_fingerprint,
            node.lean_identity,
        )

    for owner in source_owners:
        for node in owner.expression.occurrences:
            key = copy_key(node)
            if key is not None:
                source_copy_groups.setdefault(key, []).append((owner, node))
    for owner in target_owners:
        for node in owner.expression.occurrences:
            if owner.node_ref(node) in used_target:
                continue
            key = copy_key(node)
            if key is not None:
                target_copy_groups.setdefault(key, []).append((owner, node))

    copy_frontier: list[
        tuple[
            _ExpressionOwner,
            ExprOccurrence,
            _ExpressionOwner,
            ExprOccurrence,
        ]
    ] = []
    copy_keys = sorted(
        source_copy_groups.keys() & target_copy_groups.keys(),
        key=lambda key: (
            min(len(node.path) for _owner, node in source_copy_groups[key]),
            repr(key),
        ),
    )
    for key in copy_keys:
        sources = source_copy_groups[key]
        targets = target_copy_groups[key]
        if len(sources) != 1:
            continue
        source_owner, source_node = sources[0]
        source_ref = source_owner.node_ref(source_node)
        # An unused unique 1→1 pair was already handled by the normal typed
        # pass.  Reaching it here would add no information.
        if source_ref not in used_source and len(targets) == 1:
            continue
        uncovered_targets = [
            (target_owner, target_node)
            for target_owner, target_node in targets
            if not any(
                previous_source_owner.root_ref == source_owner.root_ref
                and source_node.path[: len(previous_source.path)]
                == previous_source.path
                and previous_target_owner.root_ref == target_owner.root_ref
                and target_node.path[: len(previous_target.path)]
                == previous_target.path
                for (
                    previous_source_owner,
                    previous_source,
                    previous_target_owner,
                    previous_target,
                ) in copy_frontier
            )
        ]
        for target_owner, target_node in sorted(
            uncovered_targets,
            key=lambda item: (
                len(item[1].path),
                expression_path_sort_key(item[1].path),
                item[0].root_ref.key,
                item[1].occurrence_id,
            ),
        ):
            add(
                source_owner,
                source_node,
                target_owner,
                target_node,
                MatchProvenance.TYPED_STRUCTURE,
                "unique-typed-subtree-copy-across-goal",
                relation=RelationKind.COPY,
                allow_reuse=True,
            )
            copy_frontier.append((source_owner, source_node, target_owner, target_node))

    # Text is a local diagnostic fallback only.  It never crosses owners and
    # adapters do not promote it to physical motion.
    for pair_index, (source_owner, target_owner) in enumerate(owner_pairs):
        pair_key = f"owner:{pair_index}"
        unique_pass(
            (source_owner,),
            (target_owner,),
            MatchProvenance.TEXT_FALLBACK,
            lambda owner, node, pair_key=pair_key: (
                (
                    pair_key,
                    node.kind,
                    _rendered_occurrence(node, owner.expression),
                )
                if not node.lean_identity
                and _rendered_occurrence(node, owner.expression)
                else None
            ),
            "unique-rendered-fallback-within-owner",
        )

    edges: list[CorrespondenceEdge] = []
    remaining = list(matches)
    while remaining:
        component = [remaining.pop(0)]
        source_refs = {component[0][0].node_ref(component[0][1])}
        target_refs = {component[0][2].node_ref(component[0][3])}
        changed = True
        while changed:
            changed = False
            for item in list(remaining):
                source_ref = item[0].node_ref(item[1])
                target_ref = item[2].node_ref(item[3])
                if source_ref in source_refs or target_ref in target_refs:
                    remaining.remove(item)
                    component.append(item)
                    source_refs.add(source_ref)
                    target_refs.add(target_ref)
                    changed = True
        provenance = min(
            (item[4] for item in component),
            key=lambda item: _PROVENANCE_PRIORITY[item],
        )
        preferred = next(
            (item[7] for item in component if item[7] is not None),
            RelationKind.PRESERVE
            if all(
                item[1].kind == item[3].kind
                and item[1].fingerprint == item[3].fingerprint
                for item in component
            )
            else RelationKind.REWRITE,
        )
        edges.append(
            CorrespondenceEdge(
                tuple(sorted(source_refs)),
                tuple(sorted(target_refs)),
                _relation_for_arity(
                    tuple(sorted(source_refs)),
                    tuple(sorted(target_refs)),
                    preferred,
                ),
                provenance,
                tuple(dict.fromkeys(item[5] for item in component)),
                min(item[6] for item in component),
            )
        )

    mapped_source = {
        ref
        for edge in edges
        for ref in edge.sources
        if ref.kind == EntityKind.OCCURRENCE
    }
    mapped_target = {
        ref
        for edge in edges
        for ref in edge.targets
        if ref.kind == EntityKind.OCCURRENCE
    }
    for owner in source_owners:
        for node in owner.expression.occurrences:
            ref = owner.node_ref(node)
            if ref not in mapped_source:
                edges.append(
                    CorrespondenceEdge(
                        (ref,), (), RelationKind.REMOVE, MatchProvenance.REMOVAL
                    )
                )
    for owner in target_owners:
        for node in owner.expression.occurrences:
            ref = owner.node_ref(node)
            if ref not in mapped_target:
                edges.append(
                    CorrespondenceEdge(
                        (), (ref,), RelationKind.CREATE, MatchProvenance.CREATION
                    )
                )
    return edges


def build_correspondence(
    before: ProofState,
    after: ProofState,
    *,
    explicit_occurrence_edges: Iterable[ExplicitOccurrenceEdge] = (),
    explicit_goal_edges: Iterable[ExplicitGoalEdge] = (),
    explicit_entity_edges: Iterable[CorrespondenceEdge] = (),
) -> Correspondence:
    explicit_all = tuple(explicit_occurrence_edges)
    goal_pairs, edges = _goal_pairs(before, after, tuple(explicit_goal_edges))
    for source_goal, target_goal, _goal_provenance in goal_pairs:
        local_pairs, local_edges = _local_pairs(source_goal, target_goal)
        edges.extend(local_edges)
        target_explicit = tuple(
            item
            for item in explicit_all
            if (not item.source_goal_id or item.source_goal_id == source_goal.goal_id)
            and (not item.target_goal_id or item.target_goal_id == target_goal.goal_id)
        )
        owner_pairs = _paired_expression_owners(source_goal, target_goal, local_pairs)
        paired_source_roots = {source.root_ref for source, _target in owner_pairs}
        paired_target_roots = {target.root_ref for _source, target in owner_pairs}
        for source_owner, target_owner in owner_pairs:
            exact = (
                source_owner.expression.canonical_key
                == target_owner.expression.canonical_key
            )
            provenance = (
                MatchProvenance.LEAN_IDENTITY
                if source_owner.expression.expression_id
                and source_owner.expression.expression_id
                == target_owner.expression.expression_id
                else MatchProvenance.TYPED_STRUCTURE
            )
            edges.append(
                CorrespondenceEdge(
                    (source_owner.root_ref,),
                    (target_owner.root_ref,),
                    RelationKind.PRESERVE if exact else RelationKind.REWRITE,
                    provenance,
                    ("same-expression" if exact else "changed-expression",),
                )
            )
        for source_owner in _goal_expression_owners(source_goal):
            if source_owner.root_ref not in paired_source_roots:
                edges.append(
                    CorrespondenceEdge(
                        (source_owner.root_ref,),
                        (),
                        RelationKind.REMOVE,
                        MatchProvenance.REMOVAL,
                    )
                )
        for target_owner in _goal_expression_owners(target_goal):
            if target_owner.root_ref not in paired_target_roots:
                edges.append(
                    CorrespondenceEdge(
                        (),
                        (target_owner.root_ref,),
                        RelationKind.CREATE,
                        MatchProvenance.CREATION,
                    )
                )
        edges.extend(
            _global_expression_edges(
                source_goal, target_goal, owner_pairs, target_explicit
            )
        )
    result = Correspondence(tuple(edges)).normalized()
    return _augment_native_correspondence(
        before, after, result, tuple(explicit_entity_edges)
    )


def _augment_native_correspondence(
    before: ProofState,
    after: ProofState,
    base: Correspondence,
    additions: tuple[CorrespondenceEdge, ...],
) -> Correspondence:
    """Overlay extractor-certified hyperedges without weakening identity.

    The ordinary matcher first establishes exact Lean identities and aliases.
    Native ABI-5 evidence is then allowed to replace creation/removal and
    weaker structural guesses.  A native n-ary edge may absorb an exact
    one-to-one subedge (for example, preserve one occurrence and copy it to a
    second owner), but it may never redirect an exact identity to a different
    entity.  Such a contradiction is an extractor/schema error, not a place
    for visual guessing.
    """

    if not additions:
        return base
    source_refs = set(_all_entity_refs(before))
    target_refs = set(_all_entity_refs(after))
    edges = list(base.edges)
    for addition in Correspondence(additions).normalized().edges:
        missing_sources = set(addition.sources) - source_refs
        missing_targets = set(addition.targets) - target_refs
        if missing_sources or missing_targets:
            missing = sorted((ref.key for ref in (*missing_sources, *missing_targets)))
            raise ValueError(
                "native correspondence references nonexistent entities: "
                + ", ".join(missing)
            )
        touching = [
            edge
            for edge in edges
            if set(addition.sources).intersection(edge.sources)
            or set(addition.targets).intersection(edge.targets)
        ]
        absorbed: list[CorrespondenceEdge] = []
        for edge in touching:
            if edge.relation in {RelationKind.CREATE, RelationKind.REMOVE}:
                absorbed.append(edge)
                continue
            contained = set(edge.sources).issubset(addition.sources) and set(
                edge.targets
            ).issubset(addition.targets)
            stronger = (
                _PROVENANCE_PRIORITY[edge.provenance]
                < _PROVENANCE_PRIORITY[addition.provenance]
            )
            if stronger and not contained:
                raise ValueError(
                    "native correspondence conflicts with stronger semantic "
                    f"identity: {edge.sort_key!r} vs {addition.sort_key!r}"
                )
            absorbed.append(edge)
        if absorbed:
            strongest = min(
                (addition, *absorbed),
                key=lambda edge: _PROVENANCE_PRIORITY[edge.provenance],
            )
            addition = CorrespondenceEdge(
                addition.sources,
                addition.targets,
                addition.relation,
                strongest.provenance,
                tuple(
                    dict.fromkeys(
                        item for edge in (addition, *absorbed) for item in edge.evidence
                    )
                ),
                min(edge.confidence for edge in (addition, *absorbed)),
            )
            edges = [edge for edge in edges if edge not in absorbed]
        edges.append(addition)
    return Correspondence(tuple(edges)).normalized()


def validate_correspondence(
    before: ProofState, after: ProofState, correspondence: Correspondence
) -> tuple[str, ...]:
    source_refs = set(_all_entity_refs(before))
    target_refs = set(_all_entity_refs(after))
    errors: list[str] = []
    source_uses: dict[EntityRef, list[int]] = {}
    target_uses: dict[EntityRef, list[int]] = {}
    for index, edge in enumerate(correspondence.edges):
        for source in edge.sources:
            source_uses.setdefault(source, []).append(index)
            if source not in source_refs:
                errors.append(f"edge {index}: nonexistent source {source.key}")
        for target in edge.targets:
            target_uses.setdefault(target, []).append(index)
            if target not in target_refs:
                errors.append(f"edge {index}: nonexistent target {target.key}")
        for source in edge.sources:
            for target in edge.targets:
                kinds = {source.kind, target.kind}
                compatible = source.kind is target.kind or kinds == {
                    EntityKind.LOCAL,
                    EntityKind.OCCURRENCE,
                }
                if not compatible:
                    errors.append(
                        f"edge {index}: incompatible entity kinds "
                        f"{source.kind.value}→{target.kind.value}"
                    )
                if kinds == {EntityKind.LOCAL, EntityKind.OCCURRENCE} and not any(
                    "binder" in item.lower() for item in edge.evidence
                ):
                    errors.append(
                        f"edge {index}: cross-role local/occurrence relation "
                        "has no binder evidence"
                    )
        if edge.provenance == MatchProvenance.TEXT_FALLBACK:
            # Text may propose continuity, but never overrides a conflicting
            # Lean identity. The matcher already excludes such atoms; keep a
            # validator guard for hand-authored/imported relations.
            if any(ref.occurrence_id == "" for ref in (*edge.sources, *edge.targets)):
                errors.append(f"edge {index}: text fallback is not occurrence-scoped")
    for ref, indices in sorted(source_uses.items(), key=lambda item: item[0].key):
        if len(indices) > 1:
            errors.append(
                f"source {ref.key} is consumed by conflicting edges {indices}"
            )
    for ref, indices in sorted(target_uses.items(), key=lambda item: item[0].key):
        if len(indices) > 1:
            errors.append(
                f"target {ref.key} is produced by conflicting edges {indices}"
            )
    return tuple(errors)


def complete_correspondence(
    before: ProofState,
    after: ProofState,
    correspondence: Correspondence,
) -> Correspondence:
    """Make a partial relation total with explicit removal/creation edges.

    Semantic matching is intentionally conservative: an entity for which no
    certified continuity exists must not be paired merely to improve an
    animation.  Totalization records that honest outcome explicitly.  It is
    particularly important for goal splits and merges, where branch-local
    declarations and target subtrees otherwise sit outside the 1→n goal
    edge and could silently appear in a renderer.
    """

    normalized = correspondence.normalized()
    used_sources = {ref for edge in normalized.edges for ref in edge.sources}
    used_targets = {ref for edge in normalized.edges for ref in edge.targets}
    missing_sources = tuple(
        sorted(set(_all_entity_refs(before)) - used_sources, key=lambda ref: ref.key)
    )
    missing_targets = tuple(
        sorted(set(_all_entity_refs(after)) - used_targets, key=lambda ref: ref.key)
    )
    additions = (
        *(
            CorrespondenceEdge((ref,), (), RelationKind.REMOVE, MatchProvenance.REMOVAL)
            for ref in missing_sources
        ),
        *(
            CorrespondenceEdge(
                (), (ref,), RelationKind.CREATE, MatchProvenance.CREATION
            )
            for ref in missing_targets
        ),
    )
    return Correspondence((*normalized.edges, *additions)).normalized()


def validate_total_correspondence(
    before: ProofState,
    after: ProofState,
    correspondence: Correspondence,
) -> tuple[str, ...]:
    """Validate referential integrity and coverage of both endpoint states."""

    errors = list(validate_correspondence(before, after, correspondence))
    used_sources = {ref for edge in correspondence.edges for ref in edge.sources}
    used_targets = {ref for edge in correspondence.edges for ref in edge.targets}
    missing_sources = sorted(
        set(_all_entity_refs(before)) - used_sources, key=lambda ref: ref.key
    )
    missing_targets = sorted(
        set(_all_entity_refs(after)) - used_targets, key=lambda ref: ref.key
    )
    errors.extend(f"unrelated source entity {ref.key}" for ref in missing_sources)
    errors.extend(f"unrelated target entity {ref.key}" for ref in missing_targets)
    return tuple(errors)


def _all_entity_refs(state: ProofState) -> Iterable[EntityRef]:
    for goal in state.goals:
        yield goal_ref(goal)
        yield expression_ref(goal, EntityKind.TARGET, "target")
        for occurrence in goal.target.occurrences:
            yield occurrence_ref(goal, occurrence, "target")
        for local in goal.locals:
            yield local_ref(goal, local)
            yield expression_ref(
                goal,
                EntityKind.LOCAL_TYPE,
                "local-type",
                local_id=local.decl_id,
            )
            for occurrence in local.type_expr.occurrences:
                yield occurrence_ref(
                    goal, occurrence, "local-type", local_id=local.decl_id
                )
            if local.value_expr is not None:
                yield expression_ref(
                    goal,
                    EntityKind.LOCAL_VALUE,
                    "local-value",
                    local_id=local.decl_id,
                )
                for occurrence in local.value_expr.occurrences:
                    yield occurrence_ref(
                        goal, occurrence, "local-value", local_id=local.decl_id
                    )
