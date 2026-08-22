import { useCallback, useEffect, useMemo, type ReactNode } from 'react';
import { useQuery } from 'urql';

import { AccountExecutionSafetyQuery } from './operations';
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
    query: AccountExecutionSafetyQuery,
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

  const safety = data?.accountExecutionSafety;
  const blockedReasons = useMemo(() => {
    if (!accountId) return ['当前用户没有可用资金账户'];
    if (error) return [`安全状态查询失败：${error.message}`];
    if (!safety) return ['实盘安全状态尚未加载'];
    return Array.from(new Set(safety.blockedReasons ?? []));
  }, [accountId, error, safety]);

  const refreshSafety = useCallback(() => {
    refresh({ requestPolicy: 'network-only' });
  }, [refresh]);

  const value = useMemo<TradingSafetyContextValue>(
    () => ({
      accountId,
      canIncreaseRisk: Boolean(safety?.canIncreaseRisk) && !error,
      canReduceRisk: Boolean(safety?.canReduceRisk) && !error,
      blockedReasons,
      executionMode: safety?.executionMode || 'OBSERVE_ONLY',
      fetching,
      refreshSafety,
    }),
    [
      accountId,
      blockedReasons,
      error,
      fetching,
      refreshSafety,
      safety?.canIncreaseRisk,
      safety?.canReduceRisk,
      safety?.executionMode,
    ]
  );

  return (
    <TradingSafetyContext.Provider value={value}>
      {children}
    </TradingSafetyContext.Provider>
  );
}
