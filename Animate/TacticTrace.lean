import Lean
import Lean.Widget.Diff
import Batteries.Data.RBMap.Basic
import Annotations
import HighlightSyntax
import StringMatching
import SemanticTransitions
import Animate.Config
import Animate.Schema

namespace Animate

instance : Lean.ToJson String.Pos.Raw where
  toJson := fun p ↦ Lean.Json.num p.byteIdx

instance : Lean.FromJson String.Pos.Raw where
    fromJson? := fun j ↦ do
  let idx ← j.getNat?
  return ⟨idx⟩

structure StringSpan where
  startPos : String.Pos.Raw
  endPos : String.Pos.Raw
deriving Lean.ToJson, Lean.FromJson, BEq

/-- Assignment evidence belongs to the source metavariable that Lean checked.
    Keeping it per source prevents a multi-goal TacticInfo from overwriting the
    preceding source's proof data with the final loop iteration. -/
structure GoalProofObservation where
  sourceGoalId : String
  proofKind : String := "unassigned"
  proofFingerprint : String := ""
  proofTerm : String := ""
  proofDescendants : Array String := #[]
  proofPremises : Array String := #[]
  proofConstants : Array String := #[]
deriving Lean.ToJson, Lean.FromJson

structure TacticStepData where
  elaborator : String
  name : String

  --- The text of the tactic, with any child tactic
  --- text replaced by "?_"
  text : String

  tacticSpan : StringSpan
  goals_before : List Goal
  goals_after : List Goal
  reverse_s1 : Bool := false
  reverse_s2 : Bool := false
  proofKind : String := "unassigned"
  proofFingerprint : String := ""
  proofTerm : String := ""
  proofDescendants : Array String := #[]
  proofPremises : Array String := #[]
  proofConstants : Array String := #[]
  goalDiffs : Array GoalDiffEvidence := #[]
  goalProofs : Array GoalProofObservation := #[]
  canonicalCorrespondence : Array CanonicalEntityHyperedge := #[]
deriving Lean.ToJson, Lean.FromJson

structure SeqData where
  tacticSpan : StringSpan
  goals_before : List Goal
  goals_after : List Goal
deriving Lean.ToJson, Lean.FromJson

inductive TacticStep where
| node (data : TacticStepData)
       (children : List TacticStep)
| seq (span: StringSpan) (children : List TacticStep)
deriving Lean.ToJson, Lean.FromJson

def TacticStep.goals_before : TacticStep →  List Goal
| .node data _ => data.goals_before
| .seq _ [] => []
| .seq _ (c::_) => c.goals_before

--------------------
-- stage 2: flattened map of tactic steps.

structure TacticStep' where
  /-- Stable temporal observation identity.  A metavariable id identifies a
      semantic goal, but Lean may reuse that same id across several sequential
      tactic observations. -/
  observation_id : String := ""
  /-- Identity of the observed `TacticInfo` boundary.  All source goals of a
      multi-goal tactic share this id; it is temporal identity, not MVarId. -/
  action_id : String := ""
  text : String
  span : StringSpan
  goal_before : Goal
  /-- Next observable goal frontier.  For a compound syntax node this is its
      children's entry frontier, not that frontier plus the compound node's
      already-final result. -/
  goals_after : List Goal
  /-- Ordered n→m frontier observed for the whole action.  Per-source
      `goals_after` above exists for legacy GoalAction rendering only. -/
  action_goals_before : List Goal := []
  action_goals_after : List Goal := []
  /-- Exact `TacticInfo.goalsAfter`, retained as provenance even when children
      provide finer-grained observable boundaries. -/
  direct_goals_after : List Goal := []
  /-- Parent continuation held back while a compound node's child frontier is
      active.  It becomes visible only after every child descendant closes. -/
  deferred_goals_after : List Goal := []
  /-- Opaque dependency token used to compose nested continuations without
      pretending that a child goal and its parent continuation coexist. -/
  continuation_token : Option String := none
  reverse_s1 : Bool := false
  reverse_s2 : Bool := false
  proofKind : String := "unassigned"
  proofFingerprint : String := ""
  proofTerm : String := ""
  proofDescendants : Array String := #[]
  proofPremises : Array String := #[]
  proofConstants : Array String := #[]
  goalDiffs : Array GoalDiffEvidence := #[]
  canonicalCorrespondence : Array CanonicalEntityHyperedge := #[]
deriving Lean.ToJson, Lean.FromJson

/-- Ordered tactic observations for each semantic goal identity.  Nested
    InfoTree wrappers replace the observation for the same temporal interval;
    later, non-contained spans append instead of overwriting it.
