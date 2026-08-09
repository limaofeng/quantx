# QuantX Studio 视觉迁移计划（v1.5）

## 0. 目标

将 QuantX 桌面端完整迁移为高密度 Studio Workbench 视觉体系，参考 `nexus-gw/client/src/components/studio-workbench` 的抽象实现方式，但不把它视为单一页面样式复制，而是沉淀为 QuantX 自己的通用工作台组件层。

迁移完成后，QuantX 应具备：

- 全局桌面壳层采用 Studio 风格：ActivityBar、可伸缩资源侧栏、工作区、状态栏。
- 交易、策略、持仓、数据管理、筛选、详情类页面统一进入高密度工作台表达。
- 右键菜单、tab 管理、状态栏、可拖拽面板成为通用交互能力。
- 保留 QuantX 金融交易语义：A 股红涨绿跌、风控状态、账户/委托/成交审计可读性。

本计划只覆盖桌面端 Studio 视觉迁移，不再把移动端兼容纳入 v1.0/v1.1 的交付范围。

明确不做：

- 不为手机 viewport 单独设计导航、底部栏、卡片重排或触控手势。
- 不以 `sm/md/lg` 断点适配移动端作为验收项；保留既有断点仅用于桌面窄窗口降级。
- 不迁移 `MobileTradingPage` 等移动端专用页面；后续如要恢复移动端体验，应单独立项。

## 1. 设计原则

### 1.1 Studio 是抽象组件，不是页面模板

参考 Nexus Studio 的核心抽象：

- `StudioWorkbench`：负责整体空间结构。
- `ActivityBar`：负责全局或局部模式切换。
- `TabBar`：负责多工作区、多资源、多详情页切换。
- `StudioMenu`：负责右键菜单、列菜单、资源菜单等上下文动作。
- `StatusBar`：负责连接状态、账户状态、运行状态、数据时效等低占用信息。

QuantX 迁移时应建立自己的 `apps/web/src/components/studio-workbench/`，保持组件命名和心智模型相近，但样式 token、交易语义和状态字段按 QuantX 重定义。

### 1.2 空间利用率优先

桌面端主要目标是减少当前大 header、大 padding、大圆角卡片造成的信息损耗：

- 全局 header 从 64px 的页面级标题区，收敛为 ActivityBar + StatusBar + 页面内紧凑工具栏。
- 左侧 ActivityBar 在 QuantX 中优先等价承接原桌面 Sidebar 的导航功能，必须覆盖原菜单的全部主要入口。
- 左侧 ActivityBar 的基础几何必须参考 Nexus Studio 源码：外栏 `w-16`，按钮 `w-10 h-10 rounded-xl`，栏内 `py-6 gap-4`，选中指示为按钮左侧 `-left-3 h-6 w-1 rounded-r-full`，不得按页面自行改宽度或选中态。
- 页面内容取消默认 `max-w-[1600px]` 居中容器，工作台页面默认占满可用宽高。
- Studio 页面高度采用“外层占满浏览器视口、内部面板自行滚动”的工作台模型；页面级组件不得再叠加 `h-screen`，应使用 `h-full min-h-0` 继承壳层高度。
- StatusBar 是 StudioWorkbench 最外层底栏，必须贯穿 ActivityBar、Sidebar 和主工作区，目标高度为 `22px`。
- 操作型页面避免卡片套卡片，使用细分隔线、紧凑工具条、表格、面板和 tab。
- 卡片只用于重复实体、弹窗、空状态、需要明确框选的局部对象。
- 所有关键路径按桌面鼠标、键盘和右键菜单优化，不牺牲信息密度去兼容移动端堆叠。

### 1.3 近似复刻 Nexus Studio 的密度与质感

视觉基调采用深色 Studio：

- 背景：`#0b1120` / `#09111f` / `#020617` 分层。
- 边框：`border-white/5` 到 `border-white/10`。
- 文本：`slate-100`、`slate-300`、`slate-500` 分级。
- 字号：状态、标签、工具栏以 `10px-12px` 为主；正文和表格以 `12px-14px` 为主。
- 圆角：工作台结构使用 `6px-8px`，按钮和菜单最多 `8px-12px`；逐步减少当前 `rounded-2xl` 和 `rounded-[2rem]` 的使用。

