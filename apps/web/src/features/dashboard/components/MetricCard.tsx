// 关键指标卡片组件
import { type LucideIcon } from 'lucide-react';
import type React from 'react';

import { Card } from '@/components/ui/card';
import { cn } from '@/utils/cn';

interface MetricCardProps {
  title: string;
  value: string;
  change?: string;
  changeLabel?: string;
  icon: LucideIcon;
  variant?: 'default' | 'success' | 'warning' | 'destructive';
  valueClassName?: string;
  changeClassName?: string;
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
  valueClassName,
  changeClassName,
}: MetricCardProps) {
  const styles = variantStyles[variant];

  return (
    <Card
      className={`
      rounded-lg p-4
      bg-[#0f172a]/70
      border ${styles.borderColor}
      shadow-sm
      transition-colors duration-200
      animate-slide-up
      group
      hover:bg-[#111c31]
    `}
    >
      <div className="flex items-center justify-between">
        <div className="flex-1">
          <p className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">
            {title}
          </p>
          <p
            className={cn(
              'text-2xl font-black transition-colors duration-200',
              styles.valueColor,
              valueClassName
            )}
            data-testid={testId}
          >
            {value}
          </p>
        </div>
        <div
          className={`
            w-10 h-10 ${styles.iconBg} ${styles.borderColor}
            rounded-lg
            flex items-center justify-center
            border
            shadow-sm ${styles.shadowColor}
            transition-colors duration-200
          `}
        >
          <Icon className={`${styles.iconColor} h-5 w-5`} />
        </div>
      </div>

      {(change || children) && (
        <div className="flex items-center mt-3 pt-3 border-t border-white/5">
          {change && (
            <>
              <span
                className={cn(
                  'inline-flex items-center rounded-md bg-white/[0.04] px-2 py-1 text-xs font-bold',
                  changeClassName || 'text-slate-400'
                )}
                data-testid={`${testId}-change`}
              >
                {change}
              </span>
              {changeLabel && (
                <span className="ml-2 text-sm font-medium text-slate-400">
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
