import { Bot, ArrowUpRight, BarChart3, Shield } from 'lucide-react';
import { useMemo } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Card } from '@/components/ui/card';
import {
  getRiskLevelColor,
  getRiskLevelName,
  getCategoryIcon,
} from '@/shared/utils/strategyHelpers';
import { cn } from '@/utils/cn';

import { mapStrategyDefinitionView, type StrategyDefinition } from '../domain';
import { StrategyDefinitionsQuery } from '../hooks/strategyInstanceOperations';

interface AvailableStrategiesProps {
  compact?: boolean;
}

export default function AvailableStrategies({
  compact = false,
}: AvailableStrategiesProps) {
  const [, setLocation] = useLocation();
  const [{ data, fetching: isLoading, error }] = useQuery({
    query: StrategyDefinitionsQuery,
    requestPolicy: 'cache-and-network',
  });

  const strategies: StrategyDefinition[] = useMemo(
    () =>
      ((data?.strategyDefinitions || []) as unknown[]).map(
        mapStrategyDefinitionView
      ),
    [data]
  );

  if (isLoading && strategies.length === 0) {
    return (
      <div
        className={cn(
          'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
          compact ? 'gap-3' : 'gap-ui-panel'
        )}
      >
        {[1, 2, 3].map(i => (
          <Card
            key={i}
            className={cn(
              'animate-pulse',
              compact ? 'rounded-lg p-ui-section' : 'rounded-panel p-ui-panel'
            )}
          >
            <div
              className={cn(
                'mb-4 bg-slate-200 dark:bg-slate-700',
                compact ? 'h-9 w-9 rounded-lg' : 'h-12 w-12 rounded-panel'
              )}
            />
            <div className="h-4 w-2/3 bg-slate-200 dark:bg-slate-700 rounded mb-2" />
            <div className="h-3 w-full bg-slate-100 dark:bg-slate-800 rounded" />
          </Card>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <Card
        className={cn(
          'border-rose-500/20 bg-rose-500/5 text-center',
          compact ? 'rounded-lg p-ui-section' : 'rounded-panel p-ui-section'
        )}
      >
        <p className="text-rose-500 font-black text-ui-caption uppercase tracking-widest">
          初始化失败: {error.message}
        </p>
      </Card>
    );
  }

  if (!strategies || strategies.length === 0) {
    return (
      <Card
        className={cn(
          'border-2 border-dashed border-slate-200 bg-slate-50/50 text-center dark:border-white/10 dark:bg-slate-900/20',
          compact ? 'rounded-lg p-ui-section' : 'rounded-panel p-16'
        )}
      >
        <Bot className="mx-auto h-12 w-12 text-slate-300 mb-6" />
        <h3 className="text-ui-page-title font-black text-slate-900 dark:text-white mb-2 uppercase tracking-tight">
          未发现策略模板
        </h3>
        <p className="text-slate-400 text-ui-body max-w-xs mx-auto">
          请检查后端策略目录配置。
        </p>
      </Card>
    );
  }

  // 根据风险等级获取渐变色
  const getGradientByRisk = (riskLevel: string) => {
    switch (riskLevel) {
      case 'LOW':
        return 'from-emerald-500/20 via-emerald-500/5 to-transparent';
      case 'MEDIUM':
        return 'from-blue-500/20 via-blue-500/5 to-transparent';
      case 'HIGH':
        return 'from-amber-500/20 via-amber-500/5 to-transparent';
      case 'VERY_HIGH':
        return 'from-rose-500/20 via-rose-500/5 to-transparent';
      default:
        return 'from-slate-500/20 via-slate-500/5 to-transparent';
    }
  };

  // 根据风险等级获取图标颜色
  const getIconColorByRisk = (riskLevel: string) => {
    switch (riskLevel) {
      case 'LOW':
        return 'bg-emerald-500 shadow-emerald-500/30';
      case 'MEDIUM':
        return 'bg-blue-500 shadow-blue-500/30';
      case 'HIGH':
        return 'bg-amber-500 shadow-amber-500/30';
      case 'VERY_HIGH':
        return 'bg-rose-500 shadow-rose-500/30';
      default:
        return 'bg-primary shadow-primary/30';
    }
  };

  return (
    <div
      className={cn(
        'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3',
        compact ? 'gap-3' : 'gap-ui-panel'
      )}
    >
      {strategies.map((definition: StrategyDefinition) => {
        const IconComponent = getCategoryIcon(definition.category);
        const gradient = getGradientByRisk(definition.riskLevel as string);
        const iconColor = getIconColorByRisk(definition.riskLevel as string);

        return (
          <Card
            key={definition.key}
            onClick={() =>
              setLocation(`/strategies/${definition.strategyId}/run`)
            }
            className={cn(
              'group relative cursor-pointer overflow-hidden border border-slate-200 bg-white transition-all duration-300 hover:border-primary/20 hover:shadow-none hover:shadow-primary/5 dark:border-white/5 dark:bg-slate-900/40',
              compact ? 'rounded-lg' : 'rounded-panel'
            )}
          >
            {/* 渐变装饰背景 */}
            <div
              className={`absolute top-0 right-0 w-40 h-40 bg-gradient-radial ${gradient} opacity-0 group-hover:opacity-100 transition-opacity duration-500`}
            />

            {/* 顶部装饰条 */}
            <div
              className={`absolute top-0 left-8 right-8 h-0.5 bg-gradient-to-r ${gradient.replace('/20', '/40').replace('/5', '/20')} opacity-0 group-hover:opacity-100 transition-opacity duration-300`}
            />

            <div
              className={cn(
                'relative',
                compact ? 'p-ui-section' : 'p-ui-panel'
              )}
            >
              {/* 头部区域 */}
              <div className="flex items-start justify-between mb-5">
                <div
                  className={cn(
                    'flex shrink-0 items-center justify-center text-white shadow-lg transition-all duration-300 group-hover:scale-105',
                    compact
                      ? 'h-10 w-10 rounded-lg'
                      : 'h-14 w-14 rounded-panel group-hover:rotate-3',
                    iconColor
                  )}
                >
                  <IconComponent size={compact ? 20 : 26} />
                </div>

                <div className="opacity-0 group-hover:opacity-100 transition-all duration-300 translate-x-2 group-hover:translate-x-0">
                  <div className="w-8 h-8 rounded-panel bg-primary/10 flex items-center justify-center">
                    <ArrowUpRight size={16} className="text-primary" />
                  </div>
                </div>
              </div>

              {/* 策略名称和描述 */}
              <div className={cn(compact ? 'mb-4' : 'mb-5')}>
                <div className="flex items-center gap-2 mb-2">
                  <h3 className="text-ui-title font-black text-slate-900 dark:text-white uppercase tracking-tight group-hover:text-primary transition-colors duration-300">
                    {definition.displayName}
                  </h3>
                </div>
                <p className="text-ui-label text-slate-500 dark:text-slate-400 line-clamp-2 leading-relaxed">
                  {definition.description}
                </p>
              </div>

              {/* 底部信息区 */}
              <div className="flex items-center justify-between border-t border-slate-100 pt-4 dark:border-white/5">
                <div className="flex items-center gap-3">
                  {/* 类型标签 */}
                  <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-50 dark:bg-white/5">
                    <BarChart3 size={12} className="text-slate-400" />
                    <span className="text-ui-caption font-bold text-slate-500 dark:text-slate-400 uppercase">
                      {definition.market} · {definition.supportedInstruments[0]}
                    </span>
                  </div>
                </div>

                {/* 风险等级 */}
                <div
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border ${getRiskLevelColor(definition.riskLevel)}`}
                >
                  <Shield size={12} />
                  <span className="text-ui-caption font-black uppercase tracking-tight">
                    {getRiskLevelName(definition.riskLevel)}
                  </span>
                </div>
              </div>

              {/* 悬停时的快速操作提示 */}
              <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-primary/0 via-primary/50 to-primary/0 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
            </div>
          </Card>
        );
      })}
    </div>
  );
}
