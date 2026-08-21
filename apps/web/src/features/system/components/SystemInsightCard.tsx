import {
  Activity,
  AlertTriangle,
  Bot,
  Cable,
  CheckCircle2,
  Database,
  RefreshCw,
  Wifi,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { API_ENDPOINTS } from '@/shared/constants/api';
import { cn } from '@/utils/cn';

type SystemStatus = 'healthy' | 'warning' | 'error';

interface ServiceStatus {
  name: string;
  status: SystemStatus;
  metric: string;
  icon: React.ElementType;
  group: 'platform' | 'runtime';
  optional?: boolean;
}

interface ComponentStatus {
  status?: string;
  version?: string;
  configVersion?: number;
  connectedDevices?: number;
  readyDevices?: number;
  onlineDevices?: number;
  reconcilingDevices?: number;
  registeredDevices?: number;
  onlineWorkers?: number;
  dependencies?: {
    redis?: string;
  };
}

interface HealthComponents {
  api?: ComponentStatus;
  database?: ComponentStatus;
  engine?: ComponentStatus;
  worker?: ComponentStatus;
  qmtAgent?: ComponentStatus;
  aiRuntime?: ComponentStatus;
  marketData?: ComponentStatus;
  marketGateway?: ComponentStatus;
  prefect?: ComponentStatus;
}

interface HealthResponse {
  components?: HealthComponents;
}

function toSystemStatus(
  component: ComponentStatus | undefined,
  optional = false
): SystemStatus {
  const status = component?.status?.toLowerCase();
  if (status === 'ready') return 'healthy';
  if (
    optional &&
    (status === 'disabled' || status === 'offline' || status === 'unconfigured')
  ) {
    return 'warning';
  }
  if (status === 'starting' || status === 'reconciling') return 'warning';
  return 'error';
}

export function SystemInsightCard() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [lastCheck, setLastCheck] = useState<Date | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const loadHealth = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const response = await fetch(`${API_ENDPOINTS.HEALTH}/components`, {
        cache: 'no-store',
      });
      if (!response.ok) {
        throw new Error(`Health check failed: ${response.status}`);
      }
      const payload = (await response.json()) as HealthResponse;
      setHealth(payload);
      setHealthError(false);
      setLastCheck(new Date());
    } catch {
      setHealthError(true);
      setLastCheck(new Date());
    } finally {
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadHealth();
    const timer = window.setInterval(loadHealth, 30000);
    return () => {
      window.clearInterval(timer);
    };
  }, [loadHealth]);

  const services: ServiceStatus[] = useMemo(() => {
    if (!health && !healthError) {
      return [
        {
          name: 'API',
          status: 'warning',
          metric: 'Checking',
          icon: Activity,
          group: 'platform',
        },
        {
          name: '策略引擎',
          status: 'warning',
          metric: 'Checking',
          icon: Activity,
          group: 'platform',
        },
        {
          name: '数据存储',
          status: 'warning',
          metric: 'Checking',
          icon: Database,
          group: 'platform',
        },
        {
          name: '任务调度',
          status: 'warning',
          metric: 'Checking',
          icon: Activity,
          group: 'platform',
        },
        {
          name: 'Market Gateway',
          status: 'warning',
          metric: 'Checking',
          icon: Cable,
          group: 'runtime',
        },
        {
          name: '行情服务',
          status: 'warning',
          metric: 'Checking',
          icon: Wifi,
          group: 'runtime',
        },
        {
          name: 'QMT Agent',
          status: 'warning',
          metric: 'Checking',
          icon: ShieldCheck,
          group: 'runtime',
        },
        {
          name: 'AI Runtime',
          status: 'warning',
          metric: 'Checking',
          icon: Bot,
          group: 'runtime',
          optional: true,
        },
      ];
    }

    if (healthError || !health?.components) {
      return [
        {
          name: 'API',
          status: 'error',
          metric: 'Unavailable',
          icon: Activity,
          group: 'platform',
        },
        {
          name: '策略引擎',
          status: 'error',
          metric: 'Unavailable',
          icon: Activity,
          group: 'platform',
        },
        {
          name: '数据存储',
          status: 'error',
          metric: 'Unavailable',
          icon: Database,
          group: 'platform',
        },
        {
          name: '任务调度',
          status: 'error',
          metric: 'Unavailable',
          icon: Activity,
          group: 'platform',
        },
        {
          name: 'Market Gateway',
          status: 'error',
          metric: 'Unavailable',
          icon: Cable,
          group: 'runtime',
        },
        {
          name: '行情服务',
          status: 'error',
          metric: 'Unavailable',
          icon: Wifi,
          group: 'runtime',
        },
        {
          name: 'QMT Agent',
          status: 'error',
          metric: 'Unavailable',
          icon: ShieldCheck,
          group: 'runtime',
        },
        {
          name: 'AI Runtime',
          status: 'error',
          metric: 'Unavailable',
          icon: Bot,
          group: 'runtime',
          optional: true,
        },
      ];
    }

    const {
      aiRuntime,
      api,
      database,
      engine,
      marketData,
      marketGateway,
      prefect,
      qmtAgent,
      worker,
    } = health.components;
    const schedulerStatus =
      prefect?.status === 'disabled'
        ? 'warning'
        : toSystemStatus(prefect, true) !== 'healthy'
          ? toSystemStatus(prefect, true)
          : toSystemStatus(worker, true);
    return [
      {
        name: 'API',
        status: toSystemStatus(api),
        metric: api?.status === 'ready' ? 'Online' : (api?.status ?? 'Unknown'),
        icon: Activity,
        group: 'platform',
      },
      {
        name: '策略引擎',
        status: toSystemStatus(engine),
        metric:
          engine?.status === 'ready'
            ? 'Lease Active'
            : (engine?.status ?? 'Offline'),
        icon: Activity,
        group: 'platform',
      },
      {
        name: '数据存储',
        status: toSystemStatus(database),
        metric:
          database?.status === 'ready'
            ? `PostgreSQL ${database.version ?? 'Online'}`
            : (database?.status ?? 'Unavailable'),
        icon: Database,
        group: 'platform',
      },
      {
        name: '任务调度',
        status: schedulerStatus,
        metric:
          prefect?.status === 'disabled'
            ? 'Disabled'
            : worker?.status === 'ready'
              ? `${worker.onlineWorkers ?? 0} Worker`
              : (worker?.status ?? prefect?.status ?? 'Offline'),
        icon: Activity,
        group: 'platform',
      },
      {
        name: 'Market Gateway',
        status: toSystemStatus(marketGateway),
        metric:
          marketGateway?.status === 'ready'
            ? marketGateway.dependencies?.redis === 'ready'
              ? 'Redis Ready'
              : 'Ready'
            : (marketGateway?.status ?? 'Unavailable'),
        icon: Cable,
        group: 'runtime',
      },
      {
        name: '行情服务',
        status: toSystemStatus(marketData, true),
        metric:
          marketData?.status === 'ready'
            ? `${marketData.connectedDevices ?? 0} Agent`
            : (marketData?.status ?? 'Offline'),
        icon: Wifi,
        group: 'runtime',
      },
      {
        name: 'QMT Agent',
        status: toSystemStatus(qmtAgent, true),
        metric:
          qmtAgent?.status === 'ready'
            ? `${qmtAgent.connectedDevices ?? 0} Connected · ${qmtAgent.readyDevices ?? 0} Ready`
            : (qmtAgent?.status ?? 'Offline'),
        icon: ShieldCheck,
        group: 'runtime',
      },
      {
        name: 'AI Runtime',
        status: toSystemStatus(aiRuntime, true),
        metric:
          aiRuntime?.status === 'ready'
            ? `Config v${aiRuntime.version ?? aiRuntime.configVersion ?? 0}`
            : (aiRuntime?.status ?? 'Offline'),
        icon: Bot,
        group: 'runtime',
        optional: true,
      },
    ];
  }, [health, healthError]);

  const requiredServices = services.filter(service => !service.optional);
  const overallStatus: SystemStatus = requiredServices.some(
    service => service.status === 'error'
  )
    ? 'error'
    : requiredServices.some(service => service.status === 'warning')
      ? 'warning'
      : 'healthy';
  const serviceGroups = [
    {
      id: 'platform' as const,
      label: '平台服务',
      description: 'API、状态真源与任务执行',
    },
    {
      id: 'runtime' as const,
      label: '执行与运行时',
      description: '行情接入、QMT 与扩展能力',
    },
  ];

  return (
    <div
      className={cn(
        'w-full p-5 rounded-xl border relative overflow-hidden transition-all duration-300',
        overallStatus === 'healthy' &&
          'border-slate-200/40 dark:border-slate-800/40 bg-white/40 dark:bg-slate-900/40',
        overallStatus === 'warning' &&
          'border-amber-200/60 dark:border-amber-900/60 bg-amber-50/40 dark:bg-amber-900/10',
        overallStatus === 'error' &&
          'border-red-200/60 dark:border-red-900/60 bg-red-50/30 dark:bg-red-950/20'
      )}
    >
      <div className="relative z-10">
        <div className="flex flex-col gap-4 border-b border-slate-200/50 pb-4 dark:border-white/5 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-4">
            <div
              className={cn(
                'flex h-12 w-12 shrink-0 items-center justify-center rounded-xl shadow-sm',
                overallStatus === 'healthy' &&
                  'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
                overallStatus === 'warning' &&
                  'bg-amber-500/10 text-amber-600 dark:text-amber-400',
                overallStatus === 'error' &&
                  'bg-red-500/10 text-red-600 dark:text-red-400 animate-pulse'
              )}
            >
              {overallStatus === 'healthy' && (
                <CheckCircle2 className="h-7 w-7" />
              )}
              {overallStatus === 'warning' && (
                <AlertTriangle className="h-7 w-7" />
              )}
              {overallStatus === 'error' && <XCircle className="h-7 w-7" />}
            </div>
            <div>
              <h3 className="font-bold text-lg text-slate-800 dark:text-slate-100">
                {overallStatus === 'healthy'
                  ? '系统运行正常'
                  : overallStatus === 'warning'
                    ? '存在潜在风险'
                    : '系统异常'}
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                关键服务与运行时依赖状态
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 font-mono text-xs text-slate-500">
            <span>
              Last Check:{' '}
              {lastCheck
                ? lastCheck.toLocaleTimeString('zh-CN', { hour12: false })
                : '--:--:--'}
            </span>
            <button
              type="button"
              aria-label="Refresh health status"
              title="Refresh health status"
              disabled={isRefreshing}
              onClick={() => void loadHealth()}
              className="inline-flex h-5 w-5 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-200/60 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-60 dark:hover:bg-white/10 dark:hover:text-slate-200"
            >
              <RefreshCw
                className={cn('h-3.5 w-3.5', isRefreshing && 'animate-spin')}
              />
            </button>
          </div>
        </div>

        <div aria-live="polite" className="mt-4 grid gap-3 lg:grid-cols-2">
          {serviceGroups.map(group => (
            <section
              key={group.id}
              aria-labelledby={`health-group-${group.id}`}
              className="rounded-xl border border-slate-200/50 bg-white/35 p-3 dark:border-white/5 dark:bg-white/[0.025]"
            >
              <div className="mb-3 flex items-end justify-between gap-3 px-1">
                <div>
                  <h4
                    id={`health-group-${group.id}`}
                    className="text-xs font-semibold text-slate-700 dark:text-slate-200"
                  >
                    {group.label}
                  </h4>
                  <p className="mt-0.5 text-[10px] text-slate-400">
                    {group.description}
                  </p>
                </div>
                <span className="font-mono text-[10px] text-slate-400">
                  {
                    services.filter(service => service.group === group.id)
                      .length
                  }
                  {' 项'}
                </span>
              </div>
              <div className="grid gap-2 xl:grid-cols-2">
                {services
                  .filter(service => service.group === group.id)
                  .map(service => (
                    <div
                      key={service.name}
                      className="flex min-w-0 items-center gap-3 rounded-lg border border-slate-100/60 bg-white/60 p-2.5 dark:border-white/5 dark:bg-slate-950/35"
                    >
                      <div
                        className={cn(
                          'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
                          service.status === 'healthy'
                            ? 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
                            : service.status === 'warning'
                              ? 'bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400'
                              : 'bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-400'
                        )}
                      >
                        <service.icon className="h-4 w-4" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-xs font-semibold text-slate-700 dark:text-slate-300">
                            {service.name}
                          </span>
                          {service.optional && (
                            <span className="rounded bg-slate-200/60 px-1 py-0.5 text-[8px] font-medium uppercase tracking-wide text-slate-500 dark:bg-white/5 dark:text-slate-500">
                              可选
                            </span>
                          )}
                        </div>
                        <p
                          title={service.metric}
                          className={cn(
                            'mt-0.5 truncate font-mono text-[10px]',
                            service.status === 'healthy'
                              ? 'text-slate-400'
                              : service.status === 'warning'
                                ? 'font-semibold text-amber-600 dark:text-amber-400'
                                : 'font-semibold text-red-600 dark:text-red-400'
                          )}
                        >
                          {service.metric}
                        </p>
                      </div>
                      <span
                        aria-label={`${service.name}: ${service.status}`}
                        className={cn(
                          'h-2 w-2 shrink-0 rounded-full',
                          service.status === 'healthy' && 'bg-emerald-400',
                          service.status === 'warning' && 'bg-amber-400',
                          service.status === 'error' && 'bg-red-400'
                        )}
                      />
                    </div>
                  ))}
              </div>
            </section>
          ))}
        </div>
      </div>

      {/* Background Decor */}
      <div
        className={cn(
          'absolute -right-8 -top-8 z-0 w-40 h-40 rounded-full blur-3xl opacity-10 pointer-events-none',
          overallStatus === 'healthy'
            ? 'bg-emerald-500'
            : overallStatus === 'warning'
              ? 'bg-amber-500'
              : 'bg-red-600'
        )}
      />
    </div>
  );
}
