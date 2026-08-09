---
name: quantx-graphql-codegen
description: Use when changing QuantX GraphQL/API schema, Strawberry types or resolvers, web GraphQL queries, generated GraphQL types, or running npm run codegen in the QuantX monorepo. Enforces Caddy public-endpoint codegen and matching frontend validation.
---

# QuantX GraphQL Codegen

Use in `F:\Workspace\quantx`.

## Required workflow

1. Start `.\ops\quantx.ps1 up -Environment dev -Profile web`.
2. Require `http://127.0.0.1:8080/health/live` and `/graphql` to be reachable
   through Caddy.
3. Generate from the root npm workspace:

```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT = "http://127.0.0.1:8080/graphql"
npm run codegen
npm run check
```

4. Run `npm run lint`, `npm run test:run`, and `npm run build` when the
   frontend contract or generated output changed.

## Guardrails

- Generate in the same work session as every schema/query change.
- Never start API directly on public port 8080; API owns 18081 and Caddy owns
  8080.
- Never stop an untracked port owner. Report its PID/command line and wait for
  the conflict to be resolved.
- Do not restore or invoke old root/backend start scripts.
- Prefer generated `gql(...)` documents and never use `as any` to hide a
  contract mismatch.
