import { createContext, useContext } from 'react';

export interface TradingSafetyContextValue {
  accountId: string;
  canIncreaseRisk: boolean;
  canReduceRisk: boolean;
  blockedReasons: string[];
  executionMode: string;
  fetching: boolean;
  refreshSafety: () => void;
}

export const TradingSafetyContext = createContext<TradingSafetyContextValue>({
  accountId: '',
  canIncreaseRisk: false,
  canReduceRisk: false,
  blockedReasons: ['实盘安全状态尚未加载'],
  executionMode: 'OBSERVE_ONLY',
  fetching: true,
  refreshSafety: () => undefined,
});

export function useTradingSafety(): TradingSafetyContextValue {
  return useContext(TradingSafetyContext);
}
