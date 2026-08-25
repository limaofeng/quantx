import { AlertCircle, Lock, RotateCcw, Save, Settings2 } from 'lucide-react';
import {
  type Dispatch,
  type SetStateAction,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { useMutation } from 'urql';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { StrategyRunMode, type Strategy } from '@/generated/gql/graphql';

import {
  isEditableInstance,
  type StrategyInstance,
  type StrategyJsonValue,
} from '../domain';
import { UpdateStrategyInstanceParametersMutation } from '../hooks/strategyInstanceOperations';
import { type StrategyConfigValue } from '../hooks/types';

import { getCustomStrategyConfigPanel } from './strategyRegistry';

interface StrategyConfigTabProps {
  strategyId: string;
  strategy?: Pick<Strategy, 'name'> | null;
  instance?: StrategyInstance | null;
  runId?: string;
  currentParameters?: Record<string, StrategyJsonValue>;
  defaultParameters?: Record<string, StrategyJsonValue>;
  runMode?: StrategyRunMode;
}

function normalizeParameters(value?: Record<string, StrategyJsonValue>) {
  return value || {};
}

function formatParameterValue(value: StrategyJsonValue) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}

function getInstrumentCode(
  instance?: StrategyInstance | null,
  parameters?: Record<string, StrategyJsonValue>
) {
  const value =
    instance?.instrumentCode ||
    parameters?.instrument_code ||
    parameters?.instrumentCode ||
    parameters?.symbol ||
    (Array.isArray(parameters?.stockCodes) ? parameters.stockCodes[0] : '');
  return value ? String(value) : '';
}

