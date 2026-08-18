/-
Lean 4.32-only extractor frontend.

The official command-line frontend serializes a full `InitialSnapshot`; this
reader restores that exact current snapshot and converts its command InfoTrees
with the same chapter builder used by `Animate`.  It is deliberately not part
of the Lean 4.28 targets.
-/

import Animate
import SnapshotCertificate432
import Lean.Compiler.InitAttr
import Lean.Elab.Frontend

open Lean

namespace SnapshotAnimate432

/-- Layout-compatible with Lean.Elab.Frontend's private on-disk wrapper. -/
private structure IncrSnapshot where
  snap : Language.Lean.InitialSnapshot
  initModIdxs : Array Nat

private unsafe def readModuleArtifactRegions (arts : ModuleArtifacts) :
    IO (Array CompactedRegion) := do
  let mut chainDeps : Array CompactedRegion := #[]
  for partPath in arts.oleanParts do
    let (_, region) ← CompactedRegion.read (α := ModuleData) partPath chainDeps
    chainDeps := chainDeps.push region
  if let some irPath := arts.ir? then
    let (_, region) ← CompactedRegion.read (α := ModuleData) irPath #[]
    chainDeps := chainDeps.push region
  return chainDeps

private unsafe def loadSnapshot (fname : System.FilePath) : IO IncrSnapshot := do
  let depsFile := fname.addExtension "deps"
  let moduleArts : Array ModuleArtifacts ←
    match Json.parse (← IO.FS.readFile depsFile) >>= fromJson? with
    | .ok arts => pure arts
    | .error e =>
      throw <| IO.userError s!"failed to parse snapshot deps file {depsFile}: {e}"
  let defaultWorkers := min (System.Platform.Internal.getHardwareConcurrency ()).toNat 4
  let workers := max 1 <|
    ((← IO.getEnv "LEAN_IMPORT_WORKERS").bind (·.toNat?)).getD defaultWorkers
  let mut tasks := Array.emptyWithCapacity workers
  for worker in 0...workers do
    tasks := tasks.push (← IO.asTask (do
      let mut regions : Array CompactedRegion := #[]
      let mut index := worker
      while index < moduleArts.size do
        regions := regions ++ (← readModuleArtifactRegions moduleArts[index]!)
        index := index + workers
      return regions))
  let mut dependencyRegions : Array CompactedRegion := #[]
  for task in tasks do
    dependencyRegions := dependencyRegions ++ (← IO.ofExcept task.get)
  let (data, _region) ← CompactedRegion.read (α := IncrSnapshot) fname dependencyRegions
  return data

