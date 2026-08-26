import path from 'path';

import graphql from '@rollup/plugin-graphql';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const viteEnv = loadEnv(mode, process.cwd(), '');
  const backendProxyTarget =
    viteEnv.VITE_PROXY_TARGET ||
    viteEnv.VITE_API_URL ||
    'http://127.0.0.1:8080';

  return {
    plugins: [
      react({
        // 优化 JSX 运行时
        jsxRuntime: 'automatic',
      }),
      // 支持导入 .gql 文件
      graphql(),
    ],
    resolve: {
      alias: {
        '@': path.resolve(import.meta.dirname, 'src'),
        '@/components': path.resolve(import.meta.dirname, 'src/components'),
        '@/features': path.resolve(import.meta.dirname, 'src/features'),
        '@/shared': path.resolve(import.meta.dirname, 'src/shared'),
        '@/core': path.resolve(import.meta.dirname, 'src/core'),
        '@/utils': path.resolve(import.meta.dirname, 'src/utils'),
        '@/hooks': path.resolve(import.meta.dirname, 'src/hooks'),
        '@/types': path.resolve(import.meta.dirname, 'src/types'),
        '@/config': path.resolve(import.meta.dirname, 'src/config'),
        '@/generated': path.resolve(import.meta.dirname, 'src/generated'),
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      // Production bundles must not publish source maps.
      sourcemap: mode !== 'production',
      // 目标浏览器
      target: ['es2020', 'chrome88', 'firefox85', 'safari14', 'edge88'],
      // 代码分割配置
      rollupOptions: {
        output: {
          // 手动分包策略
          manualChunks: {
            // React 相关
            'react-vendor': ['react', 'react-dom'],
            // 路由相关
            router: ['wouter'],
            // UI 组件库
            'ui-vendor': [
              '@radix-ui/react-dialog',
              '@radix-ui/react-dropdown-menu',
              '@radix-ui/react-select',
              '@radix-ui/react-tabs',
              '@radix-ui/react-toast',
            ],
            // 图表库分别拆包，避免任一图表页面加载全部图表运行时。
            'recharts-vendor': ['recharts'],
            'lightweight-charts-vendor': ['lightweight-charts'],
            // GraphQL 相关
            'graphql-vendor': ['urql', 'graphql'],
            // Codegen 的静态查询映射与操作 AST 独立缓存。两者增长节奏不同，
            // 分块后新增业务契约不会把单一生成物推过包体预算。
            'generated-graphql-map': [
              path.resolve(import.meta.dirname, 'src/generated/gql/gql.ts'),
            ],
            'generated-graphql-documents': [
              path.resolve(import.meta.dirname, 'src/generated/gql/graphql.ts'),
            ],
            // 工具库
            'utils-vendor': ['date-fns', 'clsx', 'tailwind-merge'],
            // 表单相关
            'form-vendor': ['react-hook-form', '@hookform/resolvers', 'zod'],
          },
          // 文件命名策略
          chunkFileNames: 'js/[name]-[hash].js',
          entryFileNames: 'js/[name]-[hash].js',
          assetFileNames: assetInfo => {
            const fileName =
              assetInfo.names?.[0] ||
              assetInfo.originalFileNames?.[0] ||
              'asset';
            if (/\.(png|jpe?g|svg|gif|tiff|bmp|ico)$/i.test(fileName)) {
              return `images/[name]-[hash][extname]`;
            }
            if (/\.(woff2?|eot|ttf|otf)$/i.test(fileName)) {
              return `fonts/[name]-[hash][extname]`;
            }
            return `assets/[name]-[hash][extname]`;
          },
        },
      },
      // 压缩配置
      minify: 'terser',
      // Tailwind's single global stylesheet is dominated by repeated utility
      // declarations. Lightning CSS safely merges those declarations against
      // the same browser target and keeps the enforced CSS budget meaningful.
      cssMinify: 'lightningcss',
      terserOptions: {
        compress: {
          // 移除 console
          drop_console: true,
          // 移除 debugger
          drop_debugger: true,
          // 移除无用代码
          dead_code: true,
        },
        mangle: {
          // 保留类名（用于错误追踪）
          keep_classnames: true,
          keep_fnames: true,
        },
      },
      // 设置包大小警告阈值
      chunkSizeWarningLimit: 500,
    },
    server: {
      host: '0.0.0.0',
      port: 5250,
      strictPort: true,
      open: false,
      // 启用 HMR
      hmr: {
        overlay: true,
      },
      // 文件监听优化
      watch: {
        usePolling: false,
        useFsEvents: true,
      },
      proxy: {
        '/auth': {
          target: backendProxyTarget,
          changeOrigin: true,
          secure: false,
        },
        '/graphql': {
          target: backendProxyTarget,
          changeOrigin: true,
          secure: false,
          ws: true,
          rewrite: path => path.replace(/^\/graphql/, '/graphql'),
        },
        '/health': {
          target: backendProxyTarget,
          changeOrigin: true,
          secure: false,
          rewrite: path => path.replace(/^\/health/, '/health'),
        },
      },
    },
    // 开发环境优化
    optimizeDeps: {
      include: [
        'react',
        'react-dom',
        'urql',
        'graphql',

        'recharts',
        'lightweight-charts',
        'date-fns',
        'zod',
        'react-hook-form',
      ],
      exclude: ['@vite/client', '@vite/env'],
    },
    // 性能配置
    esbuild: {
      // 移除 debugger 和 console（仅生产环境）
      drop: mode === 'production' ? ['console', 'debugger'] : [],
    },
  };
});
