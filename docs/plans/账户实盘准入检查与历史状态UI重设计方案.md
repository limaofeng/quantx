# 账户实盘准入检查与历史状态 — UI 重设计方案

## Goal

- User problem: 当前 18 个检测项等权平铺，历史页又为每项重复展示状态条、覆盖率和异常次数，用户难以快速判断“现在能否交易”“哪里需要处理”“何时异常、持续多久、是否恢复”。
- Target users: QuantX 个人单账户实盘操作者。
- Success criteria:
  - 当前页首屏可以直接回答账户当前是否具备实盘准入条件，以及唯一或少量需关注事项。
  - 历史页以异常事件为主线，明确展示日期、开始、恢复、持续时长、受影响检测项和原因。
  - 休市待机继续使用蓝色信息状态，不计入异常事件，也不使用警告或危险视觉。
  - 18 项权威检查、Monitor 只读历史和现有控制权限不发生语义变化。

## Scope

- In scope:
  - 重构 `/settings/trading-safety` 中“账户实盘准入检查”的当前准入和异常历史两种视图。
  - 当前准入改为风险优先摘要、需关注事项和三组紧凑检查清单。
  - 异常历史改为事件时间线主栏、受影响检查项汇总侧栏和时间范围筛选。
  - 补齐加载、空、Monitor 不可用、观测中断、活动异常、已恢复异常和休市待机状态。
  - 桌面和窄屏响应式布局、键盘操作、屏幕阅读器标签及非颜色状态线索。
- Out of scope:
  - 修改主系统的准入判定逻辑、18 项检查集合或状态语义。
  - 修改 Monitor 的采样、事件生成和 SQLite 历史存储。
  - 把 Monitor 变成准入判定真源，或让历史状态影响交易控制。
  - 新增账户、多租户、告警通知、事件备注或人工确认流程。
- Existing behavior to preserve:
  - 主系统负责判定，Monitor 负责见证和留痕。
  - `PASSED / STANDBY / FAILED / UNKNOWN` 四种历史状态语义。
  - `STANDBY` 仅表示休市待机；只有明确 `FAILED` 才形成异常事件。
  - 24 小时、7 天、30 天、90 天和 1 年五种范围。
  - 技术标识开关、快照和备份新鲜度、选择检测项筛选异常的能力。

## Constraints

- Stack and repository conventions: Vite、React、TypeScript、Tailwind、Lucide、URQL；复用 QuantX 现有 `rounded-panel`、语义 token 和 GraphQL 生成类型，不使用 `as any`。
- Supported devices and browsers: QuantX Studio 桌面工作区为主；必须覆盖 1545×1151、1280×800 和 768px 窄屏布局。
- Accessibility and localization: 简体中文；状态不可只依赖颜色；所有筛选和可展开行必须支持键盘焦点、`aria-pressed` 或等价语义；动态数据更新避免打断阅读。
- Brand and content constraints: 保持深色、高密度、IDE 式工作台；不使用巨型卡片、夸张留白、发光渐变、装饰性图表、手写字体或 emoji。

## Decisions

| Decision | Selected option | Rationale | Source |
|---|---|---|---|
| 当前准入层级 | 风险优先的分组清单 | 首先回答能否交易，正常项压缩，异常和待机获得解释空间 | 用户 2026-08-29 批准推荐方案 |
| 历史主视图 | 异常事件时间线 | 与用户提供的状态历史参考一致，直接回答异常发生、恢复和持续时间 | 用户 2026-08-29 批准推荐方案 |
| 历史辅助视图 | 仅汇总受影响检查项；无异常项合并 | 避免 18 张同权卡片和无意义长条占据首屏 | 用户反馈“历史 UI 设计太糟糕”及批准方案 |
| 休市语义 | 蓝色待机，不计异常 | 休市是预期市场状态，不是系统故障 | 既有业务约束和用户明确要求 |
| 权威边界 | 主系统判定，Monitor 见证和留痕 | 保持交易安全真源与独立观察边界 | 已确认架构方向 |
| API 范围 | 复用现有 GraphQL，不改 schema | 当前契约已提供事件、检查项、状态点和观测元数据 | 本地代码审查 |

## Assumptions and blockers

| Item | Status | Impact | Owner/source |
|---|---|---|---|
| 当前 18 项可以按运行链路、账户事实、执行风控分组 | Confirmed by local check codes | 只影响展示，不改变数据或判定 | `accountExecutionGatePresentation.ts` |
| 聚合异常数等于返回事件数 | Confirmed | 可直接展示活动和已恢复数量 | GraphQL history query |
| 观测覆盖率不是交易准入结论 | Confirmed | 降为 Monitor 连续性辅助信息 | Monitor 边界 |
| 视觉预览批准 | Pending | 未批准前不进入生产代码实现 | User |

