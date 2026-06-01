// 测试工具函数
/* eslint-disable react-refresh/only-export-components */
import { render, type RenderOptions } from '@testing-library/react';
import React, { type ReactElement } from 'react';
import { Provider as UrqlProvider } from 'urql';

import { ThemeProvider } from '@/components/ThemeProvider';
import { urqlClient } from '@/core/graphql';

// 测试提供者组件
interface TestProvidersProps {
  children: React.ReactNode;
}

function TestProviders({ children }: TestProvidersProps) {
  return (
    <UrqlProvider value={urqlClient}>
      <ThemeProvider>{children}</ThemeProvider>
    </UrqlProvider>
  );
}

// 自定义 render 函数
function customRender(
  ui: ReactElement,
  options: Omit<RenderOptions, 'wrapper'> = {}
) {
  return render(ui, {
    wrapper: TestProviders,
    ...options,
  });
}

// 重新导出 testing-library
export * from '@testing-library/react';
export { customRender as render };

// 导出测试工具
export { TestProviders };
