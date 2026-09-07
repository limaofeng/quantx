---
name: dev-flow
description: Use only when explicitly invoked as $dev-flow for primary-led design and verification with bounded implementation subagents.
---

# Dev Flow

Activate only for an explicit `$dev-flow` invocation. An audit or edit of this skill
is not an invocation of its implementation workflow. The primary agent owns scope,
design, coordination and final acceptance; implementation agents write code. The
primary does not edit implementation files, including generated code and formatting.

## Agent configuration

Follow the repository AGENTS.md subagent policy. Newly created implementation, audit
and Git agents inherit the primary's model and reasoning effort: omit `model` and
`reasoning_effort`, unless the user explicitly selects a different configuration.
Do not pin a model name or force maximum reasoning.

Use self-contained task packages and `fork_turns="none"` by default. Include only
needed history when the package cannot convey the context. Reuse an effective agent;
if it is unavailable or its model/permission environment is stale, first confirm its
work has stopped and inspect outstanding processes and changes. Then create a
replacement with the current configuration and a concise checkpoint. Never launch
competing writes or bypass a permission boundary. If delegation cannot be restored,
report the blocker without silently changing the model or primary's role.

## Acceptance batches

Define the next bounded batch within the full objective: goal, file ownership,
interfaces and constraints, acceptance checks, and existing changes to preserve.
Use relevant code and documentation to resolve facts. Keep the task package concise;
do not require a separate template section for information irrelevant to that batch.
Record progress and remaining issues in the existing plan when the task needs one.

Parallelize only independently writable batches with settled shared interfaces.
Implementers may choose local implementation details, refactor within the assigned
scope and fix related tests. Escalate decisions that change product behavior,
architecture, public contracts, trading semantics or write scope. Resolve ordinary
ambiguity from existing conventions and explain material local choices at handoff.

Implementers run focused checks and hand off a stable file set, results and remaining
issues. Do not continuously audit files while another agent is changing them. The
primary reviews that batch and verifies that evidence applies to its current content
and environment; it runs additional checks only where coverage or evidence is missing.
Run required combined regression when the batch is stable. A main-agent review is not
an instruction to repeat every passing command. Repository-mandated contract checks
still apply.

For defects, send the finding and affected scope back to an implementation agent.
Reverify the defect and impacted paths; expand only for new changes or evidence.
Do not restart design, discovery or full regression for every follow-up fix. Finish
when the authorized acceptance criteria are supported, without inventing further
review rounds. Batch completion does not imply completion of the whole objective.

## Long-running work

Retain the existing process handle, stage, log path and exit result. Prefer waiting
on the handle over repeatedly querying agents or rereading files. Use permitted
backoff when polling is necessary; inspect logs on stage changes or failures. Resume
from valid evidence and checkpoints, never restart a restore solely because waiting
returned no output. Normal waiting is neither failure nor completion.

## Commit and task boundaries

After final acceptance, give the dedicated Git agent the exact approved files,
validation evidence and commit scope. It follows AGENTS.md, uses the inherited model
policy, and may only inspect, stage approved files and commit. No implementation
edits, unrelated files, amend, skipped hooks or push. If it finds a defect, return it
to implementation and reverify the affected scope. The primary confirms the commit.

For substantial frontend design, apply the activation boundary in
`../frontend-design-to-code/SKILL.md`. Previews and visual approval are required only
for an explicitly requested preview-first workflow; reuse existing approval. Narrow
fixes and approved-design implementation do not trigger that workflow. Pass the
relevant design contract to implementers without making them repeat design discovery.

For QuantX schema/query changes, use `../quantx-graphql-codegen/SKILL.md`; an
implementation agent produces generated changes. Coordinate its required validation
across the batch and reuse valid results during primary acceptance.

Follow root AGENTS.md for current Windows development/production and future macOS
migration boundaries. Developer OS does not determine product viewport scope or
authorize iOS changes, QMT relocation, deployment or a second live runtime.
