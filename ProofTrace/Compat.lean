import Lean

/-!
Small compatibility boundary for proof declarations and synthetic expressions.

The proof traversal is shared by Lean 4.28 and 4.32.  Compiler-generated
placeholder expressions are not guaranteed to name declarations in the
environment, and not every `ConstantInfo` has a value.  Keeping those checks
here prevents the version-independent extractor from depending on unsafe
`value!` or unchecked `getConstInfo` calls.
-/

namespace ProofTrace.Compat

open Lean Meta

def getConstInfo? (name : Name) : MetaM (Option ConstantInfo) := do
  let env ← getEnv
  unless env.contains name do return none
  return some (← getConstInfo name)

def declarationValue? (declaration : ConstantInfo) : Option Expr :=
  declaration.value? true

def getDeclarationValue? (name : Name) : MetaM (Option (ConstantInfo × Expr)) := do
  let some declaration ← getConstInfo? name | return none
  let some value := declarationValue? declaration | return none
  return some (declaration, value)

end ProofTrace.Compat
