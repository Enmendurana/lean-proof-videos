# Canonical proof-state architecture

This document describes the semantic boundary between Lean extraction and
video presentation.  The central rule is simple:

> A renderer never decides which mathematical object survived a proof step.

The pipeline is split into four layers with one-way dependencies:

```text
Lean observation
    ABI 5 beforeState/afterState + focus + goal lineage + native hyperedges
                         │
                         ▼
Deterministic state delta
    Total correspondence hypergraph + typed effects = ProofTransition
                         │
                         ▼
Interpretation and presentation planning
    SemanticEvent + stable LayoutAnchor + VisualPrimitive
                         │
                         ▼
Renderer
    timing, geometry, camera, glyph paths, encoding
```

Manim and Remotion consume the same presentation plan.  They are free to
choose easing or pixel coordinates, but not object identity, source/target
ownership, copying, merging, or fallback semantics.

### ABI 5 observation contract

For a native ABI 5 action, Lean exports the complete ordered live frontier on
both sides of the action.  `beforeState`, `afterState`, `focusBefore`,
`focusAfter`, `goalLineage`, and `canonicalCorrespondence` are one atomic
observation.  The Python reader does not replay `goalActions` to reconstruct
time, and a pretty-printer spelling change cannot create an extra proof step.
The movie also advertises explicit capabilities, including canonical proof
state, ordered action frontiers, goal-lineage hyperedges, canonical entity
hyperedges, local-definition values, and expression occurrences.

Native hyperedges are authoritative.  The deterministic delta may add only
the bookkeeping needed to make correspondence total: every entity in the
before state is either connected to an after entity or explicitly removed,
and every after entity is either connected or explicitly created.  It cannot
replace contradictory Lean evidence with a textual, geometric, or
tactic-derived guess.

## Canonical `ProofState`

`proof_video.proof.state` owns the immutable normal representation.

- A `ProofState` is an ordered finite forest of live goals plus an ordered
  focus list.
- A `GoalState` has a stable goal/lineage identity, ordered local context,
  target expression, optional parent and branch metadata.
- A `LocalDecl` retains its Lean declaration identity, user-facing name,
  binder information, type, optional value, dependencies, aliases and source
  range.
- An `Expression` retains an elaborated fingerprint and an ordered tree of
  `ExprOccurrence` values.  Each occurrence has its own identity, path, type
  fingerprint, parent, aliases and display/source spans.

Pretty-printed Lean and LaTeX are views.  They are useful for display and
diagnostic fallback but do not define equality.  In particular, two printed
`x` glyphs are not interchangeable merely because they look alike.

The state fingerprint is SHA-256 of a deterministic JSON payload.  Goal and
local order, focus, declaration values, occurrence paths and semantic
metadata all participate.  Display-only strings do not override semantic
identity.

## Correspondence is a hypergraph

`proof_video.proof.correspondence` models continuity as typed hyperedges:

| Relation | Arity | Meaning |
|---|---:|---|
| `preserve` / `rewrite` | 1→1 | one entity survives or is rewritten |
| `copy` / `split` | 1→n | one semantic source contributes to many targets |
| `merge` | n→1 | several sources contribute to one target |
| `create` | 0→n | no surviving source exists |
| `remove` | n→0 | no surviving target exists |

An edge also records provenance, evidence and confidence.  Provenance is
ordered from Lean identity, alias and kernel-checked definitional equality
through explicit extractor evidence and typed structure to the final
rendered-text fallback.  Native ABI 5 edges are overlaid before conservative
totalization.  Text fallback is deliberately local and never becomes a
physical move: presentation turns it into remove/create.

Entity references are scoped by goal, local declaration, expression role and
occurrence ID.  This prevents accidental permutations between repeated
symbols such as the three `x` occurrences in `f(x) + x = x`.

## Typed effects and normal form

Correspondence answers *what survived*.  Effects answer *what changed*.

- Context effects: add/remove/rename/replace a declaration, update its type,
  value or metadata, clear a value, and reorder locals.
- Target effects: keep, rewrite, rewrite a subexpression, change presentation,
  or substitute an entity.
- Goal effects: preserve, create, close, split, merge, reorder and focus.

`ProofTransition.normalized()` gives a deterministic order, removes exact
duplicates and normalizes correspondence hyperedges.  Normalization is
idempotent:

```text
normalize(normalize(t)) = normalize(t)
```

The ordering is operational rather than chronological: it is chosen so that
replay has one unambiguous result.  The user's animation timing is assigned
later and cannot alter the transition.

## Replay and composition

