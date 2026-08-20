import Lean
import HighlightSyntax
import StringMatching
import SemanticTransitions

namespace Animate

structure LatexHypothesis where
  name : String
  latex : String
deriving Lean.ToJson, Lean.FromJson, BEq

/-- Source coordinates are optional because metavariable goals do not always
    retain a syntax object.  Keeping the field in the extractor-owned schema
    lets command-level captures populate it later without changing the public
    JSON contract. -/
structure CanonicalSourceRange where
  file : String
  startLine : Nat
  startColumn : Nat
  endLine : Nat
  endColumn : Nat
deriving Lean.ToJson, Lean.FromJson, BEq

/-- One occurrence in an elaborated expression.  Unlike rendered character
    matching, this identity comes from Lean's expression tree: repeated `x`s
    are separate occurrences, while free variables and constants retain their
    Lean identity. -/
structure CanonicalOccurrence where
  id : String
  kind : String
  path : String
  fingerprint : String
  identity : String := ""
  typeFingerprint : String := ""
  parentId : Option String := none
  aliases : Array String := #[]
  latexSpans : Array SemanticSpan := #[]
  sourceRange : Option CanonicalSourceRange := none
deriving Lean.ToJson, Lean.FromJson, BEq

/-- Renderer-independent observation of one elaborated expression. -/
structure CanonicalExpression where
  id : String
  fingerprint : String
  lean : String := ""
  latex : String := ""
  typeFingerprint : String := ""
  occurrences : Array CanonicalOccurrence := #[]
  sourceRange : Option CanonicalSourceRange := none
deriving Lean.ToJson, Lean.FromJson, BEq

/-- A declaration in Lean's ordered local context.  `value` distinguishes a
    local definition from a hypothesis; no pretty-printed heuristic is needed
    to recover that distinction in Python. -/
structure CanonicalLocalDecl where
  id : String
  userName : String
  «type» : CanonicalExpression
  value : Option CanonicalExpression := none
  binderInfo : String := "default"
  kind : String := "hypothesis"
  /-- Extractor-owned presentation policy.  The declaration remains part of
      the canonical proof state even when Lean marks it as an implementation
      detail (or an instance binder), but renderers must not turn it into a
      mathematical context row.  Recording the decision here avoids name
      heuristics in downstream consumers. -/
  presentationVisible : Bool := true
  dependencies : Array String := #[]
  aliases : Array String := #[]
  sourceRange : Option CanonicalSourceRange := none
  isProof : Bool := false
deriving Lean.ToJson, Lean.FromJson, BEq

structure Goal where
  --- MVarId
  goalId : String
  state : String
  /-- Semantic rendering produced directly from the elaborated target Expr. -/
  latexTarget : Option String := none
  /-- Visible local declarations rendered from their elaborated type Exprs. -/
  latexContext : Array LatexHypothesis := #[]
  /-- Elaborated expression occurrences and their exact canonical LaTeX spans. -/
  semanticNodes : Array SemanticNode := #[]
  /-- Complete ordered local context for the canonical proof-state layer.
      This deliberately includes declarations hidden by the presentation. -/
  canonicalLocals : Array CanonicalLocalDecl := #[]
  /-- Canonical elaborated target, independent of the renderer. -/
  canonicalTarget : Option CanonicalExpression := none
deriving Lean.ToJson, Lean.FromJson, BEq

def escapeLatexName (name : String) : String :=
  name.replace "_" "\\_"

/-- The exact LaTeX text layout consumed by the Manim renderer.  Keeping this
    canonical form in Lean lets the original string matcher produce stable
    character identities for the rendered mathematics as well as for Lean's
    pretty-printed goal state. -/
def Goal.latexState (goal : Goal) : String :=
  let context := goal.latexContext.toList.map fun hypothesis =>
    s!"{escapeLatexName hypothesis.name} \\;:\\; {hypothesis.latex}"
  let target := goal.latexTarget.getD ""
  String.intercalate "\n" (context ++ [s!"\\vdash\\;{target}"])

structure TransformedGoal where
  goal : Goal
  indexMaps : IndexMaps
  latexIndexMaps : Option IndexMaps := none
  semanticTransition : Option SemanticTransition := none
deriving Lean.ToJson, Lean.FromJson

structure GoalAction where
  --- MVarId from before the tactic is applied.
  startGoalId : String

  --- Pretty-printed goal state before the tactic is applied.
  startState : String

  -- empty means the goal has been closed.
  results : List TransformedGoal
  proofKind : String := "unassigned"
  proofFingerprint : String := ""
  proofTerm : String := ""
  proofDescendants : Array String := #[]
  explanation : TacticExplanation := {
    adapter := "generic"
    certificateKind := "unassigned"
    certificateFingerprint := ""
  }
deriving Lean.ToJson, Lean.FromJson

/-- One relation between goal identities observed directly at an InfoTree
    action boundary.  Arrays make split, merge and close relations explicit
    without reducing them to arbitrary pairwise edges. -/
structure ObservedGoalLineage where
  sourceGoalIds : Array String := #[]
  targetGoalIds : Array String := #[]
  relation : String := "evolve"
deriving Lean.ToJson, Lean.FromJson, BEq

/-- Stable reference to a kernel-observed proof entity.  It deliberately does
    not contain rendered coordinates; the presentation layer projects these
    references onto glyphs later. -/
structure CanonicalEntityRef where
  kind : String
  goalId : String
  localId : String := ""
  expressionRole : String := ""
  occurrenceId : String := ""
deriving Lean.ToJson, Lean.FromJson, BEq

