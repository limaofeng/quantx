export function indicatorGroupLabel(
  group: string,
  conditions?: unknown,
  definitions?: unknown
) {
  if (group === 'baseline') return '同池基准';
  if (group === 'joint') return '全部条件交集';
  if (group === 'true') return '成立（1）';
  if (group === 'false') return '不成立（0）';
  if (/^condition_\d+$/.test(group)) {
    const index = Number(group.replace('condition_', '')) - 1;
    const condition: unknown = Array.isArray(conditions)
      ? conditions[index]
      : undefined;
    if (
      condition &&
      typeof condition === 'object' &&
      'indicator_id' in condition &&
      'operator' in condition &&
      'value' in condition &&
      typeof condition.indicator_id === 'string' &&
      typeof condition.operator === 'string' &&
      typeof condition.value === 'number'
    ) {
      const definition: unknown = Array.isArray(definitions)
        ? definitions.find(
            (item: unknown) =>
              item != null &&
              typeof item === 'object' &&
              'id' in item &&
              item.id === condition.indicator_id
          )
        : undefined;
      const label =
        definition &&
        typeof definition === 'object' &&
        'label' in definition &&
        typeof definition.label === 'string'
          ? definition.label
          : condition.indicator_id;
      const operators: Record<string, string> = {
        gte: '≥',
        gt: '>',
        lte: '≤',
        lt: '<',
        eq: '=',
        between: '区间',
      };
      const upper =
        condition.operator === 'between' &&
        'value_to' in condition &&
        typeof condition.value_to === 'number'
          ? ` ~ ${condition.value_to}`
          : '';
      return `${label} ${operators[condition.operator] ?? condition.operator} ${condition.value}${upper}`;
    }
    return `单独条件 ${index + 1}`;
  }
  return group;
}

export function formatResearchRate(
  value?: number | null,
  signed = false,
  unit = '%'
) {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${signed && value > 0 ? '+' : ''}${(value * 100).toFixed(2)}${unit}`;
}
