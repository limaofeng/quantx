import { TrendingUp, TrendingDown } from 'lucide-react';
import type React from 'react';

import { Card, CardContent } from '@/components/ui/card';
import { financialToneClass } from '@/shared/utils/financialColors';

interface SummaryCardProps {
  title: string;
  value: string | number;
  icon: React.ComponentType<{ className?: string }>;
  trend?: number;
  subtitle?: string;
}

export function SummaryCard({
  title,
  value,
  icon: Icon,
  trend,
  subtitle,
}: SummaryCardProps) {
  return (
    <Card className="bg-white dark:bg-gray-800">
      <CardContent className="p-6">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
              {title}
            </p>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              {value}
            </p>
            {subtitle && (
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                {subtitle}
              </p>
            )}
          </div>
          <div className="h-12 w-12 bg-blue-100 dark:bg-blue-900/20 rounded-lg flex items-center justify-center">
            <Icon className="h-6 w-6 text-blue-600 dark:text-blue-400" />
          </div>
        </div>
        {trend !== undefined && (
          <div className="mt-4 flex items-center">
            {trend > 0 ? (
              <TrendingUp className="mr-1 h-4 w-4 text-market-up" />
            ) : (
              <TrendingDown className="mr-1 h-4 w-4 text-market-down" />
            )}
            <span
              className={`text-sm font-medium ${financialToneClass(trend)}`}
            >
              {Math.abs(trend)}%
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
