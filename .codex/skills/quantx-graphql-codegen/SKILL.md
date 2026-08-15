---
name: quantx-graphql-codegen
description: Use when changing QuantX GraphQL/API schema, Strawberry types or resolvers, web GraphQL queries, generated GraphQL types, or running npm run codegen in the QuantX monorepo. Enforces Caddy public-endpoint codegen and matching frontend validation.
---

# QuantX GraphQL Codegen

Use in the QuantX workspace. Caddy is the only public schema endpoint. When Codex runs
on the macOS/iOS client workspace, the development backend is remote at
`http://192.168.5.6:8080`; do not try to start a second local backend. When running on
the Windows backend host itself, the equivalent loopback endpoint is
`http://127.0.0.1:8080`.

## Required workflow

1. Resolve the current Caddy public base URL. In the macOS/iOS client workspace use
   `http://192.168.5.6:8080`; only start `.\ops\quantx.ps1 up -Environment dev
   -Profile web` when working on the Windows backend host and the user actually asks
   to start it.
2. Require `<caddy-base>/health/live` and `<caddy-base>/graphql` to be reachable
   through Caddy.
3. Generate from the root npm workspace:

```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT = "http://192.168.5.6:8080/graphql"
npm run codegen
npm run check
```

4. Run `npm run lint`, `npm run test:run`, and `npm run build` when the
   frontend contract or generated output changed.

## Guardrails

- Generate in the same work session as every schema/query change.
- Never replace a known remote Caddy endpoint with localhost merely because codegen
  runs from a different machine.
- Never start API directly on public port 8080; API owns 18081 and Caddy owns
  8080.
- Never stop an untracked port owner. Report its PID/command line and wait for
  the conflict to be resolved.
- Do not restore or invoke old root/backend start scripts.
- Prefer generated `gql(...)` documents and never use `as any` to hide a
  contract mismatch.
