import { useEffect, useMemo, type ReactNode } from 'react';
import { useQuery } from 'urql';

import { LiveSafetyStatusQuery } from './operations';
import {
  TradingSafetyContext,
  type TradingSafetyContextValue,
} from './trading-safety-context';

export function TradingSafetyProvider({
  accountId,
  children,
}: {
  accountId: string;
  children: ReactNode;
}) {
  const [{ data, fetching, error }, refresh] = useQuery({
    query: LiveSafetyStatusQuery,
    variables: { accountId },
    pause: !accountId,
    requestPolicy: 'network-only',
  });

  useEffect(() => {
    if (!accountId) return undefined;
    const timer = window.setInterval(
      () => refresh({ requestPolicy: 'network-only' }),
      15_000
    );
    return () => window.clearInterval(timer);
  }, [accountId, refresh]);

  const readiness = data?.liveSafetyStatus;
  const unresolvedCriticalAlertCount =
    readiness?.unresolvedCriticalAlertCount ?? 0;
  const blockedReasons = useMemo(() => {
    if (!accountId) return ['当前用户没有可用资金账户'];
    if (error) return [`安全状态查询失败：${error.message}`];
    if (!readiness) return ['实盘安全状态尚未加载'];
    const reasons = [...readiness.blockedReasons];
    if (unresolvedCriticalAlertCount > 0) {
      reasons.push(`存在 ${unresolvedCriticalAlertCount} 条严重运行告警`);
    }
    return Array.from(new Set(reasons));
  }, [accountId, error, readiness, unresolvedCriticalAlertCount]);

  const value = useMemo<TradingSafetyContextValue>(
    () => ({
      accountId,
      canTrade:
        Boolean(readiness?.ready) &&
        unresolvedCriticalAlertCount === 0 &&
        !error,
      blockedReasons,
      fetching,
    }),
    [
      accountId,
      blockedReasons,
      error,
      fetching,
      readiness?.ready,
      unresolvedCriticalAlertCount,
    ]
  );

  return (
    <TradingSafetyContext.Provider value={value}>
      {children}
    </TradingSafetyContext.Provider>
  );
}