`apply_transition(before, transition)` reconstructs the exact immutable
`after` state.  It checks both endpoint fingerprints and rejects nonexistent
entities, invalid permutations, broken focus references and mismatched
results.  This is the main regression oracle:

```text
apply(before, diff(before, after)) = after
```

Composition is extensional.  To compose `a → b` and `b → c`, the system
replays both and computes one normalized `a → c` observation.  It does not
concatenate potentially contradictory edit scripts.  Consequently:

```text
apply(a, compose(a→b, b→c)) = c
```

The composed transition preserves endpoint truth; presentation can still
choose the uncomposed steps when the intermediate mathematical state matters
to the viewer.

## Observation, interpretation and presentation

These concerns intentionally live in separate modules:

1. `proof.diff` observes two states and builds the transition.  Explicit Lean
   evidence is considered before conservative structural matching.
2. `proof.interpretation` derives labels such as introduction, substitution,
   rewriting or branch creation from typed effects.  Tactic text is only a
   narration hint; it cannot change classification.
3. `presentation.semantic_plan` compiles the transition into a finite visual
   vocabulary: keep, move, copy, rewrite, create, remove, split, merge, close,
   focus and reorder.
4. `presentation.anchors` assigns semantic row/occurrence addresses without
   pixel coordinates.  A renderer maps them onto its own geometry.
5. `presentation.goal_forest` projects every live goal into a stable
   `GoalForestLayout`.  Card identity, branch ancestry, sibling order, focus,
   split/merge/close and retirement are therefore shared by Manim and
   Remotion instead of being reconstructed independently from screen rows.

This separation makes a transition debuggable before Chromium, KaTeX, Manim
or FFmpeg starts.

## Representative proof operations

The architecture handles operations by observed state shape, not a growing
list of tactic names:

- **`intro` / `rintro`:** a binder occurrence moves from the target into a new
  local declaration while the residual target is rewritten.  Both happen in
  one transition and may animate concurrently.
- **`replace` / `have`:** the old and new local declarations are connected by
  declaration identity or alias evidence.  Unchanged dependent occurrences
  keep their identities; changed type/value data is an explicit effect.
- **Substitution-shaped changes:** a removed declaration is transported into
  affected target or local occurrences only when the extractor supplies a
  native identity, alias, definitional-equality or explicit substitution edge.
  Missing evidence produces remove/create; the planner does not infer a
  global substitution from equal variable names.  This deliberately means
  that some valid `subst`/automation steps are less visually compact.
- **`constructor`:** one goal splits into multiple child goals.  Shared
  context can be copied 1→n; branch targets are independent.
- **`cases` / `induction`:** explicit Lean goal lineage creates branch
  hyperedges.  Branch-local hypotheses are creations, while shared
  declarations are copied or preserved according to evidence.
- **Reorder/focus:** a permutation or focus change is recorded separately
  from semantic rewrites, so moving a row cannot manufacture a proof step.
- **Repeated symbols:** occurrence path, owner, parent, Lean identity and type
  disambiguate equal glyphs.  An ambiguous candidate is rendered as
  remove/create rather than guessed.

These examples are consequences of the data model.  Tactic text may improve a
diagnostic label, but ABI 5 correspondence never dispatches on a tactic name.
The separate proof-term compatibility projection still has certified
natural-deduction rule adapters; those do not participate in native ABI 5
frontier matching.

## Compatibility boundary

Old trace schemas remain readable through `proof.adapters`.  ABI 1–4 migration
creates a conservative canonical view and may use the old pairwise
`SemanticTransition` bridge.  ABI 5 does not use that bridge as a second source
of semantics: both Manim and Remotion consume the same `SemanticVisualPlan`
and the same sequential `GoalForestLayout`.  At the last renderer boundary a
canonical occurrence hyperedge may be expanded mechanically onto KaTeX/MathTex
token spans; this projection cannot introduce a new identity or change the
many-to-many canonical relation.

New extractor schemas should add semantic data, never reinterpret an existing
field.  A schema/ABI change must invalidate extractor caches but must not
invalidate renderer caches for unrelated presentation settings.

## Diagnostics

`--dump-transition-map FILE` writes schema 2.  Every transition retains the
legacy block report for compatibility and adds a `canonical` object containing:

- complete before/after fingerprints, goal order, focus, local and occurrence
  IDs;
- normalized hyperedges with arity, relation, provenance, evidence and
  confidence;
- goal/context/target effects and derived interpretation;
- layout anchors, visual primitives and explicit fallback reasons;
- replay, state and correspondence validation results.

The diagnostic is renderer independent.  A failed visual plan is represented
as `visual-plan-rejected`; it is never silently replaced by invented semantic
edges.

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the remaining boundaries.
