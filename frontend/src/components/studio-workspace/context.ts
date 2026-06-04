import { createContext, useContext, type ReactNode } from 'react';

import type {
  StudioSidebarSizing,
  StudioThemeName,
} from '@/components/studio-workbench';

export interface StudioWorkspaceSidebarConfig {
  content: ReactNode;
  ownerId: string;
  showSidebar: boolean;
  sizing?: StudioSidebarSizing;
  themeName: StudioThemeName;
  title: string;
}

export interface StudioWorkspaceContextValue {
  activeTabId: string | null;
  clearWorkspaceSidebar: (ownerId: string) => void;
  isWorkspaceHosted: boolean;
  openStudioTab: (path: string) => void;
  setWorkspaceSidebar: (config: StudioWorkspaceSidebarConfig) => void;
  tabBar?: ReactNode;
  updateActiveTab: (patch: { name?: string }) => void;
}

export const StudioWorkspaceContext =
  createContext<StudioWorkspaceContextValue | null>(null);

export function useStudioWorkspaceContext() {
  return useContext(StudioWorkspaceContext);
}
