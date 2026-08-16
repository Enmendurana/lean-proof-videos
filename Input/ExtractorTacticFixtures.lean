import Mathlib.Tactic

namespace ExtractorTacticFixtures

def twice (n : Nat) : Nat := n + n

/-- A compact extractor fixture containing the tactic families for which the
    renderer needs to distinguish an in-place goal update, branching, and a
    closed goal. The nested `have` proofs keep the fixture to one frontend run. -/
theorem tacticAdapters
    (f g : Nat → Nat) (hfg : f = g)
    (a b c : Nat) (hab : a = b) (hbc : b + 1 = c)
    (hgg : g a + g a = 0)
    (x y : ℤ) (hxy : x ≤ y)
    (hRingNormal : x ^ 2 + 2 * x + 1 = 0)
    (p q : Prop)
    (hAll : ∀ n : Nat, n = n)
    (hExists : ∃ n : Nat, n = n) : True := by
  have hRw : a + 1 = c := by
    rw [hab]
    exact hbc
  have hSimp : a + 0 = b := by
    simp only [Nat.add_zero]
    exact hab
  have hSubst : a + b = a + a := by
    subst b
    rfl
  have hChange : twice a = a + a := by
    change a + a = a + a
    rfl
  have hShow : twice b = b + b := by
    show b + b = b + b
    rfl
  have hCalc : a + b = b + a :=
    (calc a + b = b + a := Nat.add_comm a b)
  have hRing : (x + 1) ^ 2 = 0 := by
    ring_nf at hRingNormal ⊢
    exact hRingNormal
  have hLinarith : x ≤ y + 1 := by
    linarith
  have hCases : p ∨ q → q ∨ p := by
    intro h
    cases h with
    | inl hp => exact Or.inr hp
    | inr hq => exact Or.inl hq
  have hConstructor : True ∧ True := by
    constructor
    · trivial
    · trivial
  have hInduction : ∀ n : Nat, n + 0 = n := by
    intro n
    induction n with
    | zero => rfl
    | succ n ih => simp [ih]
  have hDuplicate : f a + f a = 0 := by
    rw [hfg]
    exact hgg
  have hForallFunction : ∀ z : Nat, f z = f z := by
    intro z
    rfl
  have hForallPair : ∀ u v : Nat, u = u := by
    intro u v
    rfl
  exact True.intro

end ExtractorTacticFixtures
