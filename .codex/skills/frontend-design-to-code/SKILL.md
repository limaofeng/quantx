---
name: frontend-design-to-code
description: Design and implement new screens or major frontend redesigns with unsettled visual direction; skip for narrow fixes or approved designs.
---

# Frontend Design to Code

Deliver the requested screens, interactions and real API integration using the existing
component system and QuantX density rules. Inspect only the relevant routes, components,
contracts and assets. Use established choices and safe assumptions; ask about unresolved
decisions only when they materially change the deliverable.

For an explicit preview-first request, create a concise page/state plan, generate
representative image previews using the installed imagegen skill, and obtain approval
before implementing the previewed scope. Existing approval or an explicit waiver counts;
do not ask again. Select previews by distinct layout or interaction risk, not every state.
For ordinary implementation requests, a new screen alone does not require a separate
approval ceremony: proceed within the user's authorized design scope.

Use `../ui-ux-pro-max/SKILL.md` when a concrete visual question needs database guidance.
Use `../grilling/SKILL.md` only when the user requests a design stress-test interview;
it is not a prerequisite to implementation. Do not invent endpoints or expand visual
work into unrequested backend features.

Read [artifact templates](references/artifact-templates.md) only for artifacts the task
needs. Keep approved previews under `.codex_screenshots/design-previews/`. Implement all
in-scope loading, empty, error and success states, navigation and accessibility behavior.
Respect agreed device scope; the developer's operating system does not determine the
product's supported screen sizes.

For GraphQL changes use `../quantx-graphql-codegen/SKILL.md`. Run relevant checks and
inspect the implemented result at in-scope viewports through the supported entrypoint.
Fix failures caused by the change and finish the authorized scope. Report verified
coverage and specific remaining blockers; do not stop after a first implementation.
