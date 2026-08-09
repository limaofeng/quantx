# QuantX 重构迁移基线

本文冻结当前 monorepo 迁移的承接关系。删除旧目录前，审查者应按本表确认
新位置、导入边界和验证入口均存在；不得在后续检查点中恢复旧启动主路径。

| 旧位置/职责 | 新位置 | 基线检查 |
| --- | --- | --- |
| 旧服务认证、HTTP、GraphQL | `apps/api/src/quantx_api` | API 单元与集成测试 |
| 旧服务策略与交易纯规则 | `packages/domain/src/quantx_domain` | domain 边界测试 |
| 旧服务用例与命令路由 | `packages/application/src/quantx_application` | application 契约测试 |
| ORM、Repository、外部数据适配 | `packages/infrastructure/src/quantx_infrastructure` | infrastructure 测试 |
| 策略运行、做 T、清仓、回报收敛 | `apps/engine/src/quantx_engine` | engine 安全测试 |
| Prefect flows/tasks | `apps/worker/src/quantx_worker` | worker 测试 |
| XTData/XTTrading、`miniqmt` | `apps/qmt-agent/src/quantx_qmt_agent` | QMT 模拟器测试 |
| Agent DTO、协议和状态枚举 | `packages/contracts/src/quantx_contracts` | contracts 测试 |
| 旧 Web 单体 | `apps/web` | codegen、check、strict lint、test、build |
| 根目录及 API 子进程启动脚本 | `ops/quantx.ps1`、WinSW、Caddy | ops contract 与 Windows CI |

## 可审查检查点

1. 基础包与契约：`contracts`、`domain`、`application`、`infrastructure`。
2. 服务端：API、Engine、Worker；服务间只经数据库消息箱、Redis 唤醒和版本化
   协议协作。
3. QMT 与运维：QMT Agent 是唯一券商出站边界；`ops/quantx.ps1` 是唯一运维
   入口。
4. Web 与文档：前端只通过 Caddy 的同源 `/graphql`，生成类型与 schema 同步。

每个检查点都应记录 `git diff --stat`、删除/新增映射和相应测试结果。当前工作
树含大规模未提交迁移，因此在正式发布前必须由维护者按上述四组拆分提交；自动
化实现不会替维护者重写或丢弃现有改动。

## 基线门禁

```powershell
uv lock --check
uv sync --locked --all-packages --group dev
uv run pytest tests/contracts tests/domain tests/application
uv run pytest tests/infrastructure/test_dependency_boundaries.py
uv run ruff check apps packages tests

npm run codegen
npm run check
npm run lint:strict
npm run test:run
npm run build
```

CI 另行强制关键安全不变量覆盖率不低于 85%，并以 `diff-cover` 对相对主分支
新增或修改的 Python 行强制不低于 80%。Alembic 基线带 PostgreSQL 元数据
SHA256 指纹；模型变化必须新增 revision，不能让旧基线随 ORM 漂移。

GraphQL codegen 必须在 `.\ops\quantx.ps1 up -Environment dev -Profile web`
启动后，通过 `http://127.0.0.1:8080/graphql` 执行。
