import {
  financialDirection,
  financialToneClass,
} from '@/shared/utils/financialColors';

interface MiniSparklineProps {
  changePercent?: number | null;
  high?: number | null;
  lastPrice?: number | null;
  low?: number | null;
  open?: number | null;
  preClose?: number | null;
}

function finite(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function MiniSparkline({
  changePercent,
  high,
  lastPrice,
  low,
  open,
  preClose,
}: MiniSparklineProps) {
  const values = [
    finite(open) ?? finite(preClose),
    finite(low),
    finite(high),
    finite(lastPrice),
  ].filter((value): value is number => value !== null);

  if (values.length < 2) {
    return (
      <span
        aria-label="迷你走势暂无数据"
        className="inline-flex h-7 w-16 items-center justify-center font-mono text-[10px] text-slate-600"
      >
        --
      </span>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || Math.max(Math.abs(max) * 0.01, 1);
  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 64;
      const y = 25 - ((value - min) / range) * 20;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');
  const direction = financialDirection(changePercent);
  const directionLabel =
    direction === 'up' ? '上涨' : direction === 'down' ? '下跌' : '平盘';

  return (
    <svg
      aria-label={`迷你走势，${directionLabel}`}
      className={`h-7 w-16 overflow-visible ${financialToneClass(changePercent)}`}
      viewBox="0 0 64 30"
      role="img"
    >
      <path
        d="M0 27H64"
        fill="none"
        stroke="currentColor"
        strokeOpacity="0.15"
        strokeWidth="1"
      />
      <polyline
        fill="none"
        points={points}
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.6"
      />
    </svg>
  );
}
