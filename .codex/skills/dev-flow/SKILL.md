---
name: dev-flow
description: Use only when the user explicitly invokes $dev-flow for feature work; the current task's primary agent owns discovery, planning, design, decomposition, review, and testing, while Luna max subagents execute bounded implementation. Do not trigger for ordinary uninvoked feature requests, pure explanation requests, or pure diagnosis requests.
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
  all task-relevant documentation and skills, inspect the affected code, and
  check existing working-tree changes.
- Own repository discovery, requirements clarification, planning,
  architecture, UX, contract design, task decomposition, coordination,
  combined-diff review, and final testing.
- Form the complete executable design before any implementation delegation.
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
8. After approval, use the dedicated commit subagent described below.
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

## Windows Host Scope

When development runs on a Windows host, apply this scope to frontend and client
work:

- Treat the Web experience as desktop-only. Mobile layouts, mobile breakpoints,
  touch ergonomics, and phone-browser compatibility are outside acceptance
  scope unless the user explicitly adds them.
- Treat the omitted mobile work as an explicit scope waiver when applying
  `frontend-design-to-code`; use desktop previews and browser verification.
- Do not refactor, modernize, or update `apps/ios/**` merely to mirror Windows or
  Web work, and do not run iOS-specific checks for that reason.
- Do not remove existing mobile or iOS behavior unnecessarily. If a required
  shared-contract change cannot be completed safely without an iOS change,
  report the conflict and request direction instead of silently expanding the
  task.
