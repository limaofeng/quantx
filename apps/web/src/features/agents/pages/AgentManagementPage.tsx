import {
  Cable,
  CheckCircle2,
  Clipboard,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
  Unplug,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuth } from '@/core/auth';
import { gql } from '@/generated/gql';
import type {
  AgentManagement_CreateEnrollmentMutation,
  AgentManagement_DevicesQuery,
  AgentManagement_RevokeMutation,
} from '@/generated/gql/graphql';

const AgentDevicesQuery = gql(`
  query AgentManagement_Devices {
    agentDevices {
      id
      name
      status
      authorizedAccountIds
      capabilities
      lastSeenAt
      revokedAt
      requiresReconciliation
    }
  }
`);

const CreateEnrollmentMutation = gql(`
  mutation AgentManagement_CreateEnrollment(
    $name: String!
    $authorizedAccountIds: [String!]!
  ) {
    createAgentEnrollment(
      name: $name
      authorizedAccountIds: $authorizedAccountIds
    ) {
      enrollmentCode
      expiresAt
    }
  }
`);

const RevokeDeviceMutation = gql(`
  mutation AgentManagement_Revoke($deviceId: String!) {
    revokeAgentDevice(deviceId: $deviceId) {
      success
      message
      deviceId
    }
  }
`);

type AgentDevice = AgentManagement_DevicesQuery['agentDevices'][number];

function statusAppearance(status: string) {
  const normalized = status.toUpperCase();
  if (normalized === 'READY') {
    return {
      label: '在线',
      icon: CheckCircle2,
      className: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
    };
  }
  if (normalized === 'RECONCILING') {
    return {
      label: '对账中',
      icon: RefreshCw,
      className: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    };
  }
  return {
    label: '离线',
    icon: Unplug,
    className: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
  };
}

export function AgentManagementPage() {
  const { user } = useAuth();
  const [name, setName] = useState('本机 QMT Agent');
  const [enrollment, setEnrollment] = useState<{
    code: string;
    expiresAt: string;
  } | null>(null);
  const [query, refresh] = useQuery<AgentManagement_DevicesQuery>({
    query: AgentDevicesQuery,
    requestPolicy: 'cache-and-network',
  });
  const [createResult, createEnrollment] =
    useMutation<AgentManagement_CreateEnrollmentMutation>(
      CreateEnrollmentMutation
    );
  const [revokeResult, revokeDevice] =
    useMutation<AgentManagement_RevokeMutation>(RevokeDeviceMutation);

  const devices = query.data?.agentDevices ?? [];
  const accounts = useMemo(
    () => user?.authorizedAccountIds ?? [],
    [user?.authorizedAccountIds]
  );

  const handleCreate = async () => {
    const result = await createEnrollment({
      name: name.trim() || '本机 QMT Agent',
      authorizedAccountIds: accounts,
    });
    const value = result.data?.createAgentEnrollment;
    if (value) {
      setEnrollment({
        code: value.enrollmentCode,
        expiresAt: value.expiresAt,
      });
    }
  };

  const handleRevoke = async (device: AgentDevice) => {
    if (!window.confirm(`确认撤销设备“${device.name}”？`)) return;
    const result = await revokeDevice({ deviceId: device.id });
    if (result.data?.revokeAgentDevice.success) {
      refresh({ requestPolicy: 'network-only' });
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-[#08101d] p-3 custom-scrollbar">
      <div className="mx-auto max-w-6xl space-y-6">
        <header>
          <p className="text-xs font-medium uppercase tracking-[0.22em] text-sky-400">
            Local execution gateway
          </p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-100">
            QMT Agent 管理
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            Agent 只向 QuantX 建立出站连接。券商账号、QMT
            路径和设备密钥不会保存到服务端。
          </p>
        </header>

        {(query.error || createResult.error || revokeResult.error) && (
          <Alert variant="destructive">
            <ShieldAlert className="h-4 w-4" />
            <AlertDescription>
              {query.error?.message ||
                createResult.error?.message ||
                revokeResult.error?.message}
            </AlertDescription>
          </Alert>
        )}

        <section className="rounded-xl border border-white/10 bg-slate-950/40 p-5">
          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
            <div className="space-y-2">
              <Label htmlFor="agent-name">设备名称</Label>
              <Input
                id="agent-name"
                value={name}
                onChange={event => setName(event.target.value)}
                maxLength={120}
              />
              <p className="text-xs text-slate-500">
                将授权当前用户可访问的账户：{accounts.join('、') || '无'}
              </p>
            </div>
            <Button
              onClick={() => void handleCreate()}
              disabled={createResult.fetching || accounts.length === 0}
            >
              {createResult.fetching ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Cable className="h-4 w-4" />
              )}
              创建登记码
            </Button>
          </div>

          {enrollment && (
            <div className="mt-5 rounded-lg border border-sky-500/25 bg-sky-500/10 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-sky-100">
                    一次性登记码（10 分钟内有效）
                  </p>
                  <code className="mt-2 block break-all text-xs text-sky-300">
                    {enrollment.code}
                  </code>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    void navigator.clipboard.writeText(enrollment.code)
                  }
                >
                  <Clipboard className="h-4 w-4" />
                  复制
                </Button>
              </div>
              <p className="mt-3 text-xs text-slate-400">
                在本机执行：
                <code className="ml-1">
                  quantx-qmt-agent enroll --code &lt;登记码&gt;
                </code>
              </p>
            </div>
          )}
        </section>

        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-medium text-slate-100">已登记设备</h2>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refresh({ requestPolicy: 'network-only' })}
            >
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
          </div>

          {query.fetching && devices.length === 0 ? (
            <div className="flex h-32 items-center justify-center text-slate-500">
              <LoaderCircle className="h-5 w-5 animate-spin" />
            </div>
          ) : devices.length === 0 ? (
            <div className="rounded-xl border border-dashed border-white/10 p-10 text-center text-sm text-slate-500">
              还没有登记 QMT Agent。
            </div>
          ) : (
            <div className="grid gap-3">
              {devices.map(device => {
                const appearance = statusAppearance(device.status);
                const StatusIcon = appearance.icon;
                return (
                  <article
                    key={device.id}
                    className="rounded-xl border border-white/10 bg-slate-950/40 p-5"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div>
                        <div className="flex items-center gap-3">
                          <h3 className="font-medium text-slate-100">
                            {device.name}
                          </h3>
                          <span
                            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${appearance.className}`}
                          >
                            <StatusIcon className="h-3 w-3" />
                            {appearance.label}
                          </span>
                        </div>
                        <p className="mt-2 text-xs text-slate-500">
                          {device.id}
                        </p>
                        <p className="mt-3 text-sm text-slate-400">
                          账户：{device.authorizedAccountIds.join('、') || '无'}
                        </p>
                        {device.requiresReconciliation && (
                          <p className="mt-2 text-sm text-amber-300">
                            设备已连接，正在等待完整快照对账；对账完成前不会下发新命令。
                          </p>
                        )}
                      </div>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => void handleRevoke(device)}
                        disabled={revokeResult.fetching}
                      >
                        撤销
                      </Button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
