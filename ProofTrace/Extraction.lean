import Lean
import Lean.DeclarationRange
import Lean.Util.CollectAxioms
import Lean.Util.FoldConsts
import Lean.Util.NumObjs

import SemanticTransitions
import ProofTrace.Schema
import ProofTrace.Dependencies

namespace ProofTrace

open Lean Meta Animate

private def partialChunkSize : Nat := 128

private structure Builder where
  steps : Array Step := #[]
  /-- One expression can legitimately occur in multiple sibling Fitch scopes.
      Keep every emitted row and reuse only a row visible from the current
      scope; a global ExprMap-to-single-line cache creates invalid citations. -/
  seen : ExprMap (Array Nat) := {}
  cachedSteps : Array Step := #[]
  replayedSteps : Nat := 0
  partialDirectory : Option System.FilePath := none
  chapterName : String := ""
  chapterProofFingerprint : String := ""
  progressPath : Option System.FilePath := none
  progress : Option ExtractionProgress := none

private def fingerprint (expr : Expr) : String :=
  toString expr.consumeMData.hash

private partial def replaceFileWithRetries (temporary path : System.FilePath)
    (attempts : Nat) : IO Unit := do
  try
    if ← path.pathExists then
      IO.FS.removeFile path
    IO.FS.rename temporary path
  catch error =>
    if attempts == 0 then
      throw error
    -- On Windows the CLI may briefly hold progress.json open while polling it.
    -- Retrying keeps a harmless sharing violation from aborting a long proof.
    IO.sleep 25
    replaceFileWithRetries temporary path (attempts - 1)

def atomicWriteJson {α : Type} [ToJson α]
    (path : System.FilePath) (value : α) : IO Unit := do
  if let some parent := path.parent then
    IO.FS.createDirAll parent
  let temporary : System.FilePath := ⟨path.toString ++ ".tmp"⟩
  IO.FS.writeFile temporary ((toJson value).pretty (lineWidth := 200))
  replaceFileWithRetries temporary path 40

private def progressPath? (checkpointDirectory : Option System.FilePath) :
    Option System.FilePath :=
  checkpointDirectory.map (· / "progress.json")

def reportProgressPhase (checkpointDirectory : Option System.FilePath)
    (phase theoremName : String) : IO Unit := do
  if let some path := progressPath? checkpointDirectory then
    atomicWriteJson path ({ phase, theoremName } : ExtractionProgress)

def reportProgress (checkpointDirectory : Option System.FilePath)
    (progress : ExtractionProgress) : IO Unit := do
  if let some path := progressPath? checkpointDirectory then
    atomicWriteJson path progress

def reportCommandProfiles (checkpointDirectory : Option System.FilePath)
    (sourceFile : String) (commands : Array CommandProfileEntry)
    (complete : Bool := false) : IO Unit := do
  if let some directory := checkpointDirectory then
    atomicWriteJson (directory / "command-profile.json") ({
      sourceFile
      complete
      commands
    } : CommandProfileReport)

private def writeProgress (path : Option System.FilePath)
    (progress : ExtractionProgress) : MetaM Unit := do
  if let some path := path then
    atomicWriteJson path progress

private def updateBuilderProgress (builder : IO.Ref Builder) (force : Bool := false) :
    MetaM Unit := do
  let state ← builder.get
  let some progress := state.progress | return
  let processed := state.steps.size
  if !force && processed % 16 != 0 then return
  let progress := {
    progress with
    processedSteps := min processed progress.totalSteps
    emittedSteps := processed - state.replayedSteps
    replayedSteps := state.replayedSteps
  }
  builder.modify fun current => { current with progress := some progress }
  writeProgress state.progressPath progress

private def partialChapterDirectory (checkpointDirectory : System.FilePath)
    (chapterIndex : Nat) : System.FilePath :=
  checkpointDirectory / s!"chapter-{chapterIndex}.parts"

private def partialChunkPath (directory : System.FilePath) (chunkIndex : Nat) :
    System.FilePath :=
  directory / s!"steps-{chunkIndex}.json"

private def readStepChunk? (path : System.FilePath) : MetaM (Option StepChunk) := do
  unless ← path.pathExists do return none
  try
    let payload ← IO.FS.readFile path
    let json ← match Json.parse payload with
      | .ok value => pure value
      | .error message => throwError message
    match fromJson? json with
    | .ok value => return some value
    | .error message => throwError message
  catch _ =>
    return none

