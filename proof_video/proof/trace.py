"""Certified ProofTrace parsing and presentation-step selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from proof_video.proof.frontier import is_temporally_available
from proof_video.proof.schema import ProofStep

_LEXICAL_CONTEXT_KINDS = frozenset({"assumption", "eigenvariable", "definition"})
_ACTIVE_CONTEXT_KINDS = _LEXICAL_CONTEXT_KINDS | {"proof-definition"}


@dataclass(frozen=True)
class ProofChapter:
    id: int
    theorem_name: str
    theorem_latex: str
    theorem_lean: str
    start_step_id: int
    final_step_id: int
    dependencies: tuple[str, ...]
    is_main: bool

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "ProofChapter":
        return cls(
            id=int(value["id"]),
            theorem_name=str(value["theoremName"]),
            theorem_latex=str(value.get("theoremLatex", "")),
            theorem_lean=str(value.get("theoremLean", "")),
            start_step_id=int(value["startStepId"]),
            final_step_id=int(value["finalStepId"]),
            dependencies=tuple(str(item) for item in value.get("dependencies", ())),
            is_main=bool(value.get("isMain", False)),
        )


@dataclass(frozen=True)
class ProofTrace:
    schema_version: str
    theorem_name: str
    theorem_latex: str
    theorem_lean: str
    steps: tuple[ProofStep, ...]
    final_step_id: int
    axioms: tuple[str, ...]
    valid: bool
    chapters: tuple[ProofChapter, ...] = ()

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "ProofTrace":
        validation = value.get("validation", {})
        steps = tuple(ProofStep.from_json(step) for step in value.get("steps", ()))
        chapters = tuple(
            ProofChapter.from_json(chapter) for chapter in value.get("chapters", ())
        )
        if not chapters and steps:
            chapters = (
                ProofChapter(
                    id=0,
                    theorem_name=str(value.get("theoremName", "Lean theorem")),
                    theorem_latex=str(value.get("theoremLatex", "")),
                    theorem_lean=str(value.get("theoremLean", "")),
                    start_step_id=0,
                    final_step_id=int(value.get("finalStepId", len(steps) - 1)),
                    dependencies=(),
                    is_main=True,
                ),
            )
        return cls(
            schema_version=str(value.get("schemaVersion", "2.0")),
            theorem_name=str(value.get("theoremName", "Lean theorem")),
            theorem_latex=str(value.get("theoremLatex", "")),
            theorem_lean=str(value.get("theoremLean", "")),
            steps=steps,
            final_step_id=int(value.get("finalStepId", -1)),
            axioms=tuple(str(item) for item in value.get("axioms", ())),
            valid=bool(validation.get("valid", False)),
            chapters=chapters,
        )

    def chapter_for_step(self, step_id: int) -> ProofChapter | None:
        for index, chapter in enumerate(self.chapters):
            end = (
                self.chapters[index + 1].start_step_id
                if index + 1 < len(self.chapters)
                else len(self.steps)
            )
            if chapter.start_step_id <= step_id < end:
                return chapter
        return None

    def presentation_steps(self) -> tuple[ProofStep, ...]:
        """Natural-deduction timeline, hiding kernel certificate plumbing.

        Hidden rows are not discarded from the certified JSON.  They remain
        available for auditing, but the video does not spell out hundreds of
        `Eq.mpr` and tactic-internal ring-normalizer constructors.
        """
        internal_prefixes = (
            "Mathlib.Tactic.",
            "Mathlib.Meta.",
            "Lean.Meta.",
        )
        administrative = {
            "Eq.mp",
            "Eq.mpr",
            "Eq.ndrec",
            "Eq.rec",
            "Eq.refl",
            "Trans.trans",
            "congrArg",
            "id",
            "rfl",
        }
        initial_binders_by_chapter: dict[int, int] = {}
        for chapter in self.chapters:
            initial_binders = 0
            chapter_prefix = f"chapter-{chapter.id}/"
            for step in self.steps[chapter.start_step_id :]:
                if self.chapter_for_step(step.id) != chapter:
                    break
                proof_path = step.proof_path.removeprefix(chapter_prefix)
                expected_path = "root" + ".body" * initial_binders + ".binder"
                if (
                    step.kind not in {"assumption", "eigenvariable"}
                    or proof_path != expected_path
                ):
                    break
                initial_binders += 1
            initial_binders_by_chapter[chapter.id] = initial_binders
        visible: list[ProofStep] = []
        for step in self.steps:
            theorem = step.theorem_name or ""
            show = step.kind in {"introduction", "elimination", "reference"}
            if step.kind in {"theorem", "theorem-application"}:
                show = (
                    step.uses_local_context
                    and not step.is_typeclass
                    and not theorem.startswith(internal_prefixes)
                    and theorem not in administrative
                )
            # The initial theorem binders are already visible as fixed
            # variables/assumptions at the top of the board. Their final
            # lambda packaging would merely repeat the entire theorem.
            chapter = self.chapter_for_step(step.id)
            initial_binders = initial_binders_by_chapter.get(
                chapter.id if chapter is not None else -1,
                0,
            )
            if step.kind == "introduction" and step.depth < initial_binders:
                show = False
            if show:
                visible.append(step)
        return tuple(visible)

    def rigorous_steps(self) -> tuple[ProofStep, ...]:
        """Every kernel-checked inference row, without tactic-name filtering.

        Local declarations and proof-valued lets are represented in the
        sequent context rather than duplicated as conclusions.  All actual
        proof constructions—including congruence, equality transport,
        transitivity, reflexivity and tactic-generated certificate lemmas—are
        retained in their certified post-order.
        """
        context_only = {
            "assumption",
            "eigenvariable",
            "definition",
            "proof-definition",
        }
        return tuple(step for step in self.steps if step.kind not in context_only)

    @staticmethod
    def _reflexive_display(step: ProofStep) -> bool:
        """Whether Lean or LaTeX exposes a top-level identity ``a = a``."""

        for proposition in (step.proposition_lean, step.proposition_latex):
            if " = " not in proposition:
                continue
            left, right = proposition.split(" = ", 1)
            if left.strip() == right.strip():
                return True
        return False

    def administrative_reason(self, step: ProofStep) -> str | None:
        """Classify kernel plumbing that adds no presentational proposition.

        The step remains in :attr:`steps` and in the kernel audit.  This
        classification controls only the blackboard projection.
        """

        if step.is_typeclass:
            return "typeclass-resolution"
        if step.kind == "kernel":
            return "kernel-construction"
        theorem = step.theorem_name or ""
        if theorem in {
            "Eq.mp",
            "Eq.mpr",
            "Eq.ndrec",
            "Eq.rec",
            "Eq.refl",
            "Trans.trans",
            "congrArg",
            "id",
            "rfl",
        }:
            return "equality-transport-or-reflexivity"
        if theorem.startswith(("Mathlib.Tactic.", "Mathlib.Meta.", "Lean.Meta.")):
            return "tactic-certificate"
        if self._reflexive_display(step):
            return "reflexive-or-erased-cast"
        if step.kind == "definitional":
            by_id = {candidate.id: candidate for candidate in self.steps}
            for premise_id in step.premises:
                premise = by_id.get(premise_id)
                if premise is None:
                    continue
                same_fingerprint = bool(
                    step.proposition_fingerprint
                    and step.proposition_fingerprint == premise.proposition_fingerprint
                )
                if same_fingerprint or (
                    step.proposition_lean
                    and step.proposition_lean == premise.proposition_lean
                ):
                    return "definitional-pass-through"
        return None

    def render_steps(self) -> tuple[ProofStep, ...]:
        """Mathematical inferences after certified administrative contraction."""

        return tuple(
            step
            for step in self.rigorous_steps()
            if self.administrative_reason(step) is None
        )

    def rendered_premise_branches(
        self,
    ) -> dict[int, tuple[tuple[int, tuple[int, ...]], ...]]:
        """Contract plumbing while retaining each direct premise branch.

        The flat premise map is sufficient for auditing, but animation also
        needs to know *which* hidden direct premise a visible assumption fed.
        Keeping that partition lets a conclusion be assembled from several
        rows without matching equal-looking variables across unrelated
        branches.
        """

        by_id = {step.id: step for step in self.steps}
        render_ids = {step.id for step in self.render_steps()}
        context_ids = {
            step.id
            for step in self.steps
            if step.kind
            in {"assumption", "eigenvariable", "definition", "proof-definition"}
        }
        memo: dict[int, tuple[int, ...]] = {}

        def expand(step_id: int) -> tuple[int, ...]:
            if step_id in memo:
                return memo[step_id]
            if step_id in render_ids or step_id in context_ids:
                result = (step_id,)
            else:
                step = by_id.get(step_id)
                result = (
                    ()
                    if step is None
                    else tuple(
                        dict.fromkeys(
                            ancestor
                            for premise in step.premises
                            for ancestor in expand(premise)
                        )
                    )
                )
            memo[step_id] = result
            return result

        return {
            step.id: tuple((premise, expand(premise)) for premise in step.premises)
            for step in self.render_steps()
        }

    def rendered_premise_map(self) -> dict[int, tuple[int, ...]]:
        """Contract hidden proof nodes while preserving certified ancestry."""

        return {
            step_id: tuple(
                dict.fromkeys(
                    ancestor for _premise, branch in branches for ancestor in branch
                )
            )
            for step_id, branches in self.rendered_premise_branches().items()
        }

    def rigorous_states(
        self,
        *,
        render_only: bool = False,
    ) -> tuple[tuple[ProofStep, tuple[ProofStep, ...]], ...]:
        """Keep the live proof-DAG frontier on the blackboard.

        The current conclusion remains the single conclusion.  Once a derived
        fact has been proved, it is retained as a bare premise row until its
        last future direct use.  This is the usual evaluation stack of a proof
        DAG: sibling premises can be developed without making an already
        proved fact disappear and mysteriously reappear just before the join.
        Only live facts are shown, not the complete proof history.
        """
        parents: dict[str, str | None] = {"root": None}
        lexical: list[ProofStep] = []
        for step in self.steps:
            if step.parent_scope_id is None:
                parents.setdefault(step.scope_id, None)
            if step.opens_scope:
                parents[step.opens_scope] = step.parent_scope_id or "root"
            if step.kind in _ACTIVE_CONTEXT_KINDS and step.opens_scope:
                lexical.append(step)

        def is_ancestor(outer: str, inner: str) -> bool:
            visited: set[str] = set()
            current: str | None = inner
            while current is not None and current not in visited:
                if current == outer:
                    return True
                visited.add(current)
                current = parents.get(current)
            return False

        by_id = {step.id: step for step in self.steps}
        inference_steps = self.render_steps() if render_only else self.rigorous_steps()
        premise_map = (
            self.rendered_premise_map()
            if render_only
            else {step.id: step.premises for step in inference_steps}
        )
        inference_positions = {
            step.id: index for index, step in enumerate(inference_steps)
        }
        last_use_position: dict[int, int] = {}
        for consumer_position, consumer in enumerate(inference_steps):
            for premise_id in premise_map[consumer.id]:
                producer_position = inference_positions.get(premise_id)
                if producer_position is None or producer_position >= consumer_position:
                    continue
                last_use_position[premise_id] = max(
                    consumer_position,
                    last_use_position.get(premise_id, consumer_position),
                )
        states = []
        previous_step: ProofStep | None = None
        previous_context: tuple[ProofStep, ...] = ()
        for index, step in enumerate(inference_steps):
            lexical_context = [
                binder
                for binder in lexical
                if binder.id < step.id
                and binder.opens_scope is not None
                and is_ancestor(binder.opens_scope, step.scope_id)
            ]
            # Lean's ``replace h`` elaborates to a new proof-valued local
            # declaration named ``h`` whose proof term still depends on the
            # older ``h``. The kernel must retain that dependency, but the old
            # declaration is shadowed in the user-facing local context. Keep
            # the latest proof-definition and hide only older binders with
            # the exact same certified binder name. This is scope/identity
            # based, not a comparison of rendered formulas.
            replacement_by_name = {
                binder.binder_name: binder.id
                for binder in lexical_context
                if binder.kind == "proof-definition" and binder.binder_name
            }
            lexical_context = [
                binder
                for binder in lexical_context
                if not (
                    binder.binder_name in replacement_by_name
                    and binder.id < replacement_by_name[binder.binder_name]
                )
            ]
            staging_for = (
                inference_steps[index + 1] if index + 1 < len(inference_steps) else None
            )
            if index == 0:
                staging_for = step
            staged = [
                producer
                for producer in inference_steps
                if inference_positions[producer.id]
                < index
                < last_use_position.get(producer.id, -1)
            ]
            lexical_ids = {binder.id for binder in lexical_context}
            if staging_for is not None:
                for premise_id in premise_map[staging_for.id]:
                    if premise_id == step.id or premise_id in lexical_ids:
                        continue
                    premise = by_id[premise_id]
                    # The next rendered inference may be separated from this
                    # frame by hidden declarations (notably a proof-valued
                    # ``let``/``have``).  Those rows become available in the
                    # *target* state; preloading them here would display the
                    # result before its proof has finished.
                    if not is_temporally_available(premise, step):
                        continue
                    staged.append(premise)
            # Proof-producing lets and their elaborated proof term frequently
            # have different proof identities but exactly the same certified
            # proposition.  Keep one live row per proposition fingerprint.
            # Do not deduplicate by rendered LaTeX: two differently typed
            # expressions can print the same after implicit casts are hidden.
            unique_staged: list[ProofStep] = []
            seen_propositions: set[str] = set()
            seen_lean_propositions: set[str] = set()
            for producer in staged:
                key = producer.proposition_fingerprint
                if key and key in seen_propositions:
                    continue
                if (
                    producer.kind == "proof-definition"
                    and producer.proposition_lean
                    and producer.proposition_lean in seen_lean_propositions
                ):
                    continue
                unique_staged.append(producer)
                if key:
                    seen_propositions.add(key)
                if producer.proposition_lean:
                    seen_lean_propositions.add(producer.proposition_lean)
            staged = unique_staged
            # A completed proof-valued let is the named/contextual form of
            # its immediately preceding proof, not a second mathematical
            # fact.  Once that declaration is active, prefer it over an
            # otherwise staged anonymous producer with the same certified
            # proposition.  This prevents the next state from showing both
            # the old conclusion and its let-bound alias.
            proof_definition_fingerprints = {
                binder.proposition_fingerprint
                for binder in lexical_context
                if binder.kind == "proof-definition" and binder.proposition_fingerprint
            }
            proof_definition_propositions = {
                binder.proposition_lean
                for binder in lexical_context
                if binder.kind == "proof-definition" and binder.proposition_lean
            }
            staged = [
                producer
                for producer in staged
                if not (
                    (
                        producer.proposition_fingerprint
                        and producer.proposition_fingerprint
                        in proof_definition_fingerprints
                    )
                    or (
                        producer.proposition_lean
                        and producer.proposition_lean in proof_definition_propositions
                    )
                )
            ]
            # Remove a premise cited twice, then preserve the visual order of
            # every row that was already on the board.  In particular, when
            # the previous conclusion is promoted to a premise of a newly
            # opened branch, it must remain above the branch assumption that
            # was derived from it (for example ``P ∨ Q`` before ``h : Q``).
            candidate_context = tuple(
                dict.fromkeys(
                    [*lexical_context, *staged],
                )
            )
            candidate_ids = {item.id for item in candidate_context}
            prior_visible = [*previous_context]
            if previous_step is not None:
                prior_visible.append(previous_step)
            preserved = [item for item in prior_visible if item.id in candidate_ids]
            preserved_ids = {item.id for item in preserved}
            context = tuple(
                [
                    *preserved,
                    *(
                        item
                        for item in candidate_context
                        if item.id not in preserved_ids
                    ),
                ]
            )
            states.append((step, context))
            previous_step = step
            previous_context = context
        return tuple(states)

    def presentation_states(
        self,
    ) -> tuple[tuple[ProofStep, tuple[ProofStep, ...]], ...]:
        """Project certified rows into one live sequent at a time."""
        parents: dict[str, str | None] = {"root": None}
        binders: list[ProofStep] = []
        for step in self.steps:
            if step.parent_scope_id is None:
                parents.setdefault(step.scope_id, None)
            if step.opens_scope:
                parents[step.opens_scope] = step.parent_scope_id or "root"
            if (
                step.kind in {"assumption", "eigenvariable", "definition"}
                and step.opens_scope
            ):
                binders.append(step)

        def is_ancestor(outer: str, inner: str) -> bool:
            visited: set[str] = set()
            current: str | None = inner
            while current is not None and current not in visited:
                if current == outer:
                    return True
                visited.add(current)
                current = parents.get(current)
            return False

        states = []
        for step in self.presentation_steps():
            context = tuple(
                binder
                for binder in binders
                if binder.id < step.id
                and binder.opens_scope is not None
                and is_ancestor(binder.opens_scope, step.scope_id)
            )
            states.append((step, context))
        return tuple(states)
