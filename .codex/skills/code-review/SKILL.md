---
name: code-review
description: Review code changes for security, performance, and correctness. Trigger with a PR URL, commit hash, diff, "review this before I merge", "is this code safe?", or when checking a change for N+1 queries, injection risks, missing edge cases, or error handling gaps.
argument-hint: "<PR URL, commit hash, diff, or file path>"
---

# /code-review

> If you see unfamiliar placeholders or need to check which tools are connected, see [CONNECTORS.md](../../CONNECTORS.md).

Review code changes with a structured lens on security, performance, correctness, and maintainability.

When used inside `F:\Workspace\quantx` or another QuantX workspace, enforce additional QuantX trading-strategy review checks.

## Usage

```
/code-review <PR URL | commit hash | file path | diff>
```

Review the provided code changes: @$1

If input is a commit hash (7+ hex chars), review the commit diff directly.

If no specific file or URL is provided, ask what to review.

QuantX gating rules (project-level override):

- Apply QuantX checklist only when the target context appears QuantX-related:
  - `AGENTS.md` exists and contains the QuantX memory prompt
  - targets include `backend/core/strategies`, `backend/core/strategy_executor.py`, `backend/main.py`, `backend/docs`, or `交易系统文档`
- If no QuantX signals are detected, skip QuantX-specific items and keep only generic Security/Performance/Correctness/Maintainability review.

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                      CODE REVIEW                                   │
├─────────────────────────────────────────────────────────────────┤
│  STANDALONE (always works)                                       │
│  ✓ Paste a diff, PR URL, or point to files                      │
│  ✓ Security audit (OWASP top 10, injection, auth)               │
│  ✓ Performance review (N+1, memory leaks, complexity)           │
│  ✓ Correctness (edge cases, error handling, race conditions)    │
│  ✓ Style (naming, structure, readability)                        │
│  ✓ Actionable suggestions with code examples                    │
├─────────────────────────────────────────────────────────────────┤
│  SUPERCHARGED (when you connect your tools)                      │
│  + Source control: Pull PR diff automatically                    │
│  + Project tracker: Link findings to tickets                     │
│  + Knowledge base: Check against team coding standards           │
└─────────────────────────────────────────────────────────────────┘
```

## Review Dimensions

### Security
- SQL injection, XSS, CSRF
- Authentication and authorization flaws
- Secrets or credentials in code
- Insecure deserialization
- Path traversal
- SSRF

### Performance
- N+1 queries
- Unnecessary memory allocations
- Algorithmic complexity (O(n²) in hot paths)
- Missing database indexes
- Unbounded queries or loops
- Resource leaks

### Correctness
- Edge cases (empty input, null, overflow)
- Race conditions and concurrency issues
- Error handling and propagation
- Off-by-one errors
- Type safety

### Maintainability
- Naming clarity
- Single responsibility
- Duplication
- Test coverage
- Documentation for non-obvious logic

## QuantX Project Checklist (only when QuantX signals are detected)

When reviewing QuantX-related code, additionally check:

1. **Strategy migration path**
   - New strategy changes should use `StrategyBase.step(input)` only.
   - No new logic in old entry points like `Signal / on_bar / on_tick / generate_signal`.
2. **No business-semantics side effects in strategy layer**
   - Strategy code should not read/write accounts, brokers, order status, network, files, DB, or Redis as business source-of-truth.
3. **Single-instrument invariants**
   - A strategy instance should not dynamically switch instruments mid-run.
   - Preserve explicit instrument binding behavior in the tested scope.
4. **Trading correctness**
   - No logic that assumes future bars/future actions.
   - Edge cases around empty/missing market data handled conservatively.
5. **Contract consistency**
   - If API/GraphQL or backend contracts changed, verify no temporary `as any` or type bypass was used to compensate.
6. **Commit/Review alignment**
   - Flag long patch sequences that look like incremental fixes for the same root issue.
   - Recommend refactor to reduce churn before final merge.

## Output

```markdown
## Code Review: [PR title or file]

### Summary
[1-2 sentence overview of the changes and overall quality]

### Critical Issues
| # | File | Line | Issue | Severity |
|---|------|------|-------|----------|
| 1 | [file] | [line] | [description] | 🔴 Critical |

### Suggestions
| # | File | Line | Suggestion | Category |
|---|------|------|------------|----------|
| 1 | [file] | [line] | [description] | Performance |

### What Looks Good
- [Positive observations]

### Verdict
[Approve / Request Changes / Needs Discussion]
```

## If Connectors Available

If **~~source control** is connected:
- Pull the PR diff automatically from the URL
- Check CI status and test results

If **~~project tracker** is connected:
- Link findings to related tickets
- Verify the PR addresses the stated requirements

If **~~knowledge base** is connected:
- Check changes against team coding standards and style guides

## Tips

1. **Provide context** — "This is a hot path" or "This handles PII" helps me focus.
2. **Specify concerns** — "Focus on security" narrows the review.
3. **Include tests** — I'll check test coverage and quality too.
