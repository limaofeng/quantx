import { useRealTimeHoldings } from '../hooks/useRealTimeHoldings';
import type { Position } from '../types';

import { HoldingCard } from './HoldingCard';

interface HoldingsListProps {
  holdings: Position[];
  enableRealTime?: boolean;
}

export function HoldingsList({
  holdings,
  enableRealTime = true,
}: HoldingsListProps) {
  // 使用实时持仓 Hook
  const { holdings: realtimeHoldings, isConnected } = useRealTimeHoldings({
    holdings,
    enabled: enableRealTime,
  });

  return (
    <div className="space-y-4">
      {isConnected && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
          </span>
          <span>实时价格更新中</span>
        </div>
      )}
      {realtimeHoldings.map(holding => (
        <HoldingCard key={holding.id} holding={holding} />
      ))}
    </div>
  );
}
