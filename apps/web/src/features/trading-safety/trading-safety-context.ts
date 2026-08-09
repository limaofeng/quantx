import { createContext, useContext } from 'react';

export interface TradingSafetyContextValue {
  accountId: string;
  canTrade: boolean;
  blockedReasons: string[];
  fetching: boolean;
}

export const TradingSafetyContext = createContext<TradingSafetyContextValue>({
  accountId: '',
  canTrade: false,
  blockedReasons: ['实盘安全状态尚未加载'],
  fetching: true,
});

export function useTradingSafety(): TradingSafetyContextValue {
  return useContext(TradingSafetyContext);
}
