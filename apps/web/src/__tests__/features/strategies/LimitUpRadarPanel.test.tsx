import { render, screen } from '@testing-library/react';

import { LimitUpRadarPanel } from '@/features/strategies/components/LimitUpRadarPanel';

describe('LimitUpRadarPanel empty and error states', () => {
  it('keeps an offline error explicit without inventing candidates', () => {
    render(
      <LimitUpRadarPanel
        armedCodes={new Set()}
        assistantEnabled={false}
        autoScore={70}
        candidates={[]}
        errorMessage="行情服务暂不可用"
        exitPlanCodes={new Set()}
        fetching={false}
        industries={[]}
        industry="ALL"
        isScannerRunning={false}
        onArm={vi.fn()}
        onDisarm={vi.fn()}
        onIndustryChange={vi.fn()}
        onOpenStock={vi.fn()}
        onSearchChange={vi.fn()}
        onStageChange={vi.fn()}
        pendingCodes={new Set()}
        search=""
        stage="ALL"
        summary={{
          brokenCount: 0,
          candidateCount: 0,
          excludedCount: 0,
          nearLimitCount: 0,
          scannedCount: 0,
          sealedCount: 0,
          staleCount: 0,
        }}
        systemWarnings={['Engine 全市场打板雷达尚未就绪']}
      />
    );

    expect(screen.getByRole('alert')).toHaveTextContent('系统保护');
    expect(screen.getByRole('alert')).toHaveTextContent('行情服务暂不可用');
    expect(screen.queryByText('雷达数据提示')).not.toBeInTheDocument();
    expect(screen.getByText('暂无匹配候选')).toBeVisible();
    expect(screen.getByText('雷达离线')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: '创建监控实例' })
    ).not.toBeInTheDocument();
  });
});
