---
name: code-review
description: Review a requested diff, commit, PR, or code scope for concrete correctness, security, and performance defects.
---

# Code Review

Review the requested scope and enough surrounding code to establish behavior. Use the
current diff when the request refers to ongoing changes; ask only if the target cannot
be inferred. Report actionable findings with severity, file/line, trigger and impact.
Separate verified defects from suggestions. Do not invent scores or require unrelated
refactors. A review request alone does not authorize implementation changes.

For QuantX, apply the relevant invariants from root AGENTS.md: StrategyBase.step,
strategy purity, single-instrument binding, conservative missing-data behavior,
QMT process isolation, durable inbox/outbox and atomic GraphQL contracts. Review
only the invariants touched by the change. Check actual code and evidence rather
than repeating the full checklist in the answer.
