import Lean

namespace Animate

inductive TraceMode where
  | hybrid
  | proofTerm
  | tactic
deriving BEq, Repr

--- animate.lean command line arguments
structure Config where
  file_path : System.FilePath := "."
  const_name : Lean.Name := `Unknown
  print_infotree : Bool := false
  print_stage1 : Bool := false
  print_stage2 : Bool := false
  min_match_len : Nat := 2
  nonmatchers : String := ""
  trace_mode : TraceMode := .hybrid
  trace_checkpoint_dir : Option System.FilePath := none
  /-- When present, hybrid chapters are written independently and stdout only
      contains a small manifest. The inline schema remains available for old
      callers that do not pass this option. -/
  trace_output_dir : Option System.FilePath := none
  /-- Independent chapter certificates may be checked concurrently after the
      sequential source environment has been elaborated. -/
  postprocess_workers : Nat := 4
  /-- Optional `.olean` publication for a modular trace unit.  The same
      elaboration then serves both the animation chapter and later imports. -/
  module_output : Option System.FilePath := none
  /-- Recompute one theorem chapter while retaining all other fingerprint-
      validated command, action, and chapter checkpoints. -/
  rebuild_chapter : Option Lean.Name := none

def parseArgs (args : Array String) : IO Config := do
  if args.size < 2 then
    throw <| IO.userError "usage: animate FILE_PATH CONST_NAME"
  let mut cfg : Config := {}
  cfg := { cfg with file_path := ⟨args[0]!⟩ }
  cfg := { cfg with const_name := args[1]!.toName }
  let mut idx := 2
  while idx < args.size do
    match args[idx]! with
    | "--print-infotree" =>
       cfg := {cfg with print_infotree := true}
    | "--print-stage1" =>
       cfg := {cfg with print_stage1 := true}
    | "--print-stage2" =>
       cfg := {cfg with print_stage2 := true}
    | "--min-match-len" =>
       idx := idx + 1
       let x := args[idx]!.toNat!
       cfg := {cfg with min_match_len := x}
    | "--nonmatchers" =>
       idx := idx + 1
       let x := args[idx]!
       cfg := {cfg with nonmatchers := x}
    | "--trace-mode" =>
       idx := idx + 1
       let mode ← match args[idx]! with
         | "hybrid" => pure TraceMode.hybrid
         | "proof-term" => pure TraceMode.proofTerm
         | "tactic" => pure TraceMode.tactic
         | other => throw <| IO.userError s!"unknown trace mode {other}; expected hybrid, proof-term or tactic"
       cfg := {cfg with trace_mode := mode}
    | "--trace-checkpoint-dir" =>
       idx := idx + 1
       cfg := {cfg with trace_checkpoint_dir := some ⟨args[idx]!⟩}
    | "--trace-output-dir" =>
       idx := idx + 1
       cfg := {cfg with trace_output_dir := some ⟨args[idx]!⟩}
    | "--postprocess-workers" =>
       idx := idx + 1
       let workers := args[idx]!.toNat!
       if workers == 0 then
         throw <| IO.userError "--postprocess-workers must be positive"
       cfg := {cfg with postprocess_workers := workers}
    | "--module-output" =>
       idx := idx + 1
       cfg := {cfg with module_output := some ⟨args[idx]!⟩}
    | "--rebuild-chapter" =>
       idx := idx + 1
       cfg := {cfg with rebuild_chapter := some args[idx]!.toName}
    | s => throw <| IO.userError s!"unknown argument {s}"
    idx := idx + 1

  return cfg

end Animate
