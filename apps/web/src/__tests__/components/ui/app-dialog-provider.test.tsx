import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { useAppDialog } from '@/components/ui/app-dialog-context';
import { AppDialogProvider } from '@/components/ui/app-dialog-provider';

function DialogHarness() {
  const dialogs = useAppDialog();
  const [result, setResult] = useState('等待操作');

  return (
    <>
      <button
        type="button"
        onClick={() => {
          void dialogs
            .confirm({
              title: '确认危险操作',
              description: '这个操作会改变交易状态。',
              confirmText: '执行操作',
              cancelText: '返回检查',
              variant: 'destructive',
            })
            .then(confirmed => setResult(confirmed ? '已确认' : '已取消'));
        }}
      >
        打开确认框
      </button>
      <button
        type="button"
        onClick={() => {
          void dialogs
            .prompt({
              title: '输入精确确认',
              description: '输入指定短语后才能继续。',
              inputLabel: '确认短语：LIVE:demo',
              confirmText: '验证并继续',
              validate: value =>
                value === 'LIVE:demo' ? null : '确认短语不匹配',
            })
            .then(value => setResult(value || '已取消'));
        }}
      >
        打开输入框
      </button>
      <button
        type="button"
        onClick={() => {
          void dialogs.alert({
            title: '第一条通知',
            description: '队列中的第一条通知。',
          });
          void dialogs.alert({
            title: '第二条通知',
            description: '队列中的第二条通知。',
          });
        }}
      >
        打开通知队列
      </button>
      <output>{result}</output>
    </>
  );
}

function renderHarness() {
  return render(
    <AppDialogProvider>
      <DialogHarness />
    </AppDialogProvider>
  );
}

describe('AppDialogProvider', () => {
  it('returns the confirmation result and closes the div dialog', async () => {
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByRole('button', { name: '打开确认框' }));

    expect(
      screen.getByRole('alertdialog', { name: '确认危险操作' })
    ).toBeInTheDocument();
    expect(screen.getByText('这个操作会改变交易状态。')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '执行操作' }));

    expect(screen.getByText('已确认')).toBeInTheDocument();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('keeps an exact-confirmation prompt open until validation passes', async () => {
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByRole('button', { name: '打开输入框' }));
    const input = screen.getByLabelText('确认短语：LIVE:demo');
    expect(input).toHaveFocus();

    await user.type(input, 'wrong');
    await user.click(screen.getByRole('button', { name: '验证并继续' }));
    expect(screen.getByRole('alert')).toHaveTextContent('确认短语不匹配');
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, 'LIVE:demo');
    await user.click(screen.getByRole('button', { name: '验证并继续' }));

    expect(screen.getByText('LIVE:demo')).toBeInTheDocument();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('presents concurrent requests in order instead of replacing one', async () => {
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByRole('button', { name: '打开通知队列' }));
    expect(
      screen.getByRole('alertdialog', { name: '第一条通知' })
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '知道了' }));
    expect(
      screen.getByRole('alertdialog', { name: '第二条通知' })
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '知道了' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });
});
