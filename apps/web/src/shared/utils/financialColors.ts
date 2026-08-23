export type FinancialColorContext = 'market' | 'holding';
export type FinancialDirection = 'up' | 'down' | 'flat';

export const FINANCIAL_CHART_COLORS = {
  up: '#EF4444',
  down: '#22C55E',
  holdingDown: '#60A5FA',
  flat: '#94A3B8',
} as const;

export function financialDirection(value: unknown): FinancialDirection {
  const amount = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(amount) || amount === 0) return 'flat';
  return amount > 0 ? 'up' : 'down';
}

export function financialToneClass(
  value: unknown,
  context: FinancialColorContext = 'market'
) {
  const direction = financialDirection(value);
  if (direction === 'up') return 'text-market-up';
  if (direction === 'down') {
    return context === 'holding' ? 'text-holding-down' : 'text-market-down';
  }
  return 'text-market-flat';
}

export function financialToneBadgeClass(
  value: unknown,
  context: FinancialColorContext = 'market'
) {
  const direction = financialDirection(value);
  if (direction === 'up') {
    return 'border-market-up/20 bg-market-up/10 text-market-up';
  }
  if (direction === 'down') {
    return context === 'holding'
      ? 'border-holding-down/20 bg-holding-down/10 text-holding-down'
      : 'border-market-down/20 bg-market-down/10 text-market-down';
  }
  return 'border-market-flat/20 bg-market-flat/10 text-market-flat';
}

export function financialChartColor(
  value: unknown,
  context: FinancialColorContext = 'market'
) {
  const direction = financialDirection(value);
  if (direction === 'up') return FINANCIAL_CHART_COLORS.up;
  if (direction === 'down') {
    return context === 'holding'
      ? FINANCIAL_CHART_COLORS.holdingDown
      : FINANCIAL_CHART_COLORS.down;
  }
  return FINANCIAL_CHART_COLORS.flat;
}