private def loadPartialSteps (directory : System.FilePath) (theoremName : Name)
    (proofFingerprint : String) : MetaM (Array Step) := do
  let mut result := #[]
  let mut chunkIndex := 0
  while true do
    let some chunk ← readStepChunk? (partialChunkPath directory chunkIndex) | break
    if chunk.theoremName != theoremName.toString ||
        chunk.proofFingerprint != proofFingerprint ||
        chunk.startId != result.size || chunk.steps.isEmpty then
      break
    let mut valid := true
    for step in chunk.steps, offset in [0 : chunk.steps.size] do
      if step.id != result.size + offset then valid := false
    if !valid then break
    result := result ++ chunk.steps
    chunkIndex := chunkIndex + 1
  return result

private def flushPartialChunk (builder : IO.Ref Builder) : MetaM Unit := do
  let state ← builder.get
  let some directory := state.partialDirectory | return
  let newCount := state.steps.size - state.cachedSteps.size
  if newCount == 0 || newCount % partialChunkSize != 0 then return
  let startId := state.steps.size - partialChunkSize
  let chunk : StepChunk := {
    theoremName := state.chapterName
    proofFingerprint := state.chapterProofFingerprint
    startId
    steps := state.steps.extract startId state.steps.size
  }
  let chunkIndex := startId / partialChunkSize
  atomicWriteJson (partialChunkPath directory chunkIndex) chunk

private def escapeLatexName (name : Name) : String :=
  name.toString.replace "_" "\\_"

private def theoremHead? (expr : Expr) : Option Name :=
  match expr.getAppFn.consumeMData with
  | .const name _ => some name
  | _ => none

private def applicationRule (fn : Expr) : MetaM String := do
  let fnType <- whnf (← inferType fn)
  match fnType with
  | .forallE _ domain _ _ =>
    if ← isProp domain then pure "implies-elimination" else pure "forall-elimination"
  | _ => pure "application"

private def introductionRule (domain : Expr) : MetaM String := do
  if ← isProp domain then pure "implies-introduction" else pure "forall-introduction"

private def administrativeTheorem (name : Name) : Bool :=
  let text := name.toString
  text.startsWith "Mathlib.Tactic." || text.startsWith "Mathlib.Meta." ||
    text.startsWith "Lean.Meta." ||
    [``Eq.mp, ``Eq.mpr, ``Eq.ndrec, ``Eq.refl, ``congrArg, ``id].contains name

