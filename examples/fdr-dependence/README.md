# False-discovery control under dependence

This study tests whether the Benjamini–Hochberg (BH) rule exceeds its nominal false-discovery level on a controlled dependent all-null distribution and whether the Benjamini–Yekutieli (BY) adjustment prevents that excess. The source is Ruodu Wang, *Elementary proofs of several results on false discovery rate*, [arXiv:2201.09350v3](https://arxiv.org/abs/2201.09350v3), §2 Theorem 1 and §5 Theorem 4. The paper was retrieved and verified locally with PDF SHA-256 `ac0887a3b11ebc9c8f084582b594d6bd5787e128b53d8c60877fb86fb9de21d0`.

## What is tested

The registered estimand is the all-null false-discovery rate of each procedure on a specified dependent joint law across three family sizes and two nominal levels. Theorem 1 covers independent uniform null p-values. Theorem 4 bounds BH under arbitrary dependence. This study checks a derived construction near the harmonic bound and measures rejection rates on independent families.

The decision rule, family sizes, nominal levels, independent unit, seed, replicate counts, confidence adjustment and single joint decision are frozen in [study-plan.json](study-plan.json) with SHA-256 `892405e168f20befdadcba9d14fa02ea7612d246af1127ff559c737fcfe196f3`. The [protocol.json](protocol.json) acceptance vectors carry SHA-256 `7c93baeae8bcefb652ee269011e9424a3f5c1a8c63920132ba96f540a71970c6`. The full methodology, including the dependent construction and the analytic rates, is in [methodology.md](methodology.md).

## Pinned reference tools

The simulation and the small acceptance vectors are derived with SciPy and NumPy at the versions recorded in the plan. Reproduce them with the same pinned interpreter environment.

```bash
mkdir -p .local
uv run --locked --with numpy==2.2.6 --with scipy==1.16.3 \
  python examples/fdr-dependence/simulation.py > .local/fdr-result.json
uv run --locked --with numpy==2.2.6 --with scipy==1.16.3 \
  python examples/fdr-dependence/oracle.py
```

`simulation.py` uses `scipy.stats.false_discovery_control` for the rejection events and `scipy.stats.binomtest` for the Clopper–Pearson intervals. It refuses to run under a different SciPy or NumPy version and it recomputes the frozen artifact digests before it starts. The committed [simulation-result.json](simulation-result.json) contains the reference simulation results. The experiment runs with independently reviewed host code and does not read guest outputs. No fixture is produced by any code linked inside the paper.

## Seal and verify

Recreate the managed study in a new directory, then compare the two guests.

```bash
example=examples/fdr-dependence
study=.local/fdr-replay
uv run --locked paper-falsifier study new https://arxiv.org/abs/2201.09350v3 "$study"
cp "$example"/{protocol.json,methodology.md,oracle.py,study-plan.json} "$study/"
uv run --locked paper-falsifier study seal "$study" \
  --artifact oracle.py --artifact study-plan.json
cp "$example"/{candidate.wat,negative-control.wat} "$study/"
uv run --locked paper-falsifier study verify "$study"
```

The [recorded verification](verification.json) passed **20 of 20** acceptance cases. The [negative control](negative-control.wat), which omits BY's harmonic divisor, produced wrong counts in **three cases**. The [preregistered file digests](preregistration.sha256) were computed before guest authoring. The [managed seal](preregistration.json) records a separate local replay using the same frozen vectors and reference plan. It does not supply an external timestamp.

## Result

The committed simulation reports `supported_in_registered_simulation`. In every near-BH-bound cell the BH simultaneous interval lies wholly above the nominal level and the BY interval lies wholly below it.

| Family size | Level | BH interval | BY interval |
| --- | --- | --- | --- |
| 4 | 0.05 | [0.0996, 0.1058] | [0.0000, 0.0001] |
| 16 | 0.05 | [0.1640, 0.1716] | [0.0000, 0.0001] |
| 64 | 0.05 | [0.2304, 0.2389] | [0.0000, 0.0001] |
| 4 | 0.10 | [0.2013, 0.2094] | [0.0000, 0.0001] |
| 16 | 0.10 | [0.3289, 0.3384] | [0.0000, 0.0001] |
| 64 | 0.10 | [0.4660, 0.4761] | [0.0000, 0.0001] |

All six primary comparisons return `supports`. The intervals are two-sided Clopper–Pearson exact binomial intervals with a Bonferroni adjustment over all 36 measured rates, so the 95% family confidence applies to the simulated rates alone. The independent and near-BY-bound cells are diagnostics and may sit near the cutoff.

This supports the registered boundary construction only. No Monte Carlo experiment establishes general validity for every dependence structure, and this all-null study measures no power under alternatives.
