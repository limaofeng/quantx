// 关键指标卡片组件
import { type LucideIcon } from 'lucide-react';
import type React from 'react';

import { Card } from '@/components/ui/card';

interface MetricCardProps {
  title: string;
  value: string;
  change?: string;
  changeLabel?: string;
  icon: LucideIcon;
  variant?: 'default' | 'success' | 'warning' | 'destructive';
  testId?: string;
  children?: React.ReactNode;
}

const variantStyles = {
  default: {
    iconBg: 'bg-blue-50 dark:bg-blue-950/30',
    iconColor: 'text-blue-600 dark:text-blue-400',
    valueColor: 'text-slate-900 dark:text-white',
    shadowColor: 'shadow-blue-600/20',
    borderColor: 'border-blue-200/50 dark:border-blue-800/30',
  },
  success: {
    iconBg: 'bg-emerald-50 dark:bg-emerald-950/30',
    iconColor: 'text-emerald-600 dark:text-emerald-400',
    valueColor: 'text-emerald-600 dark:text-emerald-400',
    shadowColor: 'shadow-emerald-600/20',
    borderColor: 'border-emerald-200/50 dark:border-emerald-800/30',
  },
  warning: {
    iconBg: 'bg-amber-50 dark:bg-amber-950/30',
    iconColor: 'text-amber-600 dark:text-amber-400',
    valueColor: 'text-amber-600 dark:text-amber-400',
    shadowColor: 'shadow-amber-600/20',
    borderColor: 'border-amber-200/50 dark:border-amber-800/30',
  },
  destructive: {
    iconBg: 'bg-rose-50 dark:bg-rose-950/30',
    iconColor: 'text-rose-600 dark:text-rose-400',
    valueColor: 'text-rose-600 dark:text-rose-400',
    shadowColor: 'shadow-rose-600/20',
    borderColor: 'border-rose-200/50 dark:border-rose-800/30',
  },
};

export function MetricCard({
  title,
  value,
  change,
  changeLabel,
  icon: Icon,
  variant = 'default',
  testId,
  children,
}: MetricCardProps) {
  const styles = variantStyles[variant];

  return (
    <Card
      className={`
      p-6 rounded-2xl
      bg-white dark:bg-slate-900/50
      border border-slate-200 dark:border-slate-800/50
      shadow-sm hover:shadow-lg
      transition-all duration-200
      animate-slide-up
      group
      hover:border-${styles.borderColor}
    `}
    >
      <div className="flex items-center justify-between">
        <div className="flex-1">
          <p className="text-xs font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">
            {title}
          </p>
          <p
            className={`text-3xl font-black ${styles.valueColor} transition-colors duration-200`}
            data-testid={testId}
          >
            {value}
          </p>
        </div>
        <div
          className={`
            w-14 h-14 ${styles.iconBg} ${styles.borderColor}
            rounded-2xl
            flex items-center justify-center
            border
            shadow-lg ${styles.shadowColor}
            group-hover:scale-110
            transition-all duration-200
          `}
        >
          <Icon className={`${styles.iconColor} h-7 w-7`} />
        </div>
      </div>

      {(change || children) && (
        <div className="flex items-center mt-4 pt-4 border-t border-slate-100 dark:border-slate-800/50">
          {change && (
            <>
              <span
                className="inline-flex items-center px-2 py-1 rounded-md bg-emerald-50 dark:bg-emerald-950/30 text-emerald-600 dark:text-emerald-400 text-xs font-bold"
                data-testid={`${testId}-change`}
              >
                {change}
              </span>
              {changeLabel && (
                <span className="text-sm text-slate-500 dark:text-slate-400 ml-2 font-medium">
                  {changeLabel}
                </span>
              )}
            </>
          )}
          {children}
        </div>
      )}
    </Card>
  );
}
