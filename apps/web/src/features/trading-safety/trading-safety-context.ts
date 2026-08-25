import { createContext, useContext } from 'react';
import type { CombinedError } from 'urql';

import type { TradingSafety_AccountExecutionSafetyQuery } from '@/generated/gql/graphql';

export type AccountExecutionSafetySnapshot =
  TradingSafety_AccountExecutionSafetyQuery['accountExecutionSafety'];

export interface TradingSafetyContextValue {
  accountId: string;
  canIncreaseRisk: boolean;
  canReduceRisk: boolean;
  blockedReasons: string[];
  error: CombinedError | undefined;
  executionMode: string;
  fetching: boolean;
  refreshSafety: () => void;
  safety: AccountExecutionSafetySnapshot | null;
}

export const TradingSafetyContext = createContext<TradingSafetyContextValue>({
  accountId: '',
  canIncreaseRisk: false,
  canReduceRisk: false,
  blockedReasons: ['实盘安全状态尚未加载'],
  error: undefined,
  executionMode: 'OBSERVE_ONLY',
  fetching: true,
  refreshSafety: () => undefined,
  safety: null,
});

export function useTradingSafety(): TradingSafetyContextValue {
  return useContext(TradingSafetyContext);
}
