(module
  ;; Benjamini-Hochberg (BH) and Benjamini-Yekutieli (BY) multiple-testing
  ;; rejection counts (applicability / matched-reproduction check for the
  ;; step-up FDR procedures of Benjamini & Hochberg 1995 and Benjamini &
  ;; Yekutieli 2001).
  ;;
  ;; No imports. Exports:
  ;;   - memory: one 64 KiB page
  ;;   - solve(input_ptr, input_count, output_ptr, output_capacity) -> i32
  ;;
  ;; f64 input array layout:
  ;;   [0]            : alpha  (0 < alpha < 1, finite)
  ;;   [1 .. m]       : p_1 .. p_m  (finite, each in [0, 1]), m = input_count-1
  ;; m must satisfy 1 <= m <= 256.
  ;;
  ;; Method (step-up):
  ;;   Sort a private scratch copy of the m p-values ascending, giving the
  ;;   ordered values p_(1) <= ... <= p_(m).  For a per-test budget `thr`
  ;;   (thr = alpha for BH; thr = alpha / H_m for BY, with harmonic number
  ;;   H_m = sum_{j=1..m} 1/j), the largest one-indexed rank k satisfying
  ;;       p_(k) <= thr * k / m
  ;;   is the BH / BY cutoff.  ALL ranks 1..k are rejected, so the rejection
  ;;   count equals that maximal qualifying rank (0 if none qualifies).
  ;;
  ;;   The test is applied as the division-free, overflow-safe form
  ;;       p_(k) * m <= thr * k
  ;;   so no intermediate quotient loses precision.  thr is itself computed
  ;;   once; for BY, H_m is accumulated in f64 ascending (1,2,...,m) which is
  ;;   the standard, well-conditioned summation order for a harmonic series.
  ;;
  ;; f64 output array layout (exactly two values):
  ;;   [0] : rejection_count_BH
  ;;   [1] : rejection_count_BY
  ;;
  ;; Return value: 2 on success, or a NEGATIVE count on malformed input.
  ;;
  ;; The sorting scratch lives in a private region (byte offset 32768) well
  ;; above the host input/output buffers and is never aliased with them:
  ;; with input_count <= 257 and output immediately after, the host region
  ;; ends far below 32768 (257 * 8 = 2056 bytes), so there is no overlap.

  (memory (export "memory") 1 1)

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

  ;; Return 1 if x is finite (not NaN, not +/-inf), else 0.
  (func $is_finite (param $x f64) (result i32)
    (if (f64.ne (local.get $x) (local.get $x)) (then (return (i32.const 0)))) ;; NaN
    (if (f64.eq (f64.abs (local.get $x)) (f64.const inf))
      (then (return (i32.const 0))))
    (i32.const 1)
  )

  ;; Largest one-indexed rank k with sorted[k-1] * m <= thr * k, else 0.
  ;; sorted[] lives in scratch, ascending, length m.  Scans downward so the
  ;; first qualifying rank encountered is the maximal one.
  (func $cutoff (param $m i32) (param $thr f64) (result i32)
    (local $k i32)
    (local $mf f64)
    (local.set $mf (f64.convert_i32_s (local.get $m)))
    (local.set $k (local.get $m))
    (block $done
      (loop $scan
        (br_if $done (i32.lt_s (local.get $k) (i32.const 1)))
        (if (f64.le
              (f64.mul (call $sload (i32.sub (local.get $k) (i32.const 1)))
                       (local.get $mf))
              (f64.mul (local.get $thr) (f64.convert_i32_s (local.get $k))))
          (then (return (local.get $k))))
        (local.set $k (i32.sub (local.get $k) (i32.const 1)))
        (br $scan)))
    (i32.const 0)
  )

  ;; -------------------------------------------------------------------------
  ;; solve
  ;; -------------------------------------------------------------------------
  (func (export "solve")
        (param $input_ptr i32) (param $input_count i32)
        (param $output_ptr i32) (param $output_capacity i32)
        (result i32)
    (local $m i32)
    (local $alpha f64)
    (local $i i32)
    (local $j i32)
    (local $v f64)
    (local $key f64)
    (local $harm f64)   ;; harmonic number H_m
    (local $bh i32)
    (local $by i32)

    ;; --- Need alpha header plus at least one p-value ---
    (if (i32.lt_s (local.get $input_count) (i32.const 2))
      (then (return (i32.const -1))))

    ;; m = input_count - 1, bounded 1..256
    (local.set $m (i32.sub (local.get $input_count) (i32.const 1)))
    (if (i32.lt_s (local.get $m) (i32.const 1)) (then (return (i32.const -1))))
    (if (i32.gt_s (local.get $m) (i32.const 256)) (then (return (i32.const -1))))

    ;; --- Output capacity: exactly two values required ---
    (if (i32.lt_s (local.get $output_capacity) (i32.const 2))
      (then (return (i32.const -1))))

    ;; --- alpha finite and strictly inside (0, 1) ---
    (local.set $alpha (call $load (local.get $input_ptr) (i32.const 0)))
    (if (i32.eqz (call $is_finite (local.get $alpha)))
      (then (return (i32.const -1))))
    (if (f64.le (local.get $alpha) (f64.const 0.0)) (then (return (i32.const -1))))
    (if (f64.ge (local.get $alpha) (f64.const 1.0)) (then (return (i32.const -1))))

    ;; --- Validate and copy p-values into scratch (input index i+1) ---
    (local.set $i (i32.const 0))
    (block $copy_done
      (loop $copy
        (br_if $copy_done (i32.ge_s (local.get $i) (local.get $m)))
        (local.set $v
          (call $load (local.get $input_ptr)
            (i32.add (local.get $i) (i32.const 1))))
        (if (i32.eqz (call $is_finite (local.get $v)))
          (then (return (i32.const -1))))
        ;; each p-value must lie in [0, 1]
        (if (f64.lt (local.get $v) (f64.const 0.0)) (then (return (i32.const -1))))
        (if (f64.gt (local.get $v) (f64.const 1.0)) (then (return (i32.const -1))))
        (call $sstore (local.get $i) (local.get $v))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $copy)))

    ;; --- Insertion sort scratch[0..m) ascending (handles unsorted + ties) ---
    (local.set $i (i32.const 1))
    (block $sort_done
      (loop $sort
        (br_if $sort_done (i32.ge_s (local.get $i) (local.get $m)))
        (local.set $key (call $sload (local.get $i)))
        (local.set $j (i32.sub (local.get $i) (i32.const 1)))
        (block $shift_done
          (loop $shift
            (br_if $shift_done (i32.lt_s (local.get $j) (i32.const 0)))
            ;; shift while scratch[j] > key
            (br_if $shift_done
              (f64.le (call $sload (local.get $j)) (local.get $key)))
            (call $sstore (i32.add (local.get $j) (i32.const 1))
              (call $sload (local.get $j)))
            (local.set $j (i32.sub (local.get $j) (i32.const 1)))
            (br $shift)))
        (call $sstore (i32.add (local.get $j) (i32.const 1)) (local.get $key))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $sort)))

    ;; --- Harmonic number H_m = sum_{j=1..m} 1/j (ascending accumulation) ---
    (local.set $harm (f64.const 0.0))
    (local.set $j (i32.const 1))
    (block $harm_done
      (loop $harm_loop
        (br_if $harm_done (i32.gt_s (local.get $j) (local.get $m)))
        (local.set $harm
          (f64.add (local.get $harm)
            (f64.div (f64.const 1.0) (f64.convert_i32_s (local.get $j)))))
        (local.set $j (i32.add (local.get $j) (i32.const 1)))
        (br $harm_loop)))

    ;; --- Cutoffs ---
    ;; BH budget per test: alpha.
    (local.set $bh (call $cutoff (local.get $m) (local.get $alpha)))
    ;; BY budget per test: alpha / H_m.
    (local.set $by
      (call $cutoff (local.get $m)
        (f64.div (local.get $alpha) (local.get $harm))))

    ;; --- Emit [rejection_count_BH, rejection_count_BY] ---
    (f64.store (local.get $output_ptr) (f64.convert_i32_s (local.get $bh)))
    (f64.store
      (i32.add (local.get $output_ptr) (i32.const 8))
      (f64.convert_i32_s (local.get $by)))

    (i32.const 2)
  )
)
