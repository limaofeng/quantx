import { Download, Plus } from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

import { HoldingsList } from '../components/HoldingsList';
import { PortfolioSummary } from '../components/PortfolioSummary';
import { useHoldings } from '../hooks/useHoldings';

export function HoldingsPage() {
  const {
    holdings = [],
    portfolioSummary,
    dailyAssetSnapshots,
    isLoading,
    error,
    refetch,
  } = useHoldings();

  if (isLoading) {
    return <div>加载持仓数据中...</div>;
  }

  if (error) {
    return (
      <div className="text-center p-8">
        <p className="text-destructive mb-4">加载持仓数据失败</p>
        <p className="text-muted-foreground text-sm mb-4">{error.message}</p>
        <Button onClick={() => refetch()}>重新加载</Button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <div>
          <h3 className="text-xl font-semibold">持仓管理</h3>
          <p className="text-muted-foreground">查看和管理您的股票持仓</p>
        </div>
        <div className="hidden md:flex gap-3">
          <Button variant="secondary" data-testid="export-holdings">
            <Download className="mr-2 h-4 w-4" />
            导出
          </Button>
          <Link href="/trading">
            <Button data-testid="buy-stocks">
              <Plus className="mr-2 h-4 w-4" />
              买入股票
            </Button>
          </Link>
        </div>
      </div>

      {portfolioSummary && (
        <PortfolioSummary
          summary={portfolioSummary}
          dailyAssetSnapshots={dailyAssetSnapshots}
        />
      )}

      {(holdings || []).length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-muted-foreground">暂无持仓数据</p>
        </Card>
      ) : (
        <HoldingsList holdings={holdings || []} />
      )}
    </div>
  );
}
