import Mathlib

open scoped BigOperators
open Finset Classical

private def testShiftL (m : ℕ) : ℕ := 128 * m ^ 3 + 1

/-- Compile-only replacement candidate for the previously hour-scale
`bad_min_count_lt` command.  It uses Mathlib's cardinality lemmas instead of
asking `aesop`/`grind` to rediscover the coordinate bijections. -/
lemma bad_min_count_lt_optimized (m : ℕ) (_hm : 20 ≤ m) :
    2 * (univ.filter (fun ω : Fin (testShiftL m) → Fin (2 ^ m) =>
      ∃ j, ¬(2 ^ m < ((ω j : ℕ) + 1) * (2 * testShiftL m + 2)))).card <
    Fintype.card (Fin (testShiftL m) → Fin (2 ^ m)) := by
  have h_bad_count : ∀ j : Fin (testShiftL m),
      (univ.filter (fun t : Fin (2 ^ m) =>
        (t : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2))).card ≤
      2 ^ m / (2 * testShiftL m + 2) := by
    intro _j
    simpa only [Nat.lt_iff_add_one_le] using
      (show (univ.filter (fun t : Fin (2 ^ m) =>
        (t : ℕ) < 2 ^ m / (2 * testShiftL m + 2))).card ≤
          2 ^ m / (2 * testShiftL m + 2) from by
        rw [Fin.card_filter_val_lt]
        exact min_le_right _ _)
  have h_union_bound :
      (univ.filter (fun ω : Fin (testShiftL m) → Fin (2 ^ m) =>
        ∃ j, (ω j : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2))).card ≤
      testShiftL m * (2 ^ m / (2 * testShiftL m + 2)) *
        (2 ^ m) ^ (testShiftL m - 1) := by
    have h_union_bound :
        (univ.filter (fun ω : Fin (testShiftL m) → Fin (2 ^ m) =>
          ∃ j, (ω j : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2))).card ≤
        ∑ j : Fin (testShiftL m),
          (univ.filter (fun t : Fin (2 ^ m) =>
            (t : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2))).card *
            (2 ^ m) ^ (testShiftL m - 1) := by
      have h_coordinate : ∀ j : Fin (testShiftL m),
          (univ.filter (fun ω : Fin (testShiftL m) → Fin (2 ^ m) =>
            (ω j : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2))).card ≤
          (univ.filter (fun t : Fin (2 ^ m) =>
            (t : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2))).card *
            (2 ^ m) ^ (testShiftL m - 1) := by
        intro j
        let bad := univ.filter (fun t : Fin (2 ^ m) =>
          (t : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2))
        have h_fiber (t : Fin (2 ^ m)) :
            (univ.filter (fun ω : Fin (testShiftL m) → Fin (2 ^ m) =>
              ω j = t)).card = (2 ^ m) ^ (testShiftL m - 1) := by
          simpa using Fintype.card_filter_piFinset_const_eq_of_mem
            (univ : Finset (Fin (2 ^ m))) j (mem_univ t)
        rw [show univ.filter (fun ω : Fin (testShiftL m) → Fin (2 ^ m) =>
            (ω j : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2)) =
            bad.biUnion (fun t => univ.filter fun ω => ω j = t) by
          ext ω
          simp [bad]]
        calc
          _ ≤ ∑ t ∈ bad,
              (univ.filter (fun ω : Fin (testShiftL m) → Fin (2 ^ m) =>
                ω j = t)).card := card_biUnion_le
          _ = bad.card * (2 ^ m) ^ (testShiftL m - 1) := by
            simp_rw [h_fiber]
            simp
      refine le_trans ?_ (sum_le_sum fun j _ => h_coordinate j)
      rw [show univ.filter (fun ω : Fin (testShiftL m) → Fin (2 ^ m) =>
          ∃ j, (ω j : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2)) =
          univ.biUnion fun j => univ.filter fun ω :
            Fin (testShiftL m) → Fin (2 ^ m) =>
            (ω j : ℕ) + 1 ≤ 2 ^ m / (2 * testShiftL m + 2) by
        ext
        simp]
      exact card_biUnion_le
    refine le_trans h_union_bound ?_
    rw [← sum_mul]
    gcongr
    convert sum_le_sum fun i (_hi : i ∈ univ) => h_bad_count i
    norm_num
  have h_simplify :
      2 * testShiftL m * (2 ^ m / (2 * testShiftL m + 2)) *
          (2 ^ m) ^ (testShiftL m - 1) <
        (2 ^ m) ^ testShiftL m := by
    have h_simplify :
        2 * testShiftL m * (2 ^ m / (2 * testShiftL m + 2)) < 2 ^ m := by
      nlinarith [
        Nat.div_mul_le_self (2 ^ m) (2 * testShiftL m + 2),
        Nat.one_le_pow m 2 zero_lt_two,
        show testShiftL m > 0 from by unfold testShiftL; positivity
      ]
    convert mul_lt_mul_of_pos_right h_simplify
      (pow_pos (pow_pos (zero_lt_two' ℕ) m) (testShiftL m - 1)) using 1
    rw [← pow_succ', Nat.sub_add_cancel (show 1 ≤ testShiftL m from by
      unfold testShiftL
      omega)]
  convert lt_of_le_of_lt (Nat.mul_le_mul_left 2 h_union_bound) _ using 1
  · norm_num [Nat.le_div_iff_mul_le (by positivity : 0 < 2 * testShiftL m + 2)]
  · convert h_simplify using 1
    ring
    norm_num [Fintype.card_pi]
