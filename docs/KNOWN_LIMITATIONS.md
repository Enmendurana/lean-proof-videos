# Known limitations

This project prefers an honest visual fallback to a plausible but false
animation.  The following limits are therefore visible in diagnostics and QA.

## Extractor evidence

- Lean identities and typed expression fingerprints are authoritative, but
  older traces contain only rendered goal text and legacy token maps.  Their
  canonical representation is a migration view, not retroactively recovered
  `InfoTree` evidence.
- Source ranges, `FVarAliasInfo`, declaration values and goal lineage are only
  as complete as the selected Lean frontend exports them.  A missing field is
  not synthesized from a name.
- Native ABI 5 local aliases are intentionally sparse: the current extractor
  does not turn a reused user-facing binder name into alias evidence.  Until
  Lean supplies a true elaborator alias, replacement continuity must come from
  identity, definitional equality or another explicit edge.
- ABI 5 observes complete action frontiers and native canonical hyperedges,
  but it does not expose every internal state of a compound tactic.  The
  exported action boundary is authoritative; the application does not split
  it into invented micro-steps.
- A snapshot accelerates elaboration; it is not a proof certificate.  Kernel
  audit and fingerprint validation remain required after reuse.

## Correspondence

- Two entities with identical LaTeX are not considered the same object.  If
  Lean identity, alias, explicit evidence or a unique typed structural match
  cannot establish continuity, the planner emits remove/create.
- Commutative normalization, arbitrary algebraic equivalence and human
  mathematical intent are not semantic identity.  They require a certified
  Lean rewrite/congruence path to animate as preservation.
- A substitution-looking state change is not enough to connect one removed
  declaration to every equal-looking occurrence.  That transport needs an
  extractor identity, alias, definitional-equality or explicit native edge.
  Otherwise the affected pieces are removed and created, even when a person
  can recognize the substitution immediately.
- Goal merging needs explicit many-to-one lineage/evidence.  Similar goal text
  alone cannot establish a merge.
- Imported legacy `SemanticTransition` records may only express 1→1 token
  edges.  ABI 5 traces bypass that restriction: both Manim and Remotion consume
  the shared `SemanticVisualPlan` directly.  The pairwise projection remains
  only for ABI 1–4 compatibility and therefore cannot recover n-ary evidence
  that an old trace never recorded.

## Interpretation and pacing

- Interpretation is a compact explanation of typed effects, not an attempt to
  reconstruct the tactic program.  Several tactics can induce the same event,
  and one automation tactic can induce a composite event.
- Very large automation steps can remain one kernel-certified transition when
  Lean exposes no stable intermediate tactic states.  The system does not
  invent intermediate mathematics to make the video longer.
- Tactic text is retained as a diagnostic/narration hint, not as the cause of
  ABI 5 correspondence.  The proof-term compatibility projection still has a
  small set of certified natural-deduction rule adapters; it is a separate
  presentation mode and cannot override native frontier evidence.
- Composition preserves semantic endpoints but intentionally removes an
  intermediate state.  A presentation requiring that state must retain the
  original two transitions.

## Layout and rendering

- Layout anchors are semantic row/occurrence addresses, not pixel geometry.
  Font metrics, line breaking, camera bounds and collision avoidance are still
  renderer responsibilities.
- `GoalForestLayout` gives both renderers the same stable card identities,
  ancestry, order and focus for all live goals.  It does not prescribe exact
  card coordinates, camera easing or line wrapping, so the two renderers may
  still place an otherwise identical forest a little differently.
- LaTeX glyph decomposition does not have a universal one-to-one mapping to
  Lean expression occurrences.  A certified occurrence can cover multiple SVG
  paths, and delimiters/operators may be presentational only.
- The compatibility bridge still contains safe whole-row fallbacks for ABI
  1–4 traces.  Those paths may fade or write more text than a fully canonical
  ABI 5 trace, but must not move a token on glyph equality alone.
- ABI 5 visual primitives are authoritative, but the final KaTeX/MathTex
  boundary still has to map semantic occurrence spans onto renderer tokens.
  Presentational punctuation or a compound SVG path may therefore be written
  rather than moved when no unambiguous covered token span exists.
- Manim and Remotion can differ by sub-frame easing or rasterization.  They
  must agree on the semantic plan, duration and endpoint state, not necessarily
  every intermediate pixel.

## Diagnostic and schema stability

- Transition-map schema 2 adds canonical data while retaining schema-1 block
  fields.  Consumers should branch on `schemaVersion` and ignore unknown
  additive fields.
- `validation.valid` in the diagnostic reports canonical replay and structural
  checks.  It complements rather than replaces strict source-tactic/kernel
  audit and presentation QA.
- A `visual-plan-rejected` diagnostic is a hard indication that canonical
  planning failed.  Debug output remains available, but a production render
  should fail closed rather than reinterpret the transition.

## Deliberately unsupported shortcuts

The following are not planned as semantic authorities:

- matching characters or SVG glyph shapes globally;
- using SymPy equality as proof-object identity;
- matching locals only by user-facing names;
- tactic-name dispatch that decides what moved;
- treating a legacy pairwise `SemanticTransition` as additional ABI 5
  correspondence after the canonical visual plan has been built;
- silently dropping duplicate-looking proof states without comparing complete
  canonical fingerprints.

Any future optimization must preserve `apply(before, transition) = after` and
the same canonical hypergraph.  Performance work may cache or parallelize
observation and presentation, but it may not weaken those invariants.
