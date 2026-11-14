(module
  (memory (export "memory") 1 1)
  ;; Deliberately incorrect fixed-temperature, uniform predictor.
  (func (export "solve") (param $in i32) (param $n i32) (param $out i32)
        (param $cap i32) (result i32)
    (local $i i32) (local $uniform f64)
    (local.set $uniform (f64.div (f64.const 1) (f64.load offset=16 (local.get $in))))
    (f64.store (local.get $out) (f64.const 1))
    (local.set $i (i32.const 1))
    (block $done
      (loop $next
        (br_if $done (i32.ge_u (local.get $i) (local.get $cap)))
        (f64.store
          (i32.add (local.get $out) (i32.shl (local.get $i) (i32.const 3)))
          (local.get $uniform))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $next)))
    (local.get $cap)))
