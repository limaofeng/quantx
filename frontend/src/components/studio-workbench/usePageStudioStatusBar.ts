import { useContext, useEffect, useRef, type ReactNode } from 'react';

import { StudioStatusBarContext } from './StudioStatusBarContext';

export function usePageStudioStatusBar({
  enabled,
  left,
  right,
}: {
  enabled: boolean;
  left?: ReactNode;
  right?: ReactNode;
}) {
  const controller = useContext(StudioStatusBarContext);
  const setStatusBar = controller?.setStatusBar;
  const clearStatusBar = controller?.clearStatusBar;
  const ownerIdRef = useRef(
    `studio-status-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2)}`
  );
  const shouldUseGlobalStatusBar = enabled && Boolean(controller);

  useEffect(() => {
    if (!shouldUseGlobalStatusBar || !setStatusBar || !clearStatusBar) return;

    const ownerId = ownerIdRef.current;
    setStatusBar({ left, ownerId, right });

    return () => {
      clearStatusBar(ownerId);
    };
  }, [clearStatusBar, left, right, setStatusBar, shouldUseGlobalStatusBar]);

  return shouldUseGlobalStatusBar;
}
