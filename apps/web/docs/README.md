# QuantX 前端项目文档中心

> 🚀 现代化的量化交易前端应用，基于 React 18 + TypeScript + Vite 构建

[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-blue)](https://www.typescriptlang.org/)
[![React](https://img.shields.io/badge/React-18.3-61dafb)](https://reactjs.org/)
[![Vite](https://img.shields.io/badge/Vite-5.4-646cff)](https://vitejs.dev/)
[![TailwindCSS](https://img.shields.io/badge/TailwindCSS-3.4-38bdf8)](https://tailwindcss.com/)

---

## 📚 文档结构

### 开发指南 (guides/)

核心技术栈和最佳实践指南，为开发者提供系统级的技术参考。

- **[基础设施指南](./guides/INFRASTRUCTURE.md)**
  项目架构、技术栈、目录结构和核心配置说明

- **[Mock 系统指南](./guides/MOCK_SYSTEM.md)**
  MSW (Mock Service Worker) 使用指南，包含配置、数据管理和调试技巧

- **[日志系统指南](./guides/LOGGER_GUIDE.md)**
  统一日志系统的使用规范、配置和最佳实践

- **[日志系统示例](./guides/LOGGER_EXAMPLES.md)**
  日志系统的实际代码示例和常见场景演示

- **[ESLint 配置指南](./guides/ESLINT_CONFIG.md)**
  ESLint 规则配置说明，包括 `any` 类型使用策略

### 归档文件 (archive/)

已归档的历史文档和废弃的技术方案。

> 注：归档目录用于存放不再使用但具有参考价值的历史文档。

---

## 🚀 快速开始

### 环境要求

- Node.js >= 18.0.0
- npm >= 9.0.0

### 安装依赖

```bash
npm install
```

### 环境配置

复制环境变量模板：

```bash
cp .env.example .env.local
```

编辑 `.env.local` 文件，配置必要的环境变量。

### 开发服务器

```bash
npm run dev
```

访问 [http://localhost:3000](http://localhost:3000) 查看应用。

### 构建部署

```bash
# 构建生产版本
npm run build

# 预览生产版本
npm run preview
```

### 新手指南

如果你是新加入的开发者，建议按以下顺序阅读文档：

1. **[基础设施指南](./guides/INFRASTRUCTURE.md)** - 了解项目整体架构
2. **[Mock 系统指南](./guides/MOCK_SYSTEM.md)** - 掌握本地开发工具
3. **[日志系统指南](./guides/LOGGER_GUIDE.md)** - 学习调试和日志记录

---

## ✨ 核心特性

- 🎯 **现代技术栈**: React 18 + TypeScript + Vite
- 🎨 **UI 组件库**: Radix UI + shadcn/ui + TailwindCSS
- 📊 **数据可视化**: Recharts + Lightweight Charts
- 🔌 **GraphQL 集成**: Apollo Client + GraphQL WebSocket
- 🧪 **测试框架**: Vitest + Testing Library
- 🖥️ **桌面工作台**: 面向 PC，最低支持 1280px 视口宽度
- 🌙 **主题切换**: 明暗主题支持
- ⚡ **性能优化**: 代码分割、懒加载
- 🔧 **开发体验**: ESLint + Prettier + Husky
- 🎭 **GraphQL Mock**: MSW 驱动的部分查询 Mock 系统

---

## 🏗️ 项目架构

```
src/
├── components/          # 通用组件
│   ├── ui/             # shadcn/ui 基础组件
│   └── ...             # 业务组件
├── features/           # 功能模块
│   ├── dashboard/      # 仪表板
│   ├── trading/        # 交易相关
│   ├── screening/      # 股票筛选
│   └── strategies/     # 策略管理
├── shared/             # 共享资源
│   ├── types/          # 类型定义
│   ├── constants/      # 常量
│   └── utils/          # 工具函数
├── mocks/              # GraphQL Mock 系统
│   ├── data/           # Mock 数据文件
│   ├── handlers.ts     # MSW 处理器
│   ├── browser.ts      # 浏览器端配置
│   └── mockManager.ts  # Mock 管理工具
├── core/               # 核心功能
├── hooks/              # React Hooks
├── pages/              # 页面组件
└── __tests__/          # 测试文件
```

---

## 📦 主要功能模块

### 📊 交易仪表板

- 实时行情数据
- 投资组合概览
- 性能指标分析

### 💹 股票交易

- 实时下单
- 订单管理
- 交易历史

### 🔍 股票筛选

- 多维度筛选
- 自定义条件
- 实时数据更新

### 📈 策略管理

- 策略创建和编辑
- 回测分析
- 风险控制

---

## 🛠️ 开发工作流

### 代码质量检查

```bash
# 代码检查
npm run lint

# 代码格式化
npm run format

# 类型检查
npm run check

# 完整验证（类型 + 检查 + 格式）
npm run validate
```

### 测试

```bash
# 运行测试（watch 模式）
npm test

# 单次运行测试
npm run test:run

# 测试覆盖率
npm run test:coverage

# 测试 UI
npm run test:ui
```

### GraphQL 代码生成

```bash
# 生成 GraphQL 类型和 hooks
npm run codegen

# Watch 模式
npm run codegen:watch
```

### Git 提交规范

项目使用 Conventional Commits 规范：

```bash
feat: 新功能
fix: 修复bug
docs: 文档更新
style: 代码格式调整
refactor: 代码重构
test: 测试相关
chore: 构建工具或辅助工具的变动
```

Husky 会在提交前自动运行代码检查和格式化。

---

## 🔧 技术栈

### 核心框架

- **React 18**: 用户界面库
- **TypeScript**: 类型安全
- **Vite**: 构建工具

### UI 组件

- **TailwindCSS**: 原子化 CSS 框架
- **Radix UI**: 无样式组件库
- **shadcn/ui**: 预设计组件
- **Lucide React**: 图标库

### 数据管理

- **Apollo Client**: GraphQL 客户端
- **TanStack Query**: 状态管理
- **React Hook Form**: 表单处理
- **Zod**: Schema 验证

### 可视化

- **Recharts**: 通用图表库
- **Lightweight Charts**: 金融 K 线图表
- **Framer Motion**: 动画库

### 开发工具

- **ESLint**: 代码检查
- **Prettier**: 代码格式化
- **Husky**: Git hooks
- **Vitest**: 测试框架
- **MSW**: API Mock

---

## 🌍 环境配置

项目支持多环境配置：

- `.env.development` - 开发环境
- `.env.staging` - 测试环境
- `.env.production` - 生产环境

### 主要环境变量

```bash
# API 配置
VITE_GRAPHQL_HTTP_URL=/graphql
VITE_GRAPHQL_WS_URL=ws://192.168.5.6:8080/graphql

# 功能开关
VITE_MOCK_ENABLED=true
VITE_ENABLE_DEBUG=true
VITE_ENABLE_PERFORMANCE_MONITORING=true

# Mock 配置
VITE_MOCK_DEFAULT_QUERIES=portfolioSummary,GetCurrentAccount
VITE_MOCK_DELAY=200
VITE_MOCK_VERBOSE=true
```

---

## 📱 浏览器支持

- Chrome >= 88
- Firefox >= 85
- Safari >= 14
- Edge >= 88

---

## 📝 文档维护

### 添加新文档

- **guides/** - 添加新的技术指南或系统级文档

### 归档文档

当文档不再适用于当前项目时，请移动至 `archive/` 目录，并在文件头部注明归档原因和时间。

---

## 🤝 贡献指南

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'feat: add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

---

## 📄 许可证

本项目采用 MIT 许可证。

---

**最后更新**: 2025-10-05
**维护者**: QuantX 开发团队

---

⭐ 如果这个项目对你有帮助，请给一个 Star！
