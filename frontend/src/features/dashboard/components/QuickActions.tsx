// 快速操作组件
import { Plus, Hand } from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

export function QuickActions() {
  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">快速操作</h3>
      <div className="grid grid-cols-2 gap-4">
        <Link href="/trading">
          <Button
            className="w-full bg-primary text-primary-foreground hover:bg-primary/90"
            data-testid="quick-action-buy"
          >
            <Plus className="mr-2 h-4 w-4" />
            买入股票
          </Button>
        </Link>
        <Link href="/liquidation">
          <Button
            variant="destructive"
            className="w-full"
            data-testid="quick-action-liquidate"
          >
            <Hand className="mr-2 h-4 w-4" />
            一键清仓
          </Button>
        </Link>
      </div>
    </Card>
  );
}
