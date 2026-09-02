import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Router } from 'wouter';
import { memoryLocation } from 'wouter/memory-location';

import ResearchRunsPage from '@/features/research/pages/ResearchRunsPage';

const mocks = vi.hoisted(() => ({
  lifecycle: vi.fn(),
  refresh: vi.fn(),
}));

const originalHasPointerCapture = HTMLElement.prototype.hasPointerCapture;
const originalSetPointerCapture = HTMLElement.prototype.setPointerCapture;
const originalReleasePointerCapture =
  HTMLElement.prototype.releasePointerCapture;
const originalScrollIntoView = Element.prototype.scrollIntoView;

vi.mock('@/features/research/hooks', () => ({
  useResearchLifecycleRuns: mocks.lifecycle,
}));

function renderPage() {
  const location = memoryLocation({ path: '/research/runs' });
  return render(
    <Router hook={location.hook}>
      <ResearchRunsPage />
    </Router>
  );
}

function resetMocks() {
  mocks.refresh.mockReset();
  mocks.lifecycle.mockReset();
  mocks.lifecycle.mockReturnValue({
    error: undefined,
    fetching: false,
    items: [],
    limit: 20,
    offset: 0,
    polling: false,
    refresh: mocks.refresh,
    runs: [],
    total: 45,
  });
}

beforeEach(resetMocks);
beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
    configurable: true,
    value: () => false,
  });
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
    configurable: true,
    value: () => undefined,
  });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
    configurable: true,
    value: () => undefined,
  });
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    value: () => undefined,
  });
});
afterEach(() => {
  cleanup();
  if (originalHasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
      configurable: true,
      value: originalHasPointerCapture,
    });
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, 'hasPointerCapture');
  }
  if (originalSetPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
      configurable: true,
      value: originalSetPointerCapture,
    });
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, 'setPointerCapture');
  }
  if (originalReleasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
      configurable: true,
      value: originalReleasePointerCapture,
    });
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, 'releasePointerCapture');
  }
  if (originalScrollIntoView) {
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: originalScrollIntoView,
    });
  } else {
    Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
  }
});

describe('ResearchRunsPage', () => {
  it('sends all filters to the authoritative hook and resets offset after changes', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.type(
      screen.getByRole('textbox', { name: '搜索运行标识、数据集或版本' }),
      'dataset-v1'
    );
    await user.click(screen.getByRole('combobox', { name: '研究类型' }));
    await user.click(screen.getByRole('option', { name: '次日上涨概率' }));
    await user.click(screen.getByRole('combobox', { name: '生命周期阶段' }));
    await user.click(screen.getByRole('option', { name: 'DEVELOPMENT' }));
    await user.click(screen.getByRole('combobox', { name: '运行状态' }));
    await user.click(screen.getByRole('option', { name: '运行中' }));
    fireEvent.change(screen.getByLabelText('起始日期'), {
      target: { value: '2026-09-01' },
    });
    fireEvent.change(screen.getByLabelText('结束日期'), {
      target: { value: '2026-09-02' },
    });

    await waitFor(() => {
      expect(mocks.lifecycle).toHaveBeenLastCalledWith(
        expect.objectContaining({
          dateFrom: '2026-09-01',
          dateTo: '2026-09-02',
          search: 'dataset-v1',
          stages: ['DEVELOPMENT'],
          statuses: ['RUNNING'],
          studyId: 'next-day-selection',
        }),
        20,
        0
      );
    });
    expect(
      screen.getByRole('textbox', { name: '搜索运行标识、数据集或版本' })
    ).toHaveAttribute('maxLength', '128');
  });

  it('uses server total for pagination and exposes independent refresh errors', async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByText('1–20 / 45 条')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() =>
      expect(mocks.lifecycle).toHaveBeenLastCalledWith(
        expect.anything(),
        20,
        20
      )
    );

    mocks.lifecycle.mockReturnValue({
      error: new Error('index unavailable'),
      fetching: false,
      items: [],
      limit: 20,
      offset: 0,
      polling: false,
      refresh: mocks.refresh,
      runs: [],
      total: 0,
    });
    cleanup();
    renderPage();
    expect(screen.getByRole('alert')).toHaveTextContent('index unavailable');
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });
});
