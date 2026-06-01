import { useParams } from 'wouter';

import { Button } from '@/components/ui/button';

import { StockHeader } from '../components/index';
import PriceInfo from '../components/PriceInfo';
import { useStockDetail } from '../hooks/useStockDetail';

export default function StockDetailPage() {
  const { stockCode } = useParams();
  const { stock, isLoading, error, refetch } = useStockDetail(stockCode || '');

  if (!stockCode) {
    return <div>加载股票详情中...</div>;
  }

  if (isLoading) {
    return <div>加载股票详情中...</div>;
  }

  if (error || !stock) {
    return (
      <div className="text-center p-8">
        <p className="text-destructive mb-4">加载股票详情失败</p>
        <p className="text-muted-foreground text-sm mb-4">
          {error?.message || '股票不存在'}
        </p>
        <Button onClick={() => refetch()}>重新加载</Button>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto">
      {/* Stock Header */}
      <StockHeader stock={stock} />

      {/* Price Information */}
      <PriceInfo stock={stock} />

      {/* Stock Metrics */}
      {/* <StockMetrics stock={stock} /> */}

      {/* Stock Chart */}
      {/* <StockChart stockCode={stock.code} /> */}

      {/* Position Information */}
      {/* {holding && <StockPosition stock={stock} holding={holding} />} */}

      {/* Transaction History */}
      {/* {transactions.length > 0 && (
        <StockTransactions transactions={transactions} />
      )} */}
    </div>
  );
}
