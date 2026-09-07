---
name: quantx-graphql-codegen
description: Generate and validate QuantX GraphQL contracts after schema or query changes, using the actual backend Caddy endpoint.
---

# QuantX GraphQL Codegen

Caddy is the only public schema endpoint. Resolve the backend host before generating:
- Current Windows development host: `http://127.0.0.1:8080/graphql`.
- A client using the Windows backend, including a future macOS developer machine:
  use the confirmed Windows Caddy address (currently `http://192.168.5.6:8080/graphql`).
  Do not replace a remote address with the client's localhost.

Verify Caddy `/health/live` and `/graphql`. Service startup uses the unified Windows
entrypoint and the authorization in the task; never start a second backend or QMT
Agent on a client. If the endpoint is unavailable, continue independent work and
report the exact blocked validation without silently switching schema sources.

After any schema or query change, in the same session run from the repository root:
```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT = "http://127.0.0.1:8080/graphql"
npm run codegen
npm run check
npm run lint
npm run test:run
npm run build
```
Set the endpoint to the resolved host. Future macOS shells use their native environment
assignment syntax; this is not authorization to enable a macOS service topology.

Switch schema, queries, generated types, clients, documentation and tests atomically.
Use generated documents; never use `as any` to hide a mismatch. API owns internal
port 18081, Caddy owns public port 8080. Do not stop an untracked port owner.