## 2. 主色建议

主色待最终确认，v1.0 计划建议先实现主题 token，让主色可配置，不把色值散落在业务组件中。

### 2.1 推荐默认：Quant Red

- Primary：`#DC2626`
- Active：`#F87171`
- Glow：`rgba(220, 38, 38, 0.18)`
- 适用理由：
  - 符合 A 股“红色上涨”的交易语境，有正向品牌暗示。
  - 使用低噪音朱砂红作为 Studio 选中、焦点和品牌色，不直接复用行情上涨数值红。
  - 与深色 Slate 背景配合后辨识度高，但需控制填充面积，避免界面过热。
  - 与 QuantX 的交易属性更贴合，比通用 SaaS 蓝更有记忆点。

### 2.2 备选一：Quant Blue

- Primary：`#3B82F6`
- Active：`#60A5FA`
- 适用理由：稳健、通用、迁移风险低。
- 缺点：品牌记忆点较弱，和普通 SaaS 后台更接近。

### 2.3 备选二：Quant Cyan

- Primary：`#06B6D4`
- Active：`#22D3EE`
- 适用理由：与实时行情、数据流、终端感匹配。
- 缺点：大面积使用时偏亮，容易让界面显得“发青”，不适合作为当前默认主色。

### 2.4 备选三：Quant Amber

- Primary：`#F59E0B`
- Active：`#FBBF24`
- 适用理由：金融信任感强，适合资产、资金、收益场景。
- 缺点：容易与 warning 语义冲突，不建议作为唯一全局主色。

### 2.5 交易语义色不可被主色覆盖

- Studio 主色红只用于品牌、选中态、焦点态和通用主操作，不直接代表行情涨跌。
- A 股上涨：数值红色系，建议 `#EF4444` / `#F87171`。
- A 股下跌：绿色系，建议 `#22C55E` / `#4ADE80`。
- 风险/拒单：`#F43F5E`。
- 警告/待确认：`#F59E0B`。
- 成功/连接正常：`#10B981`。

## 3. 组件与架构改造

### 3.1 新增 Studio Workbench 组件层

建议路径：

- `apps/web/src/components/studio-workbench/StudioWorkbench.tsx`
- `apps/web/src/components/studio-workbench/ActivityBar.tsx`
- `apps/web/src/components/studio-workbench/TabBar.tsx`
- `apps/web/src/components/studio-workbench/StudioMenu.tsx`
- `apps/web/src/components/studio-workbench/StudioTabContextMenu.tsx`
- `apps/web/src/components/studio-workbench/StatusBar.tsx`
- `apps/web/src/components/studio-workbench/themeStyles.ts`
- `apps/web/src/components/studio-workbench/types.ts`
- `apps/web/src/components/studio-workbench/useStudioMenu.ts`
- `apps/web/src/components/studio-workbench/useStudioTabs.ts`

QuantX 可以复制 Nexus 的 `StudioMenu` 思路，但需要补齐：

- 菜单项键盘上下移动、Enter/Space 选择、Esc 关闭。
- 打开菜单后 focus 进入菜单，关闭后 focus 回到触发元素。
- disabled 项不可聚焦或不可执行，表现一致。
- `aria-label`、`role=menu`、`role=menuitem` 保留。
- 菜单宽度、高度、viewport clamp 保留。

### 3.2 Studio 状态持久化

不建议仅为侧栏宽度引入 Zustand。QuantX 当前已用 localStorage 保存 sidebar 折叠状态，Studio v1.0 可沿用 localStorage hook：

- `studio.sidebarWidths`
- `studio.activityMode`
- `studio.openTabs`
- `studio.activeTabId`
- `studio.layoutDensity`

若后续工作台状态跨页面复杂化，再评估引入 Zustand 或独立 UI store。

### 3.3 全局 Layout 迁移

当前 `Layout` 应拆为两套壳层：

