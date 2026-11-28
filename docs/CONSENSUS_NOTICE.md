# Combined forecast notifications (2026-11-28)

The user requested only a single combined prediction instead of many independent alerts.

`consensus-v1` takes the intersection of the current model's top four trifectas and the valid, saved `reasoned-four-v1` forecast. Every delivered pick must individually have model probability >=12% and snapshot model EV >=1.0. Picks retain their original model probability order; at most two are delivered. No blended probability is invented. This is a conservative agreement filter, not demonstrated improvement in return.

Only one prediction notification per JST calendar day is allowed. The daily durable notification ID is `consensus-v1-YYYYMMDD`; the body and receipt retain the race ID. A reservation, successful receipt or unknown delivery consumes the daily slot; a confirmed failed send can retry. A confirmed pre-send deadline cancellation releases this daily key; pending or unknown delivery never does. The GitHub compare-and-swap delivery ledger protects the limit even when a runner loses its final CSV push. Prechange notifications do not get rewritten or resent.

Morning candidates remain internal CSV records. Morning lists, skip messages, standalone reasoning-four blocks, and manual test pushes no longer send forecast alerts. Collection, result ingestion and frozen baseline/challenger comparisons continue. Manual morning/exhibition/night automation prompts also suppress independent prediction emails and chat pushes. Performance reports and the requested analysis summary are separate from prediction alerts.

The numeric reasoning forecast represents only part of ChatGPT's reasoning process. It does not reproduce unrestricted discretionary analysis, video interpretation or the nationwide one-race manual selection. Lack of valid predeadline input means silence, not invented picks. Complete market and timing checks remain required. No retrospective generation is used.

## Requested retraining

The explicitly requested one-off workflow `requested-retrain` fits three targets once using cached historical files plus already settled same-day pre-race feature snapshots. It excludes future/unsettled outcomes, refunds and ambiguous multiple payouts. No mass data download or notification is performed. Research models and an input/hash manifest are saved in a workflow artifact, separate from live models and the existing frozen 67-feature challenger.

The frozen 2026-11-29 through 2026-12-26 evaluation and hashes remain unchanged. Training-sample performance is not evidence for promotion. The new artifact is a research fit, not a production replacement. Its future evaluation must start after its training cutoff. A separate concurrency group keeps the long training job from blocking live prediction runs.

## Validation

Regression coverage checks shared-pick filtering, individual probability/EV thresholds, daily limits including unknown/pending durable receipts, exclusion of legacy receipts, silent morning/skip paths, notification receipt/pick agreement, and settled-feature training labels with time/refund/ambiguity exclusions. Runtime deployment and training completion are recorded separately after GitHub confirmation.
