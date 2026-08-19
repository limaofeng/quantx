import * as React from 'react';

import { API_ENDPOINTS } from '@/shared/constants/api';

export type MarketDataHealth = {
  status: string;
  connectedDevices?: number;
  protocol?: string;
  sequence?: number;
  engineSequence?: number;
  instrumentCount?: number;
  streamAgeSeconds?: number | null;
  engineAgeSeconds?: number | null;
  tradingSession?: boolean;
};

type HealthResponse = {
  components?: {
    marketData?: MarketDataHealth;
  };
};

const CHECK_INTERVAL_MS = 3000;

export function useMarketDataHealth() {
  const [health, setHealth] = React.useState<MarketDataHealth>({
    status: 'checking',
  });

  React.useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let controller: AbortController | undefined;

    const load = async () => {
      controller = new AbortController();
      try {
        const response = await fetch(`${API_ENDPOINTS.HEALTH}/components`, {
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Health check failed: ${response.status}`);
        }
        const payload = (await response.json()) as HealthResponse;
        if (!cancelled) {
          setHealth(payload.components?.marketData ?? { status: 'offline' });
        }
      } catch (error) {
        if (
          !cancelled &&
          !(error instanceof DOMException && error.name === 'AbortError')
        ) {
          setHealth({ status: 'unavailable' });
        }
      } finally {
        if (!cancelled) {
          timer = window.setTimeout(load, CHECK_INTERVAL_MS);
        }
      }
    };

    void load();
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  return health;
}
