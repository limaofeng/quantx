# Frontend Design-to-Code Artifact Templates

Use only the templates needed for the current workflow. Keep them concise and replace every placeholder with verified information or an explicit `Unresolved` marker.

## Requirements brief

```markdown
# <Product or feature> — Requirements Brief

## Goal
- User problem:
- Target users:
- Success criteria:

## Scope
- In scope:
- Out of scope:
- Existing behavior to preserve:

## Constraints
- Stack and repository conventions:
- Supported devices and browsers:
- Accessibility and localization:
- Brand and content constraints:

## Decisions
| Decision | Selected option | Rationale | Source |
|---|---|---|---|

## Assumptions and blockers
| Item | Status | Impact | Owner/source |
|---|---|---|---|
```

## Design system record

```markdown
## Design System
- Navigation and shell:
- Layout and density:
- Responsive behavior:
- Semantic colors:
- Typography:
- Spacing and sizing:
- Radius, borders, and shadows:
- Icons and data visualization:
- Motion and reduced-motion behavior:
- Focus, keyboard, and contrast rules:
- Domain anti-patterns:
- Existing components to reuse:
```

## Page plan

```markdown
| ID | Route / overlay | Purpose | Primary components | Meaningful states | Breakpoints | Data / APIs | Entry and exit | Preview IDs |
|---|---|---|---|---|---|---|---|---|
| P01 |  |  |  | loading / empty / error / success |  |  |  |  |
```

After the table, describe cross-page workflows and failure paths that are not obvious from individual rows.

## API integration map

```markdown
| API ID | Method and contract | Consumer | Input | Used response fields | Auth | Loading / empty / error | Pagination / retry / realtime | Status |
|---|---|---|---|---|---|---|---|---|
| A01 |  |  |  |  |  |  |  | Verified / Unresolved |
```

Do not add an API row from imagination. Link or name the local schema, query, backend handler, or provided documentation that verifies it.

## Preview manifest

```markdown
| Preview ID | Page / state | Breakpoint | Why a distinct preview is needed | Source page IDs | Status |
|---|---|---|---|---|---|
| V01 |  | desktop / tablet / mobile | unique layout or interaction risk | P01 | planned / generated / approved / rejected / waived |
```

Coverage rules:

- Include every unique shell, navigation model, and task-critical state.
- Use representative previews for visually equivalent states.
- Add a separate breakpoint only when layout, navigation, or interaction materially changes.
- Never collapse a genuinely multi-page workflow into a single decorative dashboard.

## Imagegen prompt

```text
Use case: ui-mockup
Asset type: frontend design approval preview
Product context: <product, users, workflow>
Page: <route, name, purpose>
Device and breakpoint: <viewport class and constraints>
Interaction state: <default, loading, error, modal open, etc.>
Visible content: <exact headings, labels, representative data>
Layout and components: <hierarchy and placement>
Design system: <palette roles, typography, spacing, surfaces, icons, motion cues>
Accessibility: <contrast, focus, touch targets, readable density>
Constraints: implementable with the repository's stack and components
Avoid: lorem ipsum, invented brand changes, impossible controls, illegible text, decorative UI without function, watermark
```

## Approval log

```markdown
| Date | Preview IDs | Decision | Requested changes or waiver | Resulting plan/design update |
|---|---|---|---|---|
| YYYY-MM-DD | V01 | approved / rejected / partial / waived |  |  |
```

## Implementation verification report

```markdown
## Implemented
- Pages and overlays:
- States and responsive behavior:
- API integrations:

## Verification
| Check | Command / method | Result |
|---|---|---|
| Typecheck |  |  |
| Lint |  |  |
| Tests |  |  |
| Build |  |  |
| Browser and breakpoints |  |  |
| Accessibility |  |  |

## Preview comparison
| Preview ID | Implementation evidence | Deviation | Rationale / approval |
|---|---|---|---|

## Remaining questions
- None, or list the exact unresolved API/product item and its impact.
```
