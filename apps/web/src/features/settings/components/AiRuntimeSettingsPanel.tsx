import {
  Bot,
  CheckCircle2,
  Clock3,
  KeyRound,
  LoaderCircle,
  RefreshCw,
  Save,
  ShieldAlert,
  SlidersHorizontal,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { useAuth } from '@/core/auth';
import { gql } from '@/generated/gql';
import type {
  SystemSettings_AiRuntimeQuery,
  SystemSettings_UpdateAiRuntimeMutation,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

const AiRuntimeSettingsQuery = gql(`
  query SystemSettings_AiRuntime {
    aiRuntimeSettings {
      version
      source
      enabled
      apiKeyConfigured
      model
      maxConcurrentRuns
      maxTurns
      maxToolCalls
      runTimeoutSeconds
      tracingEnabled
      leaseSeconds
      runtimeStatus
      appliedVersion
      applyState
      updatedAt
    }
  }
`);

const UpdateAiRuntimeSettingsMutation = gql(`
  mutation SystemSettings_UpdateAiRuntime(
    $input: UpdateAiRuntimeSettingsInput!
  ) {
    updateAiRuntimeSettings(input: $input) {
      version
      source
      enabled
      apiKeyConfigured
      model
      maxConcurrentRuns
      maxTurns
      maxToolCalls
      runTimeoutSeconds
      tracingEnabled
      leaseSeconds
      runtimeStatus
      appliedVersion
      applyState
      updatedAt
    }
  }
`);

interface RuntimeDraft {
  enabled: boolean;
  model: string;
  maxConcurrentRuns: number;
  maxTurns: number;
  maxToolCalls: number;
  runTimeoutSeconds: number;
}

function toDraft(
  settings: SystemSettings_AiRuntimeQuery['aiRuntimeSettings']
): RuntimeDraft {
  return {
    enabled: settings.enabled,
    model: settings.model,
    maxConcurrentRuns: settings.maxConcurrentRuns,
    maxTurns: settings.maxTurns,
    maxToolCalls: settings.maxToolCalls,
    runTimeoutSeconds: settings.runTimeoutSeconds,
  };
}

function runtimeStatusAppearance(status: string) {
  switch (status) {
    case 'READY':
      return {
        label: '运行就绪',
        className: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
      };
    case 'DISABLED':
      return {
        label: '已停用',
        className: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
      };
    case 'UNCONFIGURED':
      return {
        label: '密钥未配置',
        className: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
      };
    case 'UNAVAILABLE':
      return {
        label: '依赖不可用',
        className: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
      };
    default:
      return {
        label: 'Runtime 离线',
        className: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
      };
  }
}

function NumberSetting({
  disabled,
  id,
  label,
  max,
  min,
  onChange,
  suffix,
  value,
}: {
  disabled: boolean;
  id: string;
  label: string;
  max: number;
  min: number;
  onChange: (value: number) => void;
  suffix: string;
  value: number;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input
          id={id}
          type="number"
          min={min}
          max={max}
          value={value}
          disabled={disabled}
          onChange={event => onChange(Number(event.target.value))}
          className="pr-12"
        />
        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-500">
          {suffix}
        </span>
      </div>
      <p className="text-xs text-slate-500">
        允许范围：{min}–{max}
      </p>
    </div>
  );
}

export function AiRuntimeSettingsPanel() {
  const { user } = useAuth();
  const { toast } = useToast();
  const canEdit = Boolean(user?.permissions.includes('system-config:write'));
  const [dirty, setDirty] = useState(false);
  const [draft, setDraft] = useState<RuntimeDraft | null>(null);
  const [query, refresh] = useQuery<SystemSettings_AiRuntimeQuery>({
    query: AiRuntimeSettingsQuery,
    requestPolicy: 'cache-and-network',
  });
  const [mutation, updateSettings] =
    useMutation<SystemSettings_UpdateAiRuntimeMutation>(
      UpdateAiRuntimeSettingsMutation
    );
  const runtime = query.data?.aiRuntimeSettings;

  useEffect(() => {
    if (runtime && !dirty) setDraft(toDraft(runtime));
  }, [dirty, runtime]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      refresh({ requestPolicy: 'network-only' });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const validationError = useMemo(() => {
    if (!draft) return null;
    const model = draft.model.trim();
    if (!model || model.length > 120) return '模型名称必须为 1 至 120 个字符。';
    if (draft.maxConcurrentRuns < 1 || draft.maxConcurrentRuns > 16)
      return '最大并发必须为 1 至 16。';
    if (draft.maxTurns < 1 || draft.maxTurns > 64)
      return '最大轮次必须为 1 至 64。';
    if (draft.maxToolCalls < 1 || draft.maxToolCalls > 64)
      return '工具调用上限必须为 1 至 64。';
    if (draft.runTimeoutSeconds < 30 || draft.runTimeoutSeconds > 3600)
      return '运行超时必须为 30 至 3600 秒。';
    return null;
  }, [draft]);

  const changeDraft = (patch: Partial<RuntimeDraft>) => {
    setDraft(current => (current ? { ...current, ...patch } : current));
    setDirty(true);
  };

  const handleSave = async () => {
    if (!runtime || !draft || validationError || !canEdit) return;
    if (
      runtime.enabled &&
      !draft.enabled &&
      !window.confirm(
        '停用后将停止接收和领取新的 AI 任务；正在运行的任务会继续完成。确认停用？'
      )
    ) {
      return;
    }
    const result = await updateSettings({
      input: {
        expectedVersion: runtime.version,
        enabled: draft.enabled,
        model: draft.model.trim(),
        maxConcurrentRuns: draft.maxConcurrentRuns,
        maxTurns: draft.maxTurns,
        maxToolCalls: draft.maxToolCalls,
        runTimeoutSeconds: draft.runTimeoutSeconds,
      },
    });
    if (result.error) {
      const code = result.error.graphQLErrors[0]?.extensions?.code;
      if (code === 'AI_RUNTIME_SETTINGS_VERSION_CONFLICT') {
        setDirty(false);
        refresh({ requestPolicy: 'network-only' });
      }
      toast({
        title: 'AI Runtime 配置保存失败',
        description: result.error.message,
        variant: 'destructive',
      });
      return;
    }
    setDirty(false);
    refresh({ requestPolicy: 'network-only' });
    toast({
      title: 'AI Runtime 配置已保存',
      description: 'Runtime 将通过配置通知或轮询应用新版本。',
    });
  };

  if (query.fetching && !runtime) {
    return (
      <div className="flex min-h-64 items-center justify-center text-slate-500">
        <LoaderCircle className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (!runtime || !draft) {
    return (
      <Alert variant="destructive">
        <ShieldAlert className="h-4 w-4" />
        <AlertDescription>
          {query.error?.message || '无法读取 AI Runtime 配置。'}
        </AlertDescription>
      </Alert>
    );
  }

  const appearance = runtimeStatusAppearance(runtime.runtimeStatus);
  const applyState = String(runtime.applyState);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.22em] text-violet-400">
            Model orchestration runtime
          </p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-100">
            AI Runtime
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            PostgreSQL 保存全局非敏感配置；API Key、Tracing
            与租约继续由服务端环境管理。
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => refresh({ requestPolicy: 'network-only' })}
          disabled={query.fetching}
        >
          <RefreshCw
            className={cn('h-4 w-4', query.fetching && 'animate-spin')}
          />
          刷新
        </Button>
      </header>

      {(query.error || mutation.error) && (
        <Alert variant="destructive">
          <ShieldAlert className="h-4 w-4" />
          <AlertDescription>
            {query.error?.message || mutation.error?.message}
          </AlertDescription>
        </Alert>
      )}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-white/10 bg-slate-950/40 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-500">运行状态</span>
            <Bot className="h-4 w-4 text-violet-300" />
          </div>
          <span
            className={cn(
              'mt-3 inline-flex rounded-full border px-2.5 py-1 text-xs',
              appearance.className
            )}
          >
            {appearance.label}
          </span>
        </div>
        <div className="rounded-xl border border-white/10 bg-slate-950/40 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-500">服务端密钥</span>
            <KeyRound className="h-4 w-4 text-sky-300" />
          </div>
          <p className="mt-3 text-sm font-medium text-slate-100">
            {runtime.apiKeyConfigured ? '已配置' : '未配置'}
          </p>
          <p className="mt-1 text-xs text-slate-500">仅返回配置状态</p>
        </div>
        <div className="rounded-xl border border-white/10 bg-slate-950/40 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-500">配置版本</span>
            <SlidersHorizontal className="h-4 w-4 text-cyan-300" />
          </div>
          <p className="mt-3 text-sm font-medium text-slate-100">
            v{runtime.version} / 已应用 {runtime.appliedVersion ?? '—'}
          </p>
          <p
            className={cn(
              'mt-1 text-xs',
              applyState === 'APPLIED' ? 'text-emerald-400' : 'text-amber-300'
            )}
          >
            {applyState === 'APPLIED'
              ? '已生效'
              : applyState === 'PENDING'
                ? '正在应用'
                : '等待 Runtime 上线'}
          </p>
        </div>
        <div className="rounded-xl border border-white/10 bg-slate-950/40 p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-500">配置来源</span>
            <Clock3 className="h-4 w-4 text-slate-300" />
          </div>
          <p className="mt-3 text-sm font-medium text-slate-100">
            {runtime.source === 'DATABASE_OVERRIDE' ? '数据库覆盖' : '部署环境'}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {runtime.updatedAt
              ? new Date(runtime.updatedAt).toLocaleString('zh-CN', {
                  hour12: false,
                })
              : '尚未通过网页保存'}
          </p>
        </div>
      </section>

      <section className="rounded-xl border border-white/10 bg-slate-950/40 p-5">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-white/10 pb-4">
          <div>
            <h2 className="text-base font-medium text-slate-100">运行参数</h2>
            <p className="mt-1 text-xs text-slate-500">
              新任务采用保存后的版本；已存在任务继续使用创建时的快照。
            </p>
          </div>
          <div className="flex items-center gap-3">
            {!canEdit && (
              <span className="text-xs text-amber-300">
                缺少 system-config:write
              </span>
            )}
            <Button
              onClick={() => void handleSave()}
              disabled={
                !canEdit ||
                !dirty ||
                Boolean(validationError) ||
                mutation.fetching
              }
            >
              {mutation.fetching ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              保存配置
            </Button>
          </div>
        </div>

        <div className="mt-5 grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          <div className="space-y-3 rounded-lg border border-white/10 bg-white/[0.025] p-4 md:col-span-2 xl:col-span-3">
            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="ai-runtime-enabled">接受新的 AI 任务</Label>
                <p className="mt-1 text-xs text-slate-500">
                  关闭后不再领取新任务，正在运行的任务不会被强制取消。
                </p>
              </div>
              <Switch
                id="ai-runtime-enabled"
                checked={draft.enabled}
                disabled={!canEdit}
                onCheckedChange={enabled => changeDraft({ enabled })}
              />
            </div>
          </div>

          <div className="space-y-2 md:col-span-2 xl:col-span-3">
            <Label htmlFor="ai-runtime-model">OpenAI 模型</Label>
            <Input
              id="ai-runtime-model"
              value={draft.model}
              maxLength={120}
              disabled={!canEdit}
              onChange={event => changeDraft({ model: event.target.value })}
              placeholder="gpt-5.6"
            />
          </div>

          <NumberSetting
            id="ai-runtime-concurrency"
            label="最大并发"
            min={1}
            max={16}
            suffix="任务"
            value={draft.maxConcurrentRuns}
            disabled={!canEdit}
            onChange={maxConcurrentRuns => changeDraft({ maxConcurrentRuns })}
          />
          <NumberSetting
            id="ai-runtime-turns"
            label="最大轮次"
            min={1}
            max={64}
            suffix="轮"
            value={draft.maxTurns}
            disabled={!canEdit}
            onChange={maxTurns => changeDraft({ maxTurns })}
          />
          <NumberSetting
            id="ai-runtime-tools"
            label="工具调用上限"
            min={1}
            max={64}
            suffix="次"
            value={draft.maxToolCalls}
            disabled={!canEdit}
            onChange={maxToolCalls => changeDraft({ maxToolCalls })}
          />
          <NumberSetting
            id="ai-runtime-timeout"
            label="运行超时"
            min={30}
            max={3600}
            suffix="秒"
            value={draft.runTimeoutSeconds}
            disabled={!canEdit}
            onChange={runTimeoutSeconds => changeDraft({ runTimeoutSeconds })}
          />
        </div>

        {validationError && (
          <p className="mt-4 text-sm text-rose-300">{validationError}</p>
        )}
      </section>

      <section className="rounded-xl border border-white/10 bg-slate-950/40 p-5">
        <div className="flex items-center gap-2 text-slate-100">
          <CheckCircle2 className="h-4 w-4 text-emerald-300" />
          <h2 className="text-base font-medium">服务端只读参数</h2>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg border border-white/10 bg-white/[0.025] p-4">
            <p className="text-xs text-slate-500">Tracing</p>
            <p className="mt-2 text-sm text-slate-200">
              {runtime.tracingEnabled ? '已启用' : '已关闭'}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              仅允许通过服务端环境变量配置，避免研究上下文意外外发。
            </p>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/[0.025] p-4">
            <p className="text-xs text-slate-500">任务租约</p>
            <p className="mt-2 text-sm text-slate-200">
              {runtime.leaseSeconds} 秒
            </p>
            <p className="mt-1 text-xs text-slate-500">
              由部署配置控制，用于多实例任务领取与恢复。
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
