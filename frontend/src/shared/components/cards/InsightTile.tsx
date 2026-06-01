import React, { useMemo } from 'react';
import { AreaChart, Area, ResponsiveContainer } from 'recharts';

// --- Types ---
export type SparklinePattern = 'stable' | 'volatile' | 'rising' | 'flat';
export type DashboardTheme =
  | 'emerald'
  | 'amber'
  | 'rose'
  | 'sky'
  | 'indigo'
  | 'blue'
  | 'violet';
export type SparklinePoint = number | { v: number };

// --- Sub-components ---

// 0. Mini Sparkline Background Component (using Recharts)
export const MiniSparkline: React.FC<{
  theme: DashboardTheme;
  pattern?: SparklinePattern;
  data?: SparklinePoint[];
}> = ({ theme, pattern, data: sparklineData }) => {
  const colorMap: Record<DashboardTheme, { stroke: string; fill: string }> = {
    emerald: { stroke: '#10b981', fill: 'url(#sparkGradientEmerald)' },
    amber: { stroke: '#f59e0b', fill: 'url(#sparkGradientAmber)' },
    rose: { stroke: '#f43f5e', fill: 'url(#sparkGradientRose)' },
    sky: { stroke: '#0ea5e9', fill: 'url(#sparkGradientSky)' },
    indigo: { stroke: '#6366f1', fill: 'url(#sparkGradientIndigo)' },
    blue: { stroke: '#3b82f6', fill: 'url(#sparkGradientBlue)' },
    violet: { stroke: '#8b5cf6', fill: 'url(#sparkGradientViolet)' },
  };

  const data = useMemo(() => {
    const normalized = (sparklineData || [])
      .map((point) => (typeof point === 'number' ? point : point.v))
      .filter((value) => Number.isFinite(value))
      .map((value) => ({ v: value }));

    if (normalized.length >= 2) {
      return normalized;
    }

    if (!pattern) {
      return normalized.length === 1
        ? [{ v: normalized[0].v }, { v: normalized[0].v }]
        : [];
    }

    const patternData: Record<SparklinePattern, { v: number }[]> = {
      volatile: [
        { v: 30 },
        { v: 55 },
        { v: 25 },
        { v: 70 },
        { v: 40 },
        { v: 65 },
        { v: 35 },
        { v: 50 },
      ],
      rising: [
        { v: 15 },
        { v: 20 },
        { v: 28 },
        { v: 35 },
        { v: 45 },
        { v: 55 },
        { v: 68 },
        { v: 80 },
      ],
      stable: [
        { v: 45 },
        { v: 48 },
        { v: 42 },
        { v: 47 },
        { v: 44 },
        { v: 46 },
        { v: 43 },
        { v: 45 },
      ],
      flat: [
        { v: 50 },
        { v: 50 },
        { v: 52 },
        { v: 49 },
        { v: 51 },
        { v: 50 },
        { v: 50 },
        { v: 51 },
      ],
    };

    return patternData[pattern];
  }, [pattern, sparklineData]);

  const themeConfig = colorMap[theme] || colorMap.sky;
  const { stroke, fill } = themeConfig;

  if (data.length === 0) {
    return null;
  }

  return (
    <div
      aria-hidden="true"
      className="absolute inset-0 opacity-25 pointer-events-none"
      style={{
        maskImage:
          'linear-gradient(to right, transparent 0%, transparent 40%, black 75%)',
        WebkitMaskImage:
          'linear-gradient(to right, transparent 0%, transparent 40%, black 75%)',
      }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ top: 15, right: 0, left: 0, bottom: 2 }}
        >
          <defs>
            <linearGradient
              id={`sparkGradient${theme.charAt(0).toUpperCase() + theme.slice(1)}`}
              x1="0"
              y1="0"
              x2="0"
              y2="1"
            >
              <stop offset="0%" stopColor={stroke} stopOpacity={0.5} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="v"
            stroke={stroke}
            strokeWidth={2}
            fill={fill}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};

// 1. High-level Insight Tile (Compact Horizontal Layout with Sparkline)
export const InsightTile: React.FC<{
  label: string;
  value: string | number;
  subValue: string;
  icon?: React.ReactNode;
  theme: DashboardTheme;
  status?: string;
  sparklinePattern?: SparklinePattern;
  sparklineData?: SparklinePoint[];
}> = ({
  label,
  value,
  subValue,
  icon,
  theme,
  status,
  sparklinePattern,
  sparklineData,
}) => {
  const themes: Record<DashboardTheme, string> = {
    emerald: 'bg-emerald-500/10 text-emerald-500',
    amber: 'bg-amber-500/10 text-amber-500',
    rose: 'bg-rose-500/10 text-rose-500',
    sky: 'bg-sky-500/10 text-sky-500',
    indigo: 'bg-indigo-500/10 text-indigo-500',
    blue: 'bg-blue-500/10 text-blue-500',
    violet: 'bg-violet-500/10 text-violet-500',
  };

  const statusColors: Record<DashboardTheme, string> = {
    emerald: 'text-emerald-400 bg-emerald-500/20',
    amber: 'text-amber-400 bg-amber-500/20',
    rose: 'text-rose-400 bg-rose-500/20',
    sky: 'text-sky-400 bg-sky-500/20',
    indigo: 'text-indigo-400 bg-indigo-500/20',
    blue: 'text-blue-400 bg-blue-500/20',
    violet: 'text-violet-400 bg-violet-500/20',
  };

  return (
    <div className="relative flex items-center gap-3 px-4 py-3 rounded-2xl border border-white/5 bg-white/5 dark:bg-slate-900/40 backdrop-blur-sm overflow-hidden min-h-[72px]">
      <MiniSparkline
        theme={theme}
        pattern={sparklinePattern}
        data={sparklineData}
      />
      {icon && (
        <div
          className={`p-2.5 rounded-xl flex-shrink-0 ${themes[theme]} relative z-10`}
        >
          {React.isValidElement(icon)
            ? React.cloneElement(icon as React.ReactElement, {
                size: 20,
                strokeWidth: 2.5,
              })
            : icon}
        </div>
      )}
      <div className="flex-1 min-w-0 relative z-10">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xl font-black text-slate-900 dark:text-white tracking-tight">
            {value}
          </span>
          {status && (
            <span
              className={`text-[9px] font-bold px-1.5 py-0.5 rounded uppercase flex-shrink-0 ${statusColors[theme]}`}
            >
              {status}
            </span>
          )}
        </div>
        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest truncate">
          {label}
        </p>
        <p className="text-[9px] font-medium text-slate-500 truncate mt-0.5">
          {subValue}
        </p>
      </div>
    </div>
  );
};
