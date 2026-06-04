// 快速操作组件
import { BarChart3, Hand, Plus } from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

export function QuickActions() {
  return (
    <Card className="rounded-lg border-white/10 bg-[#0f172a]/70 p-4">
      <h3 className="mb-3 text-xs font-black uppercase tracking-[0.2em] text-slate-300">
        快速操作
      </h3>
      <div className="grid grid-cols-3 gap-2">
        <Link href="/holdings">
          <Button className="h-8 w-full text-xs" data-testid="quick-action-buy">
            <Plus className="mr-2 h-3.5 w-3.5" />
            买入股票
          </Button>
        </Link>
        <Link href="/liquidation">
          <Button
            variant="destructive"
            className="h-8 w-full text-xs"
            data-testid="quick-action-liquidate"
          >
            <Hand className="mr-2 h-3.5 w-3.5" />
            一键清仓
          </Button>
        </Link>
        <Link href="/market-shortcuts">
          <Button
            variant="secondary"
            className="h-8 w-full text-xs"
            data-testid="quick-action-market-shortcuts"
          >
            <BarChart3 className="mr-2 h-3.5 w-3.5" />
            行情快捷
          </Button>
        </Link>
      </div>
    </Card>
  );
}
