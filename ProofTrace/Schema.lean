import Lean
import SemanticTransitions

namespace ProofTrace

open Lean Meta Animate

structure Step where
  id : Nat
  scopeId : String
  parentScopeId : Option String := none
  depth : Nat
  kind : String
  rule : String
  premises : Array Nat := #[]
  propositionLatex : String
  propositionLean : String
  displayLatex : String
  semanticNodes : Array SemanticNode := #[]
  proofFingerprint : String
  propositionFingerprint : String
  proofPath : String
  theoremName : Option String := none
  binderName : Option String := none
  opensScope : Option String := none
  closesScope : Option String := none
  kernelChecked : Bool := true
  /-- True when the displayed proposition mentions a local hypothesis or
      eigenvariable. Ground typeclass/certificate plumbing is normally hidden
      by the natural presentation without naming individual libraries. -/
  usesLocalContext : Bool := false
  isTypeclass : Bool := false
deriving ToJson, FromJson, Repr

structure Validation where
  valid : Bool
  dependencyOrderValid : Bool
  finalTypeMatches : Bool
  noSorry : Bool
  errors : Array String := #[]
deriving ToJson, FromJson, Repr

/-- One independently checked source-level theorem in a hierarchical trace.
    Chapters are emitted in dependency order, with the requested theorem last. -/
structure Chapter where
  id : Nat
  theoremName : String
  theoremLatex : String
  theoremLean : String
  startStepId : Nat
  finalStepId : Nat
  dependencies : Array String := #[]
  isMain : Bool := false
deriving ToJson, FromJson, Repr

structure Trace where
  schemaVersion : String := "2.1"
  theoremName : String
  source : String := "Mathlib.Tactic.Explode/structured-adapter"
  granularity : String := "natural-deduction"
  theoremLatex : String
  theoremLean : String
  steps : Array Step
  finalStepId : Nat
  chapters : Array Chapter := #[]
  axioms : Array String := #[]
  validation : Validation
deriving ToJson, FromJson, Repr

/-- Small status document polled by the Python CLI while Lean is extracting.
    It is deliberately independent from the (potentially very large) trace. -/
structure ExtractionProgress where
  schemaVersion : Nat := 2
  phase : String
  chapterIndex : Nat := 0
  chapterCount : Nat := 0
  theoremName : String := ""
  currentTactic : String := ""
  completedChapters : Nat := 0
  processedSteps : Nat := 0
  totalSteps : Nat := 0
  emittedSteps : Nat := 0
  replayedSteps : Nat := 0
  detailMode : String := "detailed"
  proofObjects : Nat := 0
  completedWeight : Nat := 0
  totalWeight : Nat := 0
  /-- Exact source command currently being elaborated.  Byte positions refer
      to the original UTF-8 input, so the polling client can show a source line
      without duplicating Lean's parser. -/
  commandIndex : Nat := 0
  commandStartByte : Nat := 0
  commandEndByte : Nat := 0
  commandElapsedMs : Nat := 0
  slowestCommandStartByte : Nat := 0
  slowestCommandElapsedMs : Nat := 0
deriving ToJson, FromJson, Repr

/-- Durable timing for one top-level source command.  This is diagnostic
    evidence only: it never participates in the proof certificate. -/
structure CommandProfileEntry where
  index : Nat
  startByte : Nat
  endByte : Nat
  elapsedMs : Nat
  declarations : Array String := #[]
deriving ToJson, FromJson, Repr

structure CommandProfileReport where
  schemaVersion : Nat := 1
  sourceFile : String
  complete : Bool := false
  commands : Array CommandProfileEntry := #[]
deriving ToJson, FromJson, Repr

/-- A bounded, append-only fragment of an unfinished theorem chapter.  On a
    restart we replay the cheap expression walk, reuse these already rendered
    rows, and continue at the first missing row. -/
structure StepChunk where
  schemaVersion : Nat := 1
  theoremName : String
  proofFingerprint : String
  startId : Nat
  steps : Array Step
deriving ToJson, FromJson, Repr

end ProofTrace
