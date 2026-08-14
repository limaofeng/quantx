import path from 'node:path';
import { defineConfig } from 'vitepress';

const docsVersion = process.env.QUANTX_DOCS_VERSION?.trim() || 'development';

export default defineConfig({
  base: '/docs/',
  cleanUrls: true,
  description: 'QuantX 原生客户端、GraphQL 与交易边界开发文档',
  head: [
    ['meta', { name: 'color-scheme', content: 'dark light' }],
    ['meta', { name: 'theme-color', content: '#0b1120' }],
  ],
  // VitePress treats the downloadable GraphQL SDL extension as a page link.
  // Keep all documentation links strict and exempt only this generated asset.
  ignoreDeadLinks: ['/contracts/graphql-schema.graphql'],
  lang: 'zh-CN',
  lastUpdated: true,
  outDir: 'dist',
  srcDir: 'content',
  title: 'QuantX Developer',
  vite: {
    publicDir: path.resolve(import.meta.dirname, '../public'),
  },
  themeConfig: {
    darkModeSwitchTitle: '切换到深色主题',
    darkModeSwitchLabel: '外观',
    docFooter: {
      next: '下一篇',
      prev: '上一篇',
    },
    externalLinkIcon: true,
    footer: {
      copyright: `当前部署文档版本：${docsVersion}`,
      message: 'QuantX 客户端契约与当前 Windows 服务版本保持一致',
    },
    lastUpdated: {
      text: '最后更新',
      formatOptions: {
        dateStyle: 'medium',
        timeStyle: 'short',
      },
    },
    lightModeSwitchTitle: '切换到浅色主题',
    nav: [
      { text: 'iOS 产品契约', link: '/guide/ios-product-contract' },
      { text: 'iOS 快速开始', link: '/guide/ios-quickstart' },
      { text: 'API 参考', link: '/reference/' },
      {
        text: `版本 ${docsVersion}`,
        link: '/reference/changelog',
      },
    ],
    outline: {
      label: '本页目录',
      level: [2, 3],
    },
    returnToTopLabel: '返回顶部',
    search: {
      provider: 'local',
      options: {
        translations: {
          button: {
            buttonAriaLabel: '搜索文档',
            buttonText: '搜索',
          },
          modal: {
            backButtonTitle: '关闭搜索',
            displayDetails: '显示详细列表',
            footer: {
              closeKeyAriaLabel: 'Escape',
              closeText: '关闭',
              navigateDownKeyAriaLabel: '向下',
              navigateText: '选择',
              navigateUpKeyAriaLabel: '向上',
              selectKeyAriaLabel: 'Enter',
              selectText: '打开',
            },
            noResultsText: '没有找到相关文档',
            resetButtonTitle: '清除搜索',
          },
        },
      },
    },
    sidebar: [
      {
        text: '开始',
        items: [
          { text: '文档首页', link: '/' },
          { text: 'iOS 产品契约', link: '/guide/ios-product-contract' },
          { text: 'iOS 快速开始', link: '/guide/ios-quickstart' },
          { text: '原生客户端会话', link: '/guide/native-session' },
          { text: 'GraphQL HTTP', link: '/guide/graphql-http' },
          { text: '普通 APNs 与通知路由', link: '/guide/ios-notifications' },
          {
            text: 'GraphQL WebSocket',
            link: '/guide/graphql-websocket',
          },
        ],
      },
      {
        text: '安全与处理边界',
        items: [
          { text: '权限模型', link: '/concepts/permissions' },
          {
            text: '委托与成交状态',
            link: '/concepts/order-lifecycle',
          },
          { text: '系统边界', link: '/concepts/system-boundaries' },
        ],
      },
      {
        text: '接口参考',
        items: [
          { text: '契约下载', link: '/reference/' },
          {
            text: 'GraphQL Schema',
            link: '/reference/graphql-api/',
            collapsed: true,
            items: [
              {
                text: 'Query',
                link: '/reference/graphql-api/query',
              },
              {
                text: 'Mutation',
                link: '/reference/graphql-api/mutation',
              },
              {
                text: 'Subscription',
                link: '/reference/graphql-api/subscription',
              },
              {
                text: '类型索引',
                link: '/reference/graphql-api/types',
              },
            ],
          },
          { text: '错误与恢复', link: '/reference/errors' },
          { text: '版本记录', link: '/reference/changelog' },
        ],
      },
    ],
    sidebarMenuLabel: '文档导航',
    siteTitle: 'QuantX Developer',
    skipToContentLabel: '跳转到正文',
  },
});
