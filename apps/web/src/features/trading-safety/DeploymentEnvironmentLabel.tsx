import { useEffect, useState } from 'react';

export function DeploymentEnvironmentLabel() {
  const [label, setLabel] = useState('环境信息未确认');
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () =>
      void fetch('/runtime/environment', {
        signal: controller.signal,
        cache: 'no-store',
      })
        .then(async response => {
          if (!response.ok) throw new Error('Environment unavailable');
          const value: unknown = await response.json();
          if (!value || typeof value !== 'object')
            throw new Error('Invalid environment');
          if (
            !('environment' in value) ||
            !('mode' in value) ||
            !('marketSource' in value)
          )
            throw new Error('Invalid environment');
          if (
            !['production', 'development', 'testing'].includes(
              String(value.environment)
            ) ||
            !['live', 'paper', 'data-only'].includes(String(value.mode)) ||
            !['remote', 'qmt', 'replay'].includes(String(value.marketSource))
          )
            throw new Error('Invalid environment');
          const environment =
            value.environment === 'production'
              ? '生产'
              : value.environment === 'development'
                ? '开发'
                : '测试';
          const mode =
            value.mode === 'live'
              ? '真实交易'
              : value.mode === 'paper'
                ? '模拟交易'
                : '仅数据';
          const source =
            value.marketSource === 'remote'
              ? '远程行情'
              : value.marketSource === 'qmt'
                ? 'QMT 行情'
                : '历史回放';
          setLabel(`${environment}／${mode} · ${source}`);
        })
        .catch(() => {
          if (!controller.signal.aborted) setLabel('环境信息未确认');
        });
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);
  return (
    <span className="whitespace-nowrap text-ui-caption text-slate-300">
      {label}
    </span>
  );
}
