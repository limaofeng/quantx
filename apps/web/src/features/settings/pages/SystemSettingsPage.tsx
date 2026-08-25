import {
  Bot,
  Cable,
  ChevronRight,
  Database,
  Gauge,
  Settings,
  ShieldCheck,
  type LucideIcon,
} from 'lucide-react';
import { useLocation } from 'wouter';

import { useStudioNavigate } from '@/components/studio-workspace';
import {
  StudioPageFrame,
  StudioPageStack,
} from '@/components/ui/studio-layout';
import { AgentManagementPanel } from '@/features/agents';
import { SystemInsightCard } from '@/features/system/components/SystemInsightCard';
import { cn } from '@/utils/cn';

import { AiRuntimeSettingsPanel } from '../components/AiRuntimeSettingsPanel';
import { TradingSafetySettingsPanel } from '../components/TradingSafetySettingsPanel';

type SettingsSection = 'overview' | 'trading-safety' | 'qmt' | 'ai-runtime';

const navigation: Array<{
  id: SettingsSection;
  label: string;
  description: string;
  href: string;
  icon: LucideIcon;
}> = [
  {
    id: 'overview',
    label: '系统概览',
    description: '服务健康与快捷入口',
    href: '/settings',
    icon: Gauge,
  },
  {
    id: 'trading-safety',
    label: '交易安全',
    description: '账户授权、对账与紧急停止',
    href: '/settings/trading-safety',
    icon: ShieldCheck,
  },
  {
    id: 'qmt',
    label: 'QMT 连接',
    description: '本机执行链路与诊断',
    href: '/settings/qmt',
    icon: Cable,
  },
  {
    id: 'ai-runtime',
    label: 'AI Runtime',
    description: '模型运行与安全限制',
    href: '/settings/ai-runtime',
    icon: Bot,
  },
];

function activeSection(path: string): SettingsSection {
  if (path.startsWith('/settings/trading-safety')) return 'trading-safety';
  if (path.startsWith('/settings/qmt')) return 'qmt';
  if (path.startsWith('/settings/ai-runtime')) return 'ai-runtime';
  return 'overview';
}

function SettingsOverview({
  onNavigate,
}: {
  onNavigate: (path: string) => void;
}) {
  const cards = [
    {
      title: '账户交易安全',
      description: '独立管理账户级实盘授权、对账窗口和紧急停止。',
      href: '/settings/trading-safety',
      icon: ShieldCheck,
      accent: 'text-sky-300 bg-sky-500/10 border-sky-500/20',
    },
    {
      title: 'QMT 本机连接',
      description: '查看 MiniQMT 连接链路、行情指标与安全交接状态。',
      href: '/settings/qmt',
      icon: Cable,
      accent: 'text-cyan-300 bg-cyan-500/10 border-cyan-500/20',
    },
    {
      title: 'AI Runtime',
      description: '查看服务端密钥状态，动态调整模型、并发与运行限制。',
      href: '/settings/ai-runtime',
      icon: Bot,
      accent: 'text-violet-300 bg-violet-500/10 border-violet-500/20',
    },
    {
      title: '数据管理',
      description: '进入独立数据工作台，管理行情、财务、公告和同步任务。',
      href: '/settings/data',
      icon: Database,
      accent: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/20',
    },
  ];

  return (
    <StudioPageStack className="space-y-ui-section">
      <header>
        <p className="text-ui-label font-medium uppercase tracking-[0.22em] text-sky-400">
          Platform control plane
        </p>
        <h1 className="mt-2 text-ui-page-title font-semibold text-slate-100">
          系统概览
        </h1>
        <p className="mt-2 max-w-3xl text-ui-body leading-6 text-slate-400">
          集中查看 QuantX 服务健康、执行网关和 AI
          Runtime；数据同步继续在独立工作台管理。
        </p>
      </header>

      <SystemInsightCard />

      <section className="grid gap-3 md:grid-cols-3">
        {cards.map(card => {
          const Icon = card.icon;
          return (
            <button
              key={card.href}
              type="button"
              onClick={() => onNavigate(card.href)}
              className="group cursor-pointer rounded-panel border border-white/10 bg-slate-950/40 p-ui-section text-left transition-colors duration-200 hover:border-white/20 hover:bg-slate-900/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/70"
            >
              <div className="flex items-start justify-between gap-ui-section">
                <span
                  className={cn(
                    'flex h-10 w-10 items-center justify-center rounded-lg border',
                    card.accent
                  )}
                >
                  <Icon className="h-5 w-5" />
                </span>
                <ChevronRight className="h-4 w-4 text-slate-600 transition-colors group-hover:text-slate-300" />
              </div>
              <h2 className="mt-4 text-ui-body font-medium text-slate-100">
                {card.title}
              </h2>
              <p className="mt-2 text-ui-label leading-5 text-slate-500">
                {card.description}
              </p>
            </button>
          );
        })}
      </section>
    </StudioPageStack>
  );
}

export function SystemSettingsPage() {
  const [location] = useLocation();
  const navigate = useStudioNavigate();
  const section = activeSection(location);

  return (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col text-slate-100 md:flex-row">
      <aside className="hidden w-60 shrink-0 border-r border-white/5 bg-[#0b1120] p-ui-section md:block">
        <div className="mb-6 flex items-center gap-3 px-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-sky-500/20 bg-sky-500/10 text-sky-300">
            <Settings className="h-5 w-5" />
          </span>
          <div>
            <p className="text-ui-body font-semibold">系统设置</p>
            <p className="text-ui-caption text-slate-500">System settings</p>
          </div>
        </div>
        <nav aria-label="系统设置导航" className="space-y-1">
          {navigation.map(item => {
            const Icon = item.icon;
            const active = item.id === section;
            return (
              <button
                key={item.id}
                type="button"
                aria-current={active ? 'page' : undefined}
                onClick={() => navigate(item.href)}
                className={cn(
                  'flex w-full cursor-pointer items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/70',
                  active
                    ? 'bg-sky-500/10 text-sky-200'
                    : 'text-slate-400 hover:bg-white/5 hover:text-slate-100'
                )}
              >
                <Icon className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  <span className="block text-ui-body font-medium">
                    {item.label}
                  </span>
                  <span className="mt-0.5 block text-ui-caption text-slate-500">
                    {item.description}
                  </span>
                </span>
              </button>
            );
          })}
        </nav>
      </aside>

      <nav
        aria-label="系统设置导航"
        className="no-scrollbar flex shrink-0 gap-1 overflow-x-auto border-b border-white/5 bg-[#0b1120] p-2 md:hidden"
      >
        {navigation.map(item => {
          const Icon = item.icon;
          const active = item.id === section;
          return (
            <button
              key={item.id}
              type="button"
              aria-current={active ? 'page' : undefined}
              onClick={() => navigate(item.href)}
              className={cn(
                'flex shrink-0 cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-ui-label font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/70',
                active
                  ? 'bg-sky-500/10 text-sky-200'
                  : 'text-slate-400 hover:bg-white/5 hover:text-slate-100'
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </button>
          );
        })}
      </nav>

      <main className="min-h-0 flex-1 overflow-hidden">
        <StudioPageFrame>
          {section === 'overview' && <SettingsOverview onNavigate={navigate} />}
          {section === 'trading-safety' && <TradingSafetySettingsPanel />}
          {section === 'qmt' && <AgentManagementPanel />}
          {section === 'ai-runtime' && <AiRuntimeSettingsPanel />}
        </StudioPageFrame>
      </main>
    </div>
  );
}
