# Autonomous operation, 2026-11-28

The existing Google Apps Script clock remains the automatic dispatcher. Its existing `kick` and spreadsheet-sync timers were changed from ten to five minutes at the user's explicit request. No second periodic dispatcher or extra per-race triggers are added. GitHub's `autonomous` workflow is manual, read-only verification by default.

The saved project does not need a ChatGPT message to collect official entries/exhibition/odds, build numeric predictions and recorded reasoning forecasts, filter shared picks, notify, collect results, refresh current-day totals or analyze new records.

## One existing cycle

1. `kick` invokes the existing `predict` workflow; its guarded entry is `autonomous.py --mode predict`.
2. Official features and reasoning picks are fixed before odds. Deadline guards remain: begin within the existing fifteen-minute window and never send within two minutes of closing.
3. The morning list is one provisional list. Published candidates receive either a shared final forecast or a concise skip message. Extra standalone reasoning forecasts are not sent. The live shared forecast remains at most once daily and two picks; four is an authorized research ceiling, not automatic evidence of profit.
4. Results are collected every cycle, with a durable fair retry queue. Old unavailable races cannot permanently monopolize the queue. An empty relay result can be checked directly once; missing outcomes stay pending.
5. `live_stats.py` updates today's actual-notification totals from saved official results, without inventing missing days or replacing manual four-pick history.
6. `fast_analysis.py` maintains a small fixed comparison set and reuses settled decisions, allocations and outcomes. Identical inputs do not rewrite the audit. This reads saved data; it does not train models or alter production selection.
7. Logs are pushed before the existing spreadsheet sync hook. The existing five-minute spreadsheet timer remains a second opportunity to render the same saved data.

## Recovery and limits

`logs/auto_runtime.json` contains shared heartbeats, bounded retries and phase health. Five-minute invocations can run normally; a duplicate invocation within four minutes reuses the successful cycle. A failed phase remains retryable. Failure state is saved even when a workflow step fails. Persistent operational failures produce one deduplicated maintenance alert, not repeated prediction messages.

`logs/result_attempts_YYYYMMDD.json` records result retries. A confirmed successful/unknown prediction receipt is still protected by the durable notification ledger. Retry logic never resends an unknown message or substitutes an outcome of zero.

Known legacy history headers can be read without crashing reports. Spreadsheet display writes are limited and only changed tabs are refreshed. Missing upstream CSVs must leave existing displayed data intact.

External publication, Apps Script trigger jitter, network availability and GitHub queue delay can still prevent a timely signal. A late signal remains cancelled rather than sent after closing. These operational limits are not repaired by changing a historical forecast.

## Model and analysis policy

The frozen 67-feature challenger and its 2026-11-29 through 2026-12-26 adoption window stay fixed. The separately requested retraining artifact is not a production replacement. No unrequested mass retraining or automatic code rewriting is performed. The existing prospective return/quality gate controls model adoption.

A one-day result cannot establish profitability or a day-level confidence interval. The four-pick allowance is conditional on validated return, so the live limit is unchanged until there is evidence for increasing it.

## Outputs

- `logs/auto_runtime.json`: cycle health and heartbeat
- `logs/result_attempts_YYYYMMDD.json`: bounded result retrieval
- `logs/daily.csv`: confirmed actual-notification daily totals, including the current day
- `logs/auto_analysis/YYYYMMDD/`: cached decisions and fixed comparison summaries
- `logs/auto_analysis_latest.json`: compact latest analysis
- Existing market/features/notify/results CSV and JSONL files remain the source records.

Tests cover duplicate cycles, retry persistence, timezone/date boundaries, pending/refunds, result fairness/direct fallback, immutable settled data, candidate/skip/final delivery, and offline audit speed/caching. No test sends a real notification or fits a model.
