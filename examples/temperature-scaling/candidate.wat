(module
  ;; Temperature Scaling (Guo et al., arXiv:1706.04599v2, section 4.2)
  ;;
  ;; No imports. Exports:
  ;;   - memory: exactly one page
  ;;   - solve(input_ptr, input_count, output_ptr, output_capacity) -> i32
  ;;
  ;; f64 input array layout:
  ;;   [0] n   : fit sample count
  ;;   [1] m   : test sample count
  ;;   [2] k   : class count
  ;;   next n rows : k logits followed by a zero-based label  (k+1 values each)
  ;;   next m rows : k logits each                            (k   values each)
  ;;
  ;; f64 output array layout:
  ;;   [0]     : chosen positive temperature T
  ;;   [1..]   : m*k normalized probabilities, row-major
  ;;
  ;; Return value: number of f64 values written to output, or 0 on failure.

  (memory (export "memory") 1)

  ;; -------------------------------------------------------------------------
  ;; exp(x) for x <= 0.
  ;; Strategy: if x <= -745, underflow -> 0. Otherwise range-reduce by halving
  ;; x r times until |x| is small, evaluate Taylor series, then square r times.
  ;; -------------------------------------------------------------------------
  (func $exp_nonpos (param $x f64) (result f64)
    (local $r i32)
    (local $t f64)      ;; reduced argument
    (local $term f64)   ;; current Taylor term
    (local $sum f64)    ;; accumulated series
    (local $i f64)      ;; term index as f64

    ;; underflow guard (checked before the reduction loop)
    (if (f64.le (local.get $x) (f64.const -745.0))
      (then (return (f64.const 0.0))))

    ;; x == 0 -> 1
    (if (f64.ge (local.get $x) (f64.const 0.0))
      (then (return (f64.const 1.0))))

    ;; Range reduction: halve until t > -0.5
    (local.set $t (local.get $x))
    (local.set $r (i32.const 0))
    (block $done
      (loop $halve
        (br_if $done (f64.gt (local.get $t) (f64.const -0.5)))
        (local.set $t (f64.mul (local.get $t) (f64.const 0.5)))
        (local.set $r (i32.add (local.get $r) (i32.const 1)))
        (br $halve)))

    ;; Taylor series: exp(t) = sum_{j>=0} t^j / j!
    (local.set $sum (f64.const 1.0))
    (local.set $term (f64.const 1.0))
    (local.set $i (f64.const 1.0))
    (block $tdone
      (loop $taylor
        ;; term *= t / i
        (local.set $term
          (f64.div (f64.mul (local.get $term) (local.get $t)) (local.get $i)))
        (local.set $sum (f64.add (local.get $sum) (local.get $term)))
        ;; stop when term is negligible
        (br_if $tdone
          (f64.lt (f64.abs (local.get $term)) (f64.const 1e-18)))
        (local.set $i (f64.add (local.get $i) (f64.const 1.0)))
        ;; safety bound on iterations
        (br_if $tdone (f64.gt (local.get $i) (f64.const 100.0)))
        (br $taylor)))

    ;; Square r times to undo halving
    (block $sdone
      (loop $square
        (br_if $sdone (i32.eqz (local.get $r)))
        (local.set $sum (f64.mul (local.get $sum) (local.get $sum)))
        (local.set $r (i32.sub (local.get $r) (i32.const 1)))
        (br $square)))

    (local.get $sum)
  )

  ;; -------------------------------------------------------------------------
  ;; Load an f64 from an array at element index `idx` (base is a byte pointer).
  ;; -------------------------------------------------------------------------
  (func $load (param $base i32) (param $idx i32) (result f64)
    (f64.load (i32.add (local.get $base) (i32.shl (local.get $idx) (i32.const 3))))
  )

  ;; -------------------------------------------------------------------------
  ;; Fit product of true-label probabilities for a given T.
  ;; For each of the n fit rows: max-shifted softmax over k logits/T, take the
  ;; probability of the true label, multiply across rows. Larger == lower NLL.
  ;; -------------------------------------------------------------------------
  (func $fit_product (param $base i32) (param $n i32) (param $k i32) (param $T f64)
        (result f64)
    (local $row i32)
    (local $rowbase i32)   ;; element index of first logit in the row
    (local $stride i32)    ;; k+1
    (local $j i32)
    (local $maxv f64)
    (local $z f64)         ;; logit / T
    (local $sum f64)
    (local $label i32)
    (local $labelz f64)
    (local $prod f64)

    (local.set $stride (i32.add (local.get $k) (i32.const 1)))
    (local.set $prod (f64.const 1.0))
    (local.set $row (i32.const 0))

    (block $rows_done
      (loop $rows
        (br_if $rows_done (i32.ge_s (local.get $row) (local.get $n)))

        ;; rowbase = 3 + row * stride
        (local.set $rowbase
          (i32.add (i32.const 3) (i32.mul (local.get $row) (local.get $stride))))

        ;; label = (i32) input[rowbase + k]
        (local.set $label
          (i32.trunc_f64_s
            (call $load (local.get $base) (i32.add (local.get $rowbase) (local.get $k)))))

        ;; find max of logits/T
        (local.set $maxv (f64.const -1e308))
        (local.set $j (i32.const 0))
        (block $max_done
          (loop $max_loop
            (br_if $max_done (i32.ge_s (local.get $j) (local.get $k)))
            (local.set $z
              (f64.div
                (call $load (local.get $base) (i32.add (local.get $rowbase) (local.get $j)))
                (local.get $T)))
            (if (f64.gt (local.get $z) (local.get $maxv))
              (then (local.set $maxv (local.get $z))))
            (local.set $j (i32.add (local.get $j) (i32.const 1)))
            (br $max_loop)))

        ;; labelz = logit[label]/T
        (local.set $labelz
          (f64.div
            (call $load (local.get $base) (i32.add (local.get $rowbase) (local.get $label)))
            (local.get $T)))

        ;; sum of exp(z - max)
        (local.set $sum (f64.const 0.0))
        (local.set $j (i32.const 0))
        (block $sum_done
          (loop $sum_loop
            (br_if $sum_done (i32.ge_s (local.get $j) (local.get $k)))
            (local.set $z
              (f64.div
                (call $load (local.get $base) (i32.add (local.get $rowbase) (local.get $j)))
                (local.get $T)))
            (local.set $sum
              (f64.add (local.get $sum)
                (call $exp_nonpos (f64.sub (local.get $z) (local.get $maxv)))))
            (local.set $j (i32.add (local.get $j) (i32.const 1)))
            (br $sum_loop)))

        ;; p_label = exp(labelz - max) / sum ; multiply into prod
        (local.set $prod
          (f64.mul (local.get $prod)
            (f64.div
              (call $exp_nonpos (f64.sub (local.get $labelz) (local.get $maxv)))
              (local.get $sum))))

        (local.set $row (i32.add (local.get $row) (i32.const 1)))
        (br $rows)))

    (local.get $prod)
  )

  ;; -------------------------------------------------------------------------
  ;; solve
  ;; -------------------------------------------------------------------------
  (func (export "solve")
        (param $input_ptr i32) (param $input_count i32)
        (param $output_ptr i32) (param $output_capacity i32)
        (result i32)
    (local $n i32)
    (local $m i32)
    (local $k i32)
    (local $expected i32)   ;; expected input element count
    (local $out_need i32)   ;; required output element count

    (local $gi i32)         ;; grid index
    (local $T f64)          ;; candidate temperature
    (local $prod f64)
    (local $bestT f64)
    (local $bestP f64)
    (local $have i32)       ;; whether a best has been chosen

    (local $row i32)
    (local $rowbase i32)
    (local $j i32)
    (local $maxv f64)
    (local $z f64)
    (local $sum f64)
    (local $outbase i32)    ;; element index in output for probabilities
    (local $label i32)
    (local $p f64)
    (local $slot i32)

    ;; --- Validate header presence ---
    (if (i32.lt_s (local.get $input_count) (i32.const 3))
      (then (return (i32.const 0))))

    (local.set $n (i32.trunc_f64_s (call $load (local.get $input_ptr) (i32.const 0))))
    (local.set $m (i32.trunc_f64_s (call $load (local.get $input_ptr) (i32.const 1))))
    (local.set $k (i32.trunc_f64_s (call $load (local.get $input_ptr) (i32.const 2))))

    ;; shape sanity: n>=1, m>=0, k>=1
    (if (i32.lt_s (local.get $n) (i32.const 1)) (then (return (i32.const 0))))
    (if (i32.lt_s (local.get $m) (i32.const 0)) (then (return (i32.const 0))))
    (if (i32.lt_s (local.get $k) (i32.const 1)) (then (return (i32.const 0))))

    ;; expected = 3 + n*(k+1) + m*k
    (local.set $expected
      (i32.add (i32.const 3)
        (i32.add
          (i32.mul (local.get $n) (i32.add (local.get $k) (i32.const 1)))
          (i32.mul (local.get $m) (local.get $k)))))
    (if (i32.ne (local.get $input_count) (local.get $expected))
      (then (return (i32.const 0))))

    ;; output needs 1 + m*k
    (local.set $out_need (i32.add (i32.const 1) (i32.mul (local.get $m) (local.get $k))))
    (if (i32.lt_s (local.get $output_capacity) (local.get $out_need))
      (then (return (i32.const 0))))

    ;; Validate all fit labels are in [0, k)
    (local.set $row (i32.const 0))
    (block $lbl_done
      (loop $lbl
        (br_if $lbl_done (i32.ge_s (local.get $row) (local.get $n)))
        (local.set $rowbase
          (i32.add (i32.const 3)
            (i32.mul (local.get $row) (i32.add (local.get $k) (i32.const 1)))))
        (local.set $label
          (i32.trunc_f64_s
            (call $load (local.get $input_ptr) (i32.add (local.get $rowbase) (local.get $k)))))
        (if (i32.lt_s (local.get $label) (i32.const 0))
          (then (return (i32.const 0))))
        (if (i32.ge_s (local.get $label) (local.get $k))
          (then (return (i32.const 0))))
        (local.set $row (i32.add (local.get $row) (i32.const 1)))
        (br $lbl)))

    ;; --- Grid search over T in {0.5, 1, 2, 4, 8} ---
    ;; Pick largest fit product (== lowest NLL); ties prefer smaller T.
    ;; Grid is ascending, so we only replace on strictly greater product.
    (local.set $have (i32.const 0))
    (local.set $bestT (f64.const 1.0))
    (local.set $bestP (f64.const 0.0))
    (local.set $gi (i32.const 0))

    (block $grid_done
      (loop $grid
        (br_if $grid_done (i32.ge_s (local.get $gi) (i32.const 5)))

        ;; T = 0.5 * 2^gi  ->  0.5, 1, 2, 4, 8
        (local.set $T
          (f64.mul (f64.const 0.5)
            (f64.convert_i32_s (i32.shl (i32.const 1) (local.get $gi)))))

        (local.set $prod
          (call $fit_product (local.get $input_ptr) (local.get $n) (local.get $k) (local.get $T)))

        (if (i32.eqz (local.get $have))
          (then
            (local.set $have (i32.const 1))
            (local.set $bestP (local.get $prod))
            (local.set $bestT (local.get $T)))
          (else
            (if (f64.gt (local.get $prod) (local.get $bestP))
              (then
                (local.set $bestP (local.get $prod))
                (local.set $bestT (local.get $T))))))

        (local.set $gi (i32.add (local.get $gi) (i32.const 1)))
        (br $grid)))

    ;; --- Emit output ---
    ;; output[0] = bestT
    (f64.store (local.get $output_ptr) (local.get $bestT))

    ;; element index of first logit of test block: 3 + n*(k+1)
    (local.set $rowbase
      (i32.add (i32.const 3)
        (i32.mul (local.get $n) (i32.add (local.get $k) (i32.const 1)))))

    ;; probabilities start at output element index 1
    (local.set $outbase (i32.const 1))
    (local.set $row (i32.const 0))

    (block $test_done
      (loop $test_rows
        (br_if $test_done (i32.ge_s (local.get $row) (local.get $m)))

        ;; find max of logits/T for this test row
        (local.set $maxv (f64.const -1e308))
        (local.set $j (i32.const 0))
        (block $tmax_done
          (loop $tmax
            (br_if $tmax_done (i32.ge_s (local.get $j) (local.get $k)))
            (local.set $z
              (f64.div
                (call $load (local.get $input_ptr) (i32.add (local.get $rowbase) (local.get $j)))
                (local.get $bestT)))
            (if (f64.gt (local.get $z) (local.get $maxv))
              (then (local.set $maxv (local.get $z))))
            (local.set $j (i32.add (local.get $j) (i32.const 1)))
            (br $tmax)))

        ;; compute exp(z-max), store raw into output, accumulate sum
        (local.set $sum (f64.const 0.0))
        (local.set $j (i32.const 0))
        (block $texp_done
          (loop $texp
            (br_if $texp_done (i32.ge_s (local.get $j) (local.get $k)))
            (local.set $z
              (f64.div
                (call $load (local.get $input_ptr) (i32.add (local.get $rowbase) (local.get $j)))
                (local.get $bestT)))
            (local.set $p (call $exp_nonpos (f64.sub (local.get $z) (local.get $maxv))))
            (local.set $sum (f64.add (local.get $sum) (local.get $p)))
            ;; store raw exp into output slot (outbase + j)
            (local.set $slot (i32.add (local.get $outbase) (local.get $j)))
            (f64.store
              (i32.add (local.get $output_ptr) (i32.shl (local.get $slot) (i32.const 3)))
              (local.get $p))
            (local.set $j (i32.add (local.get $j) (i32.const 1)))
            (br $texp)))

        ;; normalize in place (no temporary buffer): divide each slot by sum
        (local.set $j (i32.const 0))
        (block $tnorm_done
          (loop $tnorm
            (br_if $tnorm_done (i32.ge_s (local.get $j) (local.get $k)))
            (local.set $slot (i32.add (local.get $outbase) (local.get $j)))
            (f64.store
              (i32.add (local.get $output_ptr) (i32.shl (local.get $slot) (i32.const 3)))
              (f64.div
                (f64.load
                  (i32.add (local.get $output_ptr) (i32.shl (local.get $slot) (i32.const 3))))
                (local.get $sum)))
            (local.set $j (i32.add (local.get $j) (i32.const 1)))
            (br $tnorm)))

        ;; advance to next row
        (local.set $rowbase (i32.add (local.get $rowbase) (local.get $k)))
        (local.set $outbase (i32.add (local.get $outbase) (local.get $k)))
        (local.set $row (i32.add (local.get $row) (i32.const 1)))
        (br $test_rows)))

    ;; number of f64 values written
    (local.get $out_need)
  )
)
