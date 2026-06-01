import { formatCurrency } from '@/shared/utils/format';

interface AccountInfoProps {
  summary: any;
}

export function AccountInfo({ summary }: AccountInfoProps) {
  return (
    <div className="p-1 h-full flex flex-col">
      <div className="space-y-2 max-w-sm">
        <div className="flex justify-between items-center px-1">
          <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
            可用资金
          </span>
          <span
            className="text-[12px] font-mono font-bold"
            data-testid="available-cash"
          >
            {formatCurrency(summary?.availableCash || 0)}
          </span>
        </div>
        <div className="flex justify-between items-center px-1">
          <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
            冻结资金
          </span>
          <span
            className="text-[12px] font-mono text-muted-foreground"
            data-testid="frozen-funds"
          >
            {formatCurrency(summary?.frozenFunds || 0)}
          </span>
        </div>
        <div className="flex justify-between items-center px-1">
          <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
            总资产
          </span>
          <span
            className="text-[12px] font-mono font-bold text-primary"
            data-testid="account-total-assets"
          >
            {formatCurrency(summary?.totalAssets || 0)}
          </span>
        </div>

        <div className="h-px bg-border my-2" />

        <div className="flex justify-between items-center px-1 bg-muted/20 py-2 rounded">
          <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
            当前购买力
          </span>
          <span
            className="text-[14px] font-mono font-black text-primary"
            data-testid="buying-power"
          >
            {formatCurrency(
              (summary?.availableCash || 0) - (summary?.frozenFunds || 0)
            )}
          </span>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2">
          <div className="p-2 bg-muted/10 rounded flex flex-col gap-1">
            <span className="text-[9px] text-muted-foreground uppercase">
              当日盈亏
            </span>
            <span className="text-[11px] font-mono font-bold text-success">
              +¥1,240.00
            </span>
          </div>
          <div className="p-2 bg-muted/10 rounded flex flex-col gap-1">
            <span className="text-[9px] text-muted-foreground uppercase">
              当日胜率
            </span>
            <span className="text-[11px] font-mono font-bold">68.5%</span>
          </div>
        </div>
      </div>
    </div>
  );
}
