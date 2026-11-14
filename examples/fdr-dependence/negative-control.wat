(module
  ;; NEGATIVE CONTROL for the BH / BY FDR module.
  ;;
  ;; Deliberately faulty: the Benjamini-Hochberg count is computed correctly,
  ;; but the Benjamini-Yekutieli count OMITS the harmonic divisor H_m.  BY is
  ;; supposed to use the per-test budget alpha / H_m (H_m = sum_{j=1..m} 1/j),
  ;; which for m > 1 is strictly smaller than alpha and therefore yields at
  ;; most as many rejections as BH.  Here BY reuses the plain alpha budget, so
  ;; it collapses onto the BH count.  Whenever the two procedures disagree
  ;; (i.e. H_m > 1 and some rank qualifies for BH but not BY) this control
  ;; reports a numeric mismatch, which is the informative outcome.
  ;;
  ;; Same ABI as the candidate: zero imports, one 64 KiB page, exports memory
  ;; and solve(i32,i32,i32,i32)->i32.  Output is [BH, BY]; return 2 or a
  ;; negative count on malformed input.

  (memory (export "memory") 1 1)

  (global $SCRATCH i32 (i32.const 32768))

  (func $load (param $base i32) (param $idx i32) (result f64)
    (f64.load (i32.add (local.get $base) (i32.shl (local.get $idx) (i32.const 3))))
  )
  (func $sstore (param $idx i32) (param $v f64)
    (f64.store
      (i32.add (global.get $SCRATCH) (i32.shl (local.get $idx) (i32.const 3)))
      (local.get $v))
  )
  (func $sload (param $idx i32) (result f64)
    (f64.load
      (i32.add (global.get $SCRATCH) (i32.shl (local.get $idx) (i32.const 3))))
  )
  (func $is_finite (param $x f64) (result i32)
    (if (f64.ne (local.get $x) (local.get $x)) (then (return (i32.const 0))))
    (if (f64.eq (f64.abs (local.get $x)) (f64.const inf))
      (then (return (i32.const 0))))
    (i32.const 1)
  )

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
    (local $bh i32)
    (local $by i32)

    (if (i32.lt_s (local.get $input_count) (i32.const 2))
      (then (return (i32.const -1))))
    (local.set $m (i32.sub (local.get $input_count) (i32.const 1)))
    (if (i32.lt_s (local.get $m) (i32.const 1)) (then (return (i32.const -1))))
    (if (i32.gt_s (local.get $m) (i32.const 256)) (then (return (i32.const -1))))
    (if (i32.lt_s (local.get $output_capacity) (i32.const 2))
      (then (return (i32.const -1))))

    (local.set $alpha (call $load (local.get $input_ptr) (i32.const 0)))
    (if (i32.eqz (call $is_finite (local.get $alpha)))
      (then (return (i32.const -1))))
    (if (f64.le (local.get $alpha) (f64.const 0.0)) (then (return (i32.const -1))))
    (if (f64.ge (local.get $alpha) (f64.const 1.0)) (then (return (i32.const -1))))

    (local.set $i (i32.const 0))
    (block $copy_done
      (loop $copy
        (br_if $copy_done (i32.ge_s (local.get $i) (local.get $m)))
        (local.set $v
          (call $load (local.get $input_ptr)
            (i32.add (local.get $i) (i32.const 1))))
        (if (i32.eqz (call $is_finite (local.get $v)))
          (then (return (i32.const -1))))
        (if (f64.lt (local.get $v) (f64.const 0.0)) (then (return (i32.const -1))))
        (if (f64.gt (local.get $v) (f64.const 1.0)) (then (return (i32.const -1))))
        (call $sstore (local.get $i) (local.get $v))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $copy)))

    (local.set $i (i32.const 1))
    (block $sort_done
      (loop $sort
        (br_if $sort_done (i32.ge_s (local.get $i) (local.get $m)))
        (local.set $key (call $sload (local.get $i)))
        (local.set $j (i32.sub (local.get $i) (i32.const 1)))
        (block $shift_done
          (loop $shift
            (br_if $shift_done (i32.lt_s (local.get $j) (i32.const 0)))
            (br_if $shift_done
              (f64.le (call $sload (local.get $j)) (local.get $key)))
            (call $sstore (i32.add (local.get $j) (i32.const 1))
              (call $sload (local.get $j)))
            (local.set $j (i32.sub (local.get $j) (i32.const 1)))
            (br $shift)))
        (call $sstore (i32.add (local.get $j) (i32.const 1)) (local.get $key))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $sort)))

    ;; BH: correct per-test budget alpha.
    (local.set $bh (call $cutoff (local.get $m) (local.get $alpha)))
    ;; BY: FAULTY -- harmonic divisor H_m omitted, reuses alpha budget.
    (local.set $by (call $cutoff (local.get $m) (local.get $alpha)))

    (f64.store (local.get $output_ptr) (f64.convert_i32_s (local.get $bh)))
    (f64.store
      (i32.add (local.get $output_ptr) (i32.const 8))
      (f64.convert_i32_s (local.get $by)))

    (i32.const 2)
  )
)
