import Lean
import Lean.Data.Json.FromToJson
import Annotations
import ProofTrace
import Animate.TacticTrace

namespace Animate

section infotrees

open Lean Elab

private def hybridCheckpointPath (directory : System.FilePath) (chapterIndex : Nat) :
    System.FilePath :=
  directory / s!"source-chapter-{chapterIndex}.json"

private def hybridActionDirectory (directory : System.FilePath) (chapterIndex : Nat) :
    System.FilePath :=
  directory / s!"source-chapter-{chapterIndex}.parts"

private def hybridActionChunkPath (directory : System.FilePath) (chunkIndex : Nat) :
    System.FilePath :=
  directory / s!"actions-{chunkIndex}.json"

private def hybridActionChunkSize : Nat := 128

private def proofIndexPath (directory : System.FilePath) : System.FilePath :=
  directory / "proof-index.json"

private def shouldRebuildChapter (config : Config) (theoremName : Name) : Bool :=
  config.rebuild_chapter == some theoremName

private def atomicWriteJson {α : Type} [ToJson α]
    (path : System.FilePath) (value : α) : IO Unit := do
  ProofTrace.atomicWriteJson path value

private def readHybridCheckpoint? (path : System.FilePath) (theoremName : Name)
    (proofFingerprint : String) :
    IO (Option HybridChapter) := do
  unless ← path.pathExists do return none
  try
    let payload ← IO.FS.readFile path
    let json ← match Json.parse payload with
      | .ok value => pure value
      | .error message => throw <| IO.userError message
    let chapter ← match fromJson? json with
      | .ok value => pure value
      | .error message => throw <| IO.userError message
    if chapter.theoremName == theoremName.toString &&
        chapter.proofFingerprint == proofFingerprint && chapter.validation.valid then
      return some chapter
    return none
  catch _ => return none

private def readHybridActionChunk? (path : System.FilePath) :
    IO (Option HybridActionChunk) := do
  unless ← path.pathExists do return none
  try
    let payload ← IO.FS.readFile path
    let json ← match Json.parse payload with
      | .ok value => pure value
      | .error message => throw <| IO.userError message
    match fromJson? json with
    | .ok value => return some value
    | .error message => throw <| IO.userError message
  catch _ => return none

private def hybridCommandCapturePath (directory : System.FilePath)
    (theoremName : Name) : System.FilePath :=
  directory / "command-captures" / s!"{theoremName.hash}.json"

private def theoremProofFingerprint? (env : Environment)
    (theoremName : Name) : Option String := do
  let declaration ← env.find? theoremName
  let proof ← declaration.value? true
  return toString proof.cleanupAnnotations.hash

private def readHybridCommandCapture? (path : System.FilePath)
    (theoremName : Name) (proofFingerprint : String) : IO (Option Movie) := do
  unless ← path.pathExists do return none
  try
    let payload ← IO.FS.readFile path
    let json ← match Json.parse payload with
      | .ok value => pure value
      | .error message => throw <| IO.userError message
    let capture ← match (fromJson? json : Except String HybridCommandCapture) with
      | .ok value => pure value
      | .error message => throw <| IO.userError message
    if capture.theoremName == theoremName.toString &&
        capture.proofFingerprint == proofFingerprint then
      return some capture.movie
    return none
  catch _ => return none

private def loadHybridActionChunks (directory : System.FilePath)
    (theoremName : Name) (proofFingerprint : String) : IO (Array Action) := do
  let mut result := #[]
  let mut chunkIndex := 0
  while true do
    let some chunk ← readHybridActionChunk? (hybridActionChunkPath directory chunkIndex) |
      break
    if chunk.theoremName != theoremName.toString ||
        chunk.proofFingerprint != proofFingerprint ||
        chunk.startIndex != result.size || chunk.actions.isEmpty then
      break
    result := result ++ chunk.actions
    chunkIndex := chunkIndex + 1
  return result

private def declarationMovie (theoremName : Name) : MetaM Movie := do
  let declaration ← getConstInfo theoremName
  let (latex, semanticNodes) ← renderSemanticExpr "target" declaration.type
  let state := (← Meta.ppExpr declaration.type).pretty (width := GOAL_PP_WIDTH)
  return {
    theoremName := theoremName.toString
    startGoal := {
      goalId := s!"declaration/{theoremName}"
      state
      latexTarget := some latex
      semanticNodes
    }
    actions := []
    highlighting := #[]
  }