- `AppLayout`：保留基础 provider、错误边界、路由渲染，不再强制大 header 和大内容 padding。
- `StudioAppShell`：桌面端默认壳层，提供全局 ActivityBar、全局 StatusBar、主工作区。

迁移后的桌面主导航建议：

- ActivityBar 主区域：仪表板、持仓管理、交易下单、清仓管理、策略管理、股票筛选、数据管理，顺序和原桌面 Sidebar 保持一致。
- ActivityBar 底部：总资产/账户入口、主题切换、通知、当前 Studio 标识。
- 页面级功能、资源列表、动作按钮、账户/布局/模式切换应优先进入 ActivityBar 右侧的 Studio 独立 Sidebar，不应放进最左侧全局快捷区。
- 同一个 Studio Sidebar 的宽度不应因页面内部功能切换而自动变化；除非明确是不同资源树，否则宽度只能通过拖拽/键盘调整改变。
- StatusBar 位于 `StudioWorkbench` 最外层底部，而不是主内容区内部；布局结构应是上层 `ActivityBar + Sidebar + Content`，下层全宽 `StatusBar`。
- 页面内部模式切换不得替代全局菜单；交易页的图表、下单、委托、成交、账户等内部模式应放在页面内紧凑工具栏、TabBar 或资源侧栏中。
- 资源侧栏按路由或模式渲染，如策略实例、持仓列表、数据分类、筛选条件。

### 3.4 主题 token

新增 Studio token，避免在业务组件中散落 `bg-[#0b1120]`：

- `--studio-bg`
- `--studio-panel`
- `--studio-panel-muted`
- `--studio-border`
- `--studio-text`
- `--studio-text-muted`
- `--studio-primary`
- `--studio-primary-soft`
- `--studio-danger`
- `--studio-success`
- `--studio-warning`

Tailwind 使用上优先通过 CSS 变量或集中 class helper 组合，不在页面组件重复维护大段色值。

## 4. 页面迁移路线

### 4.1 第一阶段：工作台基础设施

目标：不改业务逻辑，只建立 Studio 组件层和视觉 token。

- 新增 `studio-workbench` 组件目录。
- 复制并 QuantX 化 `StudioMenu`、`TabBar`、`ActivityBar`、`StatusBar`。
- 增加 `StudioTheme`，支持 `cyan | blue | red | amber | emerald | rose`。
- 为右键菜单建立通用 item schema：`id / label / icon / shortcut / danger / disabled / checked / onSelect`。
- 为 tab 建立通用 schema：`id / type / name / icon / isDirty / isPreview / payload`。

验收：

- 组件可在 story-like 本地测试页或临时示例中渲染。
- 右键菜单不会溢出 viewport。
- 侧栏拖拽和键盘调整可用。
- `npm run check` 通过。

### 4.2 第二阶段：交易终端迁移

目标：将当前交易页从“大卡片终端”迁入真正全屏 Studio。

- `TradingPage` 改为 `StudioWorkbench` 页面模式。
- Activity modes：
  - `CHART`：图表与盘口。
  - `ORDER`：下单面板。
  - `ORDERS`：当日委托、活跃委托。
  - `TRADES`：当日成交、历史成交。
  - `ACCOUNT`：账户资产与持仓。
- 主内容默认三栏：图表、盘口、下单/活跃委托。
- 历史记录从大浮层改为 tab 或右侧/底部可切换 panel。
- 状态栏展示账户、交易连接、行情连接、当前标的、活跃委托数。

验收：

- 交易页桌面首屏没有全局 header 占位损耗。
- 图表、盘口、下单区在 1440px 宽度下同时可见。
- 当日委托、今日成交、历史委托、历史成交可达且不遮挡主工作流。
- 不改变任何 GraphQL 查询和下单业务语义。

### 4.3 第三阶段：策略工作台迁移

目标：将策略管理、策略运行、策略详情统一为多 tab 工作台。

- Activity modes：
  - `CATALOG`：可用策略。
  - `RUNS`：运行实例。
  - `MONITOR`：图表监控。
  - `BACKTEST`：回测与版本。
  - `TRACE`：DecisionTrace、TradeIntent、执行日志。
  - `CONFIG`：参数、网格账本、仓位归因。
