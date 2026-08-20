import Mathlib.Tactic

namespace CanonicalHyperedgeSmoke

/-- A minimal real InfoTree boundary where one forall binder is introduced and
copied into two child goals by a single kernel-checked proof assignment. -/
theorem splitBinder : ∀ n : Nat, n = n ∧ n = n := by
  refine fun n => ⟨?_, ?_⟩
  · rfl
  · rfl

end CanonicalHyperedgeSmoke
