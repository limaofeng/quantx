import { useCallback, useMemo, useState } from 'react';

import {
  createDefaultMarketIndexPreferences,
  normalizeMarketIndexPreferenceItems,
  preferenceItemsToDefinitions,
  readMarketIndexPreferences,
  saveMarketIndexPreferences,
  type MarketIndexPreferenceItem,
} from '../marketWorkbench';

export type MarketIndexStorageStatus = 'available' | 'unavailable';

export function useMarketIndexPreferences() {
  const [initialState] = useState(() => readMarketIndexPreferences());
  const [items, setItems] = useState<MarketIndexPreferenceItem[]>(
    initialState.items
  );
  const [storageStatus, setStorageStatus] = useState<MarketIndexStorageStatus>(
    initialState.storageAvailable ? 'available' : 'unavailable'
  );

  const visibleItems = useMemo(
    () => items.filter(item => item.visible),
    [items]
  );
  const visibleDefinitions = useMemo(
    () => preferenceItemsToDefinitions(items),
    [items]
  );

  const updateItems = useCallback(
    (nextItems: readonly MarketIndexPreferenceItem[]) => {
      const normalized = normalizeMarketIndexPreferenceItems(nextItems);
      setItems(normalized);
      const persisted = saveMarketIndexPreferences(normalized);
      setStorageStatus(persisted ? 'available' : 'unavailable');
      return persisted;
    },
    []
  );

  const reset = useCallback(() => {
    const defaults = createDefaultMarketIndexPreferences().items;
    return updateItems(defaults);
  }, [updateItems]);

  return {
    items,
    reset,
    storageStatus,
    updateItems,
    visibleDefinitions,
    visibleItems,
  };
}