- `StrategyMonitor` 去除大圆角 Card 容器，成为 Workbench 主面板。
- 运行实例、回测版本、单标的详情通过 tab 打开。
- 右键菜单用于策略实例、回测版本、日志行、交易意图行的上下文操作。

验收：

- 同一策略可同时打开多个 run/backtest tab。
- 决策追踪、执行日志、交易意图之间切换不造成页面跳转。
- 活跃运行状态、回测版本、连接状态进入状态栏。

### 4.4 第四阶段：数据管理与筛选迁移

目标：把数据门户、市场数据、板块、财务、交易流水、筛选器迁为资源树 + 表格/详情的 Studio。

- 左侧资源侧栏：
  - 市场数据、板块、交易日历、持仓同步、交易流水、财务数据。
  - 支持搜索、折叠、刷新。
- 主内容：
  - 表格、详情、同步任务、日志通过 tab 打开。
  - 表格列菜单支持排序、隐藏/显示、固定列、复制字段名。
- 筛选器：
  - 条件构建器作为顶部或左侧 compact panel。
  - 结果表格占主区域。

验收：

- 大表格页面减少卡片 padding，表格有效可视行数明显增加。
- 列菜单和资源菜单可用。
- 同步任务状态可在状态栏或底部任务区查看。

### 4.5 第五阶段：持仓、清仓、仪表板与详情页统一

目标：完成全应用桌面端视觉统一。

- 持仓管理：
  - 从大卡片网格逐步迁为列表/表格 + 右侧详情抽屉或 tab。
  - 持仓卡片保留为可选视图，但密度降低到 secondary。
- 清仓管理：
  - 用 Activity mode 区分计划、执行、历史、审计。
- 仪表板：
  - 不强制成为 IDE 页面，但应使用 Studio token 和紧凑 dashboard 布局。
  - KPI 卡片缩小，图表和任务列表提高信息密度。
- 个股详情：
  - 图表、财务、交易流水、策略关联通过 tab 管理。

验收：

- 桌面端所有主路由不再出现旧式大圆角、大 padding、营销式卡片堆叠。
- 页面切换后 ActivityBar、StatusBar、菜单、tab 视觉一致。

## 5. 右键菜单迁移范围

### 5.1 v1.0 必须支持

- TabBar：
  - 关闭。
  - 关闭其他。
  - 关闭右侧。
  - 关闭全部。
- 交易终端：
  - 标的、委托、成交、持仓行上下文菜单。
  - 常用动作：复制代码、复制名称、查看详情、打开图表、创建策略、撤单入口。
- 策略工作台：
  - 策略实例、run、backtest、trace 行上下文菜单。
  - 常用动作：打开详情、复制 ID、查看日志、重新回测入口。
- 数据管理：
  - 资源树菜单、表格列菜单、表格行菜单。

### 5.2 安全约束

右键菜单中不得隐藏高风险交易动作：

- 真实下单、撤单、清仓、重跑实盘策略必须走显式按钮或确认弹窗。
- 菜单可提供入口，但必须二次确认。
- 菜单 action 不得绕过现有权限、风控、GraphQL mutation 校验。

## 6. 视觉替换清单

### 6.1 全局替换方向

- `rounded-2xl`、`rounded-[2rem]`：工作台结构中替换为 `rounded-md` 或 `rounded-lg`。
- `p-6`、`p-8`、`p-12`：操作型页面替换为 `p-2`、`p-3`、`p-4`。
- `shadow-xl`、`shadow-2xl`：主工作台结构中减少使用，改用边框和背景层级。
- `glass-effect`：仅保留在浮层或轻量 overlay，不作为主结构默认效果。
- 大标题说明文案：从工作流页面移除，必要信息进入状态栏、tooltip 或详情面板。

### 6.2 保留场景

- 仪表板 KPI 可以保留卡片，但缩小圆角与 padding。
- 空状态可以保留更强视觉表现，但不能占据已打开工作区的常规路径。
- 确认弹窗、危险操作弹窗保留清晰层级和充足留白。

