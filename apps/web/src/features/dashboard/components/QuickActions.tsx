// 快速操作组件
import { BarChart3, Hand, Plus } from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

export function QuickActions() {
  return (
    <Card className="h-fit rounded-lg border-white/10 bg-[#0f172a]/70 p-4">
      <div className="mb-3">
        <h3 className="text-sm font-black text-slate-200">快速操作</h3>
        <p className="mt-1 text-[11px] text-slate-400">
          常用交易入口，危险操作进入管理页后再次确认
        </p>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <Button
          asChild
          className="h-9 w-full rounded-md text-xs"
          data-testid="quick-action-buy"
        >
          <Link href="/holdings">
            <Plus className="mr-2 h-3.5 w-3.5" />
            买入股票
          </Link>
        </Button>
        <Button
          asChild
          variant="outline"
          className="h-9 w-full rounded-md border-rose-500/40 text-xs text-rose-300 hover:border-rose-400/70 hover:bg-rose-500/10 hover:text-rose-200"
          data-testid="quick-action-liquidate"
        >
          <Link href="/liquidation">
            <Hand className="mr-2 h-3.5 w-3.5" />
            清仓管理
          </Link>
        </Button>
        <Button
          asChild
          variant="secondary"
          className="h-9 w-full rounded-md text-xs"
          data-testid="quick-action-market-shortcuts"
        >
          <Link href="/market-shortcuts">
            <BarChart3 className="mr-2 h-3.5 w-3.5" />
            行情快捷
          </Link>
        </Button>
      </div>
    </Card>
  );
}
