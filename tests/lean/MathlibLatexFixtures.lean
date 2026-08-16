import ProofLatex
import Input.Erdos38
import Lean.Elab.Tactic.Guard

open LeanTeX Classical
open scoped Pointwise

noncomputable section

variable (d : ShiftApproxData)
variable (A : Set ℕ)
variable (C : Finset ℕ)
variable (b N h s m M : ℕ)
variable (α : ℝ)
variable (a : ℕ → ℝ)

/-- info: \mathcal{D}_{\mathrm{shift}} -/
#guard_msgs in #latex ShiftApproxData
/-- info: B_{d} -/
#guard_msgs in #latex constructB d
/-- info: \sigma(A) -/
#guard_msgs in #latex schnirelmannDensity A
/-- info: f(\alpha) -/
#guard_msgs in #latex erdos_f α
/-- info: \mathsf{AddBasis}(B_{d}) -/
#guard_msgs in #latex IsAdditiveBasis (constructB d)
/-- info: \left|A \cap [1,N]\right| -/
#guard_msgs in #latex countIn A N
/-- info: A+b -/
#guard_msgs in #latex translateSet A b
/-- info: \left|\left(A \cup (A+b)\right) \cap [1,N]\right| -/
#guard_msgs in #latex unionTranslateCount A b N
/-- info: \Sigma_{h}(B_{d}) -/
#guard_msgs in #latex hSumset h (constructB d)
/-- info: \left|(A+s) \cap C\right| -/
#guard_msgs in #latex hitCount A C s
/-- info: L_{m} -/
#guard_msgs in #latex shiftL m
/-- info: \omega_{M} -/
#guard_msgs in #latex omegaPrim M
/-- info: \mathcal{B}_{N}(a, A, C) -/
#guard_msgs in #latex shiftBilinForm a A C N
/-- info: \mathcal{M}_{N,m + 1}(a) -/
#guard_msgs in #latex maxPolyOnGrid a N (m + 1) (Nat.succ_pos m)
/-- info: S_{m}(d) -/
#guard_msgs in #latex d.shifts m

variable {G H : Type*}
variable [Group G] [Group H]
variable (φ : MonoidHom G H)

/-- info: G / \ker(\varphi) \cong \operatorname{im}(\varphi) -/
#guard_msgs in #latex QuotientGroup.quotientKerEquivRange φ
/-- info: \ker(\varphi) -/
#guard_msgs in #latex MonoidHom.ker φ
/-- info: \operatorname{im}(\varphi) -/
#guard_msgs in #latex MonoidHom.range φ
