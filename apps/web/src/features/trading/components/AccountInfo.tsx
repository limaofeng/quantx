import { financialToneClass } from '@/shared/utils/financialColors';
import { formatCurrency } from '@/shared/utils/format';
import { cn } from '@/utils/cn';

interface AccountInfoProps {
  summary?: {
    cash: number;
    frozenCash: number;
    totalAsset: number;
    marketValue: number;
    totalProfitLoss?: number | null;
    profitLossPercent?: number | null;
  } | null;
}

function displayCurrency(value?: number | null) {
  return typeof value === 'number' ? formatCurrency(value) : '--';
}

export function AccountInfo({ summary }: AccountInfoProps) {
  return (
    <div className="p-1 h-full flex flex-col">
      <div className="space-y-2 max-w-sm">
        <div className="flex justify-between items-center px-1">
          <span className="text-ui-caption text-muted-foreground uppercase tracking-wider">
            可用资金
          </span>
          <span
            className="text-ui-label font-mono font-bold"
            data-testid="available-cash"
          >
            {displayCurrency(summary?.cash)}
          </span>
        </div>
        <div className="flex justify-between items-center px-1">
          <span className="text-ui-caption text-muted-foreground uppercase tracking-wider">
            冻结资金
          </span>
          <span
            className="text-ui-label font-mono text-muted-foreground"
            data-testid="frozen-funds"
          >
            {displayCurrency(summary?.frozenCash)}
          </span>
        </div>
        <div className="flex justify-between items-center px-1">
          <span className="text-ui-caption text-muted-foreground uppercase tracking-wider">
            总资产
          </span>
          <span
            className="text-ui-label font-mono font-bold text-primary"
            data-testid="account-total-assets"
          >
            {displayCurrency(summary?.totalAsset)}
          </span>
        </div>

        <div className="h-px bg-border my-2" />

        <div className="flex justify-between items-center px-1 bg-muted/20 py-2 rounded">
          <span className="text-ui-caption font-bold text-muted-foreground uppercase tracking-wider">
            当前购买力
          </span>
          <span
            className="text-ui-title font-mono font-black text-primary"
            data-testid="buying-power"
          >
            {displayCurrency(summary?.cash)}
          </span>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2">
          <div className="p-2 bg-muted/10 rounded flex flex-col gap-1">
            <span className="text-ui-micro text-muted-foreground uppercase">
              持仓市值
            </span>
            <span className="text-ui-caption font-mono font-bold">
              {displayCurrency(summary?.marketValue)}
            </span>
          </div>
          <div className="p-2 bg-muted/10 rounded flex flex-col gap-1">
            <span className="text-ui-micro text-muted-foreground uppercase">
              总盈亏
            </span>
            <span
              className={cn(
                'text-ui-caption font-mono font-bold',
                financialToneClass(summary?.totalProfitLoss, 'holding')
              )}
            >
              {typeof summary?.totalProfitLoss === 'number'
                ? `${displayCurrency(summary.totalProfitLoss)}${
                    typeof summary.profitLossPercent === 'number'
                      ? ` (${summary.profitLossPercent.toFixed(2)}%)`
                      : ''
                  }`
                : '--'}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
