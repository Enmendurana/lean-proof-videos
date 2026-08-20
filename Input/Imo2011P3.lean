/-
Copyright (c) 2021 David Renshaw. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: David Renshaw
-/

import Mathlib.Data.Real.Basic
import Mathlib.Tactic.Linarith

/-!
# International Mathematical Olympiad 2011, Problem 3

Let f : ℝ → ℝ be a function that satisfies

   f(x + y) ≤ y * f(x) + f(f(x))

for all x and y. Prove that f(x) = 0 for all x ≤ 0.
-/

namespace Imo2011P3

theorem imo2011_p3 (f : ℝ → ℝ) (hf : ∀ x y, f (x + y) ≤ y * f x + f (f x)) :
    ∀ x ≤ 0, f x = 0 := by
  -- Direct translation of the solution found in
  -- https://www.imo-official.org/problems/IMO2011SL.pdf

  -- reparameterize
  replace hf : ∀ x t, f t ≤ t * f x - x * f x + f (f x) := by
    intro x t
    have harg : f t = f (x + (t - x)) := by
      rw [add_eq_of_eq_sub' rfl]
    have hspec : f (x + (t - x)) ≤ (t - x) * f x + f (f x) :=
      hf x (t - x)
    have halg : (t - x) * f x + f (f x) = t * f x - x * f x + f (f x) := by
      rw [sub_mul]
    calc
      f t = f (x + (t - x)) := harg
      _ ≤ (t - x) * f x + f (f x) := hspec
      _ = t * f x - x * f x + f (f x) := halg

  have f_of_neg : ∀ x < 0, 0 ≤ f x := by
    intro x hx
    have h_outer := hf (2 * f x) (f x)
    have h_inner := hf x (f (2 * f x))
    have hprod : x * f x ≤ 0 := by
      nlinarith [h_outer, h_inner]
    nlinarith [hprod]

  have f_nonpos : ∀ x, f x ≤ 0 := by
    intro x
    by_contra! hp
    -- If we choose a small enough argument for f, then we get a contradiction.
    let s := (x * f x - f (f x)) / (f x)
    let a := min 0 s - 1
    have ha_lt_s : a < s := by
      dsimp [a]
      exact (sub_one_lt _).trans_le (min_le_right 0 s)
    have ha_neg : a < 0 := by
      dsimp [a]
      exact (sub_one_lt _).trans_le (min_le_left 0 s)
    have h_at_a : f a ≤ a * f x - x * f x + f (f x) := hf x a
    have hmul : a * f x < s * f x := (mul_lt_mul_iff_left₀ hp).mpr ha_lt_s
    have htail :
        a * f x - x * f x + f (f x) < s * f x - x * f x + f (f x) := by
      linarith [hmul]
    have hzero : s * f x - x * f x + f (f x) = 0 := by
      rw [(eq_div_iff hp.ne.symm).mp rfl]
      linarith
    have hfa_neg : f a < 0 := by
      calc
        f a ≤ a * f x - x * f x + f (f x) := h_at_a
        _ < s * f x - x * f x + f (f x) := htail
        _ = 0 := hzero
    have hfa_nn : 0 ≤ f a := f_of_neg a ha_neg
    exact (not_lt_of_ge hfa_nn) hfa_neg

  replace f_of_neg : ∀ x < 0, f x = 0 := by
    intro x hx
    have hle : f x ≤ 0 := f_nonpos x
    have hge : 0 ≤ f x := f_of_neg x hx
    exact hle.antisymm hge

  intro x hx
  obtain (h_x_neg : x < 0) | (rfl : x = 0) := hx.lt_or_eq
  · exact f_of_neg _ h_x_neg
  · have hle0 : f 0 ≤ 0 := f_nonpos 0
    have hno : f (-1) = 0 := f_of_neg (-1) neg_one_lt_zero
    have hp := hf (-1) (-1)
    have hge0 : 0 ≤ f 0 := by
      rw [hno, mul_zero, sub_zero, zero_add] at hp
      exact hp
    exact hle0.antisymm hge0
