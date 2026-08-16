import Lake
open Lake DSL

package «animate» where
  leanOptions := #[
    ⟨`pp.unicode.fun, true⟩, -- pretty-prints `fun a ↦ b`
    ⟨`autoImplicit, false⟩,
    ⟨`relaxedAutoImplicit, false⟩
  ]

@[default_target]
lean_lib Annotations

@[default_target]
lean_lib Input

@[default_target]
lean_lib StringMatching

@[default_target]
lean_lib HighlightSyntax

@[default_target]
lean_lib MathlibLatex

@[default_target]
lean_lib ProofLatex

@[default_target]
lean_lib SemanticTransitions

@[default_target]
lean_lib ProofTrace

lean_lib ProofVideoExtractor where
  roots := #[`Animate]

@[default_target]
lean_exe «Animate» where
  root := `AnimateMain
  -- The extractor itself stays small; ProofLatex/Mathlib is imported only
  -- into the input proof environment. Interpreter support is therefore safe
  -- on Windows without exporting the complete Mathlib native graph.
  supportInterpreter := true

require mathlib from git "https://github.com/leanprover-community/mathlib4" @ "v4.28.0"
-- Match Mathlib 4.28's exact transitive revisions while overriding LeanTeX's
-- older pins. Exact commits make the long proof environment reproducible.
require proofwidgets from git "https://github.com/leanprover-community/ProofWidgets4" @ "be3b2e63b1bbf496c478cef98b86972a37c1417d"
require batteries from git "https://github.com/leanprover-community/batteries" @ "495c008c3e3f4fb4256ff5582ddb3abf3198026f"
require LeanTeX from git "https://github.com/kmill/LeanTeX" @ "main"
