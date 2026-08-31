/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  applyUiDensity,
  readUiDensity,
  saveUiDensity,
  type UiDensity,
} from '@/core/ui-density';

interface UiDensityContextValue {
  density: UiDensity;
  setDensity: (density: UiDensity) => void;
}

const UiDensityContext = createContext<UiDensityContextValue | undefined>(
  undefined
);

export function UiDensityProvider({ children }: { children: ReactNode }) {
  const [density, setCurrentDensity] = useState(readUiDensity);
  useLayoutEffect(() => applyUiDensity(density), [density]);
  const setDensity = useCallback((nextDensity: UiDensity) => {
    saveUiDensity(nextDensity);
    setCurrentDensity(nextDensity);
  }, []);
  const value = useMemo(() => ({ density, setDensity }), [density, setDensity]);

  return (
    <UiDensityContext.Provider value={value}>
      {children}
    </UiDensityContext.Provider>
  );
}

export function useUiDensity() {
  const context = useContext(UiDensityContext);
  if (!context) throw new Error('useUiDensity requires UiDensityProvider');
  return context;
}
