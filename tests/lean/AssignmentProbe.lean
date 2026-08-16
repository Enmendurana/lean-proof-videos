import Mathlib.Tactic

open Lean Meta Elab Tactic

syntax (name := probeTac) "probe " tactic : tactic

elab_rules : tactic
  | `(tactic| probe $inner:tactic) => do
      let before ← getGoals
      evalTactic inner
      let after ← getGoals
      let mctx ← getMCtx
      for goal in before do
        let assignment := mctx.getExprAssignmentCore? goal
        let descendants ← match assignment with
          | some proof => getMVars proof
          | none => pure #[]
        let rendered ← match assignment with
          | some proof => ppExpr proof
          | none => pure "<none>"
        logInfo m!"PROBE old={goal.name} after={after.map (·.name)} descendants={descendants.map (·.name)} assignment={rendered}"

example (a b c : Nat) (hab : a = b) (hbc : b + 1 = c) : a + 1 = c := by
  probe rw [hab]
  exact hbc

example (a b : Nat) (hab : a = b) : a + 0 = b := by
  probe simp only [Nat.add_zero]
  exact hab

example (a b : Nat) (hab : a = b) : a + b = a + a := by
  probe subst b
  rfl

def twice (n : Nat) : Nat := n + n

example (a : Nat) : twice a = a + a := by
  probe change a + a = a + a
  rfl

example (a : Nat) : twice a = a + a := by
  probe show a + a = a + a
  rfl

example (x : ℤ) (h : x ^ 2 + 2 * x + 1 = 0) : (x + 1) ^ 2 = 0 := by
  probe ring_nf at h ⊢
  exact h

example (x y : ℤ) (h : x ≤ y) : x ≤ y + 1 := by
  probe linarith

example (f g : Nat → Nat) (hfg : f = g) (a : Nat)
    (h : g a + g a = 0) : f a + f a = 0 := by
  probe rw [hfg]
  exact h

example : True ∧ True := by
  probe constructor
  · trivial
  · trivial

example (p q : Prop) (h : p ∨ q) : q ∨ p := by
  probe cases h
  · exact Or.inr ‹p›
  · exact Or.inl ‹q›

example (n : Nat) : n + 0 = n := by
  probe induction n
  · rfl
  · simp
