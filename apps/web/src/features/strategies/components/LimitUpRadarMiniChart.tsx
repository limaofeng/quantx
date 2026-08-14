import { Activity, CandlestickChart } from 'lucide-react';
import { useMemo } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';

const LIMIT_UP_RADAR_KLINES_QUERY = gql(`
  query LimitUpRadarKlines($code: String!) {
    klines(stockCode: $code, period: MIN_1, limit: 60, order: "asc") {
      time
      close
      volume
    }
  }
`);

export function LimitUpRadarMiniChart({ code }: { code: string }) {
  const [result] = useQuery({
    query: LIMIT_UP_RADAR_KLINES_QUERY,
    variables: { code },
    pause: !code,
    requestPolicy: 'cache-and-network',
  });
  const values = useMemo(() => result.data?.klines ?? [], [result.data?.klines]);
  const geometry = useMemo(() => {
    if (values.length < 2) return null;
    const closes = values.map(value => value.close);
    const volumes = values.map(value => value.volume);
    const minimum = Math.min(...closes);
    const maximum = Math.max(...closes);
    const range = Math.max(maximum - minimum, maximum * 0.002, 0.01);
    const maxVolume = Math.max(...volumes, 1);
    const points = closes
      .map((close, index) => {
        const x = (index / (closes.length - 1)) * 100;
        const y = 8 + (1 - (close - minimum) / range) * 58;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(' ');
    return { maxVolume, points };
  }, [values]);

  if (result.fetching && values.length === 0) {
    return (
      <div className="flex h-40 items-center justify-center text-xs text-slate-500">
        <Activity className="mr-2 h-4 w-4 animate-pulse" />
        加载 1 分钟行情
      </div>
    );
  }

  if (!geometry) {
    return (
      <div className="flex h-40 flex-col items-center justify-center text-xs text-slate-600">
        <CandlestickChart className="mb-2 h-6 w-6" />
        暂无可用分钟 K 线
      </div>
    );
  }

  return (
    <div className="h-40 w-full" aria-label={`${code} 最近 60 根一分钟行情`}>
      <svg
        className="h-full w-full overflow-visible"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        role="img"
      >
        <defs>
          <linearGradient id="radar-line-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#fb7185" stopOpacity="0.3" />
            <stop offset="100%" stopColor="#fb7185" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[20, 40, 60].map(y => (
          <line
            key={y}
            x1="0"
            x2="100"
            y1={y}
            y2={y}
            stroke="#334155"
            strokeOpacity="0.35"
            strokeWidth="0.35"
          />
        ))}
        {values.map((value, index) => {
          const x = (index / Math.max(1, values.length - 1)) * 100;
          const height = (value.volume / geometry.maxVolume) * 22;
          return (
            <rect
              key={value.time}
              x={x}
              y={98 - height}
              width={Math.max(0.3, 80 / values.length)}
              height={height}
              fill="#38bdf8"
              fillOpacity="0.22"
            />
          );
        })}
        <polygon
          points={`0,72 ${geometry.points} 100,72`}
          fill="url(#radar-line-fill)"
        />
        <polyline
          points={geometry.points}
          fill="none"
          stroke="#fb7185"
          strokeWidth="1.1"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}
