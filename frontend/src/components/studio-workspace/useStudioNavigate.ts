import { useCallback } from 'react';
import { useLocation } from 'wouter';

import { useStudioWorkspaceContext } from './context';

export function useStudioNavigate() {
  const workspace = useStudioWorkspaceContext();
  const [, setLocation] = useLocation();

  return useCallback(
    (path: string) => {
      if (workspace?.isWorkspaceHosted) {
        workspace.openStudioTab(path);
        return;
      }

      setLocation(path);
    },
    [setLocation, workspace]
  );
}
