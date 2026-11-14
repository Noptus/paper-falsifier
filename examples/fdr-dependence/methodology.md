# False-discovery control under dependence

## Research question

Does the Benjamini–Hochberg (BH) rule exceed its nominal false-discovery level on a controlled dependent all-null distribution, and does the Benjamini–Yekutieli (BY) adjustment prevent that excess? Wang's [Theorem 1 and Theorem 4](https://arxiv.org/abs/2201.09350v3) give the exact level under independent uniform null p-values and the harmonic upper bound under arbitrary valid dependent p-values. The experiment tests a derived boundary example and the implementation of both procedures. It does not reproduce the paper's proofs.

## Protocol and controls

The simulation freezes family sizes, nominal levels, independent unit, random seed, sample sizes, confidence adjustment and the single joint decision in [study-plan.json](study-plan.json). The WAT candidate is checked separately against exact rejection counts from SciPy on edge and seed-generated fixtures. The negative control omits BY's harmonic divisor. A numerical mismatch is required to show sensitivity. Timeouts, invalid output shapes and crashes cannot establish a useful control.

Under the global null, the false discovery rate is the probability of at least one rejection in a family. The independent control draws all p-values uniformly and independently. The two dependent distributions use a scale `g`. With probability `g/k` for each `k=1,...,m`, assign `g*k/m` to a uniformly rotated block of `k` coordinates and assign 1 elsewhere. Otherwise assign 1 to all coordinates. For any fixed coordinate, the cumulative mass at `g*j/m` is exactly `g*j/m` since each block includes that coordinate with probability `k/m`. Between support points the null p-value remains superuniform. Choose `g=0.99*alpha` for the near-BH-bound regime and `g=0.99*alpha/H_m` for the near-BY-bound regime. The factor 0.99 avoids threshold ambiguity in binary floating-point arithmetic.

The analytic all-null rates in the independent regime are `alpha` for BH and `alpha/H_m` for BY. In the near-BH-bound regime they are `g*H_m` and 0, respectively. At the near-BY boundary both rates are `g*H_m`, close to the nominal level. The analytic derivation is independent of the numerical algorithms. The simulation uses SciPy to obtain rejection events and to calculate confidence intervals. Each random family is a single observation even though many p-values within it are dependent.

## Interpretation

The registered claim is supported in this construction only if every near-BH-bound cell shows a simultaneous rate interval wholly above nominal for BH and wholly below nominal for BY. The independent and near-BY-bound cells are diagnostics. They can lie near the cutoff and do not need to exclude nominal. A 95% simultaneous confidence statement applies to the simulated rates only. No Monte Carlo experiment can establish general validity for all dependence structures, and this all-null experiment does not measure power under alternatives.
