---
name: dev-flow
description: Use only when explicitly invoked as $dev-flow to have the primary agent design and verify bounded Luna implementation tasks.
---

# Dev Flow

Use this workflow only when the user explicitly invokes `$dev-flow`; ordinary
uninvoked feature implementation requests must not activate it. For an
authorized invocation, the primary agent means the model selected for the
current Codex task/conversation, including its selected reasoning strength—not
a separate fixed model. It is the user-facing interface, planner, designer,
and final quality gate. It may inspect, coordinate, and validate, but it must
not write implementation code or edit implementation files.

## Role boundaries

### Primary agent

- Before any implementation delegation, read the repository instructions and
  the documentation and skills needed for the affected scope, inspect the affected code, and
  check existing working-tree changes.
- Own repository discovery, requirements clarification, planning,
  architecture, UX, contract design, task decomposition, coordination,
  combined-diff review, and final testing.
- Define the interfaces, constraints and acceptance criteria needed for the next bounded delegation. Refine later work from implementation evidence.
- Give each implementation subagent a complete, executable task package. The
  package must be self-contained because a model override cannot rely on the
  full conversation history.
- Resolve any ambiguity, design conflict, or requested scope change raised by a
  subagent, then send a revised follow-up task to the same implementation
  subagent using the available agent follow-up mechanism. The primary agent
  must not fix implementation code directly.

### Implementation subagents

- Every implementation subagent, including a follow-up fix, uses exactly
  `model: "gpt-5.6-luna"`, `reasoning_effort: "max"`, and `fork_turns: "1"`.
- Execute only the supplied task package. A package must state:
  - goal and non-goals;
  - exact write scope and relevant paths;
  - explicit design decisions;
  - interfaces and contracts;
  - boundaries and error handling;
  - acceptance criteria;
  - focused validation commands;
  - repository constraints; and
  - known user changes that must be preserved.
- Read related code needed to implement the package and make only local
  mechanical choices that do not change architecture, UX, contracts, or scope.
  Do not make product, architecture, UX, contract, or scope decisions. Stop
  and report when the package is ambiguous, conflicts with the repository, or
  needs to change.
- Implement the package, run its focused checks, and report exact changed files
  and validation results. Never stage or commit changes.
- Any codegen, formatting, snapshot regeneration, or other command that
  produces tracked implementation changes must be run by an implementation
  subagent. The primary agent performs the final validation afterward.

Use parallel subagents only for genuinely independent tasks whose write sets do
not overlap. Run coupled or dependent work in order. If delegation or the
required Luna max model is unavailable, report the blocker; do not silently
substitute another model or write the implementation in the primary agent.

## Workflow

Work in acceptance batches within the full objective. For each batch, record the file
set, interfaces, acceptance checks and remaining issues in the existing plan. Implementers
hand off a stable ready-to-review batch before the primary runs its final audit. Avoid
having an auditor repeatedly inspect files that another agent is actively changing.
Follow-up fixes reuse that batch and invalidate only affected evidence. Run combined
regression once the batch is stable, and repeat only for new changes or failures.

For long-running tests, backups or restores, retain the process handle and log path.
Prefer waiting for that handle to polling agents or rereading files. Ask for status only
when it changes the next action; do not create a fresh model turn solely to restate an
unchanged wait. Resume from the recorded stage and valid evidence, not from discovery.

1. The primary agent discovers and understands the repository, its rules, the
   relevant code, existing changes, and required skills.
2. The primary agent determines the boundary, design, acceptance criteria, and
   verification plan.
3. The primary agent splits the work into clear, non-overlapping implementation
   task packages.
4. Luna max implementation subagents execute those packages and focused checks.
5. The primary agent resolves subagent-reported design gaps and sends a revised
   follow-up task to the same implementation subagent using the available agent
   follow-up mechanism when needed.
6. The primary agent reviews the complete diff and runs final validation
   commands that do not produce tracked implementation changes, including
   tests, lint, type checks, and builds appropriate to the feature.
7. If verification fails, the primary agent diagnoses from evidence, revises
   the design or task package, sends the fix as a revised follow-up task to the
   same implementation subagent using the available agent follow-up mechanism,
   and repeats review and acceptance.
8. After the primary agent approves the verified diff, use the dedicated commit subagent described below.
9. Verify the resulting commit and confirm unrelated working-tree changes were
   not modified.

## Commit gate

The dedicated commit subagent uses exactly `model: "gpt-5.6-luna"`,
`reasoning_effort: "max"`, and `fork_turns: "1"`. Give it the exact approved
file set, verification results, repository commit rules, and intended commit
scope. It may only inspect status and diffs, stage the approved files, create
the commit, and report its hash. It must not edit files, absorb unrelated
changes, amend, skip hooks, or push. If the model is unavailable, report a
blocker. If the agent finds a code problem, it stops and returns to the primary
agent for the fix and full re-verification flow.

## Frontend development

For frontend work, the primary agent loads the repository sibling
`../frontend-design-to-code/SKILL.md` and follows its activation boundary and
workflow. The primary agent clarifies requirements, owns UI/UX design, creates
previews, obtains any required approval, and sends the approved design in the
implementation task package. Each frontend implementation subagent also loads
that skill before editing, but only applies the approved design and must not
redesign it.

When a frontend task changes QuantX GraphQL schema, queries, or generated types,
the primary agent also loads `../quantx-graphql-codegen/SKILL.md` and designs an
atomic contract switch. Any tracked codegen output is produced by an
implementation subagent; the primary agent performs the final validation.

## Platform and product scope

Follow root AGENTS.md: current development and production runtime are Windows; future
macOS development does not move QMT off Windows or authorize a second live runtime.
Desktop-first Web work follows the existing design system. Device acceptance scope is
determined by the requested product behavior, not the developer OS. Do not modify iOS
or remove mobile behavior merely because the task is executed on Windows or macOS.