## 7. 测试与验收

### 7.1 工程验证

每阶段至少执行：

```powershell
cd apps/web
npm run check
npm run lint
```

涉及 GraphQL 查询或 schema 变更时，按项目规则额外执行：

```powershell
cd apps/web
npm run codegen
npm run check
```

视觉迁移原则上不应改 GraphQL schema；若发生字段变化，必须同轮更新前端查询和生成类型。

### 7.2 桌面视觉验收 viewport

标准桌面：

- 1440 x 900
- 1920 x 1080
- 2560 x 1440

中等桌面与窄窗口：

- 1024 x 768
- 1280 x 800

不验收手机与平板竖屏 viewport。若现有页面仍存在 `Mobile*` 组件或移动断点，本次迁移不以它们作为交付目标。

### 7.3 核心冒烟路径

- 交易页：
  - 选择标的。
  - 查看图表。
  - 点击盘口价格填入下单价。
  - 查看当日委托、今日成交、历史委托、历史成交。
  - 打开右键菜单并关闭。
- 策略页：
  - 打开策略详情。
  - 打开运行监控。
  - 切换回测版本。
  - 查看 DecisionTrace、TradeIntent、日志。
- 数据管理：
  - 打开资源分类。
  - 刷新数据。
  - 查看表格。
  - 使用列菜单和资源菜单。
- 持仓：
  - 查看当前持仓。
  - 打开持仓详情。
  - 进入清仓管理。

### 7.4 视觉质量门槛

- 所有可点击元素有 hover 与 focus-visible 状态。
- 右键菜单不遮挡 viewport 外且可 Esc 关闭。
- 文字不溢出按钮、tab、状态栏、表格 cell。
- 不出现卡片嵌套卡片的主要布局。
- 工作台页面无不必要横向滚动。
- 暗色模式达到可读对比，亮色模式至少不退化；若 v1.0 以暗色为主，亮色可作为 v1.1 完整补齐项。

## 8. 交付顺序

建议按以下顺序落地，避免全量重构失控：

1. 冻结本计划与主色方向。
2. 新增 Studio token 和基础组件层。
3. 迁移 `TradingPage`，作为空间利用率样板。
4. 迁移策略详情与策略运行页，验证多 tab 和状态栏。
5. 迁移数据管理与筛选页，验证资源树和表格菜单。
6. 迁移持仓、清仓、个股详情。
7. 收敛仪表板和全局导航视觉。

## 9. 风险与控制

- 风险：完整视觉迁移影响面大。
  - 控制：按路由逐步迁移，先保留旧页面组件，外层用 Studio shell 承载。
- 风险：复制 `StudioMenu` 后可访问性弱于 Radix。
  - 控制：在迁入时补齐键盘导航、focus 管理和测试。
- 风险：过度深色终端化导致信息疲劳。
  - 控制：使用 Slate 层级、细边框、有限主色，不使用大面积高饱和色。
- 风险：主色与交易红绿语义冲突。
  - 控制：主色只用于导航、active、focus、非交易 CTA；涨跌、风险、成交状态使用专用语义色。
- 风险：业务回归被视觉改造掩盖。
  - 控制：每阶段只改布局与表现，不改 GraphQL 契约、不改下单/策略业务逻辑。

## 10. 决策点

本计划默认值：

- 默认主色：Quant Red。
- 默认桌面风格：近似复刻 Nexus Studio 的深色高密度工作台。
- 默认菜单实现：复制并增强 Nexus `StudioMenu`。
- 默认状态持久化：localStorage hook，不立即引入 Zustand。
- 默认迁移范围：仅桌面端完整视觉迁移，不考虑移动端兼容。

待确认：

- 是否继续采用 Quant Red 作为默认主色，或在品牌确认后切换到其他 Studio 主题。
- 是否为 Studio Workbench 增加独立演示页或 Storybook 类验证环境。

## 11. 当前落地进度（2026-06-01）

### 11.1 已完成

