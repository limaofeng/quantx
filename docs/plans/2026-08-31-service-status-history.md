# 单指标服务历史 — 设计与实现验收

## 目标与范围

用户需要从服务状态展开区进入单指标完整历史，用事故列表回顾真实原因，避免
事故越积越多导致左右栏高度失衡。保留现有状态总览、中文原因解释和最近事故区域。

- 入口：展开区右上角最大化按钮，显示“查看完整历史”提示和指标专属可访问名称。
- 历史页：`/settings/status/:targetId/history`，复用系统设置外壳与同一个工作区标签。
- 顶部：全部 15 个监测指标快速切换；不隐藏外部依赖。
- 中部：当前状态、中文原因、状态带、P50/P95 曲线和时间范围。
- 主体：事故列表项，展示状态、中文原因、辅助错误码、编号、开始/恢复时间及持续时长。
- 分页：服务端按范围读取全部保留记录，默认每页 20 条，可选 10/20/50 条。
- URL：保留指标、时间范围、页码、每页条数；返回总览恢复指标与范围。
- 排除：交易门禁、实盘执行、对账、账户安全历史重构、主运行时重启。

## 设计系统与页面计划

沿用 QuantX 深色运维界面、既有字号/间距 token、Lucide 图标、Button/Select 和
Recharts。蓝色表示选择及焦点，绿色表示已恢复，黄色表示降级，玫红表示活动事故。
事故状态同时用图标与文字表达；错误码不是主要信息。刷新动画尊重 reduced motion，
历史曲线不播放入场动画。

| 页面 | 组件与交互 | 状态与退出 |
| --- | --- | --- |
| `/settings/status` | 保留紧凑展开区；增加最大化入口；最近事故只取首 20 条并标明总数 | 现有加载/空/失败状态不变；进入所选指标历史 |
| `/settings/status/:targetId/history` | 快速切换、紧凑概览、事故列表、页码与条数选择 | 独立加载、独立失败重试、无事故、无样本、无独立延迟、监测陈旧、未知目标；返回总览 |

桌面切换区五列，1024 宽度四列，更窄屏两列；指标名可省略但保留 title 和完整
可访问名称。概览控制可换行，事故条目在 1280 以下上下堆叠。长错误码可断行，页面不新增
横向滚动区。翻页完成后焦点及滚动位置回到事故标题，当前页与边界按钮状态明确。

## API 集成

权威实现见 `apps/monitor/src/quantx_monitor/api.py`、`storage.py` 和
`apps/web/src/features/system/monitor-api.ts`；公开契约见
`docs/engineering/api/API.md` 的“单指标状态与事故历史”。

| API | 消费方 | 处理规则 |
| --- | --- | --- |
| `GET /monitor/api/v1/summary?window=24h` | 当前状态、指标切换、概览统计 | 明确标注近 24 小时；失败不伪造健康状态 |
| `GET /monitor/api/v1/targets/{id}/history?range=...` | 状态带、延迟图 | 24h/7d/30d/90d/1y；无延迟样本不填 0 |
| `GET /monitor/api/v1/incidents` | 总览最近事故、独立历史列表 | `range/targetId/page/pageSize/asOf`；返回总数和当前页；取消过期请求并忽略迟到响应 |

事故按开始时间和 ID 双重倒序，范围匹配包含跨边界的长事故，不再截断为 200 条。
第一次响应的 `asOf` 固定本轮查询截止点，后续翻页回传它；新事故不挤入当前页。
恢复时间和最近原因不是不可变快照，保留期清理后总数可以减少，越界页会回到末页。
刷新、切换指标或范围后建立新截止点。页面刷新重新建立截止点，不将其存入 URL。
全部操作均为只读 Monitor 查询，不新增 GraphQL 字段或交易请求。

## 预览与批准记录

| 预览 | 文件 | 结果 |
| --- | --- | --- |
| V01 单指标桌面历史页 | `.codex_screenshots/design-previews/20260831-service-history/approved-desktop.png` | 已批准 |

