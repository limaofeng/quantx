import {
  Activity,
  AlertTriangle,
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
}

interface ComponentStatus {
  status?: string;
  version?: string;
  connectedDevices?: number;
  onlineDevices?: number;
  reconcilingDevices?: number;
  registeredDevices?: number;
  onlineWorkers?: number;
}

interface HealthComponents {
  api?: ComponentStatus;
  database?: ComponentStatus;
  engine?: ComponentStatus;
  worker?: ComponentStatus;
  qmtAgent?: ComponentStatus;
  marketData?: ComponentStatus;
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
  if (optional && (status === 'disabled' || status === 'offline')) {
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
        { name: 'API', status: 'warning', metric: 'Checking', icon: Activity },
        {
          name: '策略引擎',
          status: 'warning',
          metric: 'Checking',
          icon: Activity,
        },
        { name: '行情服务', status: 'warning', metric: 'Checking', icon: Wifi },
        {
          name: '数据存储',
          status: 'warning',
          metric: 'Checking',
          icon: Database,
        },
        {
          name: '任务调度',
          status: 'warning',
          metric: 'Checking',
          icon: Activity,
        },
        {
          name: 'QMT Agent',
          status: 'warning',
          metric: 'Checking',
          icon: ShieldCheck,
        },
      ];
    }

    if (healthError || !health?.components) {
      return [
        { name: 'API', status: 'error', metric: 'Unavailable', icon: Activity },
        {
          name: '策略引擎',
          status: 'error',
          metric: 'Unavailable',
          icon: Activity,
        },
        {
          name: '行情服务',
          status: 'error',
          metric: 'Unavailable',
          icon: Wifi,
        },
        {
          name: '数据存储',
          status: 'error',
          metric: 'Unavailable',
          icon: Database,
        },
        {
          name: '任务调度',
          status: 'error',
          metric: 'Unavailable',
          icon: Activity,
        },
        {
          name: 'QMT Agent',
          status: 'error',
          metric: 'Unavailable',
          icon: ShieldCheck,
        },
      ];
    }

    const { api, database, engine, marketData, prefect, qmtAgent, worker } =
      health.components;
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
      },
      {
        name: '策略引擎',
        status: toSystemStatus(engine),
        metric:
          engine?.status === 'ready'
            ? 'Lease Active'
            : (engine?.status ?? 'Offline'),
        icon: Activity,
      },
      {
        name: '行情服务',
        status: toSystemStatus(marketData, true),
        metric:
          marketData?.status === 'ready'
            ? `${marketData.connectedDevices ?? 0} Agent`
            : (marketData?.status ?? 'Offline'),
        icon: Wifi,
      },
      {
        name: '数据存储',
        status: toSystemStatus(database),
        metric:
          database?.status === 'ready'
            ? `PostgreSQL ${database.version ?? 'Online'}`
            : (database?.status ?? 'Unavailable'),
        icon: Database,
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
      },
      {
        name: 'QMT Agent',
        status: toSystemStatus(qmtAgent, true),
        metric:
          qmtAgent?.status === 'ready'
            ? `${qmtAgent.connectedDevices ?? 0} Ready`
            : (qmtAgent?.status ?? 'Offline'),
        icon: ShieldCheck,
      },
    ];
  }, [health, healthError]);

  const overallStatus: SystemStatus = services.some(s => s.status === 'error')
    ? 'error'
    : services.some(s => s.status === 'warning')
      ? 'warning'
      : 'healthy';

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
      <div className="relative z-10 flex flex-col md:flex-row gap-6 items-start md:items-center">
        {/* Left: Overall Health Banner */}
        <div className="flex items-center gap-4 min-w-[200px]">
          <div
            className={cn(
              'h-14 w-14 rounded-2xl flex items-center justify-center shadow-sm',
              overallStatus === 'healthy' &&
                'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
              overallStatus === 'warning' &&
                'bg-amber-500/10 text-amber-600 dark:text-amber-400',
              overallStatus === 'error' &&
                'bg-red-500/10 text-red-600 dark:text-red-400 animate-pulse'
            )}
          >
            {overallStatus === 'healthy' && (
              <CheckCircle2 className="w-8 h-8" />
            )}
            {overallStatus === 'warning' && (
              <AlertTriangle className="w-8 h-8" />
            )}
            {overallStatus === 'error' && <XCircle className="w-8 h-8" />}
          </div>
          <div>
            <h3 className="font-bold text-lg text-slate-800 dark:text-slate-100">
              {overallStatus === 'healthy'
                ? '系统运行正常'
                : overallStatus === 'warning'
                  ? '存在潜在风险'
                  : '系统异常'}
            </h3>
            <div className="mt-1 flex items-center gap-2 text-xs text-slate-500 font-mono">
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
        </div>

        {/* Vertical Divider */}
        <div className="hidden md:block w-px h-12 bg-slate-200/50 dark:bg-slate-800/50" />

        {/* Middle: Service Grid */}
        <div className="flex-1 grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 w-full">
          {services.map(svc => (
            <div
              key={svc.name}
              className="flex items-center gap-3 p-2 rounded-lg bg-white/50 dark:bg-white/5 border border-slate-100/50 dark:border-white/5"
            >
              <div
                className={cn(
                  'p-1.5 rounded-md',
                  svc.status === 'healthy'
                    ? 'bg-slate-100 dark:bg-slate-800 text-slate-500'
                    : svc.status === 'warning'
                      ? 'bg-amber-50 text-amber-600'
                      : 'bg-red-50 text-red-600'
                )}
              >
                <svc.icon className="w-4 h-4" />
              </div>
              <div>
                <div className="text-xs font-bold text-slate-700 dark:text-slate-300">
                  {svc.name}
                </div>
                <div
                  className={cn(
                    'text-[10px] font-mono',
                    svc.status === 'healthy'
                      ? 'text-slate-400'
                      : svc.status === 'warning'
                        ? 'text-amber-600 font-bold'
                        : 'text-red-600 font-bold'
                  )}
                >
                  {svc.metric}
                </div>
              </div>
            </div>
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
