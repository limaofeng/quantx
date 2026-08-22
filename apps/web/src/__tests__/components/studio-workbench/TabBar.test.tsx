import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

import { TabBar, type StudioTab } from '@/components/studio-workbench';

function buildTabs(count: number): StudioTab[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `tab-${index + 1}`,
    name: `标签 ${index + 1}`,
    type: 'test',
  }));
}

describe('TabBar', () => {
  it('renders semantic tabs and moves focus selection with arrow keys', () => {
    const tabs = buildTabs(3);
    const onTabChange = vi.fn();

    render(
      <TabBar
        activeTabId="tab-1"
        onTabChange={onTabChange}
        onTabClose={vi.fn()}
        tabs={tabs}
        themeColor="red"
      />
    );

    const firstTab = screen.getByRole('tab', { name: '标签 1' });
    const tabBar = screen.getByTestId('studio-tab-bar');
    expect(screen.getByRole('tablist', { name: '工作区标签' })).toBeVisible();
    expect(tabBar).toHaveClass('h-[52px]', 'bg-[#07111f]');
    expect(firstTab.parentElement).toHaveClass('rounded-t-md');
    expect(firstTab).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(firstTab, { key: 'ArrowRight' });
    expect(onTabChange).toHaveBeenCalledWith('tab-2');
  });

  it('exposes a functional workspace create action', () => {
    const onTabCreate = vi.fn();

    render(
      <TabBar
        activeTabId="tab-1"
        createTooltip="打开行情工作台"
        onTabChange={vi.fn()}
        onTabClose={vi.fn()}
        onTabCreate={onTabCreate}
        tabs={buildTabs(1)}
        themeColor="red"
      />
    );

    fireEvent.click(screen.getByRole('button', { name: '打开行情工作台' }));
    expect(onTabCreate).toHaveBeenCalledTimes(1);
  });

  it('uses the desktop-shell tab geometry without changing default tabs', () => {
    render(
      <TabBar
        activeTabId="tab-1"
        onTabChange={vi.fn()}
        onTabClose={vi.fn()}
        tabs={buildTabs(1)}
        themeColor="red"
        variant="workspace"
      />
    );

    const tabBar = screen.getByTestId('studio-tab-bar');
    const tab = screen.getByRole('tab', { name: '标签 1' });
    expect(tabBar).toHaveAttribute('data-variant', 'workspace');
    expect(tabBar).toHaveClass('studio-shell-tabbar', 'bg-transparent');
    expect(tab.parentElement).toHaveClass('h-[44px]', 'rounded-t-[8px]');
    expect(tab.parentElement).toHaveStyle({ width: 'min(13rem, 100%)' });
  });

  it('keeps every tab in a horizontally scrollable strip', () => {
    const tabs = buildTabs(10);

    render(
      <TabBar
        activeTabId="tab-10"
        onTabChange={vi.fn()}
        onTabClose={vi.fn()}
        tabs={tabs}
        themeColor="red"
      />
    );

    const tablist = screen.getByRole('tablist', { name: '工作区标签' });
    expect(screen.getAllByRole('tab')).toHaveLength(10);
    expect(screen.getByRole('tab', { name: '标签 8' })).toBeVisible();
    expect(screen.getByRole('tab', { name: '标签 10' })).toBeVisible();
    expect(tablist).toHaveClass(
      'overflow-x-auto',
      'overscroll-x-contain',
      'no-scrollbar'
    );
    expect(
      screen.queryByTestId('studio-tab-overflow-trigger')
    ).not.toBeInTheDocument();
  });

  it('reveals the active tab again after the workspace is resized', async () => {
    render(
      <TabBar
        activeTabId="tab-3"
        onTabChange={vi.fn()}
        onTabClose={vi.fn()}
        tabs={buildTabs(3)}
        themeColor="red"
        variant="workspace"
      />
    );

    const tabList = screen.getByRole('tablist', { name: '工作区标签' });
    const activeTab = screen.getByRole('tab', {
      name: '标签 3',
    }).parentElement!;
    Object.defineProperty(tabList, 'clientWidth', {
      configurable: true,
      value: 200,
    });
    Object.defineProperty(activeTab, 'offsetLeft', {
      configurable: true,
      value: 450,
    });
    Object.defineProperty(activeTab, 'offsetWidth', {
      configurable: true,
      value: 208,
    });

    fireEvent(window, new Event('resize'));

    await waitFor(() => {
      expect(tabList.scrollLeft).toBe(466);
    });
  });

  it('pins a preview tab on double click', () => {
    const onTabPin = vi.fn();

    render(
      <TabBar
        activeTabId="preview"
        onTabChange={vi.fn()}
        onTabClose={vi.fn()}
        onTabPin={onTabPin}
        tabs={[
          {
            id: 'preview',
            isPreview: true,
            isPreviewable: true,
            name: '个股 601318.SH',
            type: 'test',
          },
        ]}
        themeColor="red"
      />
    );

    fireEvent.doubleClick(
      screen.getByRole('tab', {
        name: '个股 601318.SH',
      })
    );
    expect(onTabPin).toHaveBeenCalledWith('preview', true);
  });
});
