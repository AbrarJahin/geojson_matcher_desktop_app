# Paper 1 — Risk-limiting road-network conflation

**Status:** proposed research; not a validated risk guarantee. **Reference:** Road Matcher `v1.3.0+87fe`.
**Question:** Can unsupervised geometric–semantic scoring safely reject invalid cross-county road-connection candidates while deferring all uncertain/possibly valid pairs to experts?

## Existing implementation (read only as needed)
- `app/core/legacy_program.py`: candidate generation (`notebook_cell_16`), features (`20`), geometric GMM/rules (`24`/`26`), textual Beta mixture (`28`), Empirical-Bayes-style fusion and `SAFE_REJECT` safeguard (`32_safe_reject`), two-category review plan (`34_two_category_plan`).
- `app/core/pipeline.py`: `PipelineConfig` (50 m default, EPSG:26916), fixed threshold, review partition and audit; `tests/test_pipeline_smoke.py` and `tests/test_review_policy.py`: behavior checks.
- **Caveats:** scoring models are unsupervised, but the fixed cutoff was chosen using labeled valid pairs; final fused scores are batch-min-max-rescaled. No automatic accept exists. README's older 2%–30% review language is not the current two-category policy.

## Research requirements
1. Define valid **topological connection** versus coincident/same-name road; separately quantify candidate-generation misses.
2. Build expert-labeled **multiple county-boundary** dataset (urban, rural, ramps, divided/parallel roads, junctions, attribute mismatch); second-rater subset and adjudication.
3. Split by *geographic boundary* into development, calibration and locked test areas; freeze thresholds before final tests.
4. Compare proximity-only, geometry rules, geometry-only, text-only, simple averaging, full fusion, and a feasible established conflation baseline; ablate GMM, Beta model, EB fusion, safeguard, feature families, and batch rescaling.
5. Report valid-pair false-rejection rate, automatic-reject error, invalid-pair rejection recall, overall rejection coverage, human-review fraction, candidate recall, confidence intervals, risk–coverage curves and errors by road type.

## Paper-ready gate
Reproducible data/label protocol and scripts; held-out per-boundary results; fair calibrated baselines; ablations; bounded claims that distinguish **observed zero errors** from a proved guarantee.

**Non-goal:** change the shipping matcher or claim zero false rejections without evidence. Any research implementation belongs in separately reviewed work.
