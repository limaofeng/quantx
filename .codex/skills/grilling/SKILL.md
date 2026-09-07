---
name: grilling
description: Conduct a design stress-test interview when the user asks to be grilled or explicitly requests questioning of their plan.
license: MIT
metadata:
  source: "https://github.com/mattpocock/skills/tree/main/skills/productivity/grilling"
  adaptation: "Codex question UI and repository delegation policy"
---

Use only for a requested interview, not as an automatic gate for implementation. Interview the user until the material decisions in the requested scope are clear. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

When a structured question tool is available, respect its per-call question limit, put the recommended option first, and suffix its label with `(Recommended)`. Split a larger frontier into consecutive tool-sized batches without asking a question whose prerequisite is still unsettled. When structured input is unavailable, use the plain-text round format below.

Format a round like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), inspect it with the available tools; delegate only when the user or applicable project instructions authorize sub-agent work. Don't ask the user for anything you could look up yourself. A running exploration is an unsettled prerequisite, so only the questions downstream of it wait; ask the rest of the frontier now. Put material unresolved decisions to the user; reuse decisions and authorization already given. Do not reopen settled choices or ask about reversible implementation details.

Finish when the requested decision scope is resolved or the user ends the interview. Summarize decisions and stated assumptions. Continue implementation if the user already authorized it; an interview-only request does not authorize implementation.
