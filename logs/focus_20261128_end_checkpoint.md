# 2026-11-28 fixed two-hour checkpoint

Snapshot: `b5504489cb586651e1ce42b756b3e3f5d5db047f`; audit SHA256 verified. Previous mid cache reused; 2715 settled detail records preserved byte-for-field without recalculation.

Window: 2026-11-28T13:13:27+09:00 to 2026-11-28T15:13:27+09:00. Matched races 27; settled 0; pending 27. Focus ROI, largest-hit exclusion ROI and max drawdown are unavailable.

Input: 120 verified snapshots, 105 matched rules; results 62 (unchanged from mid cache). New snapshot keys 38, new focus keys 12.

## Prior settled daily evidence (49 matched races)

| Source | Picks | ROI % | Exclude best ROI % | MDD yen |
|---|---:|---:|---:|---:|
| baseline | 1 | 79.591836734694 | 60.833333333333 | 2510 |
| baseline | 2 | 127.040816326531 | 103.854166666667 | 2200 |
| baseline | 4 | 104.081632653061 | 93.333333333333 | 3170 |
| candidate | 1 | 47.551020408163 | 28.958333333333 | 3400 |
| candidate | 2 | 77.142857142857 | 63.125000000000 | 5610 |
| candidate | 4 | 132.295918367347 | 106.979166666667 | 3110 |
| rules | 1 | 206.938775510204 | 116.875000000000 | 3100 |
| rules | 2 | 161.020408163265 | 117.187500000000 | 2000 |
| rules | 4 | 111.020408163265 | 89.739583333333 | 6620 |

All stakes: 100 yen per selected pick; prior evidence is unchanged and does not measure the fixed two-hour window. Day-level 95% CI unavailable with one day.

Consensus is a selection-count check only: {"all_matched": {"races": 105, "agreement_size_histogram": {"0": 104, "1": 1}, "at_least_3_picks": 0, "at_least_4_picks": 0, "definition": "baseline top4 intersects recorded reasoned picks; each p>=0.12 and p*saved_odds>=1.0; selection-count only, no additional settlement"}, "focus_matched": {"races": 27, "agreement_size_histogram": {"0": 27}, "at_least_3_picks": 0, "at_least_4_picks": 0, "definition": "baseline top4 intersects recorded reasoned picks; each p>=0.12 and p*saved_odds>=1.0; selection-count only, no additional settlement"}, "new_focus_matched": {"races": 12, "agreement_size_histogram": {"0": 12}, "at_least_3_picks": 0, "at_least_4_picks": 0, "definition": "baseline top4 intersects recorded reasoned picks; each p>=0.12 and p*saved_odds>=1.0; selection-count only, no additional settlement"}}

Previously settled detail rows reused unchanged. Pending excluded from invested, returned, ROI and drawdown. No results fetched or forecasts, models or production code changed.
