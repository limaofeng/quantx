# 归档目录说明

## 目录概述

`docs/archive/` 目录用于存放已被替代、废弃或不再维护的文档和代码文件。这些文件保留下来用于参考、历史记录或未来可能的恢复需求。

## 归档原则

### 应该归档的文档

以下类型的文件应当移入归档目录：

1. **已废弃的功能文档** - 功能已被移除或重写
2. **过时的技术方案** - 已被更好的方案替代
3. **历史版本代码** - 重大重构前的旧实现
4. **实验性特性文档** - 未被采纳的实验性功能
5. **迁移完成的临时文档** - 如迁移指南、ADR 等

### 不应归档的文档

以下类型的文件应当保留在主文档区：

- 当前正在使用的功能文档
- 有效的开发指南和规范
- 活跃的架构决策记录 (ADR)
- 正在进行的项目文档

## 归档流程

### 标准归档步骤

1. **评估文档状态**
   - 确认文档已完全过时或被替代
   - 检查是否有其他文档依赖此文档
   - 评估保留价值（历史参考、回滚需求等）

2. **创建归档子目录**
   ```bash
   # 在 docs/archive/ 下创建语义化的子目录
   docs/archive/[category-name]/
   ```

3. **移动文件并保留元信息**
   - 移动文件到对应的归档子目录
   - 在归档子目录中添加 `README.md` 说明归档原因和时间
   - 更新主文档中的引用链接

4. **记录归档信息**
   在归档子目录的 `README.md` 中应包含：
   - 归档日期
   - 归档原因
   - 被什么替代（如适用）
   - 相关背景信息

### 归档目录命名规范

推荐使用以下命名模式：

```
docs/archive/
  ├── [feature-name]-legacy/     # 功能名称 + legacy
  ├── [tech-stack]-old/          # 技术栈 + old
  ├── [date-topic]/              # 日期 + 主题 (如: 2024-q1-migration)
  └── deprecated-[category]/     # deprecated + 类别
```

## pages-legacy/ 目录说明

### 目录用途

`pages-legacy/` 存放了项目早期的页面级组件实现。这些组件已被基于 feature-based 架构的新实现替代。

### 归档背景

- **归档日期**: 2024年10月
- **归档原因**: 架构重构 - 从页面级组件迁移到基于功能模块的架构
- **新架构位置**: `src/features/*/pages/`

### 包含的文件

该目录包含以下旧页面组件：

- `Dashboard.tsx` - 旧版仪表盘页面
- `History.tsx` - 旧版历史记录页面
- `Holdings.tsx` - 旧版持仓页面
- `Liquidation.tsx` - 旧版清算页面
- `StockDetail.tsx` - 旧版股票详情页面
- `StockScreening.tsx` - 旧版股票筛选页面
- `Strategies.tsx` - 旧版策略列表页面
- `StrategyDetail.tsx` - 旧版策略详情页面
- `Trading.tsx` - 旧版交易页面
- `TradingGraphQL.tsx` - 旧版 GraphQL 交易页面
- `not-found.tsx` - 旧版 404 页面

### 参考价值

这些文件保留的价值：

1. **业务逻辑参考** - 旧实现中的业务规则和计算逻辑
2. **UI 设计参考** - 旧版界面的设计思路和交互模式
3. **数据结构参考** - 旧版数据模型和 API 集成方式
4. **回滚备份** - 如新架构出现重大问题时的参考

### 对应的新实现

| 旧文件 | 新实现位置 |
|--------|-----------|
| `Dashboard.tsx` | `src/features/dashboard/pages/DashboardPage.tsx` |
| `Holdings.tsx` | `src/features/portfolio/pages/HoldingsPage.tsx` |
| `Liquidation.tsx` | `src/features/portfolio/pages/LiquidationPage.tsx` |
| `StockDetail.tsx` | `src/features/stocks/pages/StockDetailPage.tsx` |
| `StockScreening.tsx` | `src/features/screening/pages/StockScreeningPage.tsx` |
| `Strategies.tsx` | `src/features/strategies/pages/StrategiesPage.tsx` |
| `Trading.tsx` / `TradingGraphQL.tsx` | `src/features/trading/pages/TradingPage.tsx` |
| `not-found.tsx` | `src/pages/NotFound.tsx` |

## 归档文件的使用

### 查看归档文件

归档文件仅供参考，不应在生产代码中引用。如需查看：

```bash
# 查看特定归档目录
ls docs/archive/pages-legacy/

# 搜索归档中的内容
grep -r "关键词" docs/archive/
```

### 恢复归档文件

如需恢复归档内容：

1. 评估恢复的必要性
2. 检查与当前代码库的兼容性
3. 更新依赖和 API 调用
4. 进行充分测试
5. 创建 ADR 记录恢复决策

### 清理归档

归档文件应定期审查（建议每季度）：

- 评估是否仍有保留价值
- 完全过时的文件可以删除
- 更新归档说明文档

## 最佳实践

1. **及时归档** - 文档过时后应尽快归档，避免混淆
2. **详细说明** - 归档时添加充分的上下文信息
3. **保持结构** - 归档目录也应保持良好的组织结构
4. **定期清理** - 定期审查归档内容，删除无价值文件
5. **更新引用** - 归档后及时更新主文档中的相关引用

## 相关文档

- [文档结构说明](../README.md)
- [开发指南](../guides/)
- [架构决策记录](../development/adr/)
