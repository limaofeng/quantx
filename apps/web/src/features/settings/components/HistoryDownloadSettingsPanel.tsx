import { useEffect, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAuth } from '@/core/auth';
import { gql } from '@/generated/gql';
import { HistoryDownloadMode } from '@/generated/gql/graphql';

const SettingsQuery = gql(`
  query HistoryDownloadSettings {
    historyDownloadSettings {
      version mode nonTradingDaysAllowed updatedAt windows { start end }
    }
  }
`);

const SettingsMutation = gql(`
  mutation UpdateHistoryDownloadSettings($input: UpdateHistoryDownloadSettingsInput!) {
    updateHistoryDownloadSettings(input: $input) {
      version mode nonTradingDaysAllowed updatedAt windows { start end }
    }
  }
`);

interface Draft {
  version: number;
  mode: HistoryDownloadMode;
  nonTradingDaysAllowed: boolean;
  windows: { start: string; end: string }[];
}

export function HistoryDownloadSettingsPanel() {
  const { user } = useAuth();
  const canWrite = user?.permissions.includes('system-config:write') ?? false;
  const [query, refresh] = useQuery({
    query: SettingsQuery,
    requestPolicy: 'network-only',
  });
  const [mutation, save] = useMutation(SettingsMutation);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (query.data) setDraft(query.data.historyDownloadSettings);
  }, [query.data]);

  if (!draft)
    return (
      <p role="status" className="text-ui-body text-slate-400">
        {query.error?.message || '正在读取补采设置…'}
      </p>
    );
  const custom = draft.mode === HistoryDownloadMode.Custom;
  const saveSettings = async () => {
    setMessage('');
    const result = await save({
      input: {
        expectedVersion: draft.version,
        mode: draft.mode,
        nonTradingDaysAllowed: draft.nonTradingDaysAllowed,
        windows: custom
          ? draft.windows.map(({ start, end }) => ({ start, end }))
          : [],
      },
    });
    if (result.data) {
      setDraft(result.data.updateHistoryDownloadSettings);
      setMessage('已保存，下次补采检查即生效。');
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-ui-section text-ui-body">
      <header className="space-y-2">
        <h1 className="text-ui-page-title font-semibold">行情数据</h1>
        <p className="text-slate-400">
          设置开发端历史数据补采的允许时间。生产行情请求优先，已有且验证通过的数据随时可导出。
        </p>
      </header>
      <fieldset
        disabled={mutation.fetching || !canWrite}
        className="space-y-ui-section rounded-panel border border-white/10 p-ui-section"
      >
        <legend className="px-2 text-ui-title">补采时段（北京时间）</legend>
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="radio"
            name="history-mode"
            checked={!custom}
            onChange={() =>
              setDraft({ ...draft, mode: HistoryDownloadMode.Always })
            }
          />
          全天允许（默认，盘中也可补采）
        </label>
        <label className="flex cursor-pointer items-center gap-2">
          <input
            type="radio"
            name="history-mode"
            checked={custom}
            onChange={() =>
              setDraft({
                ...draft,
                mode: HistoryDownloadMode.Custom,
                windows: draft.windows.length
                  ? draft.windows
                  : [
                      { start: '11:30', end: '13:00' },
                      { start: '16:00', end: '08:30' },
                    ],
              })
            }
          />
          自定义允许时段
        </label>
        {custom && (
          <div className="space-y-3">
            <p className="text-ui-label text-slate-400">
              每日按以下时段补采，包含开始时间、不包含结束时间；最多 12
              段，时段不可重叠。
            </p>
            {draft.windows.map((window, index) => (
              <div key={index} className="flex flex-wrap items-center gap-2">
                <Input
                  type="time"
                  aria-label={`时段 ${index + 1} 开始`}
                  className="w-32"
                  value={window.start}
                  onChange={e =>
                    setDraft({
                      ...draft,
                      windows: draft.windows.map((w, i) =>
                        i === index ? { ...w, start: e.target.value } : w
                      ),
                    })
                  }
                />
                <span className="text-slate-400">
                  至{window.end < window.start ? '次日' : ''}
                </span>
                <Input
                  type="time"
                  aria-label={`时段 ${index + 1} 结束`}
                  className="w-32"
                  value={window.end}
                  onChange={e =>
                    setDraft({
                      ...draft,
                      windows: draft.windows.map((w, i) =>
                        i === index ? { ...w, end: e.target.value } : w
                      ),
                    })
                  }
                />
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`删除时段 ${index + 1}`}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      windows: draft.windows.filter((_, i) => i !== index),
                    })
                  }
                >
                  删除
                </Button>
              </div>
            ))}
            <Button
              variant="outline"
              size="sm"
              disabled={draft.windows.length >= 12}
              onClick={() =>
                setDraft({
                  ...draft,
                  windows: [...draft.windows, { start: '09:00', end: '10:00' }],
                })
              }
            >
              添加时段
            </Button>
            <label className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={draft.nonTradingDaysAllowed}
                onChange={e =>
                  setDraft({
                    ...draft,
                    nonTradingDaysAllowed: e.target.checked,
                  })
                }
              />
              非交易日全天允许
            </label>
            <p className="text-ui-label text-slate-400">
              非交易日按周末和已保存的沪市休市日历判断。日历未知时，仅使用已配置时段。
            </p>
          </div>
        )}
      </fieldset>
      {(query.error || mutation.error) && (
        <p role="alert" className="text-red-400">
          {query.error?.message || mutation.error?.message}
        </p>
      )}
      {message && (
        <p role="status" className="text-sky-300">
          {message}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          onClick={saveSettings}
          disabled={
            !canWrite ||
            mutation.fetching ||
            (custom && draft.windows.length === 0)
          }
        >
          保存设置
        </Button>
        <Button
          variant="outline"
          disabled={query.fetching || mutation.fetching}
          onClick={() => {
            setMessage('');
            refresh({ requestPolicy: 'network-only' });
          }}
        >
          重新读取
        </Button>
      </div>
      <p className="text-ui-label text-slate-500">
        {!canWrite && '当前用户仅可查看配置。'}
        更改不会取消已派发的补采任务。保存后无需重启服务。
      </p>
    </div>
  );
}
