/// <reference types="vitest" />
import path from 'path';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      '@/components': path.resolve(__dirname, 'src/components'),
      '@/features': path.resolve(__dirname, 'src/features'),
      '@/shared': path.resolve(__dirname, 'src/shared'),
      '@/core': path.resolve(__dirname, 'src/core'),
      '@/utils': path.resolve(__dirname, 'src/utils'),
      '@/hooks': path.resolve(__dirname, 'src/hooks'),
      '@/types': path.resolve(__dirname, 'src/types'),
      '@/config': path.resolve(__dirname, 'src/config'),
      '@/mocks': path.resolve(__dirname, 'src/mocks'),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/__tests__/setup.ts'],
    css: true,
    // Full-suite jsdom and user-event tests share CPU with 100+ transformed
    // files. Keep a bounded margin above their isolated 2-3 second runtime so
    // scheduler contention does not turn successful interactions into flakes.
    testTimeout: 10_000,
    reporters: ['verbose', 'html'],
    coverage: {
      reporter: ['text', 'json', 'html'],
      exclude: [
        'node_modules/',
        'src/__tests__/',
        'src/**/*.test.{ts,tsx}',
        'src/**/*.spec.{ts,tsx}',
      ],
      thresholds: {
        global: {
          branches: 80,
          functions: 80,
          lines: 80,
          statements: 80,
        },
      },
    },
  },
});