-/
abbrev StepMap := Std.HashMap String (Array TacticStep')

structure Stage2State where
  startGoal : Goal
  steps : StepMap

def Stage2State.dump (s : Stage2State) : IO Unit := do
  IO.println <| "stage2 with top goal " ++ s.startGoal.goalId
  for ⟨_, observations⟩ in s.steps.toList do
    for ts in observations do
      IO.println s!"STEP-SUMMARY observation={ts.observation_id} goal={ts.goal_before.goalId} span={ts.span.startPos.byteIdx}-{ts.span.endPos.byteIdx} after={repr (ts.goals_after.map (·.goalId))} direct={repr (ts.direct_goals_after.map (·.goalId))} deferred={repr (ts.deferred_goals_after.map (·.goalId))} token={repr ts.continuation_token}"
  -- TODO

section syntax_manip

open Lean

def StringSpan.ofSyntax (stx : Syntax) : Option StringSpan := Id.run do
  let some p1 := stx.getPos? | return none
  let some p2 := stx.getTailPos? | return none
  return .some ⟨p1, p2⟩

def replace_inner_syntax (src : String) (outer : StringSpan) (inners : List StringSpan)
    (replacement : String := "?_") :
    String := Id.run do
  let mut result := ""
  let mut curPos := outer.startPos
  for inner in inners do
    result := result ++
      (Substring.Raw.mk src curPos inner.startPos).toString ++ replacement
    curPos := inner.endPos
  result := result ++ (Substring.Raw.mk src curPos outer.endPos).toString
  return result

--#eval replace_inner_syntax
--       "have h := by rfl" ⟨⟨0⟩, ⟨17⟩⟩ [⟨⟨13⟩, ⟨17⟩⟩]

def left_trim_lines (lines : String) (col : Nat) : String := Id.run do
  let mut lines := lines.splitOn "\n"
  let mut rev_result := []
  for line in lines do
    let pfx := Substring.Raw.mk line ⟨0⟩ ⟨col⟩
    if pfx.toString = String.ofList (List.replicate col ' ')
    then rev_result := (line.drop col).toString :: rev_result
    else rev_result := line :: rev_result
  return String.intercalate "\n" rev_result.reverse

-- #eval left_trim_lines "a\n  bc\n   d" 2

def StringSpan.union (s1 s2 : StringSpan) : StringSpan :=
  { startPos := ⟨min s1.startPos.byteIdx s2.startPos.byteIdx⟩,
    endPos := ⟨max s1.endPos.byteIdx s2.endPos.byteIdx⟩,
  }

def EMPTY_SPAN : StringSpan := {startPos := ⟨0xffffffffffffffff⟩, endPos := ⟨0⟩}

def StringSpan.union_list : List StringSpan → StringSpan
| [] => EMPTY_SPAN
| s :: ss => s.union (StringSpan.union_list ss)

unsafe def TacticStep.span_union : TacticStep → StringSpan
| .node data children =>
     let children_spans := children.map (fun c ↦ c.span_union)
     data.tacticSpan.union (StringSpan.union_list children_spans)
| .seq span children =>
     span.union (StringSpan.union_list (children.map (fun c ↦ c.span_union)))


end syntax_manip

section infotrees

open Lean Elab

def collectNodesBottomUpM {α : Type} {m : Type → Type} [Monad m]
    (p : ContextInfo → Info → PersistentArray InfoTree → List α → m (List α))
    (i : InfoTree) : m (List α) := do
  let x ← i.visitM
   (m := m)
   (postNode := fun ci i cs as => p ci i cs (as.filterMap id).flatten)
  let y := x.getD []
  return y

/-- Find the name for the outermost `Syntax` in this `TacticInfo`. -/
def _root_.Lean.Elab.TacticInfo.name? (t : TacticInfo) : Option Name :=
  match t.stx with
  | Syntax.node _ n _ => some n
  | _ => none

def GOAL_PP_WIDTH : Nat := 80

private def canonicalExprChildren : Expr → Array Expr
  | .app fn arg => #[fn, arg]
  | .lam _ type body _ => #[type, body]
  | .forallE _ type body _ => #[type, body]
  | .letE _ type value body _ => #[type, value, body]
  | .mdata _ body => #[body]
  | .proj _ _ body => #[body]
  | _ => #[]

private def canonicalFingerprint (expr : Expr) : String :=
  toString expr.consumeMData.hash

private def inferredTypeFingerprint (expr : Expr) : MetaM String := do
  try
    return canonicalFingerprint (← Meta.inferType expr)
  catch _ =>
    -- Detached binder bodies contain loose de Bruijn variables.  The empty
    -- value is explicit evidence that Lean could not provide a local type at
    -- this occurrence; it is never replaced by a textual guess.
    return ""

private partial def collectOccurrenceTypes
    (path : String) (expr : Expr) (boundTypes : Array Expr := #[]) :
    MetaM (Array (String × String)) := do
  let expr := expr.consumeMData
  let inferred ← inferredTypeFingerprint expr
  let typeFingerprint := if !inferred.isEmpty then inferred else
    match expr with
    | .bvar index => boundTypes[index]?.map canonicalFingerprint |>.getD ""
    | _ => ""
  let mut result := #[(path, typeFingerprint)]
  match expr with
  | .forallE _ type body _ | .lam _ type body _ =>
    result := result ++ (← collectOccurrenceTypes (path ++ ".0") type boundTypes)
    result := result ++ (← collectOccurrenceTypes (path ++ ".1") body
      (#[type] ++ boundTypes))
  | .letE _ type value body _ =>
    result := result ++ (← collectOccurrenceTypes (path ++ ".0") type boundTypes)
    result := result ++ (← collectOccurrenceTypes (path ++ ".1") value boundTypes)
    result := result ++ (← collectOccurrenceTypes (path ++ ".2") body
      (#[type] ++ boundTypes))
  | _ =>
    let children := canonicalExprChildren expr
    for index in [0 : children.size] do
      result := result ++ (← collectOccurrenceTypes
        (path ++ "." ++ toString index) children[index]! boundTypes)
  return result

private def lookupOccurrenceType
    (entries : Array (String × String)) (path : String) : String :=
  (entries.find? fun entry => entry.1 == path).map (·.2) |>.getD ""

private def orderedUniqueStrings (values : Array String) : Array String :=
  values.foldl (fun result value =>
    if value.isEmpty || result.contains value then result else result.push value) #[]

private def canonicalAliases (_node : SemanticNode) : Array String :=
  -- An occurrence id/path is not a Lean alias.  Until InfoTree supplies an
  -- explicit alias edge for this occurrence, keep the alias relation empty
  -- rather than manufacturing continuity from equal display positions.
  #[]

/-- Capture one expression once and expose the same semantic nodes to both the
    legacy renderer and the canonical proof-state layer.  This is pure
    observation: it does not infer tactics or visual effects. -/
private def captureCanonicalExpression
    (rootId : String) (expr : Expr) : MetaM (CanonicalExpression × Array SemanticNode) := do
  let (latex, semanticNodes) ← renderSemanticExpr rootId expr
  let formatted ← Meta.ppExpr expr
  let typeFingerprint ← inferredTypeFingerprint expr
  let occurrenceTypes ← collectOccurrenceTypes "0" expr
  let occurrences := semanticNodes.map fun node => {
    id := node.id
    kind := node.kind
    path := node.path
    fingerprint := node.fingerprint
    identity := node.identity
    typeFingerprint :=
      let inferred := lookupOccurrenceType occurrenceTypes node.path
      if !inferred.isEmpty then inferred
      else if node.kind == "declaration" || node.kind == "declaration-punctuation" then
        node.fingerprint
      else ""
    parentId := node.parentId
    aliases := canonicalAliases node
    latexSpans := node.latexSpans
  }
  let rootFingerprint :=
    (semanticNodes.find? fun node => node.parentId.isNone).map (·.fingerprint)
      |>.getD (canonicalFingerprint expr)
  return ({
    id := rootId
    fingerprint := rootFingerprint
    lean := formatted.pretty (width := GOAL_PP_WIDTH)
    latex
    typeFingerprint
    occurrences
  }, semanticNodes)

private def canonicalBinderInfo : BinderInfo → String
  | .default => "default"
  | .implicit => "implicit"
  | .strictImplicit => "strictImplicit"
  | .instImplicit => "instImplicit"

private def canonicalLocalAliases (_decl : LocalDecl) : Array String :=
  -- User names are presentation and can be shadowed; fvar identity already
  -- lives in `CanonicalLocalDecl.id`.  Only true elaborator alias evidence may
  -- populate this field in a future schema revision.
  #[]

private def canonicalLocalDependencies (decl : LocalDecl) : Array String :=
  let fromType := collectProofFVars decl.type
  let fromValue := decl.value?.map collectProofFVars |>.getD #[]
  orderedUniqueStrings (fromType ++ fromValue)

private def captureCanonicalLocal
    (decl : LocalDecl) : MetaM (CanonicalLocalDecl × Array SemanticNode) := do
  let id := decl.fvarId.name.toString
  let (typeExpr, typeNodes) ← captureCanonicalExpression
    s!"context/{id}" decl.type
  let valueExpr ← match decl.value? with
    | some value =>
      let (captured, _) ← captureCanonicalExpression s!"local/{id}/value" value
      pure (some captured)
    | none => pure none
  return ({
    id
    userName := decl.userName.toString
    «type» := typeExpr
    value := valueExpr
    binderInfo := canonicalBinderInfo decl.binderInfo
    kind := if decl.isLet then "definition" else "hypothesis"
    presentationVisible :=
      !(decl.isImplementationDetail || decl.binderInfo.isInstImplicit)
    dependencies := canonicalLocalDependencies decl
    aliases := canonicalLocalAliases decl
    isProof := ← Meta.isProp decl.type
  }, typeNodes)

/-- Render a metavariable goal both with Lean's regular pretty printer (needed
for the original expression matching algorithm) and with LeanTeX. Keeping both
representations also provides backwards compatibility for existing traces. -/
def renderGoal (g : MVarId) : MetaM Goal := g.withContext do
  let formatted ← Meta.ppGoal g
  let target ← g.getType
  let (canonicalTarget, targetNodes) ← captureCanonicalExpression "target" target
  let latexTarget := canonicalTarget.latex
  let mut latexContext := #[]
  let mut semanticNodes := #[]
  let mut canonicalLocals := #[]
  let mut stateOffset := 0
  let mut contextIndex := 0
  for decl in ← getLCtx do
    let (canonicalLocal, localTypeNodes) ← captureCanonicalLocal decl
    canonicalLocals := canonicalLocals.push canonicalLocal
    if decl.isImplementationDetail || decl.binderInfo.isInstImplicit then
      continue
    let escapedName := escapeLatexName decl.userName.toString
    let nodeRoot := s!"context/{decl.fvarId.name}"
    let latex := canonicalLocal.«type».latex
    latexContext := latexContext.push ⟨decl.userName.toString, latex⟩
    let nameStart := stateOffset
    semanticNodes := semanticNodes.push {
      id := nodeRoot ++ "/name"
      kind := "declaration"
      identity := "fvar:" ++ decl.fvarId.name.toString
      fingerprint := toString decl.type.consumeMData.hash
      path := s!"context.{contextIndex}.name"
      latexSpans := #[{ start := nameStart, «end» := nameStart + escapedName.length }]
    }
    let colonStart := stateOffset + escapedName.length + " \\;".length
    semanticNodes := semanticNodes.push {
      id := nodeRoot ++ "/colon"
      kind := "declaration-punctuation"
      identity := "fvar-colon:" ++ decl.fvarId.name.toString
      fingerprint := toString decl.type.consumeMData.hash
      path := s!"context.{contextIndex}.colon"
      latexSpans := #[{ start := colonStart, «end» := colonStart + 1 }]
    }
    let typeOffset := stateOffset + escapedName.length + " \\;:\\; ".length
    semanticNodes := semanticNodes ++ shiftSemanticNodes localTypeNodes typeOffset
    stateOffset := typeOffset + latex.length + 1
    contextIndex := contextIndex + 1
  let targetOffset := stateOffset + "\\vdash\\;".length
  semanticNodes := semanticNodes ++ shiftSemanticNodes targetNodes targetOffset
  return {
    goalId := g.name.toString
    state := formatted.pretty (width := GOAL_PP_WIDTH)
    latexTarget := some latexTarget
    latexContext
    semanticNodes
    canonicalLocals
    canonicalTarget := some canonicalTarget
  }

private partial def widgetChangedPaths
    (text : Lean.Widget.CodeWithInfos) : Array String :=
  match text with
  | .text _ => #[]
  | .append children => children.foldl
      (fun result child => result ++ widgetChangedPaths child) #[]
  | .tag info child =>
      let own := if info.diffStatus?.isSome then #[toString info.subexprPos] else #[]
      own ++ widgetChangedPaths child

private def interactiveChangedPaths (goal : Lean.Widget.InteractiveGoal) : Array String :=
  let target := (widgetChangedPaths goal.type).map ("target:" ++ ·)
  goal.hyps.foldl (fun result hypothesis =>
    let pathPrefix := match hypothesis.fvarIds[0]? with
      | some id => "context/" ++ id.name.toString ++ ":"
      | none => "context/unknown:"
    result ++ (widgetChangedPaths hypothesis.type).map (pathPrefix ++ ·)
  ) target

/-- Run Lean's public infoview tactic diff before Exprs are flattened to
    LaTeX.  This also uses the official metavariable-parent relation based on
    ``Meta.getMVars``. -/
unsafe def goalDiffEvidence? (ci : ContextInfo) (ti : TacticInfo)
    (source target : MVarId) : IO (Option GoalDiffEvidence) := do
  try
    let descendants ← ci.runCoreM <|
      (Meta.getMVars (.mvar source)).run' (s := { mctx := ti.mctxAfter })
    if !descendants.contains target then return none
    let sourceInteractive ← ci.runCoreM <|
      (Lean.Widget.goalToInteractive source).run' (s := { mctx := ti.mctxBefore })
    let targetInteractive ← ci.runCoreM <|
      (Lean.Widget.goalToInteractive target).run' (s := { mctx := ti.mctxAfter })
    let sourceDiff ← ci.runCoreM <|
      (Lean.Widget.diffInteractiveGoals false ti {
        goals := #[sourceInteractive]
      }).run' (s := { mctx := ti.mctxAfter })
    let targetDiff ← ci.runCoreM <|
      (Lean.Widget.diffInteractiveGoals true ti {
        goals := #[targetInteractive]
      }).run' (s := { mctx := ti.mctxAfter })
    let sourceChangedPaths := match sourceDiff.goals[0]? with
      | some goal => interactiveChangedPaths goal
      | none => #[]
    let targetChangedPaths := match targetDiff.goals[0]? with
      | some goal => interactiveChangedPaths goal
      | none => #[]
    return some {
      sourceGoalId := source.name.toString
      targetGoalId := target.name.toString
      sourceChangedPaths
      targetChangedPaths
    }
  catch _ =>
    -- Goal-diff evidence improves animation, but must never make trace
    -- extraction fail for an exotic pretty-printer or metavariable state.
    -- In that rare case the renderer keeps the conservative lineage-only
    -- transition and creates changed material instead of guessing.
    return none

private def orderedLocalDecls (lctx : LocalContext) : Array LocalDecl :=
  lctx.foldl (fun result decl => result.push decl) #[]

private def localDeclIds (lctx : LocalContext) : Array String :=
  orderedLocalDecls lctx |>.map fun decl => decl.fvarId.name.toString

private def canonicalOccurrenceRef (goalId expressionRole occurrenceId : String)
    (localId : String := "") : CanonicalEntityRef := {
  kind := "occurrence"
  goalId
  localId
  expressionRole
  occurrenceId
}

private def canonicalLocalRef (goalId localId : String) : CanonicalEntityRef := {
  kind := "local"
  goalId
  localId
}

private def canonicalEdge (source target : CanonicalEntityRef)
    (relation evidence : String) : CanonicalEntityHyperedge := {
  sources := #[source]
  targets := #[target]
  relation
  provenance := "lean-defeq"
  evidence := #[evidence]
}

private partial def introductionCorrespondence?
    (sourceGoalId targetGoalId : String)
    (sourceTarget target : Expr)
    (introduced : List LocalDecl)
    (path : String := "0")
    (edges : Array CanonicalEntityHyperedge := #[]) :
    MetaM (Option (Array CanonicalEntityHyperedge)) := do
  match introduced with
  | [] =>
    if ← Meta.isDefEq (← instantiateMVars sourceTarget)
        (← instantiateMVars target) then
      return some <| edges.push <| canonicalEdge
        (canonicalOccurrenceRef sourceGoalId "target" s!"target/{path}")
        (canonicalOccurrenceRef targetGoalId "target" "target/0")
        "rewrite" "instantiated-forall-body-defeq-target"
    return none
  | decl :: rest =>
    match sourceTarget.consumeMData with
    | .forallE _ domain body _ =>
      if !(← Meta.isDefEq (← instantiateMVars domain)
          (← instantiateMVars decl.type)) then
        return none
      let localId := decl.fvarId.name.toString
      let edges := edges
        |>.push (canonicalEdge
          (canonicalOccurrenceRef sourceGoalId "target" s!"target/{path}/binder")
          (canonicalLocalRef targetGoalId localId)
          "preserve" "forall-binder-introduced-as-fvar")
        |>.push (canonicalEdge
          (canonicalOccurrenceRef sourceGoalId "target" s!"target/{path}.0")
          (canonicalOccurrenceRef targetGoalId "local-type"
            s!"context/{localId}/0" localId)
          "preserve" "forall-domain-defeq-local-type")
      introductionCorrespondence? sourceGoalId targetGoalId
        (body.instantiate1 decl.toExpr) target rest (path ++ ".1") edges
    | _ => return none

/-- Capture the kernel-certified binder prefix of an introduction even when
    one proof assignment simultaneously opens several descendant goals.  In
    that case the instantiated forall body is not definitionally equal to any
    *single* child target (for example, an `And` body has one child per field),
    but the introduced free variable is still the very same binder in every
    descendant local context. -/
private partial def introductionBinderPrefixCorrespondence?
    (sourceGoalId targetGoalId : String)
    (sourceTarget : Expr)
    (introduced : List LocalDecl)
    (path : String := "0")
    (edges : Array CanonicalEntityHyperedge := #[]) :
    MetaM (Option (Array CanonicalEntityHyperedge)) := do
  match introduced with
  | [] => return some edges
  | decl :: rest =>
    match sourceTarget.consumeMData with
    | .forallE _ domain body _ =>
      if !(← Meta.isDefEq (← instantiateMVars domain)
          (← instantiateMVars decl.type)) then
        return none
      let localId := decl.fvarId.name.toString
      let edges := edges
        |>.push (canonicalEdge
          (canonicalOccurrenceRef sourceGoalId "target" s!"target/{path}/binder")
          (canonicalLocalRef targetGoalId localId)
          "preserve" "forall-binder-introduced-as-fvar")
        |>.push (canonicalEdge
          (canonicalOccurrenceRef sourceGoalId "target" s!"target/{path}.0")
          (canonicalOccurrenceRef targetGoalId "local-type"
            s!"context/{localId}/0" localId)
          "preserve" "forall-domain-defeq-local-type")
      introductionBinderPrefixCorrespondence? sourceGoalId targetGoalId
        (body.instantiate1 decl.toExpr) rest (path ++ ".1") edges
    | _ => return none

private def assignmentContainsDescendant
    (afterMctx : MetavarContext) (source target : MVarId) : MetaM Bool := do
  let some assignment := afterMctx.getExprAssignmentCore? source | return false
  return (← Meta.getMVars assignment).contains target

private partial def reversionCorrespondence?
    (sourceGoalId targetGoalId : String)
    (sourceTarget target : Expr)
    (removed : List LocalDecl)
    (path : String := "0")
    (edges : Array CanonicalEntityHyperedge := #[]) :
    MetaM (Option (Array CanonicalEntityHyperedge)) := do
  match removed with
  | [] =>
    if ← Meta.isDefEq (← instantiateMVars sourceTarget)
        (← instantiateMVars target) then
      return some <| edges.push <| canonicalEdge
        (canonicalOccurrenceRef sourceGoalId "target" "target/0")
        (canonicalOccurrenceRef targetGoalId "target" s!"target/{path}")
        "rewrite" "target-defeq-reverted-forall-body"
    return none
  | decl :: rest =>
    match target.consumeMData with
    | .forallE _ domain body _ =>
      if !(← Meta.isDefEq (← instantiateMVars decl.type)
          (← instantiateMVars domain)) then
        return none
      let localId := decl.fvarId.name.toString
      let edges := edges
        |>.push (canonicalEdge
          (canonicalLocalRef sourceGoalId localId)
          (canonicalOccurrenceRef targetGoalId "target" s!"target/{path}/binder")
          "preserve" "fvar-reverted-as-forall-binder")
        |>.push (canonicalEdge
          (canonicalOccurrenceRef sourceGoalId "local-type"
            s!"context/{localId}/0" localId)
          (canonicalOccurrenceRef targetGoalId "target" s!"target/{path}.0")
          "preserve" "local-type-defeq-forall-domain")
      reversionCorrespondence? sourceGoalId targetGoalId sourceTarget
        (body.instantiate1 decl.toExpr) rest (path ++ ".1") edges
    | _ => return none

/-- Recognize introduction/reversion from kernel state alone.  This inspects
    ordered local contexts and checks the instantiated forall body with Lean's
    definitional equality; tactic names and rendered text are irrelevant. -/
private def canonicalBinderCorrespondence?
    (beforeMctx afterMctx : MetavarContext)
    (source target : MVarId) : MetaM (Array CanonicalEntityHyperedge) := do
  let some sourceDecl := beforeMctx.findDecl? source | return #[]
  let some targetDecl := afterMctx.findDecl? target | return #[]
  let sourceIds := localDeclIds sourceDecl.lctx
  let targetIds := localDeclIds targetDecl.lctx
  let introduced := orderedLocalDecls targetDecl.lctx |>.filter fun decl =>
    !sourceIds.contains decl.fvarId.name.toString
  let removed := orderedLocalDecls sourceDecl.lctx |>.filter fun decl =>
    !targetIds.contains decl.fvarId.name.toString
  if !introduced.isEmpty && removed.isEmpty && introduced.all fun decl => !decl.isLet then
    return ← Meta.withLCtx targetDecl.lctx targetDecl.localInstances do
      if let some correspondence ← introductionCorrespondence?
          source.name.toString target.name.toString
          sourceDecl.type targetDecl.type introduced.toList then
        return correspondence
      -- A source assignment such as `fun n => And.intro ?left ?right`
      -- witnesses that each child is a real descendant of this exact source
      -- goal.  Together with the shared fvar identity and defeq domain checks
      -- above, this certifies binder copying across a split without inspecting
      -- tactic syntax or comparing rendered text.
      if ← assignmentContainsDescendant afterMctx source target then
        return (← introductionBinderPrefixCorrespondence?
          source.name.toString target.name.toString
          sourceDecl.type introduced.toList).getD #[]
      return #[]
  if !removed.isEmpty && introduced.isEmpty && removed.all fun decl => !decl.isLet then
    return ← Meta.withLCtx sourceDecl.lctx sourceDecl.localInstances do
      return (← reversionCorrespondence? source.name.toString target.name.toString
        sourceDecl.type targetDecl.type removed.toList).getD #[]
  return #[]

private def uniqueEntityRefs (refs : Array CanonicalEntityRef) : Array CanonicalEntityRef :=
  refs.foldl (fun result ref => if result.contains ref then result else result.push ref) #[]

private def hyperedgesTouch (left right : CanonicalEntityHyperedge) : Bool :=
  left.sources.any right.sources.contains || left.sources.any right.targets.contains ||
    left.targets.any right.sources.contains || left.targets.any right.targets.contains

/-- Preserve/rewrite describe a one-to-one correspondence.  Once equal
    pairwise observations have been coalesced, their arity carries additional
    semantic information: a preserved entity copied into several successor
    goals is a copy, while a rewritten expression that branches is a split.
    The inverse shape is a merge. -/
private def canonicalRelationForArity (preferred : String)
    (sources targets : Array CanonicalEntityRef) : String :=
  if sources.size == 1 && targets.size > 1 then
    -- Coalescing runs once at the InfoTree boundary and again after temporal
    -- action assembly.  Keep the already-normalized relation stable: a native
    -- copy must not turn into a split merely because it is coalesced twice.
    if preferred == "preserve" || preferred == "copy" then "copy" else "split"
  else if sources.size > 1 && targets.size == 1 then
    "merge"
  else
    preferred

/-- Pairwise kernel checks are coalesced into connected n→m components.  This
    prevents a copied source entity from being exported as several competing
    consumptions while retaining every piece of Lean evidence. -/
private def coalesceCanonicalHyperedges
    (edges : Array CanonicalEntityHyperedge) : Array CanonicalEntityHyperedge :=
  let grouped := edges.foldl (fun result edge =>
    let related := result.filter fun old =>
      old.relation == edge.relation && old.provenance == edge.provenance &&
        hyperedgesTouch old edge
    let unrelated := result.filter fun old =>
      !(old.relation == edge.relation && old.provenance == edge.provenance &&
        hyperedgesTouch old edge)
    let merged := related.foldl (fun current old => {
      sources := uniqueEntityRefs (current.sources ++ old.sources)
      targets := uniqueEntityRefs (current.targets ++ old.targets)
      relation := current.relation
      provenance := current.provenance
      evidence := orderedUniqueStrings (current.evidence ++ old.evidence)
    }) edge
    unrelated.push merged) #[]
  grouped.map fun edge => {
    edge with relation := canonicalRelationForArity edge.relation edge.sources edge.targets
  }

unsafe def visitTacticInfo (ci : ContextInfo) (ti : TacticInfo)
    (acc : List TacticStep) : IO (List TacticStep) := do
  let src := ci.fileMap.source
  let stx := ti.stx

  let .some startPos := stx.getPos? | return acc
  let startPosition := ci.fileMap.toPosition startPos
  let .some span := StringSpan.ofSyntax stx | return acc

  let mut goals_before := []
  let mut stepProofKind := "unassigned"
  let mut stepProofFingerprint := ""
  let mut stepProofTerm := ""
  let mut stepProofDescendants := #[]
  let mut stepProofPremises := #[]
  let mut stepProofConstants := #[]
  let mut goalProofs : Array GoalProofObservation := #[]
  let mut canonicalCorrespondence : Array CanonicalEntityHyperedge := #[]
  for g in ti.goalsBefore do
    let cm := (renderGoal g).run' (s := { mctx := ti.mctxBefore })
    let goal ← ci.runCoreM cm
    goals_before := goals_before ++ [goal]

    let assignment := ti.mctxAfter.getExprAssignmentCore? g
    let goalProofKind := classifyProofAssignment assignment
    let goalProofFingerprint := proofFingerprint assignment
    let mut goalProofTerm := ""
    let mut goalProofPremises := #[]
    let mut goalProofConstants := #[]
    if let some proof := assignment then
      goalProofPremises := collectProofFVars proof
      goalProofConstants := collectConstants proof
      let renderProof := (g.withContext do Meta.ppExpr proof).run' (s := { mctx := ti.mctxAfter })
      goalProofTerm := (← ci.runCoreM renderProof).pretty (width := 120)
    let collect := (Meta.getMVars (.mvar g)).run' (s := { mctx := ti.mctxAfter })
    let descendants ← ci.runCoreM collect
    let goalProofDescendants := descendants.map (·.name.toString)
    goalProofs := goalProofs.push {
      sourceGoalId := g.name.toString
      proofKind := goalProofKind
      proofFingerprint := goalProofFingerprint
      proofTerm := goalProofTerm
      proofDescendants := goalProofDescendants
      proofPremises := goalProofPremises
      proofConstants := goalProofConstants
    }
    -- Preserve the legacy aggregate for old readers.  The flattened stage
    -- below selects the exact per-source row whenever it is available.
    stepProofKind := goalProofKind
    stepProofFingerprint := goalProofFingerprint
    stepProofTerm := goalProofTerm
    stepProofDescendants := goalProofDescendants
    stepProofPremises := stepProofPremises ++ goalProofPremises
    stepProofConstants := stepProofConstants ++ goalProofConstants

  let mut goals_after := []
  for g in ti.goalsAfter do
    let cm := (renderGoal g).run' (s := { mctx := ti.mctxAfter })
    let goal ← ci.runCoreM cm
    goals_after := goals_after ++ [goal]

  let mut goalDiffs := #[]
  for source in ti.goalsBefore do
    for target in ti.goalsAfter do
      if let some evidence ← goalDiffEvidence? ci ti source target then
        goalDiffs := goalDiffs.push evidence
      -- Canonical correspondence is optional evidence: a failed defeq check
      -- means “no certified edge”, never a failed proof trace.
      try
        let capture := (canonicalBinderCorrespondence?
          ti.mctxBefore ti.mctxAfter source target).run' (s := { mctx := ti.mctxAfter })
        canonicalCorrespondence := canonicalCorrespondence ++ (← ci.runCoreM capture)
      catch _ => pure ()
  canonicalCorrespondence := coalesceCanonicalHyperedges canonicalCorrespondence

  if let some ``Lean.Parser.Tactic.tacticSeq1Indented := ti.name?
  then
    if acc.length > 0 then
    return [TacticStep.seq EMPTY_SPAN acc]

  if let .atom _ "by" := ti.stx then
    if acc.length > 0 then
      return [TacticStep.seq span acc]
    else return acc

  match stx.getHeadInfo? with
  | .some (.synthetic ..) =>
    -- Not actual concrete syntax the user wrote. Ignore.
    return acc
  | _ => pure ()

  -- Tactic step is a no-op. Ignore it.
  if goals_before == goals_after then return acc

  let .some name := ti.name? | return acc
  match name with
  | `null => return acc
--  | ``Lean.Parser.Term.byTactic =>
--      return [TacticStep.seq ⟨span, goals_before, goals_after⟩ acc]
  | ``cdotTk => return acc
  | ``atomicTac =>
     if let .node _ ``atomicTac #[_, _, inner, _] := ti.stx then
       let .some span' := StringSpan.ofSyntax inner | panic! "bad atomic span"
       let .some startPos := inner.getPos? | panic! "bad inner span"
       let startPosition := ci.fileMap.toPosition startPos
       let text := replace_inner_syntax src span' []
       let text := left_trim_lines text startPosition.column

       let d := { elaborator := ti.elaborator.toString
                  name := name.toString
                  tacticSpan := span'
                  text
                  proofKind := stepProofKind
                  proofFingerprint := stepProofFingerprint
                  proofTerm := stepProofTerm
                  proofDescendants := stepProofDescendants
                  proofPremises := stepProofPremises
                  proofConstants := stepProofConstants
                  goalDiffs
                  goalProofs
                  canonicalCorrespondence
                  goals_before, goals_after }
       return [TacticStep.node d []]
     else
       panic! "bad atomic syntax"
  | ``reverseS2Tac =>
     if let .node _ ``reverseS2Tac #[_, _, inner, _] := ti.stx then
       let .some span' := StringSpan.ofSyntax inner | panic! "bad reverse_s2 span"
       let .some startPos := inner.getPos? | panic! "bad inner span"
       let startPosition := ci.fileMap.toPosition startPos
       let text := replace_inner_syntax src span' []
       let text := left_trim_lines text startPosition.column

       let d := { elaborator := ti.elaborator.toString
                  name := name.toString
                  tacticSpan := span'
                  text
                  proofKind := stepProofKind
                  proofFingerprint := stepProofFingerprint
                  proofTerm := stepProofTerm
                  proofDescendants := stepProofDescendants
                  proofPremises := stepProofPremises
                  proofConstants := stepProofConstants
                  goalDiffs
                  goalProofs
                  canonicalCorrespondence
                  reverse_s2 := true
                  goals_before, goals_after }
       return [TacticStep.node d []]
     else
       panic! "bad reverse_s2 syntax"

  | ``reverseS1S2Tac =>
     if let .node _ ``reverseS1S2Tac #[_, _, inner, _] := ti.stx then
       let .some span' := StringSpan.ofSyntax inner | panic! "bad reverse_s1_s2 span"
       let .some startPos := inner.getPos? | panic! "bad inner span"
       let startPosition := ci.fileMap.toPosition startPos
       let text := replace_inner_syntax src span' []
       let text := left_trim_lines text startPosition.column

       let d := { elaborator := ti.elaborator.toString
                  name := name.toString
                  tacticSpan := span'
                  text
                  proofKind := stepProofKind
                  proofFingerprint := stepProofFingerprint
                  proofTerm := stepProofTerm
                  proofDescendants := stepProofDescendants
                  proofPremises := stepProofPremises
                  proofConstants := stepProofConstants
                  goalDiffs
                  goalProofs
                  canonicalCorrespondence
                  reverse_s1 := true
                  reverse_s2 := true
                  goals_before, goals_after }
       return [TacticStep.node d []]
     else
       panic! "bad reverse_s1_s2 syntax"


  | ``reverseS1Tac =>
     if let .node _ ``reverseS1Tac #[_, _, inner, _] := ti.stx then
       let .some span' := StringSpan.ofSyntax inner | panic! "bad reverse_s1 span"
       let .some startPos := inner.getPos? | panic! "bad inner span"
       let startPosition := ci.fileMap.toPosition startPos
       let text := replace_inner_syntax src span' []
       let text := left_trim_lines text startPosition.column

       let d := { elaborator := ti.elaborator.toString
                  name := name.toString
                  tacticSpan := span'
                  text
                  proofKind := stepProofKind
                  proofFingerprint := stepProofFingerprint
                  proofTerm := stepProofTerm
                  proofDescendants := stepProofDescendants
                  proofPremises := stepProofPremises
                  proofConstants := stepProofConstants
                  goalDiffs
                  goalProofs
                  canonicalCorrespondence
                  reverse_s1 := true
                  goals_before, goals_after }
       return [TacticStep.node d []]
     else
       panic! "bad reverse_s1 syntax"


  | ``Lean.Parser.Tactic.inductionAlt =>
    -- induction alternative. We want the direct children
    -- of `induction` to be `seq` nodes, so we collapse these.
    return acc
  | _ => pure ()

  let mut inners := []
  for c in acc do
    inners := inners ++ [c.span_union]

  let text := replace_inner_syntax src span inners
  let text := left_trim_lines text startPosition.column

  let d := { elaborator := ti.elaborator.toString
             name := name.toString
             tacticSpan := span
             text
             proofKind := stepProofKind
             proofFingerprint := stepProofFingerprint
             proofTerm := stepProofTerm
             proofDescendants := stepProofDescendants
             proofPremises := stepProofPremises
             proofConstants := stepProofConstants
             goalDiffs
             goalProofs
             canonicalCorrespondence
             goals_before, goals_after }
  return [TacticStep.node d acc]

unsafe def visitNode (ci : ContextInfo) (info : Info)
    (_children : PersistentArray InfoTree)
    (acc : List TacticStep) : IO (List TacticStep) :=
  match info with
  | .ofTacticInfo ti => visitTacticInfo ci ti acc
  | _ => pure acc

unsafe def extractToplevelStep (tree : InfoTree) : IO TacticStep := do
  let steps ← collectNodesBottomUpM (m := IO) visitNode tree
  let [step] := steps | throw <| IO.userError "got more than one toplevel step"
  return step

private def orderedUniqueGoals (goals : List Goal) : List Goal :=
  goals.foldl (fun result goal =>
    if result.any fun old => old.goalId == goal.goalId then result
    else result ++ [goal]) []

/-- Project an n→m action boundary onto one source goal without guessing from
    printed symbols.  Lean's assignment descendants, explicit goal diff and a
    persistent MVar identity are the only admissible witnesses. -/
private def sourceResults (data : TacticStepData) (source : Goal)
    (actionResults : List Goal) : List Goal :=
  let sourceId := source.goalId
  let descendants := (data.goalProofs.find? fun proof =>
    proof.sourceGoalId == sourceId).map (·.proofDescendants) |>.getD #[]
  let diffTargets := data.goalDiffs.filterMap fun evidence =>
    if evidence.sourceGoalId == sourceId then some evidence.targetGoalId else none
  let witnessed := actionResults.filter fun target =>
    target.goalId == sourceId || descendants.contains target.goalId ||
      diffTargets.contains target.goalId
  if witnessed.isEmpty && data.goals_before.length == 1 then actionResults
  else witnessed

partial def stage2_aux (step : TacticStep) : StateM StepMap Unit := match step with
| .node data children => do
  if !data.goals_before.isEmpty then
    let span := data.tacticSpan
    -- `TacticInfo` may expose the entire live frontier even when a focused
    -- syntax node consumes only one goal.  Equal goals present on both sides
    -- are passive context, not sources of this action.  A pure permutation is
    -- the sole case where all equal goals are active because order itself is
    -- the observed change.
    let unchangedSourceIds := data.goals_before.filterMap fun source =>
      if data.goals_after.any fun target => target == source then some source.goalId else none
    let changedSources := data.goals_before.filter fun source =>
      !unchangedSourceIds.contains source.goalId
    let actionSources := if changedSources.isEmpty && data.goals_before != data.goals_after then
      data.goals_before
    else changedSources
    let passiveSourceIds := data.goals_before.filterMap fun source =>
      if actionSources.any fun active => active.goalId == source.goalId then none
      else some source.goalId
    let directActionResults := data.goals_after.filter fun target =>
      !passiveSourceIds.contains target.goalId
    -- Child entry goals are an intermediate frontier, not simultaneous with
    -- the enclosing node's already-final goalsAfter.  Deduplication is by
    -- Lean MVar identity and retains InfoTree order.
    let mut childGoals := []
    for child in children do
      childGoals := childGoals ++ child.goals_before
    childGoals := orderedUniqueGoals childGoals
    let actionGoalsAfter := if childGoals.isEmpty then directActionResults else childGoals
    let deferredGoalsAfter := if childGoals.isEmpty then [] else directActionResults
    let sourceIds := String.intercalate "," (actionSources.map (·.goalId))
    let actionId :=
      s!"action:{span.startPos.byteIdx}:{span.endPos.byteIdx}:{data.name}:{sourceIds}"
    let continuationToken := if childGoals.isEmpty then none
      else some s!"continuation:{actionId}"
    for goal in actionSources do
      let sm ← get
      let goalId := goal.goalId
      let existing := (sm.get? goalId).getD #[]
      -- Recursive traversal visits a wrapper before its more specific child.
      -- A contained child refines the same temporal occurrence.  A disjoint
      -- later span appends even when Lean reuses the MVar identity.
      let refinesLast := existing.back?.any fun previous =>
        previous.span.startPos.byteIdx <= span.startPos.byteIdx &&
          span.endPos.byteIdx <= previous.span.endPos.byteIdx
      let temporalIndex := if refinesLast then existing.size - 1 else existing.size
      let sourceProof? := data.goalProofs.find? fun proof => proof.sourceGoalId == goalId
      let goalsAfter := sourceResults data goal actionGoalsAfter
      let ts' := { span,
                   observation_id :=
                     s!"observation:{goalId}:{span.startPos.byteIdx}:{span.endPos.byteIdx}:{temporalIndex}",
                   action_id := actionId,
                   goal_before := goal,
                   goals_after := goalsAfter,
                   action_goals_before := actionSources,
                   action_goals_after := actionGoalsAfter,
                   direct_goals_after := data.goals_after,
                   deferred_goals_after := deferredGoalsAfter,
                   continuation_token := continuationToken,
                   text := data.text,
                   reverse_s1 := data.reverse_s1
                   reverse_s2 := data.reverse_s2
                   proofKind := sourceProof?.map (·.proofKind) |>.getD data.proofKind
                   proofFingerprint := sourceProof?.map (·.proofFingerprint)
                     |>.getD data.proofFingerprint
                   proofTerm := sourceProof?.map (·.proofTerm) |>.getD data.proofTerm
                   proofDescendants := sourceProof?.map (·.proofDescendants)
                     |>.getD data.proofDescendants
                   proofPremises := sourceProof?.map (·.proofPremises)
                     |>.getD data.proofPremises
                   proofConstants := sourceProof?.map (·.proofConstants)
                     |>.getD data.proofConstants
                   goalDiffs := data.goalDiffs
                   canonicalCorrespondence := data.canonicalCorrespondence.filter fun edge =>
                     edge.sources.any fun source => source.goalId == goalId }
      let observations := if refinesLast then existing.pop.push ts' else existing.push ts'
      set (sm.insert goalId observations)
  for child in children do
    stage2_aux child
| .seq _ children => do
  for child in children do
    stage2_aux child

partial def stage2 (step : TacticStep) : IO Stage2State := do
  let ⟨_, m⟩ := StateT.run (stage2_aux step) default
  let [top_goal] := step.goals_before |
         throw <| IO.userError "got more than one toplevel step"
  return { startGoal := top_goal, steps := m }


def stage3_inner (config : Config) (step : TacticStep') : GoalAction := Id.run do
    let mut ts_rev : List TransformedGoal := []
    let adapter := tacticAdapter step.text
    for g in step.goals_after do
      let im := Animate.do_match step.goal_before.state g.state
                                 (min_match_len := config.min_match_len)
                                 (nonmatchers := config.nonmatchers)
                                 (s1_reverse_order := step.reverse_s1)
                                 (s2_reverse_order := step.reverse_s2)
      let latexIm := Animate.do_match step.goal_before.latexState g.latexState
                                      (min_match_len := 1)
                                      (s1_reverse_order := step.reverse_s1)
                                      (s2_reverse_order := step.reverse_s2)
      let transition : SemanticTransition := {
        sourceNodes := step.goal_before.semanticNodes
        targetNodes := g.semanticNodes
        edges := semanticEdges adapter step.goal_before.semanticNodes g.semanticNodes
          step.proofPremises
        proofKind := step.proofKind
        adapter
        proofFingerprint := step.proofFingerprint
        proofTerm := step.proofTerm
        proofDescendants := step.proofDescendants
        proofPremises := step.proofPremises
        proofConstants := step.proofConstants
        goalDiff := step.goalDiffs.find? fun evidence =>
          evidence.sourceGoalId == step.goal_before.goalId &&
            evidence.targetGoalId == g.goalId
        fallbackReason := if step.goal_before.semanticNodes.isEmpty || g.semanticNodes.isEmpty then
          some "semantic expression nodes unavailable"
        else none
      }
      ts_rev := {
        goal := g
        indexMaps := im
        latexIndexMaps := some latexIm
        semanticTransition := some transition
      } :: ts_rev
    let ga : GoalAction := {
      startGoalId := step.goal_before.goalId
      startState := step.goal_before.state
      results := ts_rev.reverse
      proofKind := step.proofKind
      proofFingerprint := step.proofFingerprint
      proofTerm := step.proofTerm
      proofDescendants := step.proofDescendants
      explanation := tacticExplanation step.text step.proofKind step.proofFingerprint
        step.proofPremises step.proofConstants
    }
    return ga

partial def stage3 (config : Config) (state2 : Stage2State)
    (cachedActions : Array Action := #[])
    (onNewAction : Option (Nat → Action → IO Unit) := none) : IO Movie := do
  let mut rev_actions := []
  let mut visited : Std.HashSet String := {}
  let mut cursors : Std.HashMap String Nat := {}
  let mut colorings := #[]
  -- A blocked continuation is a dependency edge in the observed goal forest.
  -- `blockers` may contain live goal ids or another continuation's opaque
  -- token; resolving a nested token substitutes its eventual goals.
  let mut deferredGoalFrontiers : List (String × List String × List String) := []
  let mut currentGoals := [state2.startGoal.goalId]
  let mut actionIndex := 0
  let peekStep (cursorMap : Std.HashMap String Nat) (goalId : String) : Option TacticStep' := do
    let observations ← state2.steps.get? goalId
    observations[(cursorMap.get? goalId).getD 0]?
  let goalForId (preferred : List Goal) (cursorMap : Std.HashMap String Nat)
      (goalId : String) : Option Goal :=
    (preferred.find? fun goal => goal.goalId == goalId).orElse fun _ =>
      (peekStep cursorMap goalId).map (·.goal_before)
  let advanceDeferredAction
      (frontiers : List (String × List String × List String))
      (sourceGoalIds replacements : List String) :=
    frontiers.map fun (token, blockers, goals) =>
      if blockers.any sourceGoalIds.contains then
        let survivors := blockers.filter fun blocker => !sourceGoalIds.contains blocker
        (token, (survivors ++ replacements).eraseDups, goals)
      else
        (token, blockers, goals)
  while currentGoals.length > 0 do
    if config.print_stage2 then
      IO.println s!"FRONTIER[{actionIndex}] live={repr currentGoals} deferred={repr deferredGoalFrontiers}"
    let currentGoal :: _ := currentGoals | panic "impossible"
    let .some step :=
      peekStep cursors currentGoal | panic s!"goal observation not found {currentGoal}"
    let actionSourceIds := step.action_goals_before.map (·.goalId)
    let activeSourceIds := actionSourceIds.filter currentGoals.contains
    if activeSourceIds.isEmpty then
      panic s!"action {step.action_id} has no live source in {repr currentGoals}"
    let mut actionSteps : List TacticStep' := []
    for sourceGoalId in activeSourceIds do
      let .some sourceStep := peekStep cursors sourceGoalId |
        panic s!"goal observation not found {sourceGoalId} for action {step.action_id}"
      if sourceStep.action_id != step.action_id then
        panic s!"temporal action mismatch for {sourceGoalId}: expected {step.action_id}, got {sourceStep.action_id}"
      if visited.contains sourceStep.observation_id then
        panic s!"re-visited goal observation {sourceStep.observation_id}"
      visited := visited.insert sourceStep.observation_id
      let sourceCursor := (cursors.get? sourceGoalId).getD 0
      cursors := cursors.insert sourceGoalId (sourceCursor + 1)
      colorings := colorings.push ⟨sourceGoalId,
        ← HighlightSyntax.assign_colors sourceStep.goal_before.state⟩
      actionSteps := actionSteps ++ [sourceStep]
    let beforeState := currentGoals.filterMap fun goalId =>
      goalForId step.action_goals_before cursors goalId
    let cachedAction? := cachedActions[actionIndex]?
    let mut goal_actions := match cachedAction? with
      | some _ => []
      | none => actionSteps.map (stage3_inner config)
    let targetGoalIds := step.action_goals_after.map (·.goalId)
    let mut lineage : Array ObservedGoalLineage := #[{
      sourceGoalIds := activeSourceIds.toArray
      targetGoalIds := targetGoalIds.toArray
      relation := if targetGoalIds.isEmpty then "close"
        else if activeSourceIds.length > 1 && targetGoalIds.length == 1 then "merge"
        else if targetGoalIds.length > activeSourceIds.length then "split" else "evolve"
    }]
    let outerReplacements := match step.continuation_token with
      | some token => [token]
      | none => targetGoalIds
    deferredGoalFrontiers := advanceDeferredAction deferredGoalFrontiers
      activeSourceIds outerReplacements
    if let some token := step.continuation_token then
      deferredGoalFrontiers := (token, targetGoalIds,
        step.deferred_goals_after.map (·.goalId)) :: deferredGoalFrontiers
    let untouched := currentGoals.filter fun goalId => !activeSourceIds.contains goalId
    currentGoals := (targetGoalIds ++ untouched).eraseDups
    -- Resolve ready continuations one at a time.  Substituting the token into
    -- outer blockers is what makes arbitrarily nested `have`/branch proofs
    -- resume in the correct order, including continuations with no live goal.
    let mut resolving := true
    while resolving do
      match deferredGoalFrontiers.find? fun (_, blockers, _) => blockers.isEmpty with
      | none => resolving := false
      | some (token, _, resumedGoals) =>
        deferredGoalFrontiers := deferredGoalFrontiers.filter fun (candidate, _, _) =>
          candidate != token
        deferredGoalFrontiers := deferredGoalFrontiers.map fun
          (candidate, blockers, goals) =>
            let blockers := blockers.flatMap fun blocker =>
              if blocker == token then resumedGoals else [blocker]
            (candidate, blockers.eraseDups, goals)
        currentGoals := resumedGoals ++ currentGoals
        if !resumedGoals.isEmpty then
          lineage := lineage.push {
            sourceGoalIds := #[]
            targetGoalIds := resumedGoals.toArray
            relation := "resume"
          }
    if config.print_stage2 then
      IO.println s!"FRONTIER[{actionIndex}] next={repr currentGoals} deferred={repr deferredGoalFrontiers}"
    let afterState := currentGoals.filterMap fun goalId =>
      goalForId step.action_goals_after cursors goalId
    -- A single action may have several source observations.  Coalesce again
    -- at the action boundary so one semantic entity consumed by multiple
    -- successor goals is exported as one valid n-ary edge, never as several
    -- competing pairwise consumptions.
    let canonicalCorrespondence := coalesceCanonicalHyperedges <|
      actionSteps.foldl
        (fun result sourceStep => result ++ sourceStep.canonicalCorrespondence) #[]
    let shared : Action := {
      tacticText := step.text
      goalActions := goal_actions
      beforeState := beforeState.toArray
      afterState := afterState.toArray
      focusBefore := activeSourceIds.toArray
      focusAfter := targetGoalIds.toArray
      goalLineage := lineage
      canonicalCorrespondence
    }
    -- Cached per-goal certificates remain reusable, but they must never
    -- overwrite the action-wide frontier observed in this extraction.
    let action := match cachedAction? with
      | some cached => { shared with goalActions := cached.goalActions }
      | none => shared
    if actionIndex >= cachedActions.size then
      if let some callback := onNewAction then callback actionIndex action
    rev_actions := action :: rev_actions
    actionIndex := actionIndex + 1
    pure ()
  if !deferredGoalFrontiers.isEmpty then
    panic s!"unresolved goal continuations: {repr deferredGoalFrontiers}"
  -- TODO: verify that everything in state2 was visited.
  return { theoremName := config.const_name.toString
           actions := rev_actions.reverse
           startGoal := state2.startGoal,
           highlighting := colorings}

end infotrees

end Animate