def certifyHybridChapter (theoremName : Name) : MetaM
    (String × Array String × HybridChapterValidation) := do
  let declaration ← getConstInfo theoremName
  let some proof := declaration.value? true | do
    let validation : HybridChapterValidation := {
      valid := false
      kernelChecked := false
      noSorry := false
      errors := #["declaration has no proof value"]
    }
    return ("", #[], validation)
  let mut errors := #[]
  let kernelChecked ← try
    Meta.check proof
    pure true
  catch _ =>
    errors := errors.push "kernel check failed"
    pure false
  let axioms ← Lean.collectAxioms theoremName
  let noSorry := !axioms.contains ``sorryAx && !axioms.contains ``Lean.ofReduceBool
  unless noSorry do errors := errors.push "proof contains sorryAx or Lean.ofReduceBool"
  let validation : HybridChapterValidation := {
    valid := kernelChecked && noSorry
    kernelChecked
    noSorry
    errors
  }
  return (toString proof.cleanupAnnotations.hash, axioms.map (·.toString), validation)

unsafe def tacticMovieFromInfoState? (config : Config) (theoremName : Name)
    (infoState : InfoState)
    (cachedActions : Array Action := #[])
    (onActionTotal : Option (Nat → IO Unit) := none)
    (onNewAction : Option (Nat → Action → IO Unit) := none) : IO (Option Movie) := do
  let theoremConfig := { config with const_name := theoremName }
  for tree in infoState.trees do
    try
      let step ← extractToplevelStep tree
      let stage2state ← stage2 step
      if let some callback := onActionTotal then callback stage2state.steps.size
      let movie ← stage3 theoremConfig stage2state cachedActions onNewAction
      return some movie
    catch _ => pure ()
  return none

private def firstTheoremInfo? (steps : List (Environment × InfoState))
    (theoremName : Name) : Option (Environment × InfoState) :=
  steps.find? fun entry => entry.1.contains theoremName

structure CapturedTheorem where
  theoremName : Name
  movie? : Option Movie

private def parentDeclarations (infoState : InfoState) : IO (Array Name) := do
  let mut names := #[]
  for tree in infoState.trees do
    let found ← collectNodesBottomUpM (m := IO)
      (fun ci _ _ children => pure (ci.parentDecl?.toList ++ children)) tree
    names := names ++ found.toArray
  return names.toList.eraseDups.toArray

def newlyAddedTheorems (oldEnv newEnv : Environment)
    (infoState : InfoState) : IO (Array Name) := do
  return (← parentDeclarations infoState).filter fun name =>
    !oldEnv.contains name && match newEnv.find? name with
      | some (.thmInfo _) => true
      | _ => false

/-- Convert one command's theorem InfoTree into compact, fingerprint-keyed
    captures.  Both the legacy frontend and the 4.32 snapshot reader use this
    exact function, so changing transport does not change visible proof moves. -/
unsafe def captureTheoremsForCommand (config : Config)
    (before after : Environment) (infoState : InfoState) :
    IO (Array CapturedTheorem) := do
  let theoremNames ← newlyAddedTheorems before after infoState
  let mut captures := #[]
  for theoremName in theoremNames do
    let proofFingerprint? := theoremProofFingerprint? after theoremName
    let cachedMovie? ← match config.trace_checkpoint_dir, proofFingerprint? with
      | some directory, some fingerprint =>
        if shouldRebuildChapter config theoremName then pure none else
          readHybridCommandCapture?
            (hybridCommandCapturePath directory theoremName) theoremName fingerprint
      | _, _ => pure none
    let movie? ← match cachedMovie? with
      | some movie => pure (some movie)
      | none => tacticMovieFromInfoState? config theoremName infoState
    if cachedMovie?.isNone then if let some movie := movie? then
      if let some directory := config.trace_checkpoint_dir then
        if let some proofFingerprint := proofFingerprint? then
          atomicWriteJson (hybridCommandCapturePath directory theoremName) ({
            theoremName := theoremName.toString
            proofFingerprint
            movie
          } : HybridCommandCapture)
    captures := captures.push { theoremName, movie? }
  return captures

unsafe def processHybridCommands
    (config : Config)
    (captures : Array CapturedTheorem := #[])
    (processedCommands : Nat := 0)
    (profiles : Array ProofTrace.CommandProfileEntry := #[])
    (slowestStartByte : Nat := 0)
    (slowestElapsedMs : Nat := 0) :
    Frontend.FrontendM (Array CapturedTheorem × Environment) := do
  let preState := ← get
  let frontendContext ← read
  let commandStartByte := preState.parserState.pos.byteIdx
  ProofTrace.reportProgress config.trace_checkpoint_dir {
    phase := "elaborating-command"
    theoremName := config.const_name.toString
    processedSteps := processedCommands
    emittedSteps := captures.size
    proofObjects := captures.size
    detailMode := "streaming-commands"
    completedWeight := commandStartByte
    totalWeight := frontendContext.inputCtx.endPos.byteIdx
    commandIndex := processedCommands
    commandStartByte
    slowestCommandStartByte := slowestStartByte
    slowestCommandElapsedMs := slowestElapsedMs
  }
  let before := (← get).commandState.env
  let commandStarted ← IO.monoMsNow
  let done ← Lean.Elab.Frontend.processCommand
  let commandElapsedMs := (← IO.monoMsNow) - commandStarted
  let st := ← get
  let infoState := st.commandState.infoState
  let after := st.commandState.env
  let commandCaptures ← captureTheoremsForCommand config before after infoState
  let theoremNames := commandCaptures.map (·.theoremName)
  let profile : ProofTrace.CommandProfileEntry := {
    index := processedCommands
    startByte := commandStartByte
    endByte := st.parserState.pos.byteIdx
    elapsedMs := commandElapsedMs
    declarations := theoremNames.map (·.toString)
  }
  let profiles := profiles.push profile
  let (slowestStartByte, slowestElapsedMs) :=
    if commandElapsedMs > slowestElapsedMs then
      (commandStartByte, commandElapsedMs)
    else
      (slowestStartByte, slowestElapsedMs)
  let captures := captures ++ commandCaptures
  -- An InfoTree can dwarf the rendered chapter. Convert it while the command is
  -- still current, then drop it before elaborating the next command.
  set {st with commandState := {st.commandState with infoState := {}}}
  let processedCommands := processedCommands + 1
  if theoremNames.size > 0 || processedCommands % 16 == 0 || commandElapsedMs ≥ 5000 then
    ProofTrace.reportCommandProfiles config.trace_checkpoint_dir
      config.file_path.toString profiles
    ProofTrace.reportProgress config.trace_checkpoint_dir {
      phase := "elaborating-source"
      theoremName := config.const_name.toString
      processedSteps := processedCommands
      emittedSteps := captures.size
      proofObjects := captures.size
      detailMode := "streaming-commands"
      completedWeight := st.cmdPos.byteIdx
      totalWeight := frontendContext.inputCtx.endPos.byteIdx
      commandIndex := processedCommands - 1
      commandStartByte
      commandEndByte := st.parserState.pos.byteIdx
      commandElapsedMs
      slowestCommandStartByte := slowestStartByte
      slowestCommandElapsedMs := slowestElapsedMs
    }
  -- Declarations cannot depend on commands that appear later in the file. Once
  -- the requested theorem exists, stopping here is both sound and substantially
  -- cheaper for files that contain later material.
  if done || after.contains config.const_name then
    ProofTrace.reportCommandProfiles config.trace_checkpoint_dir
      config.file_path.toString profiles true
    return (captures, after)
  processHybridCommands config captures processedCommands profiles
    slowestStartByte slowestElapsedMs

private def capturedMovie? (captures : Array CapturedTheorem)
    (theoremName : Name) : Option Movie :=
  (captures.find? (·.theoremName == theoremName)).bind (·.movie?)

unsafe def buildHybridTrace
    (config : Config)
    (importedEnv finalEnv : Environment)
    (captures : Array CapturedTheorem)
    (snapshotCertificates? : Option SnapshotCertificateBundle := none) : IO Json := do
  let coreContext : Core.Context := {
    fileName := config.file_path.toString
    fileMap := default
    maxHeartbeats := 0
    maxRecDepth := 100000
  }
  let coreState : Core.State := { env := finalEnv }
  let runMeta {α : Type} (action : MetaM α) : IO α := do
    let (result, _, _) ← _root_.Lean.Meta.MetaM.toIO action coreContext coreState
    return result
  let order ← match snapshotCertificates? with
    | some bundle => pure <| bundle.rows.map (·.theoremName.toName)
    | none => runMeta (ProofTrace.sourceLocalProofOrder importedEnv config.const_name)
  ProofTrace.reportProgress config.trace_checkpoint_dir {
    phase := "source-tactic-chapters"
    chapterCount := order.size
    theoremName := config.const_name.toString
    totalWeight := order.size
  }
  let mut chapters : Array HybridChapter := #[]
  let mut chapterRefs : Array HybridChapterRef := #[]
  let mut finalIds : Std.HashMap String Nat := {}
  let mut dependencyOrderValid := true
  let mut allKernelChecked := true
  let mut noSorry := true
  let mut errors := #[]
  let mut lastIsMain := false
  let mut proofIndexRows : Array ProofIndexRow := #[]
  -- Source commands remain sequential because each declaration may extend the
  -- next command's environment.  Once that immutable environment is complete,
  -- chapter kernel checks are independent and can safely use bounded tasks.
  let mut certificates : Array
      (String × Array String × HybridChapterValidation) := #[]
  match snapshotCertificates? with
  | some bundle =>
    for row in bundle.rows do
      certificates := certificates.push
        (row.proofFingerprint, row.axioms, row.validation)
  | none =>
    let mut batchStart := 0
    while batchStart < order.size do
      let batchEnd := min order.size (batchStart + config.postprocess_workers)
      let mut tasks : Array (Task (Except IO.Error
          (String × Array String × HybridChapterValidation))) := #[]
      for certificateIndex in [batchStart : batchEnd] do
        tasks := tasks.push (← IO.asTask <|
          runMeta (certifyHybridChapter order[certificateIndex]!))
      for task in tasks do
        certificates := certificates.push (← IO.ofExcept task.get)
      batchStart := batchEnd
  for theoremName in order, chapterIndex in [0 : order.size] do
    -- Always re-check the current declaration. Cached presentation data is
    -- reusable only when it belongs to this exact elaborated proof value.
    let some (proofFingerprint, axioms, chapterValidation) :=
        certificates[chapterIndex]?
      | throw <| IO.userError s!"missing certificate for chapter {chapterIndex}"
    let checkpointPath := config.trace_checkpoint_dir.map fun directory =>
      hybridCheckpointPath directory chapterIndex
    let cached ← match checkpointPath with
      | some path =>
        if shouldRebuildChapter config theoremName then pure none else
          readHybridCheckpoint? path theoremName proofFingerprint
      | none => pure none
    let chapter ← match cached with
      | some chapter => pure chapter
      | none =>
        let dependencies ← if let some bundle := snapshotCertificates? then
          match bundle.rows.find?
              (fun row => row.theoremName == theoremName.toString) with
          | some row => pure <| row.dependencies.map (·.toName)
          | none => throw <| IO.userError s!"missing dependency certificate for {theoremName}"
        else
          runMeta (ProofTrace.sourceLocalProofDependencies importedEnv theoremName)
        let actionDirectory? := config.trace_checkpoint_dir.map fun directory =>
          hybridActionDirectory directory chapterIndex
        let cachedActions ← match actionDirectory? with
          | some directory =>
            if shouldRebuildChapter config theoremName then pure #[] else
              loadHybridActionChunks directory theoremName proofFingerprint
          | none => pure #[]
        let actionTotal ← IO.mkRef 0
        let onActionTotal : Nat → IO Unit := fun total => do
          actionTotal.set total
          ProofTrace.reportProgress config.trace_checkpoint_dir {
            phase := "source-tactic-actions"
            chapterIndex
            chapterCount := order.size
            theoremName := theoremName.toString
            completedChapters := chapterIndex
            processedSteps := min cachedActions.size total
            totalSteps := total
            replayedSteps := min cachedActions.size total
            detailMode := "source-tactics"
            proofObjects := 1
            completedWeight := chapterIndex
            totalWeight := order.size
          }
        let pendingActions ← IO.mkRef (#[] : Array Action)
        let onNewAction : Nat → Action → IO Unit := fun actionIndex action => do
          let pending := (← pendingActions.get).push action
          pendingActions.set pending
          if pending.size == hybridActionChunkSize then
            if let some directory := actionDirectory? then
              let startIndex := actionIndex + 1 - pending.size
              let chunk : HybridActionChunk := {
                theoremName := theoremName.toString
                proofFingerprint
                startIndex
                actions := pending
              }
              atomicWriteJson
                (hybridActionChunkPath directory
                  (startIndex / hybridActionChunkSize)) chunk
            pendingActions.set #[]
          if (actionIndex + 1) % 16 == 0 then
            let total ← actionTotal.get
            ProofTrace.reportProgress config.trace_checkpoint_dir {
              phase := "source-tactic-actions"
              chapterIndex
              chapterCount := order.size
              theoremName := theoremName.toString
              currentTactic := action.tacticText
              completedChapters := chapterIndex
              processedSteps := actionIndex + 1
              totalSteps := total
              replayedSteps := cachedActions.size
              detailMode := "source-tactics"
              proofObjects := 1
              completedWeight := chapterIndex
              totalWeight := order.size
            }
        let movie ← match capturedMovie? captures theoremName with
          | some movie =>
            onActionTotal movie.actions.length
            let reused := min cachedActions.size movie.actions.length
            for action in movie.actions.drop reused, actionIndex in [reused:movie.actions.length] do
              onNewAction actionIndex action
            pure { movie with
              actions := cachedActions.toList.take reused ++ movie.actions.drop reused }
          | none => runMeta (declarationMovie theoremName)
        let chapter : HybridChapter := {
          id := chapterIndex
          theoremName := theoremName.toString
          dependencies := dependencies.map (·.toString)
          movie
          proofFingerprint
          axioms
          validation := chapterValidation
          isMain := theoremName == config.const_name
        }
        if let some path := checkpointPath then atomicWriteJson path chapter
        pure chapter
    let location? ← runMeta do
      let ranges? ← Lean.findDeclarationRanges? theoremName
      return ranges?.map fun ranges => {
        startLine := ranges.range.pos.line
        startColumn := ranges.range.pos.column
        endLine := ranges.range.endPos.line
        endColumn := ranges.range.endPos.column
      }
    proofIndexRows := proofIndexRows.push {
      theoremName := chapter.theoremName
      proofFingerprint := chapter.proofFingerprint
      dependencies := chapter.dependencies
      axioms := chapter.axioms
      location := location?
    }
    if let some directory := config.trace_checkpoint_dir then
      atomicWriteJson (proofIndexPath directory) ({
        selectedTheorem := config.const_name.toString
        declarations := proofIndexRows
      } : ProofIndex)
    for dependency in chapter.dependencies do
      unless finalIds.contains dependency do
        dependencyOrderValid := false
        errors := errors.push s!"chapter {chapter.theoremName} precedes dependency {dependency}"
    finalIds := finalIds.insert chapter.theoremName chapter.id
    allKernelChecked := allKernelChecked && chapter.validation.kernelChecked
    noSorry := noSorry && chapter.validation.noSorry
    unless chapter.validation.valid do
      errors := errors ++ chapter.validation.errors
    lastIsMain := chapter.isMain
    match config.trace_output_dir with
    | some directory =>
      let objectPath := directory / s!"raw-chapter-{chapterIndex}.json"
      atomicWriteJson objectPath chapter
      chapterRefs := chapterRefs.push {
        id := chapter.id
        theoremName := chapter.theoremName
        dependencies := chapter.dependencies
        proofFingerprint := chapter.proofFingerprint
        axioms := chapter.axioms
        validation := chapter.validation
        isMain := chapter.isMain
        objectPath := objectPath.toString
      }
    | none =>
      chapters := chapters.push chapter
    ProofTrace.reportProgress config.trace_checkpoint_dir {
      phase := if cached.isSome then "cached-source-chapter" else "source-tactic-chapter-complete"
      chapterIndex
      chapterCount := order.size
      theoremName := theoremName.toString
      completedChapters := chapterIndex + 1
      processedSteps := chapter.movie.actions.length
      totalSteps := chapter.movie.actions.length
      emittedSteps := if cached.isSome then 0 else chapter.movie.actions.length
      replayedSteps := if cached.isSome then chapter.movie.actions.length else 0
      detailMode := "source-tactics"
      completedWeight := chapterIndex + 1
      totalWeight := order.size
    }
  let mainLast := lastIsMain
  unless mainLast do errors := errors.push "main theorem chapter is not last"
  let validation : HybridTraceValidation := {
    valid := dependencyOrderValid && allKernelChecked && noSorry && mainLast
    dependencyOrderValid
    allChaptersKernelChecked := allKernelChecked
    noSorry
    errors
  }
  ProofTrace.reportProgress config.trace_checkpoint_dir {
    phase := "complete"
    chapterIndex := order.size - 1
    chapterCount := order.size
    theoremName := config.const_name.toString
    completedChapters := order.size
    completedWeight := order.size
    totalWeight := order.size
    detailMode := "source-tactics"
  }
  match config.trace_output_dir with
  | some _ =>
    return toJson ({
      theoremName := config.const_name.toString
      chapterRefs
      validation
    } : HybridTraceManifest)
  | none =>
    return toJson ({
      theoremName := config.const_name.toString
      chapters
      validation
    } : HybridTrace)

end infotrees

end Animate