## Design System

- Navigation and shell: 保留 QuantX Studio 顶部任务栏、左侧系统设置导航和现有页面宽度；只重构准入检查 section。
- Layout and density: 4px 基础间距；12–16px section 间距；当前态采用摘要、需关注区、三组紧凑清单；历史态桌面采用 2:1 事件主栏和汇总侧栏。
- Responsive behavior: 桌面三组清单可并排；中等宽度转两列；窄屏全部单列，历史汇总移至事件列表上方的折叠筛选区。
- Semantic colors: 通过 emerald、休市待机 blue、警告 amber、异常 rose、未观测 slate；交互和选中仍使用 primary blue。
- Typography: Inter 与现有中文系统字体；标题、正文保持现有 `text-ui-*`；时间、时长、计数和技术标识使用等宽数字。
- Spacing and sizing: 32px 以上交互高度；紧凑检查行约 36–40px；只有需关注项展示两行原因说明。
- Radius, borders, and shadows: 6–8px 圆角、1px 低对比边框、无大面积阴影和玻璃效果。
- Icons and data visualization: Lucide 状态图标；不再为 18 项重复绘制长时间条；异常时间线使用左侧语义状态线和明确文字。
- Motion and reduced-motion behavior: 150–200ms 颜色和展开过渡；`prefers-reduced-motion` 下关闭位移和动画。
- Focus, keyboard, and contrast rules: 可见 primary focus ring；选中、展开和状态同时提供图标、文本与颜色；正文对比达到可读标准。
- Domain anti-patterns: 不把休市涂成黄/红；不把 Monitor 断档当作主系统失败；不以覆盖率进度条暗示交易安全百分比；不隐藏活动异常。
- Existing components to reuse: `cn`、Lucide、URQL 查询、现有按钮和 panel token、准入文案映射、新鲜度计算。

## Information architecture

### 当前准入

1. **结论摘要**：显示“准入链路正常，当前休市待机”或“买入暂不可用，需要处理 N 项”，同时给出通过/待机/异常计数和最近检查时间。
2. **需关注事项**：只在存在 `FAILED` 或 `STANDBY` 时出现。异常优先，待机其次；展示业务名称、状态、权威消息和必要的新鲜度。
3. **三组检查清单**：
   - 运行与行情链路：服务端实盘开关、账户白名单、交易引擎、QMT 代理、代理模式、全市场行情、协议版本。
   - 账户事实与数据时效：快照对账、快照时效、交易活动分类、最近备份。
   - 执行与风险控制：执行控制、严重告警、报告死信、实盘窗口、外部活动、紧急停止、买入权限。
4. **工具**：`只看需关注` 和 `显示技术标识`。正常行默认只显示名称与“通过”；需要时可展开业务解释。

### 异常历史

1. **范围和总体摘要**：范围筛选、活动异常数、已恢复事件数、最近观测时间和 Monitor 连续性说明。
2. **异常事件时间线**：按日期倒序分组；每个事件显示受影响检测项、处理中/已恢复、原因、开始、恢复和持续时长。
3. **受影响检查项汇总**：只列所选范围内有异常的检查项，显示事件次数、当前状态，并可筛选主时间线；无异常的其余检查项合并为一行。
4. **待机与断档**：休市待机不进入事件时间线；观测断档使用中性提示说明“未观测不等于准入失败”。

## Page plan

| ID | Route / overlay | Purpose | Primary components | Meaningful states | Breakpoints | Data / APIs | Entry and exit | Preview IDs |
|---|---|---|---|---|---|---|---|---|
| P01 | `/settings/trading-safety` 当前准入 | 判断当前能否交易并定位需关注项 | 结论摘要、关注事项、分组清单、技术标识开关 | loading / all passed / standby / failed / freshness warning | desktop / tablet / narrow | A01 | 系统设置 → 交易安全；切换到异常历史 | V01 |
| P02 | `/settings/trading-safety` 异常历史 | 回看异常发生、恢复和影响范围 | 范围筛选、历史摘要、事件时间线、受影响检查项汇总 | loading / empty / active incident / resolved incidents / observer stale / unavailable | desktop / narrow | A02 | 当前准入 → 异常历史；检测项筛选可清除 | V02, V03 |

失败路径：历史查询不可用时，页面必须明确主系统当前判定不受影响，并提供“服务状态”入口；观测中断时保留已有事件但标记观测新鲜度，不能推断事件已经恢复；当前准入查询失败由现有页面级加载/错误行为承接。

## API integration map

