export function resolveHoldingInstrumentName(
  stockCode: string,
  positionName?: string | null,
  catalogName?: string | null
) {
  const normalizedCode = stockCode.trim().toUpperCase();
  const codeWithoutExchange = normalizedCode.split('.', 1)[0];
  const codeAliases = new Set([normalizedCode, codeWithoutExchange]);

  for (const candidate of [positionName, catalogName]) {
    const value = String(candidate || '').trim();
    if (value && !codeAliases.has(value.toUpperCase())) return value;
  }

  return normalizedCode;
}
