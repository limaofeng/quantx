import { CircleHelp, Info, OctagonAlert, TriangleAlert } from 'lucide-react';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  AppDialogContext,
  type AppAlertDialogOptions,
  type AppConfirmDialogOptions,
  type AppDialogContextValue,
  type AppDialogVariant,
  type AppPromptDialogOptions,
} from '@/components/ui/app-dialog-context';
import { buttonVariants } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/utils/cn';

type DialogResult = boolean | string | null | undefined;

type DialogRequest =
  | { kind: 'alert'; options: AppAlertDialogOptions }
  | { kind: 'confirm'; options: AppConfirmDialogOptions }
  | { kind: 'prompt'; options: AppPromptDialogOptions };

interface QueuedDialog {
  id: number;
  request: DialogRequest;
  resolve: (result: DialogResult) => void;
}

const iconByVariant = {
  default: CircleHelp,
  destructive: OctagonAlert,
  warning: TriangleAlert,
} satisfies Record<AppDialogVariant, typeof CircleHelp>;

const iconStylesByVariant = {
  default: 'border-cyan-500/20 bg-cyan-500/10 text-cyan-600 dark:text-cyan-300',
  destructive: 'border-red-500/25 bg-red-500/10 text-red-600 dark:text-red-300',
  warning:
    'border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-300',
} satisfies Record<AppDialogVariant, string>;

function cancellationResult(kind: DialogRequest['kind']): DialogResult {
  if (kind === 'confirm') return false;
  if (kind === 'prompt') return null;
  return undefined;
}

