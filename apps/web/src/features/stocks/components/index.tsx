// Stocks 组件导出
import type { StockDetail, StockHolding, StockTransaction } from '../types';

export { StockHeader } from './StockHeader';
export { StockStudioShell } from './StockStudioShell';
export { StockDetailWorkbench } from './stock-detail-workbench/StockDetailWorkbench';
export { StockDetailWorkspace } from './StockDetailWorkspace';

export type {
  StockWorkspaceContext,
  StockWorkspaceView,
} from './stockWorkspaceConfig';

export type { StockStudioMode } from './StockStudioShell';

// 简化导出，其他组件可以后续添加
export const StockMetrics = ({ stock }: { stock: StockDetail }) => {
  return <div>股票指标 - {stock.name}</div>;
};

export const StockChart = ({ stockCode }: { stockCode: string }) => {
  return <div>股票图表 - {stockCode}</div>;
};

export const StockPosition = ({
  stock,
  holding: _holding,
}: {
  stock: StockDetail;
  holding: StockHolding;
}) => {
  return <div>持仓信息 - {stock.name}</div>;
};

export const StockTransactions = ({
  transactions,
}: {
  transactions: StockTransaction[];
}) => {
  return <div>交易记录 - {transactions.length} 条</div>;
};
