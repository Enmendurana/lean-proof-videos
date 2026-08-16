import Mathlib

theorem cinematic_square (a b : ℝ) (h : a = b) : (a + 0) ^ 2 = b ^ 2 := by
  rw [add_zero]
  rw [h]