private def addStep
    (builder : IO.Ref Builder)
    (proof proposition : Expr)
    (scopeId : String)
    (parentScopeId : Option String)
    (depth : Nat)
    (kind rule proofPath : String)
    (premises : Array Nat := #[])
    (usedTheorem : Option Name := none)
    (binderName : Option Name := none)
    (displayLatex : Option String := none)
    (opensScope : Option String := none)
    (closesScope : Option String := none)
    (remember : Bool := true) : MetaM Nat := do
  let state ← builder.get
  let id := state.steps.size
  let proofFingerprint := fingerprint proof
  let propositionFingerprint := fingerprint proposition
  if let some cached := state.cachedSteps[id]? then
    unless cached.id == id && cached.proofFingerprint == proofFingerprint &&
        cached.propositionFingerprint == propositionFingerprint &&
        cached.kind == kind && cached.rule == rule && cached.proofPath == proofPath do
      throwError "partial proof checkpoint diverged at step {id}; remove the chapter .parts directory"
    builder.modify fun current =>
      let previous := current.seen[proof]?.getD #[]
      let seen := if remember then current.seen.insert proof (previous.push id) else current.seen
      { current with
        steps := current.steps.push cached
        seen
        replayedSteps := current.replayedSteps + 1 }
    updateBuilderProgress builder
    return id
  check proof
  let isTypeclass := (← isClass? proposition).isSome
  -- A proof-valued local `let` is administrative as a *proof term*, but its
  -- proposition is still mathematical content.  Presentation keeps the
  -- enormous value hidden and renders this certified type as the reusable
  -- context row.  Erasing the type here produced an empty semantic row and
  -- made fresh terminal exports differ from previews made from older traces.
  let administrative := kind == "kernel" || isTypeclass ||
    usedTheorem.any administrativeTheorem
  let (propositionLatex, semanticNodes, propositionLean) ←
    if administrative then
      pure ("", #[], "")
    else
      let (latex, nodes) ← renderSemanticExpr s!"proof-step-{id}" proposition
      pure (latex, nodes.filter fun node => !node.latexSpans.isEmpty,
        toString (← ppExpr proposition))
  let step : Step := {
    id
    scopeId
    parentScopeId
    depth
    kind
    rule
    premises
    propositionLatex
    propositionLean
    displayLatex := displayLatex.getD propositionLatex
    semanticNodes
    proofFingerprint
    propositionFingerprint
    proofPath
    theoremName := usedTheorem.map (·.toString)
    binderName := binderName.map (·.toString)
    opensScope
    closesScope
    usesLocalContext := proposition.hasFVar
    isTypeclass
  }
  builder.modify fun state =>
    let previous := state.seen[proof]?.getD #[]
    let seen := if remember then state.seen.insert proof (previous.push id) else state.seen
    { state with steps := state.steps.push step, seen }
  flushPartialChunk builder
  updateBuilderProgress builder
  return id

private def scopeContains (outer inner : String) : Bool :=
  outer == inner || inner.startsWith (outer ++ "/")

private def seenProof? (builder : IO.Ref Builder) (proof : Expr)
    (scopeId : String) : MetaM (Option Nat) := do
  let state ← builder.get
  let some candidates := state.seen[proof]? | return none
  for reverseIndex in [0 : candidates.size] do
    let id := candidates[candidates.size - reverseIndex - 1]!
    if let some step := state.steps[id]? then
      if scopeContains step.scopeId scopeId then return some id
  return none

private structure CountBuilder where
  count : Nat := 0
  seen : ExprMap (Array (Nat × String)) := {}

private def countSeen? (builder : IO.Ref CountBuilder) (proof : Expr)
    (scopeId : String) : MetaM (Option Nat) := do
  let state ← builder.get
  let some candidates := state.seen[proof]? | return none
  for candidate in candidates.reverse do
    if scopeContains candidate.2 scopeId then return some candidate.1
  return none

private def countAdd (builder : IO.Ref CountBuilder) (proof : Expr)
    (scopeId : String) : MetaM Nat := do
  let state ← builder.get
  let id := state.count
  let previous := state.seen[proof]?.getD #[]
  builder.set {
    count := id + 1
    seen := state.seen.insert proof (previous.push (id, scopeId))
  }
  return id

/-- Cheap deterministic preflight mirroring `visit`.  It performs no kernel
    checks, pretty-printing or LaTeX conversion, and therefore gives an exact
    progress denominator before expensive extraction starts. -/
private partial def countVisit
    (builder : IO.Ref CountBuilder)
    (proof : Expr)
    (scopeId : String := "root")
    (depth : Nat := 0)
    (path : String := "root") : MetaM (Option Nat) := do
  let proof := proof.cleanupAnnotations
  if let some id ← countSeen? builder proof scopeId then return some id
  unless ← isProof proof do return none
  match proof with
  | .lam binderName domain body binderInfo =>
    let childScope := scopeId ++ "/" ++ path.replace "." "-"
    let bodyId? ← withLocalDecl binderName binderInfo domain fun localExpr => do
      let _ ← countAdd builder localExpr childScope
      countVisit builder (body.instantiate1 localExpr) childScope (depth + 1)
        (path ++ ".body")
    let _ := bodyId?
    return some (← countAdd builder proof scopeId)
  | .app fn arg =>
    if theoremHead? proof |>.isSome then
      for candidate in proof.getAppArgs do
        let _ ← countVisit builder candidate scopeId depth path
      return some (← countAdd builder proof scopeId)
    else
      let _ ← countVisit builder fn scopeId depth (path ++ ".fn")
      let _ ← countVisit builder arg scopeId depth (path ++ ".arg")
      return some (← countAdd builder proof scopeId)
  | .letE binderName binderType value body _ =>
    let _ ← countVisit builder value scopeId depth (path ++ ".value")
    let childScope := scopeId ++ "/" ++ path.replace "." "-" ++ "-let"
    let _ ← withLetDecl binderName binderType value fun localExpr => do
      let _ ← countAdd builder localExpr childScope
      countVisit builder (body.instantiate1 localExpr) childScope (depth + 1)
        (path ++ ".body")
    return some (← countAdd builder proof scopeId)
  | _ =>
    return some (← countAdd builder proof scopeId)

private partial def visit
    (builder : IO.Ref Builder)
    (proof : Expr)
    (scopeId : String := "root")
    (parentScopeId : Option String := none)
    (depth : Nat := 0)
    (path : String := "root") : MetaM (Option Nat) := do
  let proof := proof.cleanupAnnotations
  if let some id ← seenProof? builder proof scopeId then
    return some id
  unless ← isProof proof do
    return none
  match proof with
  | .lam binderName domain body binderInfo =>
    let childScope := scopeId ++ "/" ++ path.replace "." "-"
    let (binderId, bodyId?) ← withLocalDecl binderName binderInfo domain fun localExpr => do
      let domainLatex ← LeanTeX.run_latexPP domain {}
      let localName := if binderName.isAnonymous then `x else binderName
      let binderKind := if ← isProp domain then "assumption" else "eigenvariable"
      let binderRule := if ← isProp domain then "assume" else "fix"
      let binderId ← addStep builder localExpr domain childScope (some scopeId) (depth + 1)
        binderKind binderRule (path ++ ".binder")
        (binderName := some localName)
        (displayLatex := some s!"{escapeLatexName localName} \\;:\\; {domainLatex}")
        (opensScope := some childScope)
      let bodyId? ← visit builder (body.instantiate1 localExpr) childScope (some scopeId)
        (depth + 1) (path ++ ".body")
      pure (binderId, bodyId?)
    let type ← inferType proof
    let mut premises := #[binderId]
    if let some bodyId := bodyId? then premises := premises.push bodyId
    let id ← addStep builder proof type scopeId parentScopeId depth "introduction"
      (← introductionRule domain) path premises
      (binderName := some binderName)
      (closesScope := some childScope)
    return some id
  | .app fn arg =>
    let type ← inferType proof
    if let some theoremName := theoremHead? proof then
      let mut premises := #[]
      for arg in proof.getAppArgs, index in [0 : proof.getAppArgs.size] do
        if let some premise ← visit builder arg scopeId parentScopeId depth
            (path ++ ".arg" ++ toString index) then
          premises := premises.push premise
      let id ← addStep builder proof type scopeId parentScopeId depth
        "theorem-application" "theorem-application" path premises
        (usedTheorem := some theoremName)
      return some id
    else
      let mut premises := #[]
      if let some fnId ← visit builder fn scopeId parentScopeId depth (path ++ ".fn") then
        premises := premises.push fnId
      if let some argId ← visit builder arg scopeId parentScopeId depth (path ++ ".arg") then
        premises := premises.push argId
      let id ← addStep builder proof type scopeId parentScopeId depth "elimination"
        (← applicationRule fn) path premises
      return some id
  | .letE binderName binderType value body _ =>
    let valueId? ← visit builder value scopeId parentScopeId depth (path ++ ".value")
    let childScope := scopeId ++ "/" ++ path.replace "." "-" ++ "-let"
    let (definitionId, bodyId?) ← withLetDecl binderName binderType value fun localExpr => do
      let valueLatex ← LeanTeX.run_latexPP value {}
      let localName := if binderName.isAnonymous then `v else binderName
      -- A proof-valued `let` is an implementation detail of the proof term,
      -- not a mathematical definition that belongs in the visible context.
      -- Keep it in the certified trace, but give it a distinct generic kind
      -- so presentation layers can hide the proof term without name-based
      -- special cases.
      let isProofDefinition ← isProp binderType
      let definitionKind := if isProofDefinition then "proof-definition" else "definition"
      let definitionRule := if isProofDefinition then "let-proof" else "let-definition"
      let definitionId ← addStep builder localExpr binderType childScope (some scopeId)
        (depth + 1) definitionKind definitionRule (path ++ ".definition")
        (binderName := some localName)
        (displayLatex := some s!"{escapeLatexName localName} \\;:=\\; {valueLatex}")
        (opensScope := some childScope)
      let instantiated := body.instantiate1 localExpr
      let bodyId? ← visit builder instantiated childScope (some scopeId) (depth + 1)
        (path ++ ".body")
      pure (definitionId, bodyId?)
    let type ← inferType proof
    let mut premises := #[]
    if let some valueId := valueId? then premises := premises.push valueId
    premises := premises.push definitionId
    if let some bodyId := bodyId? then premises := premises.push bodyId
    let id ← addStep builder proof type scopeId parentScopeId depth "definitional"
      "let-reduction" path premises (closesScope := some childScope)
    return some id
  | .fvar _ =>
    let type ← inferType proof
    return some (← addStep builder proof type scopeId parentScopeId depth
      "reference" "assumption" path)
  | .const name _ =>
    let type ← inferType proof
    return some (← addStep builder proof type scopeId parentScopeId depth
      "theorem" "theorem" path (usedTheorem := some name))
  | _ =>
    let type ← inferType proof
    return some (← addStep builder proof type scopeId parentScopeId depth
      "kernel" "kernel-construction" path)

private def validate (steps : Array Step) (finalStepId : Nat)
    (finalTypeMatches noSorry : Bool) : Validation := Id.run do
  let mut errors := #[]
  let mut dependencyOrderValid := true
  for step in steps do
    for premise in step.premises do
      if premise >= step.id then
        dependencyOrderValid := false
        errors := errors.push s!"step {step.id} depends on non-earlier step {premise}"
  if finalStepId >= steps.size then
    errors := errors.push s!"final step {finalStepId} is outside the trace"
  if !finalTypeMatches then
    errors := errors.push "final proof type is not definitionally equal to the theorem type"
  if !noSorry then
    errors := errors.push "the theorem depends on Lean.ofReduceBool or sorryAx"
  return {
    valid := dependencyOrderValid && finalStepId < steps.size && finalTypeMatches && noSorry
    dependencyOrderValid
    finalTypeMatches
    noSorry
    errors
  }

/-- Extract one declaration without expanding source-local theorem constants. -/
private def extractSingle
    (theoremName : Name)
    (chapterIndex chapterCount completedWeight totalWeight : Nat := 0)
    (checkpointDirectory : Option System.FilePath := none) : MetaM Trace := do
  let declaration ← getConstInfo theoremName
  let proof := declaration.value!
  let proofFingerprint := fingerprint proof
  check proof
  let inferred ← inferType proof
  let finalTypeMatches ← isDefEq inferred declaration.type
  let axioms ← Lean.collectAxioms theoremName
  let noSorry := !axioms.contains ``sorryAx && !axioms.contains ``Lean.ofReduceBool
  let proofObjects ← proof.numObjs
  writeProgress (progressPath? checkpointDirectory) {
    phase := "counting-proof-nodes"
    chapterIndex
    chapterCount
    theoremName := theoremName.toString
    completedChapters := chapterIndex
    proofObjects
    completedWeight
    totalWeight
  }
  let counter ← IO.mkRef ({} : CountBuilder)
  let _ ← countVisit counter proof
  let totalSteps := (← counter.get).count
  let partialDirectory := checkpointDirectory.map fun directory =>
    partialChapterDirectory directory chapterIndex
  let cachedSteps ← match partialDirectory with
    | some directory => loadPartialSteps directory theoremName proofFingerprint
    | none => pure #[]
  let progress : ExtractionProgress := {
    phase := "extracting"
    chapterIndex
    chapterCount
    theoremName := theoremName.toString
    completedChapters := chapterIndex
    totalSteps
    detailMode := "detailed"
    proofObjects
    completedWeight
    totalWeight
  }
  let builder ← IO.mkRef ({
    cachedSteps
    partialDirectory
    chapterName := theoremName.toString
    chapterProofFingerprint := proofFingerprint
    progressPath := progressPath? checkpointDirectory
    progress := some progress
  } : Builder)
  writeProgress (progressPath? checkpointDirectory) progress
  let some finalStepId ← visit builder proof |
    throwError "the declaration value is not a proof"
  updateBuilderProgress builder true
  let steps := (← builder.get).steps
  let (theoremLatex, _) ← renderSemanticExpr "theorem" declaration.type
  let theoremLean := toString (← ppExpr declaration.type)
  let axiomNames := axioms.map (·.toString)
  let validation := validate steps finalStepId finalTypeMatches noSorry
  unless validation.valid do
    throwError "invalid ProofTrace: {String.intercalate "; " validation.errors.toList}"
  return {
    theoremName := theoremName.toString
    theoremLatex
    theoremLean
    steps
    finalStepId
    axioms := axiomNames
    validation
  }


private def namespaceSemanticNode (namespacePrefix : String)
    (node : SemanticNode) : SemanticNode :=
  { node with
    id := namespacePrefix ++ node.id
    identity := if node.identity.isEmpty then "" else namespacePrefix ++ node.identity
    parentId := node.parentId.map (namespacePrefix ++ ·) }

private def remapStep
    (chapterPrefix : String)
    (offset : Nat)
    (dependencyFinals : Std.HashMap String Nat)
    (step : Step) : Step := Id.run do
  let mut premises := step.premises.map (· + offset)
  if let some theoremName := step.theoremName then
    if let some dependencyFinal := dependencyFinals[theoremName]? then
      unless premises.contains dependencyFinal do
        premises := premises.push dependencyFinal
  return {
    step with
    id := step.id + offset
    scopeId := chapterPrefix ++ step.scopeId
    parentScopeId := step.parentScopeId.map (chapterPrefix ++ ·)
    premises
    semanticNodes := step.semanticNodes.map (namespaceSemanticNode chapterPrefix)
    proofPath := chapterPrefix ++ step.proofPath
    opensScope := step.opensScope.map (chapterPrefix ++ ·)
    closesScope := step.closesScope.map (chapterPrefix ++ ·)
  }

private def checkpointPath (directory : System.FilePath) (chapterIndex : Nat) :
    System.FilePath :=
  directory / s!"chapter-{chapterIndex}.json"

private def readCheckpoint? (path : System.FilePath) (theoremName : Name) :
    MetaM (Option Trace) := do
  unless ← path.pathExists do return none
  try
    let payload ← IO.FS.readFile path
    let json ← match Json.parse payload with
      | .ok value => pure value
      | .error message => throwError message
    let trace ← match fromJson? json with
      | .ok value => pure value
      | .error message => throwError message
    if trace.theoremName == theoremName.toString && trace.validation.valid then
      return some trace
    return none
  catch _ =>
    return none

private def writeCheckpoint (path : System.FilePath) (trace : Trace) : MetaM Unit := do
  atomicWriteJson path trace

private def extractSingleCached
    (theoremName : Name)
    (chapterIndex : Nat)
    (chapterCount completedWeight currentWeight totalWeight : Nat)
    (checkpointDirectory : Option System.FilePath) : MetaM Trace := do
  if let some directory := checkpointDirectory then
    let path := checkpointPath directory chapterIndex
    if let some trace ← readCheckpoint? path theoremName then
      writeProgress (progressPath? checkpointDirectory) {
        phase := "cached"
        chapterIndex
        chapterCount
        theoremName := theoremName.toString
        completedChapters := chapterIndex + 1
        processedSteps := trace.steps.size
        totalSteps := trace.steps.size
        replayedSteps := trace.steps.size
        detailMode := "cached"
        completedWeight := completedWeight + currentWeight
        totalWeight
      }
      return trace
    let trace ← extractSingle theoremName chapterIndex chapterCount completedWeight
      totalWeight checkpointDirectory
    writeProgress (progressPath? checkpointDirectory) {
      phase := "serializing"
      chapterIndex
      chapterCount
      theoremName := theoremName.toString
      completedChapters := chapterIndex
      processedSteps := trace.steps.size
      totalSteps := trace.steps.size
      emittedSteps := trace.steps.size
      proofObjects := currentWeight
      completedWeight
      totalWeight
    }
    writeCheckpoint path trace
    return trace
  extractSingle theoremName chapterIndex chapterCount completedWeight totalWeight none

/-- Extract a validated hierarchical ProofTrace. Theorems declared in the input
    file are traced once each in topological order; imported library theorems
    remain cited atomic steps. With no import boundary this retains the legacy
    single-declaration behavior for programmatic callers. -/
def extract
    (theoremName : Name)
    (importedEnv : Option Environment := none)
    (checkpointDirectory : Option System.FilePath := none) : MetaM Trace := do
  let some importedEnv := importedEnv | return ← extractSingle theoremName
  writeProgress (progressPath? checkpointDirectory) {
    phase := "discovering-dependencies"
    theoremName := theoremName.toString
  }
  let order ← sourceLocalProofOrder importedEnv theoremName

  let mut weights := #[]
  let mut totalWeight := 0
  for chapterName in order do
    let declaration ← getConstInfo chapterName
    let weight ← match declaration.value? true with
      | some proof => proof.numObjs
      | none => pure 1
    let weight := max 1 weight
    weights := weights.push weight
    totalWeight := totalWeight + weight
  writeProgress (progressPath? checkpointDirectory) {
    phase := "discovered"
    chapterCount := order.size
    theoremName := theoremName.toString
    totalWeight
  }

  let mut combinedSteps := #[]
  let mut chapters := #[]
  let mut dependencyFinals : Std.HashMap String Nat := {}
  let mut combinedAxioms := #[]
  let mut mainTrace? : Option Trace := none
  let mut completedWeight := 0

  for chapterName in order, chapterIndex in [0 : order.size] do
    let trace ← extractSingleCached chapterName chapterIndex order.size completedWeight
      weights[chapterIndex]! totalWeight checkpointDirectory
    if chapterName == theoremName then
      mainTrace? := some trace
    let dependencies ← sourceLocalProofDependencies importedEnv chapterName
    let offset := combinedSteps.size
    let chapterPrefix := s!"chapter-{chapterIndex}/"
    for step in trace.steps do
      let mut remapped := remapStep chapterPrefix offset dependencyFinals step
      if step.id == trace.finalStepId then
        let mut premises := remapped.premises
        for dependency in dependencies do
          if let some dependencyFinal := dependencyFinals[dependency.toString]? then
            unless premises.contains dependencyFinal do
              premises := premises.push dependencyFinal
        remapped := { remapped with premises }
      combinedSteps := combinedSteps.push remapped
    let finalStepId := offset + trace.finalStepId
    chapters := chapters.push {
      id := chapterIndex
      theoremName := trace.theoremName
      theoremLatex := trace.theoremLatex
      theoremLean := trace.theoremLean
      startStepId := offset
      finalStepId
      dependencies := dependencies.map (·.toString)
      isMain := chapterName == theoremName
    }
    dependencyFinals := dependencyFinals.insert trace.theoremName finalStepId
    for axiomName in trace.axioms do
      unless combinedAxioms.contains axiomName do
        combinedAxioms := combinedAxioms.push axiomName
    completedWeight := completedWeight + weights[chapterIndex]!
    writeProgress (progressPath? checkpointDirectory) {
      phase := "chapter-complete"
      chapterIndex
      chapterCount := order.size
      theoremName := chapterName.toString
      completedChapters := chapterIndex + 1
      processedSteps := trace.steps.size
      totalSteps := trace.steps.size
      emittedSteps := trace.steps.size
      completedWeight
      totalWeight
    }

  let some mainTrace := mainTrace? |
    throwError "the main theorem chapter was not extracted"
  let finalStepId := dependencyFinals[mainTrace.theoremName]!
  let noSorry := !combinedAxioms.contains "sorryAx" &&
    !combinedAxioms.contains "Lean.ofReduceBool"
  let validation := validate combinedSteps finalStepId
    mainTrace.validation.finalTypeMatches
    noSorry
  unless validation.valid do
    throwError "invalid hierarchical ProofTrace: {String.intercalate "; " validation.errors.toList}"
  let result : Trace := {
    schemaVersion := "2.1"
    theoremName := mainTrace.theoremName
    source := "Mathlib.Tactic.Explode/hierarchical-local-dependency-adapter"
    granularity := "natural-deduction/local-theorem-chapters"
    theoremLatex := mainTrace.theoremLatex
    theoremLean := mainTrace.theoremLean
    steps := combinedSteps
    finalStepId
    chapters
    axioms := combinedAxioms
    validation
  }
  writeProgress (progressPath? checkpointDirectory) {
    phase := "complete"
    chapterIndex := order.size - 1
    chapterCount := order.size
    theoremName := theoremName.toString
    completedChapters := order.size
    processedSteps := result.steps.size
    totalSteps := result.steps.size
    emittedSteps := result.steps.size
    completedWeight := totalWeight
    totalWeight
  }
  return result


end ProofTrace
