import Mathlib.Tactic

namespace CanonicalTacticCorpus

def twice (n : Nat) : Nat := n + n

/--
A deliberately broad, small tactic corpus for the proof-state extractor.

The theorem is mathematically uninteresting on purpose: every nested proof
exercises a state-changing tactic in a context where Lean can expose its real
`TacticInfo` before/after states.  The Python integration test treats those
states as observations and verifies the generic canonical diff/replay law; it
does not dispatch on the tactic names below.
-/
theorem canonicalStateCorpus
    (f : Nat → Nat)
    (a b c : Nat) (hab : a = b) (hbc : b = c)
    (p q : Prop) (hp : p) (hq : q)
    (hAll : ∀ n : Nat, n = n)
    (hExists : ∃ n : Nat, n = n) : True := by
  have tIntro : ∀ n : Nat, n = n := by
    intro n
    rfl
  have tIntros : ∀ n m : Nat, n = n ∧ m = m := by
    intros n m
    constructor <;> rfl
  have tRintro : (∃ n : Nat, n = n) → True := by
    rintro ⟨n, hn⟩
    trivial
  have tRevert : ∀ n : Nat, n = n := by
    intro n
    revert n
    intro n
    rfl
  have tHave : True := by
    have h : a = a := rfl
    trivial
  have tLet : True := by
    let k : Nat := a + 1
    have hk : k = k := rfl
    exact True.intro
  have tSet : a + 1 = a + 1 := by
    set k := a + 1 with hk
    rfl
  have tReplace : p ∧ q → p := by
    intro h
    replace h := h.1
    exact h
  have tSpecialize : 3 = 3 := by
    have h := hAll
    specialize h 3
    exact h
  have tClear : ∀ n m : Nat, n = n := by
    intro n m
    clear m
    rfl
  have tClearValue : True := by
    let k : Nat := a + 1
    have hk : k = k := rfl
    clear_value k
    exact True.intro
  have tSubst : a + b = a + a := by
    subst b
    rfl
  have tGeneralize : a + b = a + b := by
    generalize h : a + b = n
    rfl
  have tRw : a = c := by
    rw [hab]
    exact hbc
  have tSimp : a + 0 = a := by
    simp only [Nat.add_zero]
  have tDsimp : twice a = a + a := by
    dsimp [twice]
  have tUnfold : twice a = a + a := by
    unfold twice
    rfl
  have tChange : twice a = a + a := by
    change a + a = a + a
    rfl
  have tShow : twice a = a + a := by
    show a + a = a + a
    rfl
  have tSymm : b = a := by
    symm
    exact hab
  have tApply : a = c := by
    apply Eq.trans hab
    exact hbc
  have tRefine : a = c := by
    refine Eq.trans hab ?_
    exact hbc
  have tExact : a = b := by
    exact hab
  have tAssumption : a = b := by
    assumption
  have tRfl : a = a := by
    rfl
  have tConstructor : p ∧ q := by
    constructor
    · exact hp
    · exact hq
  have tLeft : p ∨ q := by
    left
    exact hp
  have tRight : p ∨ q := by
    right
    exact hq
  have tUse : ∃ n : Nat, n = a := by
    use a
  have tExists : ∃ n : Nat, n = a := by
    exists a
  have tTrans : a = c := by
    trans b
    · exact hab
    · exact hbc
  have tCongr : f a = f b := by
    congr 1
  have tCases : p ∨ q → q ∨ p := by
    intro h
    cases h with
    | inl hp' => exact Or.inr hp'
    | inr hq' => exact Or.inl hq'
  have tRcases : p ∧ q → q ∧ p := by
    intro h
    rcases h with ⟨hp', hq'⟩
    exact ⟨hq', hp'⟩
  have tObtain : ∃ n : Nat, n = n := by
    obtain ⟨n, hn⟩ := hExists
    exact ⟨n, hn⟩
  have tInduction : ∀ n : Nat, n + 0 = n := by
    intro n
    induction n with
    | zero => rfl
    | succ n ih => simp
  have tByCases : p ∨ ¬p := by
    by_cases h : p
    · exact Or.inl h
    · exact Or.inr h
  have tCase : p ∨ q → q ∨ p := by
    intro h
    cases h
    case inl hp' => exact Or.inr hp'
    case inr hq' => exact Or.inl hq'
  have tNext : True ∧ True := by
    constructor
    next => trivial
    next => trivial
  have tFocus : True ∧ True := by
    constructor
    focus trivial
    trivial
  have tAllGoals : True ∧ True := by
    constructor
    all_goals trivial
  have tNativeHyperedge : ∀ n : Nat, n = n ∧ n = n := by
    refine fun n => ⟨?_, ?_⟩
    · rfl
    · rfl
  have tSwap : p ∧ q := by
    constructor
    swap
    · exact hq
    · exact hp
  have tRotateLeft : p ∧ q := by
    constructor
    rotate_left
    · exact hq
    · exact hp
  have tRotateRight : p ∧ q := by
    constructor
    rotate_right
    · exact hq
    · exact hp
  exact True.intro

end CanonicalTacticCorpus
