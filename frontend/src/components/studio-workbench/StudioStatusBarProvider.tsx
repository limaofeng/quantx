import { useCallback, useContext, useState, type ReactNode } from 'react';

import { StatusBar } from './StatusBar';
import {
  StudioStatusBarContext,
  type StudioStatusBarContent,
} from './StudioStatusBarContext';

export function StudioStatusBarProvider({ children }: { children: ReactNode }) {
  const [statusBar, setStatusBarState] =
    useState<StudioStatusBarContent | null>(null);

  const setStatusBar = useCallback((content: StudioStatusBarContent) => {
    setStatusBarState(content);
  }, []);

  const clearStatusBar = useCallback((ownerId: string) => {
    setStatusBarState(current =>
      current?.ownerId === ownerId ? null : current
    );
  }, []);

  return (
    <StudioStatusBarContext.Provider
      value={{ clearStatusBar, setStatusBar, statusBar }}
    >
      {children}
    </StudioStatusBarContext.Provider>
  );
}

export function StudioStatusBarOutlet() {
  const controller = useContext(StudioStatusBarContext);

  return (
    <StatusBar
      left={controller?.statusBar?.left}
      right={controller?.statusBar?.right}
    />
  );
}
