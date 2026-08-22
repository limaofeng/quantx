import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { StockFinancialPanel } from '@/features/stocks/components/stock-detail-workbench/StockFinancialPanel';

describe('StockFinancialPanel states', () => {
  it('keeps missing financial statements explicit', () => {
    render(
      <StockFinancialPanel
        isLoading={false}
        onRetry={vi.fn()}
        statements={null}
        summary={null}
      />
    );

    expect(screen.getByText('暂无可用财务四表')).toBeInTheDocument();
    expect(
      screen.getByText('其他行情与交易功能仍可正常使用。', { exact: false })
    ).toBeInTheDocument();
  });

  it('renders a recoverable financial data error', () => {
    render(
      <StockFinancialPanel
        error={new Error('financial service unavailable')}
        isLoading={false}
        onRetry={vi.fn()}
        statements={null}
        summary={null}
      />
    );

    expect(screen.getByRole('alert')).toHaveTextContent('财务数据加载失败');
    expect(screen.getByRole('button', { name: '重试财务数据' })).toBeEnabled();
  });
});
