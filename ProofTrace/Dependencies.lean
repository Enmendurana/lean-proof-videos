import Lean
import Lean.DeclarationRange
import Lean.Util.FoldConsts
import ProofTrace.Compat

namespace ProofTrace

open Lean Meta

private partial def scanLocalProofDependencies
    (importedEnv : Environment)
    (proof : Expr)
    (visitedGenerated : IO.Ref NameSet)
    (seenDependencies : IO.Ref NameSet)
    (dependencies : IO.Ref (Array Name))
    (active : NameSet) : MetaM Unit := do
  let constants := proof.foldConsts #[] fun name result => result.push name
  for name in constants do
    if importedEnv.contains name || active.contains name then
      continue
    let some declaration ← Compat.getConstInfo? name | continue
    let some value := Compat.declarationValue? declaration | continue
    unless ← isProp declaration.type do continue
    -- Exact declaration ranges identify mathematical declarations written in
    -- the input source. Generated proof constants are transparent carriers:
    -- do not show them as chapters, but scan through them so a user lemma
    -- hidden behind elaborator plumbing is still discovered.
    if (← findDeclarationRangesCore? name).isSome then
      unless (← seenDependencies.get).contains name do
        seenDependencies.modify (·.insert name)
        dependencies.modify (·.push name)
    else
      unless (← visitedGenerated.get).contains name do
        visitedGenerated.modify (·.insert name)
        scanLocalProofDependencies importedEnv value visitedGenerated
          seenDependencies dependencies (active.insert name)

private def directLocalProofDependencies
    (importedEnv : Environment) (theoremName : Name) : MetaM (Array Name) := do
  let declaration ← getConstInfo theoremName
  let some proof := declaration.value? true | return #[]
  let visitedGenerated ← IO.mkRef ({} : NameSet)
  let seenDependencies ← IO.mkRef ({} : NameSet)
  let dependencies ← IO.mkRef (#[] : Array Name)
  scanLocalProofDependencies importedEnv proof visitedGenerated seenDependencies
    dependencies ({theoremName} : NameSet)
  dependencies.get

private partial def collectLocalProofOrder
    (importedEnv : Environment)
    (theoremName : Name)
    (visited : IO.Ref NameSet)
    (order : IO.Ref (Array Name))
    (active : NameSet := {}) : MetaM Unit := do
  if (← visited.get).contains theoremName || active.contains theoremName then
    return
  let active := active.insert theoremName
  for dependency in ← directLocalProofDependencies importedEnv theoremName do
    collectLocalProofOrder importedEnv dependency visited order active
  visited.modify (·.insert theoremName)
  order.modify (·.push theoremName)

def sourceLocalProofDependencies (importedEnv : Environment)
    (theoremName : Name) : MetaM (Array Name) :=
  directLocalProofDependencies importedEnv theoremName

def sourceLocalProofOrder (importedEnv : Environment)
    (theoremName : Name) : MetaM (Array Name) := do
  let visited ← IO.mkRef ({} : NameSet)
  let order ← IO.mkRef (#[] : Array Name)
  collectLocalProofOrder importedEnv theoremName visited order
  order.get

/-! The snapshot frontend no longer has the pre-import `Environment` after a
full command snapshot has been restored.  During the certificate command we
are still in the source module, so `getModuleIdxFor? = none` is the exact
equivalent test for declarations introduced by this file. -/

private partial def scanCurrentModuleProofDependencies
    (env : Environment)
    (proof : Expr)
    (visitedGenerated : IO.Ref NameSet)
    (seenDependencies : IO.Ref NameSet)
    (dependencies : IO.Ref (Array Name))
    (active : NameSet) : MetaM Unit := do
  let constants := proof.foldConsts #[] fun name result => result.push name
  for name in constants do
    if (env.getModuleIdxFor? name).isSome || active.contains name then
      continue
    let some declaration ← Compat.getConstInfo? name | continue
    let some value := Compat.declarationValue? declaration | continue
    unless ← isProp declaration.type do continue
    if (← findDeclarationRangesCore? name).isSome then
      unless (← seenDependencies.get).contains name do
        seenDependencies.modify (·.insert name)
        dependencies.modify (·.push name)
    else
      unless (← visitedGenerated.get).contains name do
        visitedGenerated.modify (·.insert name)
        scanCurrentModuleProofDependencies env value visitedGenerated
          seenDependencies dependencies (active.insert name)

def sourceCurrentModuleProofDependencies
    (theoremName : Name) : MetaM (Array Name) := do
  let env ← getEnv
  let declaration ← getConstInfo theoremName
  let some proof := declaration.value? true | return #[]
  let visitedGenerated ← IO.mkRef ({} : NameSet)
  let seenDependencies ← IO.mkRef ({} : NameSet)
  let dependencies ← IO.mkRef (#[] : Array Name)
  scanCurrentModuleProofDependencies env proof visitedGenerated seenDependencies
    dependencies ({theoremName} : NameSet)
  dependencies.get

private partial def collectCurrentModuleProofOrder
    (theoremName : Name)
    (visited : IO.Ref NameSet)
    (order : IO.Ref (Array Name))
    (active : NameSet := {}) : MetaM Unit := do
  if (← visited.get).contains theoremName || active.contains theoremName then
    return
  let active := active.insert theoremName
  for dependency in ← sourceCurrentModuleProofDependencies theoremName do
    collectCurrentModuleProofOrder dependency visited order active
  visited.modify (·.insert theoremName)
  order.modify (·.push theoremName)

def sourceCurrentModuleProofOrder (theoremName : Name) : MetaM (Array Name) := do
  let visited ← IO.mkRef ({} : NameSet)
  let order ← IO.mkRef (#[] : Array Name)
  collectCurrentModuleProofOrder theoremName visited order
  order.get

end ProofTrace
