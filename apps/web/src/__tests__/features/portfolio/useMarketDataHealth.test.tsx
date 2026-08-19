import { renderHook, waitFor } from '@testing-library/react';

import { useMarketDataHealth } from '@/features/portfolio/pages/t-trade-global/useMarketDataHealth';

describe('useMarketDataHealth', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reports the authoritative Agent-to-Engine watermark independently', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        components: {
          marketData: {
            status: 'ready',
            sequence: 42,
            engineSequence: 42,
            engineAgeSeconds: 0.125,
          },
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result } = renderHook(() => useMarketDataHealth());

    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.engineSequence).toBe(42);
    expect(result.current.engineAgeSeconds).toBe(0.125);
    expect(fetchMock).toHaveBeenCalledWith(
      '/health/components',
      expect.objectContaining({ cache: 'no-store' })
    );
  });

  it('does not mistake a health endpoint failure for a healthy quote stream', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    const { result } = renderHook(() => useMarketDataHealth());

    await waitFor(() => expect(result.current.status).toBe('unavailable'));
  });
});
