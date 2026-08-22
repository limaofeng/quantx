---
name: luna-dev
description: Orchestrate feature development by delegating implementation to gpt-5.6-luna agents at max reasoning while the primary agent performs only final review and testing. Use for feature implementation work, including QuantX frontend development; do not use for explanation-only or diagnosis-only requests.
---

# Luna Dev

Delegate the implementation of an authorized feature to subagents. The primary
agent remains the user-facing coordinator and final quality gate; it does not
write the implementation itself.

## Role Separation

- Spawn every implementation subagent with `model: "gpt-5.6-luna"` and
  `reasoning_effort: "max"` and `fork_turns: "1"`. Include the complete task,
  constraints, relevant paths, and acceptance criteria in the subagent message
  because model overrides cannot use a full-history fork.
- Delegate discovery, design, implementation, and implementation-level fixes.
  Use separate agents only for genuinely independent scopes and avoid assigning
  overlapping files concurrently.
- The primary agent may inspect the repository to prepare delegation, coordinate
  dependencies, relay material user decisions, monitor agents, and then perform
  final review and tests. It must not make implementation edits.
- If a delegated implementation fails review, send the evidence and required
  correction back to a Luna Max subagent with `followup_task`; do not fix it in
  the primary agent.
- If delegation or the required Luna Max model is unavailable, report the
  blocker instead of silently switching to primary-agent implementation.

## Workflow

1. Read repository instructions and identify the requested feature boundary,
   affected architecture, acceptance criteria, and required skills.
2. Split only independent work. Spawn one or more Luna Max subagents with clear
   ownership and instruct them to inspect existing user changes before editing,
   preserve unrelated work, implement the feature, and run focused checks.
3. Keep the primary agent free of implementation edits while agents work. Use
   messages or follow-up tasks to resolve gaps without duplicating their work.
4. After all implementations return, review the combined diff for correctness,
   scope, security, architecture, and repository-rule compliance.
5. Run the relevant tests, lint, type checks, code generation, and build from the
   primary agent. When a check fails because of the implementation, delegate the
   fix and repeat final review and verification.
6. Complete the task only when the authorized feature is implemented and the
   final checks pass. Follow repository completion rules, including a scoped
   commit by the primary agent when required. Otherwise report exact unresolved
   failures and their evidence.

## Frontend Development

For every frontend implementation task, load the repository sibling
`../frontend-design-to-code/SKILL.md` and follow its activation boundary and
workflow. Instruct each frontend implementation subagent to load the same skill
before editing. A narrow fix may use that skill's narrow-task path; substantial
new screens or redesigns must honor its design, preview, approval, integration,
and verification gates.

When frontend work changes QuantX GraphQL schema, queries, or generated types,
also load `../quantx-graphql-codegen/SKILL.md` and follow its atomic codegen and
validation requirements.

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
