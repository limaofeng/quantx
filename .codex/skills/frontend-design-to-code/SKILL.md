---
name: frontend-design-to-code
description: Design and implement substantial frontend experiences through clarified requirements, a UI/UX system, image previews, approval, and production integration. Use for greenfield screens, multi-page flows, or major redesigns with unsettled visual direction. Do not use for narrow fixes or faithful implementation of an already-approved design unless the user explicitly requests the preview-first workflow.
---

# Frontend Design To Code

Run a gated design-to-code workflow whose approved page plan and visual direction become the implementation contract.

## Activation Boundary

Use the complete preview-first workflow for new products, new multi-screen experiences, and major redesigns where layout or visual direction is still open. Do not turn a small component edit, bug fix, or implementation from an already approved design into a design ceremony.

If the user explicitly invokes this skill for a narrower task, preserve their requested scope. Ask whether previews are needed only when that choice materially changes the deliverable and cannot be inferred from the request.

## Required Dependencies

Use these dependencies in this order. Do not silently substitute similarly named skills.

1. **`grill-me`:** read the explicit wrapper at `../grill-me/SKILL.md`, then its required implementation at `../grilling/SKILL.md`. Apply Matt Pocock's design-tree and frontier workflow to material product, route, API, and acceptance ambiguities.
2. **`ui-ux-pro-max`:** read the canonical repository sibling at `../ui-ux-pro-max/SKILL.md`. This explicit relative path is intentional and takes precedence over any other installed skill with the same name.
3. **`imagegen`:** load the installed `imagegen` skill before generating previews. Use its built-in image generation mode by default; use its CLI fallback only when the user explicitly chooses that path.

If a required dependency or referenced file is unavailable, report the exact missing path or skill. Do not pretend the gated workflow completed.

## Workflow

### 1. Inspect and clarify

Inspect the repository before asking questions: project instructions, stack, routes, component system, styling, API clients, generated types, tests, and available assets.

Use `grill-me` only for decisions that materially affect the result. Resolve or safely document:

- product goal, users, primary workflows, and success criteria;
- page, route, modal, drawer, onboarding, auth, and settings scope;
- loading, empty, error, success, permission, and destructive-action states;
- real content, data fields, localization, accessibility, dark mode, and breakpoints;
- API contracts, auth, pagination, upload, streaming, realtime behavior, and error schemas;
- visual direction, references, must-avoid patterns, fidelity, and approval criteria.

State discoverable facts and safe assumptions instead of asking about them. Stop for user input only when proceeding would create the wrong routes, fake APIs, or an unusable workflow.

### 2. Establish the UI/UX system

Use the canonical `ui-ux-pro-max` skill before planning previews. Run its design-system search with a query derived from product type, industry, audience, style, and actual stack. Resolve its scripts relative to `../ui-ux-pro-max/`; do not use an ambiguous `skills/ui-ux-pro-max` working-directory guess.

Synthesize the relevant output into a concise system covering:

- navigation, layout, density, responsive behavior, and content hierarchy;
- semantic color roles, typography, spacing, radius, shadows, icons, charts, and motion;
- keyboard behavior, focus, contrast, reduced motion, and mobile ergonomics;
- domain-specific anti-patterns and existing repository conventions.

Treat database recommendations as guidance, not authority over existing brand rules or component systems.

### 3. Build the complete page plan

Read [references/artifact-templates.md](references/artifact-templates.md) when producing the durable requirements brief, page plan, API map, preview manifest, approval log, or verification report.

The page plan must cover every in-scope route and overlay, its major components, data dependencies, navigation entry and exit points, and meaningful states. Map each real API to its consumers and handling behavior. Mark unresolved contracts rather than inventing endpoints or fields.

Select previews by **unique layout or interaction risk**, not raw state count. A shared shell or visually identical error state needs one representative preview; a state that changes hierarchy, navigation, or task completion needs its own preview.

### 4. Generate image previews

Load `imagegen` and generate raster UI mockups for every item in the preview manifest. Include product context, route, device, exact visible content, interaction state, data density, design-system rules, accessibility expectations, and an explicit avoid list.

Generated images establish visual direction; they are not authoritative for exact copy, data, or component measurements. Keep those decisions in the page plan. Inspect every output for hierarchy, composition, text drift, impossible controls, and inconsistent global language before presenting it.

Render previews to the user. For project-bound approved references, place selected outputs under `.codex_screenshots/design-previews/<date>-<project>/` without overwriting existing files.

### 5. Approval loop

Require explicit approval before implementation in preview-first mode. Approval may cover the full manifest or named subsets.

When a preview changes:

1. use `grill-me` for the smallest unresolved decision only;
2. update the affected design-system rule, page-plan row, or prompt;
3. regenerate impacted previews, expanding scope only when the global visual language changed;
4. record what was approved, rejected, or waived.

Proceed without previews only when the user explicitly waives them. Record the waiver and the assumptions that replace visual approval.

### 6. Implement the approved experience

Follow repository instructions and existing architecture. Build all approved pages, states, responsive breakpoints, navigation, forms, validation, focus behavior, and accessibility semantics. Prefer the existing component, icon, state, and styling systems.

For backend integration:

- use provided API documentation or existing backend and client code;
- reuse or create a typed API layer consistent with the repository;
- implement auth, configuration, requests, response parsing, errors, pagination, uploads, and realtime channels that are actually in scope;
- replace production mock data with real integrations while keeping test fixtures isolated;
- show actionable loading, empty, error, retry, disabled, and success states.

Never invent production endpoints. Never broaden a visual task into backend implementation the user did not request.

When QuantX GraphQL schemas, frontend queries, or generated types change, load `quantx-graphql-codegen` and follow its required codegen and validation sequence.

### 7. Verify against both contracts

Run the repository's relevant typecheck, lint, tests, build, and formatting checks. Start the supported development stack when appropriate and inspect the result in a browser at approved breakpoints.

Compare implementation screenshots with approved previews while treating the page plan as authoritative for content and behavior. Check layout, hierarchy, overflow, scrolling, loading, empty, error, success, keyboard navigation, accessible names, console errors, and network failures.

Finish with the verification-report template: implemented scope, changed files, checks run, preview deviations and rationale, and any remaining API or product questions.

## Completion Conditions

The workflow is complete only when:

- all in-scope pages and meaningful states are represented in the page plan;
- previews are approved or explicitly waived;
- implementation matches the approved visual direction and behavioral contract;
- real APIs are integrated to the authorized extent with no hidden production mocks;
- relevant repository checks and browser verification pass, or remaining failures are reported precisely.
