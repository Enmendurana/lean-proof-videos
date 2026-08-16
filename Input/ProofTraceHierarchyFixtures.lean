import Mathlib

namespace ProofTraceHierarchyFixtures

theorem seed (P : Prop) (hP : P) : P := by
  exact hP

theorem pair (P Q : Prop) (hP : P) (hQ : Q) : P ∧ Q := by
  exact ⟨seed P hP, hQ⟩

theorem swap (P Q : Prop) (h : P ∧ Q) : Q ∧ P := by
  exact ⟨h.2, h.1⟩

theorem main (P Q : Prop) (hP : P) (hQ : Q) : Q ∧ P := by
  exact swap P Q (pair P Q hP hQ)

end ProofTraceHierarchyFixtures
