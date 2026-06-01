---
name: quantx-graphql-codegen
description: Use when changing QuantX GraphQL/API schema, Strawberry types/resolvers, frontend GraphQL queries, generated GraphQL types, or when running npm run codegen in the QuantX repo. Captures the required backend restart and fixed-port workflow for reliable codegen.
---

# QuantX GraphQL Codegen

Use this skill in `F:\Workspace\quantx` when backend GraphQL/API contracts or frontend GraphQL documents change.

## Non-Negotiables

- Backend schema changes require `npm run codegen` in the same work session.
- Codegen must target the real backend GraphQL endpoint on port `8080`.
- If port `8080` is occupied, kill the process listening on `8080` first. Do not start the backend on a new port.
- Do not run `backend/start.bat` for validation.
- Use the project Python when available:

```powershell
C:\Users\limao\miniconda3\envs\xtquant-demo\python.exe
```

## Reliable Codegen Workflow

1. Restart the backend after backend GraphQL/schema changes.
2. Before starting, inspect port `8080`; stop only the listening process on that port.
3. Start the backend on port `8080` with the `xtquant-demo` environment.
4. Wait for `http://127.0.0.1:8080/health` to return healthy.
5. Run codegen with an explicit IPv4 endpoint:

```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT = "http://127.0.0.1:8080/graphql"
npm run codegen
```

Use `127.0.0.1`, not `localhost`, when a previous codegen attempt failed with `ECONNREFUSED ::1:8080` or similar IPv6/localhost resolution noise.

## Codex Desktop Quirk

In the Codex desktop sandbox, a backend started by one short background command may be gone before the next command runs. If `npm run codegen` reports connection refused even after a successful health check, combine these steps into one PowerShell command lifecycle:

- kill existing `8080` listener
- start backend on `8080`
- poll `/health`
- set `CODEGEN_GRAPHQL_ENDPOINT=http://127.0.0.1:8080/graphql`
- run `npm run codegen`

After codegen, if the user needs the app running, restart the backend again on `8080` and verify `/health`.

## Frontend Follow-Up

- Prefer generated GraphQL documents via `gql(...)` for new or changed operations so codegen can validate the query.
- Do not hide schema/query mismatches with `as any`.
- After codegen, run `npm run check`; if it fails, distinguish changed-file errors from existing project-wide TypeScript debt.
