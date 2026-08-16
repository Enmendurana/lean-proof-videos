import Lean
import Lean.Elab.Import
import ProofTrace
import Animate.Hybrid

namespace Animate

private initialize currentStage : IO.Ref String ← IO.mkRef "startup"

section infotrees

open Lean Elab

unsafe def processCommands : Frontend.FrontendM (List (Environment × InfoState)) := do
  let done ← Lean.Elab.Frontend.processCommand
  let st := ← get
  let infoState := st.commandState.infoState
  let env' := st.commandState.env

  -- clear the infostate
  set {st with commandState := {st.commandState with infoState := {}}}
  if done
  then return [(env', infoState)]
  else
    return (env', infoState) :: (←processCommands)

unsafe def processFile (config : Config) : IO Unit := do
  currentStage.set "reading the input file"
  if config.trace_mode != .tactic then
    ProofTrace.reportProgressPhase config.trace_checkpoint_dir
      "reading-input" config.const_name.toString
  initSearchPath (← findSysroot)
  let mut input ← IO.FS.readFile config.file_path
  Lean.enableInitializersExecution
  let inputCtx := Lean.Parser.mkInputContext input config.file_path.toString
  let (header, parserState, messages) ← Lean.Parser.parseHeader inputCtx
  -- The input theorem does not need to import LeanTeX itself. Add it to the
  -- elaboration environment without changing the source text (and therefore
  -- without shifting any tactic source spans used by the animator).
  let mut imports := Lean.Elab.HeaderSyntax.imports header
  unless imports.any (·.module == `ProofLatex) do
    imports := imports.push (`ProofLatex : Lean.Import)
  currentStage.set "importing the input environment"
  if config.trace_mode != .tactic then
    ProofTrace.reportProgressPhase config.trace_checkpoint_dir
      "importing-environment" config.const_name.toString
  let (env, messages) ← Lean.Elab.processHeaderCore
    (Lean.Elab.HeaderSyntax.startPos header) imports
    (Lean.Elab.HeaderSyntax.isModule header) {} messages inputCtx

  if messages.hasErrors then
    for msg in messages.toList do
      if msg.severity == .error then
        println! "ERROR: {← msg.toString}"
    throw <| IO.userError "Errors during import; aborting"

  -- Preserve the header environment as the boundary between imported library
  -- declarations and source-local proof chapters.
  let importedEnv := env

  currentStage.set "determining the input module name"
  let mainModule ← try
    Lean.moduleNameOfFileName config.file_path none
  catch error =>
    throw <| IO.userError s!"determining the input module name failed: {error}"
  let env := env.setMainModule mainModule

  if env.contains config.const_name then
    throw <| IO.userError s!"constant of name {config.const_name} is already in environment"

  let commandState := { Lean.Elab.Command.mkState env messages {} with infoState.enabled := true }

  currentStage.set "elaborating input commands"
  if config.trace_mode != .tactic then
    ProofTrace.reportProgressPhase config.trace_checkpoint_dir
      "elaborating-source" config.const_name.toString
  if config.trace_mode == .hybrid then
    let ((captures, _capturedEnv), frontendState) ← try
      (processHybridCommands config |>.run { inputCtx := inputCtx }).run
        { commandState := commandState, parserState := parserState, cmdPos := parserState.pos }
    catch error =>
      throw <| IO.userError s!"elaborating input commands failed: {error}"
    let finalEnv := frontendState.commandState.env
    unless finalEnv.contains config.const_name do
      throw <| IO.userError s!"no constant of name {config.const_name} was found"
    currentStage.set "extracting and serializing the proof trace"
    ProofTrace.reportProgressPhase config.trace_checkpoint_dir
      "starting-extraction" config.const_name.toString
    let trace ← buildHybridTrace config importedEnv finalEnv captures
    if let some moduleOutput := config.module_output then
      if let some parent := moduleOutput.parent then
        IO.FS.createDirAll parent
      Lean.writeModule finalEnv moduleOutput
    IO.println <| trace.pretty (lineWidth := 200)
    return ()
  let (steps, _frontendState) ← try
    (processCommands.run { inputCtx := inputCtx }).run
      { commandState := commandState, parserState := parserState, cmdPos := parserState.pos }
  catch error =>
    throw <| IO.userError s!"elaborating input commands failed: {error}"

  -----
  currentStage.set "extracting and serializing the proof trace"
  if config.trace_mode != .tactic then
    ProofTrace.reportProgressPhase config.trace_checkpoint_dir
      "starting-extraction" config.const_name.toString
  for ⟨env, s⟩ in steps do
    if env.contains config.const_name then
      if config.trace_mode == .proofTerm then
        let coreContext : Core.Context := {
          fileName := config.file_path.toString
          fileMap := default
          -- Explicit proof-term compatibility mode is exact and unbounded.
          -- Its append-only fragments make long declarations resumable.
          maxHeartbeats := 0
          maxRecDepth := 100000
        }
        let coreState : Core.State := { env }
        let (trace, _, _) ← _root_.Lean.Meta.MetaM.toIO
          (ProofTrace.extract config.const_name (some importedEnv)
            config.trace_checkpoint_dir) coreContext coreState
        IO.println <| (Lean.toJson trace).pretty (lineWidth := 200)
        return ()
      for tree in s.trees do
        if config.print_infotree then
           IO.println (Format.pretty (←tree.format))
        let step ← extractToplevelStep tree
        if config.print_stage1 then
          IO.println s!"{ToJson.toJson step}"
        let stage2state ← stage2 step
        if config.print_stage2 then
          IO.println "STAGE 2:"
          stage2state.dump
        let stage3 ← stage3 config stage2state
        IO.println <| (Lean.toJson stage3).pretty (lineWidth := 200)
      -- we're done
      return ()

  throw <| IO.userError s!"no constant of name {config.const_name} was found"

end infotrees

unsafe def runMain (args : List String) : IO Unit := do
  let cfg ← Animate.parseArgs args.toArray
  try
    Animate.processFile cfg
  catch error =>
    let stage ← Animate.currentStage.get
    throw <| IO.userError s!"{stage} failed: {error}"


end Animate