export default function StrategyConfigTab({
  strategyId: _strategyId,
  strategy,
  instance,
  runId,
  currentParameters,
  defaultParameters,
  runMode,
}: StrategyConfigTabProps) {
  const initialConfig = useMemo(
    () => normalizeParameters(currentParameters || defaultParameters),
    [currentParameters, defaultParameters]
  );
  const [config, setConfig] = useState(initialConfig);
  const [stockCodes, setStockCodes] = useState(
    getInstrumentCode(instance, initialConfig)
  );
  const [hasChanges, setHasChanges] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [, updateStrategyInstanceParameters] = useMutation(
    UpdateStrategyInstanceParametersMutation
  );
  const editable = isEditableInstance(instance);

  useEffect(() => {
    setConfig(initialConfig);
    setStockCodes(getInstrumentCode(instance, initialConfig));
    setHasChanges(false);
    setSaveError(null);
  }, [initialConfig, instance]);

  const handleChange = (key: string, value: StrategyJsonValue) => {
    setConfig(prev => ({ ...prev, [key]: value }));
    setHasChanges(true);
  };

  const handleSave = async () => {
    if (!runId || !editable) return;
    const result = await updateStrategyInstanceParameters({
      instanceId: runId,
      input: {
        parameters: config,
        applyImmediately: true,
      },
    });
    if (result.error) {
      setSaveError(result.error.message);
      return;
    }
    setSaveError(null);
    setHasChanges(false);
  };

  const handleCustomConfigChange: Dispatch<
    SetStateAction<Record<string, StrategyConfigValue>>
  > = updater => {
    setConfig(prev => {
      const previousConfig = prev as Record<string, StrategyConfigValue>;
      const nextConfig =
        typeof updater === 'function' ? updater(previousConfig) : updater;
      if (JSON.stringify(previousConfig) !== JSON.stringify(nextConfig)) {
        setHasChanges(true);
      }
      return nextConfig as Record<string, StrategyJsonValue>;
    });
  };

  const handleReset = () => {
    setConfig(normalizeParameters(defaultParameters || currentParameters));
    setStockCodes(
      getInstrumentCode(
        instance,
        normalizeParameters(defaultParameters || currentParameters)
      )
    );
    setHasChanges(false);
    setSaveError(null);
  };

  const entries = Object.entries(config).filter(
    ([key]) => !key.startsWith('_')
  );
  const CustomConfigPanel = strategy
    ? getCustomStrategyConfigPanel(strategy.name)
    : null;
  const resolvedRunMode =
    runMode ||
    (instance?.mode as StrategyRunMode | undefined) ||
    StrategyRunMode.Paper;

  return (
    <div className="space-y-ui-section pb-12">
      <Card className="rounded-panel border border-slate-200 bg-white p-ui-panel shadow-none dark:border-white/10 dark:bg-slate-900/60">
        <div className="flex flex-col gap-ui-section md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-ui-micro font-black uppercase tracking-[0.3em] text-blue-500">
              参数配置
            </div>
            <h3 className="mt-1 text-ui-heading font-black text-slate-900 dark:text-white">
              {instance?.displayName || '未选择策略实例'}
            </h3>
          </div>
          <div className="grid grid-cols-1 gap-3 text-ui-caption font-bold text-slate-500 sm:grid-cols-3">
            <div className="rounded-panel border border-slate-200 px-3 py-2 dark:border-white/10">
              <div className="text-ui-micro font-black uppercase tracking-widest text-slate-400">
                绑定标的
              </div>
              <div className="mt-1 font-mono text-slate-700 dark:text-slate-200">
                {instance?.instrumentCode || '--'}
              </div>
            </div>
            <div className="rounded-panel border border-slate-200 px-3 py-2 dark:border-white/10">
              <div className="text-ui-micro font-black uppercase tracking-widest text-slate-400">
                参数版本
              </div>
              <div className="mt-1 truncate font-mono text-slate-700 dark:text-slate-200">
                {instance?.parameterVersion || '--'}
              </div>
            </div>
            <div className="rounded-panel border border-slate-200 px-3 py-2 dark:border-white/10">
              <div className="text-ui-micro font-black uppercase tracking-widest text-slate-400">
                编辑状态
              </div>
              <div
                className={
                  editable ? 'mt-1 text-emerald-500' : 'mt-1 text-amber-500'
                }
              >
                {editable ? '可更新' : '运行中请先暂停'}
              </div>
            </div>
          </div>
        </div>

        {!editable && (
          <div className="mt-5 flex items-start gap-2 rounded-panel border border-amber-500/20 bg-amber-500/10 px-ui-section py-3 text-ui-label font-medium leading-relaxed text-amber-600 dark:text-amber-300">
            <Lock className="mt-0.5 h-4 w-4 shrink-0" />
            运行中实例的参数不会直接热更新；请暂停实例后再应用更改，避免回测和实盘产生不同策略逻辑。
          </div>
        )}

        {saveError && (
          <div className="mt-5 flex items-start gap-2 rounded-panel border border-rose-500/20 bg-rose-500/10 px-ui-section py-3 text-ui-label font-medium leading-relaxed text-rose-500">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            {saveError}
          </div>
        )}
      </Card>

      {CustomConfigPanel ? (
        <CustomConfigPanel
          strategy={strategy as Strategy}
          strategyName={instance?.displayName || strategy?.name || ''}
          setStrategyName={() => {}}
          stockCodes={stockCodes}
          setStockCodes={setStockCodes}
          strategyConfig={config as Record<string, StrategyConfigValue>}
          setStrategyConfig={handleCustomConfigChange}
          runMode={resolvedRunMode}
          setRunMode={() => {}}
          onSave={handleSave}
          saveLabel="应用更改"
          saveDisabled={!hasChanges || !editable || !runId}
          showSubmit={false}
        />
      ) : (
        <>
          <Card className="overflow-hidden rounded-panel border border-slate-200 bg-white shadow-none dark:border-white/10 dark:bg-slate-900/60">
            <div className="border-b border-slate-100 px-ui-panel py-ui-section dark:border-white/5">
              <div className="flex items-center gap-3">
                <Settings2 className="h-4 w-4 text-blue-500" />
                <h3 className="text-ui-caption font-black uppercase tracking-[0.24em] text-slate-700 dark:text-slate-200">
                  策略参数
                </h3>
              </div>
            </div>

            {entries.length === 0 ? (
              <div className="p-ui-empty text-center text-ui-label font-medium text-slate-500">
                后端暂未返回参数配置。
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-ui-section p-ui-panel md:grid-cols-2">
                {entries.map(([key, value]) => {
                  const isObject = value !== null && typeof value === 'object';
                  return (
                    <div key={key} className={isObject ? 'md:col-span-2' : ''}>
                      <Label
                        htmlFor={key}
                        className="mb-2 block text-ui-micro font-black uppercase tracking-[0.2em] text-slate-400"
                      >
                        {key}
                      </Label>
                      {typeof value === 'boolean' ? (
                        <div className="flex h-10 items-center justify-between rounded-panel border border-slate-200 bg-slate-50 px-ui-section dark:border-white/10 dark:bg-white/[0.03]">
                          <span className="text-ui-caption font-bold text-slate-600 dark:text-slate-300">
                            {value ? '开启' : '关闭'}
                          </span>
                          <Switch
                            checked={value}
                            disabled={!editable}
                            onCheckedChange={checked =>
                              handleChange(key, checked)
                            }
                          />
                        </div>
                      ) : isObject ? (
                        <pre className="max-h-56 overflow-auto rounded-panel border border-slate-200 bg-slate-50 p-ui-section text-ui-caption font-medium leading-relaxed text-slate-500 dark:border-white/10 dark:bg-white/[0.03]">
                          {formatParameterValue(value)}
                        </pre>
                      ) : (
                        <Input
                          id={key}
                          type={typeof value === 'number' ? 'number' : 'text'}
                          value={formatParameterValue(value)}
                          disabled={!editable}
                          onChange={event => {
                            const nextValue =
                              typeof value === 'number'
                                ? Number(event.target.value)
                                : event.target.value;
                            handleChange(key, nextValue);
                          }}
                          className="h-10 rounded-panel border-slate-200 bg-slate-50 text-ui-caption font-bold dark:border-white/10 dark:bg-white/[0.03]"
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </Card>

          <Card className="flex flex-col gap-ui-section rounded-panel border border-slate-200 bg-white p-ui-section shadow-none dark:border-white/10 dark:bg-slate-900/60 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="text-ui-micro font-black uppercase tracking-[0.24em] text-slate-400">
                参数状态
              </div>
              <p className="mt-1 text-ui-label font-medium text-slate-500">
                {hasChanges ? '存在未应用的参数修改。' : '当前页面参数已同步。'}
              </p>
            </div>
            <div className="flex gap-3">
              <Button
                variant="ghost"
                className="rounded-panel text-ui-caption font-black uppercase tracking-widest"
                onClick={handleReset}
              >
                <RotateCcw className="mr-2 h-4 w-4" />
                重置
              </Button>
              <Button
                className="rounded-panel bg-blue-600 px-ui-panel text-ui-caption font-black uppercase tracking-widest text-white hover:bg-blue-700"
                onClick={handleSave}
                disabled={!hasChanges || !editable || !runId}
              >
                <Save className="mr-2 h-4 w-4" />
                应用更改
              </Button>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
