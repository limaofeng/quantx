import { createContext, type ReactNode } from 'react';

export interface StudioStatusBarContent {
  left?: ReactNode;
  ownerId: string;
  right?: ReactNode;
}

export interface StudioStatusBarController {
  clearStatusBar: (ownerId: string) => void;
  setStatusBar: (content: StudioStatusBarContent) => void;
  statusBar: StudioStatusBarContent | null;
}

export const StudioStatusBarContext =
  createContext<StudioStatusBarController | null>(null);
