import js from '@eslint/js';
import tseslint from '@typescript-eslint/eslint-plugin';
import tsparser from '@typescript-eslint/parser';
import prettierConfig from 'eslint-config-prettier';
import importPlugin from 'eslint-plugin-import';
import prettier from 'eslint-plugin-prettier';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';

export default [
  {
    ignores: [
      // 构建产物
      'dist',
      'node_modules',
      '.git',
      'coverage',

      // Codegen 生成的文件
      'src/generated/**',
      'src/features/dashboard/graphql/__generated__/**',
      'src/features/*/hooks/api.ts',

      // 文档和旧代码
      'docs/**',
      '**/*.legacy.*',
    ],
  },
  // 配置文件 - Node.js 环境
  {
    files: [
      '*.config.{js,ts}',
      'vite.config.ts',
      'vitest.config.ts',
      'tailwind.config.ts',
    ],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
    rules: {
      // Tailwind 配置允许使用 require
      '@typescript-eslint/no-require-imports': 'off',
    },
  },
  // 测试文件配置
  {
    files: [
      '**/*.test.{ts,tsx}',
      '**/__tests__/**/*.{ts,tsx}',
      '**/test/**/*.{ts,tsx}',
    ],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
        vi: 'readonly',
        vitest: 'readonly',
        describe: 'readonly',
        it: 'readonly',
        test: 'readonly',
        expect: 'readonly',
        beforeAll: 'readonly',
        afterAll: 'readonly',
        beforeEach: 'readonly',
        afterEach: 'readonly',
        jest: 'readonly',
      },
    },
  },
  // 通用配置
  {
    files: ['**/*.{ts,tsx,js,jsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parser: tsparser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    settings: {
      react: { version: '18.3' },
      'import/resolver': {
        typescript: {
          alwaysTryTypes: true,
          project: './tsconfig.json',
        },
        node: {
          extensions: ['.js', '.jsx', '.ts', '.tsx'],
        },
      },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      '@typescript-eslint': tseslint,
      import: importPlugin,
      prettier,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...tseslint.configs.recommended.rules,
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      ...prettierConfig.rules,

      // React specific
      'react/react-in-jsx-scope': 'off',
      'react/prop-types': 'off',
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],

      // TypeScript
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
        },
      ],
      '@typescript-eslint/explicit-function-return-type': 'off',
      '@typescript-eslint/explicit-module-boundary-types': 'off',
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/consistent-type-imports': [
        'warn',
        {
          prefer: 'type-imports',
          fixStyle: 'inline-type-imports',
        },
      ],

      // Import organization
      'import/order': [
        'error',
        {
          groups: [
            'builtin',
            'external',
            'internal',
            'parent',
            'sibling',
            'index',
          ],
          'newlines-between': 'always',
          alphabetize: {
            order: 'asc',
            caseInsensitive: true,
          },
        },
      ],
      'import/no-unresolved': 'error',
      'import/no-unused-modules': 'off', // 关闭以提升性能

      // Import 自动修复规则 (可被 --fix 修复)
      'import/no-duplicates': 'error', // 合并重复导入
      'import/first': 'error', // import 必须在顶部
      'import/newline-after-import': 'error', // import 后空行
      'import/no-useless-path-segments': 'error', // 移除无用路径

      // General code quality
      'no-console': 'warn',
      'no-debugger': 'warn',
      'no-restricted-globals': [
        'error',
        {
          name: 'alert',
          message: 'Use useAppDialog().alert() for an accessible in-app dialog.',
        },
        {
          name: 'confirm',
          message: 'Use useAppDialog().confirm() for an accessible in-app dialog.',
        },
        {
          name: 'prompt',
          message: 'Use useAppDialog().prompt() for an accessible in-app dialog.',
        },
      ],
      'no-restricted-properties': [
        'error',
        ...['alert', 'confirm', 'prompt'].map(property => ({
          object: 'window',
          property,
          message: `Use useAppDialog().${property}() instead of a browser-native dialog.`,
        })),
      ],
      'prefer-const': 'error',
      'no-var': 'error',

      // Prettier integration
      'prettier/prettier': 'error',
    },
  },
  // 核心工具文件 - 允许使用 any 类型
  // 这些文件需要处理各种未知类型的数据,any 是合理的选择
  {
    files: [
      'src/core/errors/**/*.ts',
      'src/core/errors/**/*.tsx',
      'src/core/debug/**/*.ts',
      'src/core/debug/**/*.tsx',
      'src/core/security/**/*.ts',
      'src/core/security/**/*.tsx',
      'src/shared/utils/error-handler.ts',
      'src/shared/utils/performance.ts',
    ],
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
];
