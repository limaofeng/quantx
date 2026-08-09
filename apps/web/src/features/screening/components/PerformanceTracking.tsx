import { Activity } from 'lucide-react';

import { Alert, AlertDescription } from '@/components/ui/alert';

export function PerformanceTracking() {
  return (
    <div className="space-y-6">
      <Alert>
        <Activity className="h-4 w-4" />
        <AlertDescription>
          实时追踪你选中股票的表现，包括收益率分析、风险评估和基准比较。
        </AlertDescription>
      </Alert>

      <div className="text-center py-12 text-muted-foreground">
        <p>性能跟踪功能正在开发中...</p>
        <p className="text-sm mt-2">将包含收益率分析、风险指标和回测结果</p>
      </div>
    </div>
  );
}