| API ID | Method and contract | Consumer | Input | Used response fields | Auth | Loading / empty / error | Pagination / retry / realtime | Status |
|---|---|---|---|---|---|---|---|---|
| A01 | `useTradingSafety()`，`apps/web/src/features/trading-safety` | P01 | 当前唯一 `accountId` | `checkedAt`, `checks`, `reconciliationAgeSeconds`, `lastBackupAt` | 现有应用会话 | 保留现有加载与刷新；无检查时显示未取得判定 | 现有刷新机制 | Verified |
| A02 | `AccountExecutionSafetyHistoryQuery`，`apps/web/src/features/trading-safety/operations.ts` | P02 | `accountId`, `range` | `available`, `firstObservedAt`, `lastObservedAt`, `observerFresh`, `incidentsTruncated`, `checks`, `incidents` 及事件时间字段 | 现有应用会话 | 独立 loading、空历史、Monitor 不可用、观测中断 | 范围变化 network-only；无分页 | Verified |

本轮仅重排和派生现有字段，不修改 GraphQL schema，因此不触发契约迁移。

## Preview manifest

| Preview ID | Page / state | Breakpoint | Why a distinct preview is needed | Source page IDs | Status |
|---|---|---|---|---|---|
| V01 | 当前准入：17 项通过、1 项休市待机 | desktop 1545×1151 | 验证风险优先摘要、关注事项和三组紧凑清单 | P01 | approved |
| V02 | 异常历史：3 个已恢复事件、无活动异常 | desktop 1545×1151 | 验证事件主栏与受影响检查项侧栏的信息层级 | P02 | approved |
| V03 | 异常历史：相同数据 | narrow 768px | 验证双栏坍缩、筛选和时间元数据可读性 | P02 | approved |

## Approval log

| Date | Preview IDs | Decision | Requested changes or waiver | Resulting plan/design update |
|---|---|---|---|---|
| 2026-08-29 | Information architecture | approved | 采用两个推荐方向 | 锁定风险优先当前态与事件优先历史态 |
| 2026-08-30 | V01, V02, V03 | approved | 用户明确回复“批准预览并实施” | 进入生产组件、测试和验证阶段；批准稿保存至 `.codex_screenshots/design-previews/2026-08-29-account-trading-safety/` |

## Implementation verification report

### Implemented

- Pages and overlays: `/settings/trading-safety` 的当前准入和异常历史已拆分为独立生产组件；主设置面板只保留视图切换和数据接线。
- States and responsive behavior: 当前态实现结论摘要、需关注项、三组检查清单、只看需关注和技术标识；历史态实现范围筛选、事件摘要、日期时间线、受影响项筛选、Monitor 证据、空/不可用/观测中断状态，以及 768px 单列坍缩。
- API integrations: 完整复用 `useTradingSafety()` 和 `AccountExecutionSafetyHistoryQuery`；GraphQL schema、Monitor 采样和主系统判定逻辑均未修改。

### Verification

| Check | Command / method | Result |
|---|---|---|
| Typecheck | `npm run check` | Passed；TypeScript 与 UI size contract 通过 |
| Lint | `npm run lint` | Passed |
| Tests | `npm run test:run` | Passed；119 个测试文件、629 个测试全部通过 |
| Build | `npm run build` | Passed；Vite production build 与 JS/CSS bundle budget 通过 |
| Browser and breakpoints | In-app browser at 1545×1151, 1280×800 and 768px | Blocked；浏览器运行时未发现可用浏览器连接，未伪造截图验收 |
| Accessibility | 组件语义与测试 | 通过静态与单元验证：图标+文本+颜色、`aria-pressed`、`aria-expanded`、可见 focus ring、无 hover-only 信息；实际键盘走查随浏览器连接补验 |

### Preview comparison

| Preview ID | Implementation evidence | Deviation | Rationale / approval |
|---|---|---|---|
| V01 | `AccountExecutionGateCurrentView.tsx` | 正常项支持按行展开补充说明 | 保持批准稿风险优先层级，同时避免隐藏权威检查解释 |
| V02 | `AccountExecutionSafetyHistoryView.tsx` 桌面双栏 | 不再展示装饰性右箭头 | 事件卡当前没有独立详情页，移除伪交互 |
| V03 | `AccountExecutionSafetyHistoryView.tsx` `lg` 以下单列 | 实际截图待补 | 已实现批准稿中的 2×2 指标、筛选 disclosure、单列事件与观测 footer |

### Remaining questions

- 浏览器连接恢复后，补做 1545×1151、1280×800 和 768px 的实际页面截图对比；该限制不影响类型、行为测试或生产构建结果。
