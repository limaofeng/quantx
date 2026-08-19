interface HoldingTarget {
  stockCode: string;
  volume: number;
}

export function getActiveHoldingStockCodes(
  positions: readonly HoldingTarget[] | null | undefined
) {
  return Array.from(
    new Set(
      (positions ?? [])
        .filter(position => Number(position.volume) > 0)
        .map(position => position.stockCode.trim().toUpperCase())
        .filter(Boolean)
    )
  ).sort();
}