private unsafe def collectCommandCaptures
    (config : Animate.Config)
    (before : Environment)
    (task : Language.SnapshotTask Language.Lean.CommandParsedSnapshot)
    (captures : Array Animate.CapturedTheorem := #[]) :
    IO (Array Animate.CapturedTheorem × Environment) := do
  let snapshot := task.get
  let commandState := snapshot.elabSnap.resultSnap.get.cmdState
  let after := commandState.env
  let infoTree? := snapshot.elabSnap.infoTreeSnap.get.infoTree?
  let infoState : Lean.Elab.InfoState := {
    enabled := true
    trees := match infoTree? with
      | some tree => #[tree].toPArray'
      | none => {}
  }
  let current ← Animate.captureTheoremsForCommand config before after infoState
  let captures := captures ++ current
  if after.contains config.const_name then
    return (captures, after)
  match snapshot.nextCmdSnap? with
  | some next => collectCommandCaptures config after next captures
  | none => return (captures, after)

private unsafe def processLoadedSnapshot (config : Animate.Config)
    (incremental : IncrSnapshot) (certificatePath : System.FilePath)
    (initializeModules : Bool := true) : IO Json := do
  let some headerState := incremental.snap.processedResult.get
    | throw <| IO.userError "incremental snapshot has no processed header state"
  if initializeModules then
    Lean.enableInitializersExecution
    Lean.withImporting do
      unsafe Lean.runInitAttrsForModules
        headerState.cmdState.env incremental.initModIdxs headerState.cmdState.scopes[0]!.opts
  let importedEnv := headerState.cmdState.env
  let (captures, finalEnv) ← collectCommandCaptures
    config importedEnv headerState.firstCmdSnap
  unless finalEnv.contains config.const_name do
    throw <| IO.userError s!"snapshot does not contain {config.const_name}"
  let certificateJson ← IO.FS.readFile certificatePath
  let certificates : Animate.SnapshotCertificateBundle ←
    match Json.parse certificateJson >>= fromJson? with
    | .ok value => pure value
    | .error error => throw <| IO.userError s!"invalid kernel certificate: {error}"
  unless certificates.selectedTheorem == config.const_name.toString do
    throw <| IO.userError "kernel certificate theorem does not match the request"
  let trace ← if config.trace_mode == .proofTerm then
    let coreContext : Core.Context := {
      fileName := config.file_path.toString
      fileMap := default
      maxHeartbeats := 0
      maxRecDepth := 100000
    }
    let coreState : Core.State := { env := finalEnv }
    let (proofTrace, _, _) ← _root_.Lean.Meta.MetaM.toIO
      (ProofTrace.extract config.const_name (some importedEnv)
        config.trace_checkpoint_dir) coreContext coreState
    pure (Lean.toJson proofTrace)
  else
    Animate.buildHybridTrace config importedEnv finalEnv captures certificates
  if let some moduleOutput := config.module_output then
    if let some parent := moduleOutput.parent then
      IO.FS.createDirAll parent
    Lean.writeModule finalEnv moduleOutput
  return trace

unsafe def processSnapshot (config : Animate.Config)
    (snapshotPath certificatePath : System.FilePath) : IO Json := do
  let incremental ← loadSnapshot snapshotPath
  processLoadedSnapshot config incremental certificatePath

structure WorkerRequest where
  requestId : String
  snapshotPath : String
  snapshotKey : String
  certificatePath : String
  animateArgs : Array String
deriving FromJson

structure WorkerResponse where
  requestId : String
  ok : Bool
  document : Option Json := none
  error : String := ""
  comparedCommandTrees : Bool := false
  reusedCommands : Nat := 0
  totalCommands : Nat := 0
deriving ToJson

private partial def commandCount
    (task : Language.SnapshotTask Language.Lean.CommandParsedSnapshot) : Nat :=
  let snapshot := task.get
  1 + (snapshot.nextCmdSnap?.map commandCount).getD 0

private partial def commandReusePrefix
    (oldTask newTask : Language.SnapshotTask Language.Lean.CommandParsedSnapshot)
    (reused : Nat := 0) : Nat :=
  let oldSnapshot := oldTask.get
  let newSnapshot := newTask.get
  if oldSnapshot.stx.eqWithInfo newSnapshot.stx then
    match oldSnapshot.nextCmdSnap?, newSnapshot.nextCmdSnap? with
    | some oldNext, some newNext => commandReusePrefix oldNext newNext (reused + 1)
    | none, none => reused + 1
    | _, _ => reused
  else reused

/-- A process-local snapshot cache. The Python supervisor owns the 15-minute
    idle lease and restarts this process when the import/header identity
    changes, so initializers are run exactly once for a compatible tree. -/
private unsafe def workerLoop : IO UInt32 := do
  let input ← IO.getStdin
  let output ← IO.getStdout
  let cached ← IO.mkRef (none : Option (String × IncrSnapshot))
  let initialized ← IO.mkRef false
  repeat do
    let line ← input.getLine
    if line.isEmpty then return 0
    let request : Except String WorkerRequest := do
      let json ← Json.parse line
      fromJson? json
    let response ← match request with
      | .error error => pure ({
          requestId := ""
          ok := false
          error := s!"invalid worker request: {error}"
        } : WorkerResponse)
      | .ok request =>
        try
          let (incremental, compared, reusedCommands) ← match ← cached.get with
            | some (key, snapshot) =>
              if key == request.snapshotKey then
                let some header := snapshot.snap.processedResult.get
                  | throw <| IO.userError "cached snapshot has no header state"
                pure (snapshot, true, commandCount header.firstCmdSnap) else
                let snapshot ← loadSnapshot ⟨request.snapshotPath⟩
                let some oldHeader := snapshot.snap.processedResult.get
                  | throw <| IO.userError "snapshot has no header state"
                let some previousHeader := (← cached.get).bind
                    (fun value => value.2.snap.processedResult.get)
                  | throw <| IO.userError "previous snapshot has no header state"
                let reused := commandReusePrefix
                  previousHeader.firstCmdSnap oldHeader.firstCmdSnap
                cached.set (some (request.snapshotKey, snapshot))
                pure (snapshot, true, reused)
            | none =>
              let snapshot ← loadSnapshot ⟨request.snapshotPath⟩
              cached.set (some (request.snapshotKey, snapshot))
              pure (snapshot, false, 0)
          let some header := incremental.snap.processedResult.get
            | throw <| IO.userError "snapshot has no header state"
          let totalCommands := commandCount header.firstCmdSnap
          let config ← Animate.parseArgs request.animateArgs
          let first := !(← initialized.get)
          let document ← processLoadedSnapshot config incremental
            ⟨request.certificatePath⟩ first
          if first then initialized.set true
          pure ({
            requestId := request.requestId
            ok := true
            document := some document
            comparedCommandTrees := compared
            reusedCommands
            totalCommands
          } : WorkerResponse)
        catch error =>
          pure ({
            requestId := request.requestId
            ok := false
            error := toString error
          } : WorkerResponse)
    output.putStrLn (toJson response).compress
    output.flush

end SnapshotAnimate432

unsafe def main (args : List String) : IO UInt32 := do
  try
    let args := args.toArray
    if args.size == 1 && args[0]! == "--worker" then
      return ← SnapshotAnimate432.workerLoop
    if args.size < 4 then
      throw <| IO.userError
        "usage: SnapshotAnimate FILE CONST SNAPSHOT CERTIFICATE [Animate options]"
    let snapshotPath : System.FilePath := ⟨args[2]!⟩
    let certificatePath : System.FilePath := ⟨args[3]!⟩
    let animateArgs := #[args[0]!, args[1]!] ++ args.extract 4 args.size
    let config ← Animate.parseArgs animateArgs
    let trace ← SnapshotAnimate432.processSnapshot config snapshotPath certificatePath
    IO.println <| trace.pretty (lineWidth := 200)
    return 0
  catch error =>
    IO.eprintln s!"snapshot extractor failed: {error}"
    return 1
