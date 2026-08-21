import { createContext, useContext, type ReactNode } from 'react';

export type AppDialogVariant = 'default' | 'warning' | 'destructive';

interface AppDialogBaseOptions {
  title: string;
  description: ReactNode;
  confirmText?: string;
  variant?: AppDialogVariant;
}

export interface AppConfirmDialogOptions extends AppDialogBaseOptions {
  cancelText?: string;
}

export type AppAlertDialogOptions = AppDialogBaseOptions;

export interface AppPromptDialogOptions extends AppDialogBaseOptions {
  cancelText?: string;
  defaultValue?: string;
  inputLabel: string;
  placeholder?: string;
  validate?: (value: string) => string | null;
}

export interface AppDialogContextValue {
  alert: (options: AppAlertDialogOptions) => Promise<void>;
  confirm: (options: AppConfirmDialogOptions) => Promise<boolean>;
  prompt: (options: AppPromptDialogOptions) => Promise<string | null>;
}

export const AppDialogContext = createContext<AppDialogContextValue | null>(
  null
);

export function useAppDialog(): AppDialogContextValue {
  const context = useContext(AppDialogContext);
  if (!context) {
    throw new Error('useAppDialog must be used within an AppDialogProvider');
  }
  return context;
}