2026-08-31，用户以“确认实现”批准设计并授权落地。按
`frontend-design-to-code` 的批准后实施流程完成，不重新扩大设计范围。
图片与浏览器截图保存在忽略目录，不提交。

## 实现与预览对照

- 保持“顶部切换 → 紧凑概览 → 全宽事故列表”的层级和深色视觉。
- 真实系统有 15 个指标，切换区比示意图多一行；采用紧凑布局保留全部入口。
- 概览统计明确固定为近 24 小时，避免把现有 summary 窗口当作一年统计。
  范围按钮只控制状态带、曲线和事故查询。
- 活动事故可为降级或不可用原因，使用“进行中”标签；不按示意数据伪造原因与条数。
- 列表每页真实展示 20 条（可调整），不是预览图片中用于构图的 5 条。
- 原因码置于说明下方作为辅助信息；时间区独立对齐，窄屏移到下方。

## 验证记录

| 检查 | 命令 / 方法 | 结果 |
| --- | --- | --- |
| Monitor 单元/API 测试 | `.venv\Scripts\python.exe -m pytest tests/monitor -q -p no:cacheprovider --basetemp .codex_screenshots/pytest-monitor-history-20260831-02` | 42 项通过；覆盖 207 条、边界重叠、同时间排序、新事故隔离、末页及参数校验 |
| 前端相关测试 | `npm run test:run --workspace apps/web --` 后指定历史页、状态页、Monitor client、routes、workspaceTabs 五个测试文件 | 45 项通过；含路由、分页、切换、晚到请求、重试、空态与未知指标 |
| 前端 scoped lint | 对本次 14 个 TS/TSX 文件执行 ESLint `--max-warnings 0` | 通过 |
| Python lint/格式 | 对两个 Monitor 文件与两个测试执行 `ruff check`、`ruff format --check` | 通过 |
| GraphQL codegen | 通过 Caddy `http://127.0.0.1:8080/graphql` 执行 `npm run codegen` | 被并行做 T 回放查询阻塞：运行中 schema 无 `TTradeReplaySignalFilterInput`、`TTradeReplayAuditFilterInput` 及对应 Query 字段；本次无生成文件变更 |
| 全仓类型/UI 检查 | `npm run check`、`npm run ui:check --workspace apps/web` | 早期类型检查通过；最终工作区检查被并行 portfolio 回放的缺失生成类型及 `ReplayEvidenceChrome.tsx` 间距违规阻塞；未在本次文件中报错 |
| 全仓 lint | `npm run lint` | 本次 import/type 问题已修复且 scoped lint 通过；仍有并行 portfolio 回放文件错误 |
| 全仓前端测试 | `npm run test:run` | 当次 666 通过、3 失败；失败均为并行做 T 回放相关测试，不在本次范围 |
| 构建 | `npm run build`；另行 `npm run build:docs` | Vite 产物生成、文档构建通过；CSS 231.2 KiB 超过 230 KiB 预算，预算检查未通过。改动前已有 230.3 KiB 超预算基线；未放宽预算 |
| 差异检查 | `git diff --check` | 通过 |
| 浏览器 | Caddy 实际页面、1545×1151、1024×900、768×1024 | 最大化、48 条真实数据分页（20/20/8）、10 条切换、浏览器返回、指标切换、键盘刷新通过；PostgreSQL 空事故、策略引擎无独立延迟正确；1024/768 均无横向溢出，控制台无 error/warn |

实现截图位于同一预览目录：`implemented-desktop.png`、
`implemented-1024-list.png`、`implemented-768-list.png`。

浏览器使用实际登录会话和真实 Monitor API。为加载新的分页契约，仅通过统一
`ops/quantx.ps1` 重启独立 Monitor；没有重启 API/Engine/QMT Agent 或发起交易。
工作区存在其他任务的 API/Engine/portfolio 改动，只检查、不修改、不吸收进提交。

## 剩余边界

本功能没有未决产品或 API 选择。全仓绿色验收需等待并行做 T 回放契约落地，
并单独处理既有 CSS 总包预算；本次不通过修改无关业务、伪造生成类型或放宽预算来
掩盖这些失败。
