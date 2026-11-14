(module
  ;; Split-conformal prediction intervals (Vovk / Lei et al. split-conformal
  ;; rank rule; applicability check against arXiv:2107.07511).
  ;;
  ;; No imports. Exports:
  ;;   - memory: one page
  ;;   - solve(input_ptr, input_count, output_ptr, output_capacity) -> i32
  ;;
  ;; f64 input array layout (integer header, then two blocks):
  ;;   [0] r : number of nonnegative calibration residuals (1..128)
  ;;   [1] p : coverage numerator   (0 < p < q)
  ;;   [2] q : coverage denominator (p < q <= 1000)
  ;;   [3] m : number of test predictions (1..128)
  ;;   [4 .. 4+r-1]       : r calibration residuals (>= 0)
  ;;   [4+r .. 4+r+m-1]   : m precomputed point predictions
  ;;
  ;; Method:
  ;;   Sort the r residuals ascending. The conformal rank (one-indexed) is
  ;;     rank = ceil((r+1) * p / q).
  ;;   If rank == r+1 the prediction set is all of R: report found = 0 with
  ;;   zero endpoint placeholders. Otherwise the symmetric radius is the
  ;;   rank-th smallest residual, sorted[rank-1], and each test interval is
  ;;   [pred - radius, pred + radius].
  ;;
  ;; f64 output array layout:
  ;;   [0]          : found (1.0 if a finite set, 0.0 if all of R)
  ;;   [1]          : rank (one-indexed)
  ;;   [2 .. ]      : lo1, hi1, lo2, hi2, ... (m pairs); all zero when found=0
  ;;
  ;; Return value: number of f64 values written (2 + 2*m), or a NEGATIVE
  ;; count on malformed input.
  ;;
  ;; Sorting scratch lives in a private region well above the host numeric
  ;; buffers (byte offset 32768) and is never exposed through I/O.

  (memory (export "memory") 1)

  (global $SCRATCH i32 (i32.const 32768)) ;; byte offset of private sort buffer

  ;; Load an f64 from `base` (byte pointer) at element index `idx`.
  (func $load (param $base i32) (param $idx i32) (result f64)
    (f64.load (i32.add (local.get $base) (i32.shl (local.get $idx) (i32.const 3))))
  )

  ;; Store an f64 to the scratch region at element index `idx`.
  (func $sstore (param $idx i32) (param $v f64)
    (f64.store
      (i32.add (global.get $SCRATCH) (i32.shl (local.get $idx) (i32.const 3)))
      (local.get $v))
  )

  ;; Load an f64 from the scratch region at element index `idx`.
  (func $sload (param $idx i32) (result f64)
    (f64.load
      (i32.add (global.get $SCRATCH) (i32.shl (local.get $idx) (i32.const 3))))
  )

  ;; Return 1 if x is an exact non-negative integer encoded as f64, else 0.
  (func $is_int (param $x f64) (result i32)
    (if (f64.ne (local.get $x) (local.get $x)) (then (return (i32.const 0)))) ;; NaN
    (if (f64.ne (local.get $x) (f64.floor (local.get $x)))
      (then (return (i32.const 0))))
    (i32.const 1)
  )

  ;; -------------------------------------------------------------------------
  ;; solve
  ;; -------------------------------------------------------------------------
  (func (export "solve")
        (param $input_ptr i32) (param $input_count i32)
        (param $output_ptr i32) (param $output_capacity i32)
        (result i32)
    (local $r i32)
    (local $p i32)
    (local $q i32)
    (local $m i32)
    (local $rf f64)
    (local $pf f64)
    (local $qf f64)
    (local $mf f64)
    (local $expected i32)
    (local $out_need i32)
    (local $rank i32)       ;; one-indexed conformal rank
    (local $i i32)
    (local $j i32)
    (local $resbase i32)    ;; element index of first residual in input
    (local $predbase i32)   ;; element index of first prediction in input
    (local $v f64)
    (local $key f64)
    (local $radius f64)
    (local $pred f64)
    (local $slot i32)
    (local $found i32)

    ;; --- Header presence ---
    (if (i32.lt_s (local.get $input_count) (i32.const 4))
      (then (return (i32.const -1))))

    ;; Header must be exact integers.
    (local.set $rf (call $load (local.get $input_ptr) (i32.const 0)))
    (local.set $pf (call $load (local.get $input_ptr) (i32.const 1)))
    (local.set $qf (call $load (local.get $input_ptr) (i32.const 2)))
    (local.set $mf (call $load (local.get $input_ptr) (i32.const 3)))
    (if (i32.eqz (call $is_int (local.get $rf))) (then (return (i32.const -1))))
    (if (i32.eqz (call $is_int (local.get $pf))) (then (return (i32.const -1))))
    (if (i32.eqz (call $is_int (local.get $qf))) (then (return (i32.const -1))))
    (if (i32.eqz (call $is_int (local.get $mf))) (then (return (i32.const -1))))

    (local.set $r (i32.trunc_f64_s (local.get $rf)))
    (local.set $p (i32.trunc_f64_s (local.get $pf)))
    (local.set $q (i32.trunc_f64_s (local.get $qf)))
    (local.set $m (i32.trunc_f64_s (local.get $mf)))

    ;; --- Bounds ---
    (if (i32.lt_s (local.get $r) (i32.const 1)) (then (return (i32.const -1))))
    (if (i32.gt_s (local.get $r) (i32.const 128)) (then (return (i32.const -1))))
    (if (i32.lt_s (local.get $m) (i32.const 1)) (then (return (i32.const -1))))
    (if (i32.gt_s (local.get $m) (i32.const 128)) (then (return (i32.const -1))))
    ;; 0 < p < q <= 1000
    (if (i32.lt_s (local.get $p) (i32.const 1)) (then (return (i32.const -1))))
    (if (i32.ge_s (local.get $p) (local.get $q)) (then (return (i32.const -1))))
    (if (i32.gt_s (local.get $q) (i32.const 1000)) (then (return (i32.const -1))))

    ;; --- Exact element count: 4 + r + m ---
    (local.set $expected
      (i32.add (i32.const 4) (i32.add (local.get $r) (local.get $m))))
    (if (i32.ne (local.get $input_count) (local.get $expected))
      (then (return (i32.const -1))))

    ;; --- Output capacity: 2 + 2*m ---
    (local.set $out_need
      (i32.add (i32.const 2) (i32.mul (i32.const 2) (local.get $m))))
    (if (i32.lt_s (local.get $output_capacity) (local.get $out_need))
      (then (return (i32.const -1))))

    ;; --- Residuals must be finite and nonnegative; copy into scratch ---
    (local.set $resbase (i32.const 4))
    (local.set $predbase (i32.add (i32.const 4) (local.get $r)))
    (local.set $i (i32.const 0))
    (block $copy_done
      (loop $copy
        (br_if $copy_done (i32.ge_s (local.get $i) (local.get $r)))
        (local.set $v
          (call $load (local.get $input_ptr)
            (i32.add (local.get $resbase) (local.get $i))))
        ;; reject NaN / infinity
        (if (f64.ne (local.get $v) (local.get $v)) (then (return (i32.const -1))))
        (if (f64.eq (f64.abs (local.get $v)) (f64.const inf))
          (then (return (i32.const -1))))
        ;; reject negative residuals
        (if (f64.lt (local.get $v) (f64.const 0.0)) (then (return (i32.const -1))))
        (call $sstore (local.get $i) (local.get $v))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $copy)))

    ;; --- Insertion sort of scratch[0..r) ascending (stable, ties kept) ---
    (local.set $i (i32.const 1))
    (block $sort_done
      (loop $sort
        (br_if $sort_done (i32.ge_s (local.get $i) (local.get $r)))
        (local.set $key (call $sload (local.get $i)))
        (local.set $j (i32.sub (local.get $i) (i32.const 1)))
        (block $shift_done
          (loop $shift
            (br_if $shift_done (i32.lt_s (local.get $j) (i32.const 0)))
            ;; shift while scratch[j] > key (strict: equal keys stay in order)
            (br_if $shift_done
              (f64.le (call $sload (local.get $j)) (local.get $key)))
            (call $sstore (i32.add (local.get $j) (i32.const 1))
              (call $sload (local.get $j)))
            (local.set $j (i32.sub (local.get $j) (i32.const 1)))
            (br $shift)))
        (call $sstore (i32.add (local.get $j) (i32.const 1)) (local.get $key))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $sort)))

    ;; --- rank = ceil((r+1)*p/q) via integer arithmetic ---
    ;;     ceil(a/b) = (a + b - 1) / b for positive integers.
    ;;     a = (r+1)*p, b = q. All fit in i32 (<= 129*1000).
    (local.set $rank
      (i32.div_s
        (i32.sub
          (i32.add
            (i32.mul (i32.add (local.get $r) (i32.const 1)) (local.get $p))
            (local.get $q))
          (i32.const 1))
        (local.get $q)))

    ;; --- Decide finite set vs. all of R ---
    ;; rank is in [1, r+1]; rank == r+1 means the set is all of R.
    (if (i32.eq (local.get $rank) (i32.add (local.get $r) (i32.const 1)))
      (then
        (local.set $found (i32.const 0)))
      (else
        (local.set $found (i32.const 1))))

    ;; --- Emit header ---
    (f64.store (local.get $output_ptr) (f64.convert_i32_s (local.get $found)))
    (f64.store
      (i32.add (local.get $output_ptr) (i32.const 8))
      (f64.convert_i32_s (local.get $rank)))

    ;; --- Emit interval pairs ---
    (if (i32.eqz (local.get $found))
      (then
        ;; all-of-R: zero placeholders
        (local.set $i (i32.const 0))
        (block $zero_done
          (loop $zero
            (br_if $zero_done
              (i32.ge_s (local.get $i) (i32.mul (i32.const 2) (local.get $m))))
            (f64.store
              (i32.add (local.get $output_ptr)
                (i32.shl (i32.add (i32.const 2) (local.get $i)) (i32.const 3)))
              (f64.const 0.0))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $zero))))
      (else
        ;; radius = sorted residual at rank-1 (zero-based)
        (local.set $radius (call $sload (i32.sub (local.get $rank) (i32.const 1))))
        (local.set $i (i32.const 0))
        (block $pair_done
          (loop $pair
            (br_if $pair_done (i32.ge_s (local.get $i) (local.get $m)))
            (local.set $pred
              (call $load (local.get $input_ptr)
                (i32.add (local.get $predbase) (local.get $i))))
            ;; lo slot = 2 + 2*i ; hi slot = 3 + 2*i
            (local.set $slot (i32.add (i32.const 2) (i32.mul (i32.const 2) (local.get $i))))
            (f64.store
              (i32.add (local.get $output_ptr) (i32.shl (local.get $slot) (i32.const 3)))
              (f64.sub (local.get $pred) (local.get $radius)))
            (f64.store
              (i32.add (local.get $output_ptr)
                (i32.shl (i32.add (local.get $slot) (i32.const 1)) (i32.const 3)))
              (f64.add (local.get $pred) (local.get $radius)))
            (local.set $i (i32.add (local.get $i) (i32.const 1)))
            (br $pair)))))

    (local.get $out_need)
  )
)
