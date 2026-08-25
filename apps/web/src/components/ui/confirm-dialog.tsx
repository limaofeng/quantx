'use client';

import * as React from 'react';

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
import { buttonVariants } from '@/components/ui/button';
import { cn } from '@/utils/cn';

export interface ConfirmDialogProps {
  /** 是否显示对话框 */
  open: boolean;
  /** 关闭对话框回调 */
  onOpenChange: (open: boolean) => void;
  /** 对话框标题 */
  title: string;
  /** 对话框描述内容 */
  description: React.ReactNode;
  /** 确认按钮文字，默认 "确定" */
  confirmText?: string;
  /** 加载中按钮文字，默认 "处理中..." */
  loadingText?: string;
  /** 取消按钮文字，默认 "取消" */
  cancelText?: string;
  /** 确认回调 */
  onConfirm: () => void | Promise<void>;
  /** 取消回调 */
  onCancel?: () => void;
  /** 确认按钮变体：destructive 为红色删除样式 */
  variant?: 'default' | 'destructive';
  /** 加载状态 */
  loading?: boolean;
}

/**
 * 通用确认对话框组件
 *
 * 用于替代浏览器原生的 confirm 对话框，提供更好的用户体验和样式一致性。
 *
 * @example
 * ```tsx
 * const [open, setOpen] = useState(false);
 *
 * <ConfirmDialog
 *   open={open}
 *   onOpenChange={setOpen}
 *   title="确认删除"
 *   description="删除后无法恢复，确定要继续吗？"
 *   variant="destructive"
 *   onConfirm={async () => {
 *     await deleteItem();
 *     setOpen(false);
 *   }}
 * />
 * ```
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmText = '确定',
  loadingText = '处理中...',
  cancelText = '取消',
  onConfirm,
  onCancel,
  variant = 'default',
  loading = false,
}: ConfirmDialogProps) {
  const [isLoading, setIsLoading] = React.useState(false);
  const processingRef = React.useRef(false);
  const isProcessing = loading || isLoading;

  const handleConfirm = async () => {
    if (processingRef.current || loading) return;
    processingRef.current = true;
    setIsLoading(true);
    try {
      await onConfirm();
    } finally {
      processingRef.current = false;
      setIsLoading(false);
    }
  };

  const handleCancel = () => {
    if (isProcessing) return;
    onCancel?.();
    onOpenChange(false);
  };

  const handleOpenChange = (nextOpen: boolean) => {
    if (isProcessing && !nextOpen) return;
    onOpenChange(nextOpen);
  };

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent className="sm:max-w-[425px] border-slate-200 dark:border-white/10 bg-white dark:bg-slate-900 rounded-dialog shadow-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-ui-heading font-bold text-slate-900 dark:text-white">
            {title}
          </AlertDialogTitle>
          <AlertDialogDescription
            asChild
            className="text-ui-body text-slate-500 dark:text-slate-400"
          >
            <div>{description}</div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="gap-2 sm:gap-2">
          <AlertDialogCancel
            onClick={handleCancel}
            disabled={isProcessing}
            className="rounded-control border-slate-200 dark:border-white/10 hover:bg-slate-100 dark:hover:bg-white/5"
          >
            {cancelText}
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={event => {
              event.preventDefault();
              void handleConfirm();
            }}
            disabled={isProcessing}
            className={cn(
              'rounded-control',
              variant === 'destructive' &&
                buttonVariants({ variant: 'destructive' }),
              isProcessing && 'opacity-50 cursor-not-allowed'
            )}
          >
            {isProcessing ? (
              <span className="flex items-center gap-2">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                {loadingText}
              </span>
            ) : (
              confirmText
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export default ConfirmDialog;
