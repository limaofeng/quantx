import { renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import { useStockSelectionTrainingCapabilities } from './useStockSelectionTraining';

const refresh = vi.hoisted(() => vi.fn());
vi.mock('urql', () => ({
  useQuery: () => [{ data: undefined, fetching: false }, refresh],
}));

it('keeps capability refresh stable so unrelated renders do not reset polling', () => {
  const view = renderHook(() => useStockSelectionTrainingCapabilities());
  const initialRefresh = view.result.current.refresh;
  view.rerender();
  expect(view.result.current.refresh).toBe(initialRefresh);
  view.result.current.refresh();
  expect(refresh).toHaveBeenCalledWith({ requestPolicy: 'network-only' });
});
