---
name: ui-ux-pro-max
description: Search UI design references for a specific layout, typography, color, chart, or accessibility decision when existing project guidance is insufficient.
---

# UI/UX Reference Search

QuantX's `docs/engineering/web/UI_UX_DESIGN_SYSTEM.md` and existing components take
precedence over search recommendations. Use the actual project stack. A local UI fix
does not require generating a new design system, changing branding or installing Python.

Resolve `scripts/search.py` relative to this skill directory. From the QuantX root:
```powershell
python .codex/skills/ui-ux-pro-max/scripts/search.py "keyboard focus" --domain ux -n 3
python .codex/skills/ui-ux-pro-max/scripts/search.py "table rendering" --stack react
```
Available domains and stacks are listed by `--help`. Use the configured Python runtime.
Search only domains needed for an unresolved design question. Use `--design-system` for
an open visual direction; generated guidance is advisory. Use `--persist` only when a
durable design artifact is part of the task; it writes under `design-system/<project>/`.

Verify the resulting UI against the task: readable density, semantic colors, keyboard
focus, contrast, stable layout and agreed viewport scope. Keep default and compact
QuantX density behavior. Avoid re-running searches once the decision is supported.