- Studio 基础设施：
  - 已新增 `apps/web/src/components/studio-workbench/`。
  - 已落地 `StudioWorkbench`、`ActivityBar`、`TabBar`、`StudioMenu`、`StudioTabContextMenu`、`StatusBar`、主题 token 与菜单 hook。
  - `StudioMenu` 已具备 viewport clamp、Esc 关闭、键盘方向键导航、focus 回收与 `role=menu/menuitem`。
- 全局壳层：
  - `Layout` 已对 Studio 路由隐藏旧 sidebar/header。
  - Studio 路由已覆盖 `TradingPage`、策略页、数据管理、筛选、持仓/清仓、Dashboard、个股详情与数据子页面。
- 交易终端：
  - `TradingPage` 已改为三栏 Studio 工作台。
  - 盘口、活跃委托、委托记录、成交记录已补齐右键菜单。
  - 右键菜单提供复制、查看详情、创建策略、撤单入口；撤单仍保留确认。
- 持仓：
  - 持仓页已进入 `PortfolioStudioShell`。
  - `HoldingCard` 已由展示型大卡片收敛为高密度桌面持仓行。
  - 持仓行已补齐查看详情、快速交易、创建策略、复制代码/名称、清仓入口；清仓仍保留确认。
- 策略：
  - 策略列表、运行页、详情页已进入 `StrategyStudioShell`。
  - 策略实例卡已具备上下文菜单。
  - 决策审计 `TradeIntent` 行、执行跟踪 trace 卡片、回测版本行、日志终端行已补齐上下文菜单。
- 数据与筛选：
  - 数据门户与数据子路由已进入 `DataStudioShell`。
  - 筛选页已进入 Studio 布局，筛选结果行已有右键菜单。
  - 财务数据表已支持列右键菜单：升序/降序、清除排序、复制列名、固定列、隐藏列、恢复默认列。
  - 财务数据行、交易流水行、板块行已支持打开详情与复制字段；交易流水/板块表头已支持复制列名/字段 ID。
- 仪表板与个股详情：
  - Dashboard 与个股详情已接入对应 Studio shell，并使用紧凑工具栏、tab 与状态栏。
- 视觉密度护栏：
  - `StudioWorkbench` 已增加 `data-studio-workbench` 标记。
  - Studio 壳层内残留的大圆角与重阴影会被统一压缩，降低深层旧组件对桌面工作台密度的破坏。
  - Studio 壳层内残留的 `p-6/p-8/p-12`、`px-6/px-8`、`py-6/py-8`、`gap-6/gap-8` 已增加统一压缩护栏，进一步降低旧组件大留白对桌面信息密度的影响。
- 工程验收：
  - 已清理阻塞 `npm run check` 的前端既有 TypeScript 错误，包括测试环境 mock、路由懒加载类型、日志/性能工具类型边界、清仓 mock 持仓字段与筛选 mock 类型。
  - `npm run check` 已从迁移阻塞项转为通过项。

### 11.2 已验证

- `npm run check` 通过。
- `npm run lint` 通过：0 errors，261 warnings。
- `npm run build` 通过。
- touched-file TypeScript 过滤通过：交易/持仓相关文件没有新增类型错误。
- Playwright 桌面快照已验证：
  - `http://127.0.0.1:5250/trading`
  - `http://127.0.0.1:5250/holdings`
  - `http://127.0.0.1:5250/strategies`
  - `http://127.0.0.1:5250/settings/data/financial`
  - `http://127.0.0.1:5250/settings/data/transactions`
  - `http://127.0.0.1:5250/screening`

### 11.3 后续增强项

- 部分深层业务组件仍可继续逐文件替换旧式说明型布局；当前已用 Studio 壳层护栏压缩圆角、阴影、padding 与 gap，不再阻塞桌面 Studio 迁移基础验收。
- `npm run lint` 仍有 261 个既有 warning，主要是 `any`、未使用变量和 hook dependency 警告；当前不阻塞构建与桌面视觉迁移验收，可另立代码质量清理任务。
- 移动端相关页面和断点不再纳入本计划，不阻塞 Studio 桌面迁移验收。
