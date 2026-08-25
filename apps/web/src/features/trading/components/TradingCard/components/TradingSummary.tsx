import { Card } from '@/components/ui/card';
import { formatCurrency } from '@/shared/utils/format';

interface TradingSummaryProps {
  estimatedAmount: number;
  estimatedFees: number;
  estimatedTotal: number;
}

/**
 * 交易金额汇总组件
 */
export function TradingSummary({
  estimatedAmount,
  estimatedFees,
  estimatedTotal,
}: TradingSummaryProps) {
  if (estimatedAmount <= 0) return null;

  return (
    <Card className="bg-secondary p-ui-section space-y-2">
      <div className="flex justify-between text-ui-body">
        <span>预估金额:</span>
        <span className="font-medium" data-testid="estimated-amount">
          {formatCurrency(estimatedAmount)}
        </span>
      </div>
      <div className="flex justify-between text-ui-body">
        <span>手续费:</span>
        <span data-testid="estimated-fees">
          {formatCurrency(estimatedFees)}
        </span>
      </div>
      <div className="flex justify-between text-ui-body">
        <span>印花税:</span>
        <span>¥0.00</span>
      </div>
      <div className="border-t border-border pt-2 flex justify-between font-medium">
        <span>总计:</span>
        <span data-testid="estimated-total">
          {formatCurrency(estimatedTotal)}
        </span>
      </div>
    </Card>
  );
}