/-- Native n→m semantic evidence emitted by the extractor.  In contrast to
    `SemanticTransition`, these edges never depend on tactic names or textual
    matching and are not visual instructions. -/
structure CanonicalEntityHyperedge where
  sources : Array CanonicalEntityRef := #[]
  targets : Array CanonicalEntityRef := #[]
  relation : String
  provenance : String
  evidence : Array String := #[]
deriving Lean.ToJson, Lean.FromJson, BEq

--- Application of a single tactic.
--- It may act on multiple goals (e.g. when using the <;> combinator).
structure Action where
  tacticText : String
  goalActions : List GoalAction
  /-- Complete ordered live frontier immediately before/after this action.
      These are observations, not reconstructed presentation rows. -/
  beforeState : Array Goal := #[]
  afterState : Array Goal := #[]
  focusBefore : Array String := #[]
  focusAfter : Array String := #[]
  goalLineage : Array ObservedGoalLineage := #[]
  canonicalCorrespondence : Array CanonicalEntityHyperedge := #[]
deriving Lean.ToJson, Lean.FromJson

structure GoalHighlighting where
  goalId : String
  colors : HighlightSyntax.ColorMap
deriving Lean.ToJson, Lean.FromJson

-- Result of stage 3.
-- To be jsonified and consumed by animate.py in Blender
structure Movie where
  canonicalAbi : Nat := 5
  capabilities : Array String := #[
    "canonical-proof-state",
    "ordered-action-frontiers",
    "goal-lineage-hyperedges",
    "canonical-entity-hyperedges",
    "local-definition-values",
    "local-presentation-visibility",
    "expression-occurrences"
  ]
  theoremName : String
  startGoal : Goal
  actions: List Action
  highlighting: Array GoalHighlighting
deriving Lean.ToJson, Lean.FromJson

structure HybridChapterValidation where
  valid : Bool
  kernelChecked : Bool
  noSorry : Bool
  errors : Array String := #[]
deriving Lean.ToJson, Lean.FromJson

/-- Kernel evidence emitted while the selected source module still exposes
    opaque theorem bodies.  Full incremental snapshots intentionally do not. -/
structure SnapshotCertificateRow where
  theoremName : String
  dependencies : Array String := #[]
  proofFingerprint : String
  axioms : Array String := #[]
  validation : HybridChapterValidation
deriving Lean.ToJson, Lean.FromJson

structure SnapshotCertificateBundle where
  schemaVersion : Nat := 1
  selectedTheorem : String
  sourceSha256 : String
  rows : Array SnapshotCertificateRow
deriving Lean.ToJson, Lean.FromJson

structure HybridChapter where
  id : Nat
  theoremName : String
  dependencies : Array String := #[]
  movie : Movie
  proofFingerprint : String
  axioms : Array String := #[]
  validation : HybridChapterValidation
  isMain : Bool := false
deriving Lean.ToJson, Lean.FromJson

structure HybridTraceValidation where
  valid : Bool
  dependencyOrderValid : Bool
  allChaptersKernelChecked : Bool
  noSorry : Bool
  errors : Array String := #[]
deriving Lean.ToJson, Lean.FromJson

structure HybridTrace where
  schemaVersion : String := "3.0"
  theoremName : String
  source : String := "Lean.InfoTree/source-tactics+kernel-chapter-certificates"
  granularity : String := "source-tactic/local-theorem-chapters"
  chapters : Array HybridChapter
  validation : HybridTraceValidation
deriving Lean.ToJson, Lean.FromJson

/-- Reference to one independently serialized theorem chapter. `objectHash` is
    filled by the Python object-store ingestion pass using canonical SHA-256;
    Lean supplies the kernel proof fingerprint immediately. -/
structure HybridChapterRef where
  id : Nat
  theoremName : String
  dependencies : Array String := #[]
  proofFingerprint : String
  axioms : Array String := #[]
  validation : HybridChapterValidation
  isMain : Bool := false
  objectPath : String
  objectHash : String := ""
deriving Lean.ToJson, Lean.FromJson

structure HybridTraceManifest where
  schemaVersion : String := "3.1"
  theoremName : String
  source : String := "Lean.InfoTree/source-tactics+kernel-chapter-certificates"
  granularity : String := "source-tactic/content-addressed-local-theorem-chapters"
  chapterRefs : Array HybridChapterRef
  validation : HybridTraceValidation
deriving Lean.ToJson, Lean.FromJson

structure HybridActionChunk where
  schemaVersion : Nat := 1
  theoremName : String
  proofFingerprint : String
  startIndex : Nat
  actions : Array Action
deriving Lean.ToJson, Lean.FromJson

structure HybridCommandCapture where
  schemaVersion : Nat := 1
  theoremName : String
  proofFingerprint : String
  movie : Movie
deriving Lean.ToJson, Lean.FromJson

/-- Kernel-facing declaration index.  This is a routing/performance artifact,
    never a replacement for chapter certificates or the strict audit. -/
structure ProofIndexLocation where
  startLine : Nat
  startColumn : Nat
  endLine : Nat
  endColumn : Nat
deriving Lean.ToJson, Lean.FromJson

structure ProofIndexRow where
  theoremName : String
  proofFingerprint : String
  dependencies : Array String := #[]
  axioms : Array String := #[]
  location : Option ProofIndexLocation := none
deriving Lean.ToJson, Lean.FromJson

structure ProofIndex where
  schemaVersion : Nat := 1
  selectedTheorem : String
  declarations : Array ProofIndexRow := #[]
deriving Lean.ToJson, Lean.FromJson

end Animate
