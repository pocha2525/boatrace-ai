# 2026-11-28 fixed two-hour final checkpoint

Snapshot `c7568157fa15098b09b2464df313bc4263bb4d62`; audit SHA256 verified. Previous end checkpoint retained; 2715 previously settled detail records reused unchanged.

Fixed window 2026-11-28T13:13:27+09:00 to 2026-11-28T15:13:27+09:00: 27 matched races; 2 settled, 25 pending. Results increased 62 to 68. Two new market snapshots are outside this fixed window.

| Source | Picks | Settled / pending | ROI % | Exclude best ROI % | MDD yen |
|---|---:|---:|---:|---:|---:|
| baseline | 1 | 2 / 25 | 0 | 0 | 200 |
| baseline | 2 | 2 / 25 | 0 | 0 | 400 |
| baseline | 4 | 2 / 25 | 285 | 0 | 400 |
| candidate | 1 | 2 / 25 | 0 | 0 | 200 |
| candidate | 2 | 2 / 25 | 0 | 0 | 400 |
| candidate | 4 | 2 / 25 | 0 | 0 | 800 |
| rules | 1 | 2 / 25 | 0 | 0 | 200 |
| rules | 2 | 2 / 25 | 0 | 0 | 400 |
| rules | 4 | 2 / 25 | 0 | 0 | 800 |

Only baseline four-pick selection hit among the two settled focus races: invested 800 yen, returned 2,280 yen. Removing that one hit leaves 0% ROI. This is a concentrated observation from two settlements with 25 pending.

## Existing daily fast audit: 11% versus 12%

| Source | Threshold | Cap | Selected / settled / pending races | Tickets | ROI % | Exclude best ROI % | MDD yen |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 11% | 1 | 2 / 2 / 0 | 2 | 0 | 0 | 200 |
| baseline | 12% | 1 | 1 / 1 / 0 | 1 | 0 | 0 | 100 |
| baseline | 11% | 2 | 2 / 2 / 0 | 3 | 416.666666667 | 0 | 100 |
| baseline | 12% | 2 | 1 / 1 / 0 | 2 | 625 | unavailable | 0 |
| baseline | 11% | 4 | 2 / 2 / 0 | 6 | 208.333333333 | 0 | 200 |
| baseline | 12% | 4 | 1 / 1 / 0 | 4 | 312.5 | unavailable | 0 |
| candidate | 11% | 1 | 2 / 0 / 2 | 2 | unavailable | unavailable | unavailable |
| candidate | 12% | 1 | 1 / 0 / 1 | 1 | unavailable | unavailable | unavailable |
| candidate | 11% | 2 | 2 / 0 / 2 | 3 | unavailable | unavailable | unavailable |
| candidate | 12% | 2 | 1 / 0 / 1 | 2 | unavailable | unavailable | unavailable |
| candidate | 11% | 4 | 2 / 0 / 2 | 5 | unavailable | unavailable | unavailable |
| candidate | 12% | 4 | 1 / 0 / 1 | 3 | unavailable | unavailable | unavailable |

Ticket count includes pending selections. Equal 100 yen per ticket; second-pick EV threshold 1.0. Stored EV 0.8 profiles have the same selected race counts. Caps can yield fewer actual tickets.

Consensus caps 1/2/3/4 all select the same one race and the same single actual ticket. Caps 3 and 4 each add zero tickets; no race has three or four qualifying consensus tickets. One settled hit returned 1,250 yen from 100 yen; removing it leaves no remaining sample.

## Whole-day matched exploratory evidence

| Source | Picks | Settled / pending | ROI % | Exclude best ROI % | MDD yen |
|---|---:|---:|---:|---:|---:|
| baseline | 1 | 55 / 52 | 70.9090909091 | 54.0740740741 | 2570 |
| baseline | 2 | 55 / 52 | 113.181818182 | 92.3148148148 | 3070 |
| baseline | 4 | 55 / 52 | 103.090909091 | 93.5185185185 | 5170 |
| candidate | 1 | 55 / 52 | 42.3636363636 | 25.7407407407 | 3460 |
| candidate | 2 | 55 / 52 | 68.7272727273 | 56.1111111111 | 6670 |
| candidate | 4 | 55 / 52 | 117.863636364 | 95.0925925926 | 5510 |
| rules | 1 | 55 / 52 | 184.363636364 | 103.888888889 | 3700 |
| rules | 2 | 55 / 52 | 143.454545455 | 104.166666667 | 2030 |
| rules | 4 | 55 / 52 | 98.9090909091 | 79.7685185185 | 6990 |

Pending excluded from settled ROI and MDD. Existing settled detail rows reused unchanged.
One day cannot estimate day-to-day variation; day-level 95% interval unavailable.
This is same-day exploratory evidence and a retrospectively fixed analysis window; it is not independent future performance.
No fetching results, generating forecasts, training, notification, production code change, commit or push performed for this audit.
