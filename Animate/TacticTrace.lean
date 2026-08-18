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
  text : String
  span : StringSpan
  goal_before : Goal
  goals_after : List Goal -- include children
  reverse_s1 : Bool := false
  reverse_s2 : Bool := false
  proofKind : String := "unassigned"
  proofFingerprint : String := ""
  proofTerm : String := ""
  proofDescendants : Array String := #[]
  proofPremises : Array String := #[]
  proofConstants : Array String := #[]
  goalDiffs : Array GoalDiffEvidence := #[]
deriving Lean.ToJson, Lean.FromJson

/-- map from goalId to tactic step that consumes that goal.
    That should be the latest, most specific step
    that lists the goal in its before state but not in its after state.
-/
abbrev StepMap := Std.HashMap String TacticStep'

structure Stage2State where
  startGoal : Goal
  steps : StepMap

def Stage2State.dump (s : Stage2State) : IO Unit := do
  IO.println <| "stage2 with top goal " ++ s.startGoal.goalId
  for ⟨_, ts⟩ in s.steps.toList do
    IO.println <| s!"{Lean.ToJson.toJson ts}"
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

/-- Render a metavariable goal both with Lean's regular pretty printer (needed
for the original expression matching algorithm) and with LeanTeX. Keeping both
representations also provides backwards compatibility for existing traces. -/
def renderGoal (g : MVarId) : MetaM Goal := g.withContext do
  let formatted ← Meta.ppGoal g
  let target ← g.getType
  let (latexTarget, targetNodes) ← renderSemanticExpr "target" target
  let mut latexContext := #[]
  let mut semanticNodes := #[]
  let mut stateOffset := 0
  let mut contextIndex := 0
  for decl in ← getLCtx do
    if decl.isImplementationDetail || decl.binderInfo.isInstImplicit then
      continue
    let escapedName := escapeLatexName decl.userName.toString
    let nodeRoot := s!"context/{decl.fvarId.name}"
    let (latex, nodes) ← renderSemanticExpr nodeRoot decl.type
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
    semanticNodes := semanticNodes ++ shiftSemanticNodes nodes typeOffset
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
  for g in ti.goalsBefore do
    let cm := (renderGoal g).run' (s := { mctx := ti.mctxBefore })
    let goal ← ci.runCoreM cm
    goals_before := goals_before ++ [goal]

    let assignment := ti.mctxAfter.getExprAssignmentCore? g
    stepProofKind := classifyProofAssignment assignment
    stepProofFingerprint := proofFingerprint assignment
    if let some proof := assignment then
      stepProofPremises := stepProofPremises ++ collectProofFVars proof
      stepProofConstants := stepProofConstants ++ collectConstants proof
      let renderProof := (g.withContext do Meta.ppExpr proof).run' (s := { mctx := ti.mctxAfter })
      stepProofTerm := (← ci.runCoreM renderProof).pretty (width := 120)
    let collect := (Meta.getMVars (.mvar g)).run' (s := { mctx := ti.mctxAfter })
    let descendants ← ci.runCoreM collect
    stepProofDescendants := descendants.map (·.name.toString)

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

partial def stage2_aux (step : TacticStep) : StateM StepMap Unit := match step with
| .node data children => do
  if let [goal] := data.goals_before then
    let sm ← get
    let goalId := goal.goalId
    let span := data.tacticSpan
    -- goals after includes stuff from the children in addition to data.goals_after.
    let mut goals_after := []
    for child in children do
      for g in child.goals_before do
        goals_after := g :: goals_after
    for g in data.goals_after do
       goals_after := g :: goals_after
    let ts' := { span,
                 goal_before := goal,
                 goals_after := goals_after.reverse,
                 text := data.text,
                 reverse_s1 := data.reverse_s1
                 reverse_s2 := data.reverse_s2
                 proofKind := data.proofKind
                 proofFingerprint := data.proofFingerprint
                 proofTerm := data.proofTerm
                 proofDescendants := data.proofDescendants
                 proofPremises := data.proofPremises
                 proofConstants := data.proofConstants
                 goalDiffs := data.goalDiffs }
    set (sm.insert goalId ts')
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
  let mut colorings := #[]
  let mut currentGoals := [state2.startGoal.goalId]
  let mut actionIndex := 0
  while currentGoals.length > 0 do
    let currentGoal :: rest := currentGoals | panic "impossible"
    if visited.contains currentGoal then panic s!"re-visited goal {currentGoal}"
    visited := visited.insert currentGoal
    let .some step :=
      state2.steps.get? currentGoal | panic s!"goal not found {currentGoal}"
    colorings := colorings.push ⟨currentGoal, ← HighlightSyntax.assign_colors step.goal_before.state⟩
    let cachedAction? := cachedActions[actionIndex]?
    let mut goal_actions := match cachedAction? with
      | some _ => []
      | none => [stage3_inner config step]
    currentGoals := []
    for gid in rest do
      let .some other_step :=
        state2.steps.get? gid | panic s!"goal not found {gid}"
      if other_step.span == step.span
      then
        colorings := colorings.push ⟨gid, ← HighlightSyntax.assign_colors other_step.goal_before.state⟩
        if cachedAction?.isNone then
          goal_actions := stage3_inner config other_step :: goal_actions
        currentGoals := currentGoals ++ (other_step.goals_after.map (·.goalId))
      else
        currentGoals := currentGoals ++ [gid]

    let computed : Action := { tacticText := step.text, goalActions := goal_actions }
    let action := cachedAction?.getD computed
    if actionIndex >= cachedActions.size then
      if let some callback := onNewAction then callback actionIndex action
    rev_actions := action :: rev_actions
    actionIndex := actionIndex + 1
    currentGoals := (step.goals_after.map (·.goalId)) ++ currentGoals
    pure ()
  -- TODO: verify that everything in state2 was visited.
  return { theoremName := config.const_name.toString
           actions := rev_actions.reverse
           startGoal := state2.startGoal,
           highlighting := colorings}

end infotrees

end Animate
