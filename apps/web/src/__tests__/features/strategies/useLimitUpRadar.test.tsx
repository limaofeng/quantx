import { act, renderHook } from '@testing-library/react';

import { useLimitUpRadar } from '@/features/strategies/hooks/useLimitUpRadar';

const mocks = vi.hoisted(() => ({
  refresh: vi.fn(),
  useQuery: vi.fn(),
}));

vi.mock('urql', () => ({
  useQuery: mocks.useQuery,
}));

describe('useLimitUpRadar', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.useQuery.mockReturnValue([
      {
        data: undefined,
        error: undefined,
        fetching: false,
      },
      mocks.refresh,
    ]);
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('polls every three seconds only while the radar is active and visible', () => {
    const { rerender } = renderHook(
      ({ active }) => useLimitUpRadar(active),
      { initialProps: { active: false } }
    );

    expect(mocks.useQuery).toHaveBeenLastCalledWith(
      expect.objectContaining({ pause: true })
    );
    act(() => vi.advanceTimersByTime(6000));
    expect(mocks.refresh).not.toHaveBeenCalled();

    rerender({ active: true });
    act(() => vi.advanceTimersByTime(3000));
    expect(mocks.refresh).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'hidden',
    });
    act(() => vi.advanceTimersByTime(3000));
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });
});
