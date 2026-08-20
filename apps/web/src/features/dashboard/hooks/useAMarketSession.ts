import { useEffect, useMemo, useState } from 'react';

import { getShanghaiDateKey } from '@/components/trading-chart/utils/time-utils';
import { useTradingDays } from '@/hooks/useTradingDays';

import {
  resolveAMarketSession,
  resolveMarketTargetTradingDate,
} from '../marketWorkbench';

const SESSION_CLOCK_INTERVAL_MS = 15_000;

export function useAMarketSession() {
  const { error, loading, tradingDays } = useTradingDays();
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(
      () => setNow(new Date()),
      SESSION_CLOCK_INTERVAL_MS
    );
    return () => window.clearInterval(timer);
  }, []);

  const currentTradingDay = useMemo(() => {
    if (tradingDays.length === 0) return undefined;
    return tradingDays.includes(getShanghaiDateKey(now));
  }, [now, tradingDays]);
  const session = useMemo(
    () => resolveAMarketSession(now, currentTradingDay),
    [currentTradingDay, now]
  );
  const targetTradingDate = useMemo(
    () =>
      resolveMarketTargetTradingDate(
        now,
        currentTradingDay,
        session.phase,
        tradingDays
      ),
    [currentTradingDay, now, session.phase, tradingDays]
  );

  return {
    ...session,
    calendarError: error,
    calendarLoading: loading,
    isTradingDay: currentTradingDay,
    now,
    targetTradingDate,
    tradingDays,
  };
}