export function AppDialogProvider({ children }: { children: ReactNode }) {
  const [activeDialog, setActiveDialog] = useState<QueuedDialog | null>(null);
  const [promptValue, setPromptValue] = useState('');
  const [validationError, setValidationError] = useState<string | null>(null);
  const activeDialogRef = useRef<QueuedDialog | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const nextIdRef = useRef(1);
  const queueRef = useRef<QueuedDialog[]>([]);

  const publishDialog = useCallback((dialog: QueuedDialog | null) => {
    activeDialogRef.current = dialog;
    setActiveDialog(dialog);
    setPromptValue(
      dialog?.request.kind === 'prompt'
        ? dialog.request.options.defaultValue || ''
        : ''
    );
    setValidationError(null);
  }, []);

  const enqueueDialog = useCallback(
    (request: DialogRequest): Promise<DialogResult> =>
      new Promise(resolve => {
        const dialog: QueuedDialog = {
          id: nextIdRef.current++,
          request,
          resolve,
        };
        if (activeDialogRef.current) {
          queueRef.current.push(dialog);
          return;
        }
        publishDialog(dialog);
      }),
    [publishDialog]
  );

  const settleDialog = useCallback(
    (result: DialogResult) => {
      const current = activeDialogRef.current;
      if (!current) return;
      current.resolve(result);
      publishDialog(queueRef.current.shift() || null);
    },
    [publishDialog]
  );

  useEffect(
    () => () => {
      const current = activeDialogRef.current;
      if (current) {
        current.resolve(cancellationResult(current.request.kind));
      }
      queueRef.current.forEach(dialog => {
        dialog.resolve(cancellationResult(dialog.request.kind));
      });
      queueRef.current = [];
      activeDialogRef.current = null;
    },
    []
  );

  const value = useMemo<AppDialogContextValue>(
    () => ({
      alert: async options => {
        await enqueueDialog({ kind: 'alert', options });
      },
      confirm: async options => {
        const result = await enqueueDialog({ kind: 'confirm', options });
        return result === true;
      },
      prompt: async options => {
        const result = await enqueueDialog({ kind: 'prompt', options });
        return typeof result === 'string' ? result : null;
      },
    }),
    [enqueueDialog]
  );

  const request = activeDialog?.request;
  const options = request?.options;
  const variant = options?.variant || 'default';
  const DialogIcon =
    request?.kind === 'alert' && variant === 'default'
      ? Info
      : iconByVariant[variant];

  const submitDialog = () => {
    if (!request) return;
    if (request.kind === 'prompt') {
      const error = request.options.validate?.(promptValue) || null;
      if (error) {
        setValidationError(error);
        inputRef.current?.focus();
        return;
      }
      settleDialog(promptValue);
      return;
    }
    settleDialog(request.kind === 'confirm' ? true : undefined);
  };

  const cancelDialog = () => {
    if (!request) return;
    settleDialog(cancellationResult(request.kind));
  };

  return (
    <AppDialogContext.Provider value={value}>
      {children}
      <AlertDialog
        open={Boolean(activeDialog)}
        onOpenChange={open => {
          if (!open) cancelDialog();
        }}
      >
        {request && options && (
          <AlertDialogContent
            key={activeDialog.id}
            className="w-[calc(100%-2rem)] gap-0 overflow-hidden rounded-dialog border-slate-200 bg-white p-0 text-slate-950 shadow-2xl shadow-black/30 motion-reduce:animate-none dark:border-white/10 dark:bg-slate-950 dark:text-slate-100 sm:max-w-[440px]"
            onOpenAutoFocus={event => {
              if (request.kind !== 'prompt') return;
              event.preventDefault();
              inputRef.current?.focus();
            }}
          >
            <form
              className="grid max-h-[calc(100vh-2rem)] gap-3 overflow-y-auto p-ui-section sm:p-ui-section"
              onSubmit={event => {
                event.preventDefault();
                submitDialog();
              }}
            >
              <AlertDialogHeader className="space-y-4 text-left">
                <div
                  className={cn(
                    'flex h-control-default w-control-default items-center justify-center rounded-control border',
                    iconStylesByVariant[variant]
                  )}
                  aria-hidden="true"
                >
                  <DialogIcon className="h-5 w-5" />
                </div>
                <div className="space-y-2">
                  <AlertDialogTitle className="text-ui-heading font-semibold tracking-tight text-slate-950 dark:text-white">
                    {options.title}
                  </AlertDialogTitle>
                  <AlertDialogDescription asChild>
                    <div className="text-ui-body leading-6 text-slate-600 dark:text-slate-300">
                      {options.description}
                    </div>
                  </AlertDialogDescription>
                </div>
              </AlertDialogHeader>

              {request.kind === 'prompt' && (
                <div className="space-y-2">
                  <label
                    className="text-ui-label font-medium text-slate-700 dark:text-slate-300"
                    htmlFor={`app-dialog-prompt-${activeDialog.id}`}
                  >
                    {request.options.inputLabel}
                  </label>
                  <Input
                    ref={inputRef}
                    id={`app-dialog-prompt-${activeDialog.id}`}
                    value={promptValue}
                    placeholder={request.options.placeholder}
                    aria-invalid={Boolean(validationError)}
                    aria-describedby={
                      validationError
                        ? `app-dialog-error-${activeDialog.id}`
                        : undefined
                    }
                    autoComplete="off"
                    className="h-control-large border-slate-300 bg-slate-50 font-mono text-slate-950 focus-visible:ring-cyan-500 dark:border-white/10 dark:bg-slate-900 dark:text-white"
                    onChange={event => {
                      setPromptValue(event.target.value);
                      if (validationError) setValidationError(null);
                    }}
                  />
                  {validationError && (
                    <p
                      id={`app-dialog-error-${activeDialog.id}`}
                      className="flex items-center gap-2 text-ui-label text-red-600 dark:text-red-300"
                      role="alert"
                    >
                      <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
                      {validationError}
                    </p>
                  )}
                </div>
              )}

              <AlertDialogFooter className="gap-2 border-t border-slate-200 pt-4 dark:border-white/10 sm:gap-2 sm:space-x-0">
                {request.kind !== 'alert' && (
                  <AlertDialogCancel
                    type="button"
                    className="mt-0 cursor-pointer rounded-control border-slate-300 dark:border-white/10 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
                    onClick={event => {
                      event.preventDefault();
                      cancelDialog();
                    }}
                  >
                    {request.options.cancelText || '取消'}
                  </AlertDialogCancel>
                )}
                <AlertDialogAction
                  type="button"
                  className={cn(
                    'cursor-pointer rounded-control',
                    variant === 'destructive' &&
                      buttonVariants({ variant: 'destructive' }),
                    variant === 'warning' &&
                      'bg-amber-500 text-slate-950 hover:bg-amber-400 focus-visible:ring-amber-500'
                  )}
                  onClick={event => {
                    event.preventDefault();
                    submitDialog();
                  }}
                >
                  {options.confirmText ||
                    (request.kind === 'alert' ? '知道了' : '确认')}
                </AlertDialogAction>
              </AlertDialogFooter>
            </form>
          </AlertDialogContent>
        )}
      </AlertDialog>
    </AppDialogContext.Provider>
  );
}
