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
    expect(tabBar).toHaveClass('bg-[#07111f]');
    expect(tabBar).toHaveStyle({ height: '52px' });
    expect(firstTab.parentElement).toHaveStyle({
      borderTopLeftRadius: 'calc(var(--radius) - 2px)',
      borderTopRightRadius: 'calc(var(--radius) - 2px)',
    });
    expect(
      screen.queryByTestId('studio-workspace-tab-connector')
    ).not.toBeInTheDocument();
    expect(firstTab).toHaveAttribute('title', '标签 1');
    expect(screen.getByRole('tab', { name: '标签 2' }).parentElement).toHaveClass(
      'hover:border-white/5'
    );
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
        tabs={buildTabs(2)}
        themeColor="red"
        variant="workspace"
      />
    );

    const tabBar = screen.getByTestId('studio-tab-bar');
    const tab = screen.getByRole('tab', { name: '标签 1' });
    const inactiveTab = screen.getByRole('tab', { name: '标签 2' });
    expect(tabBar).toHaveAttribute('data-variant', 'workspace');
    expect(tabBar).toHaveClass(
      'studio-shell-tabbar',
      'overflow-visible',
      'bg-transparent'
    );
    expect(tabBar).toHaveStyle({ height: '100%' });
    expect(
      screen.getByRole('tablist', { name: '工作区标签' })
    ).toHaveClass('px-4');
    expect(
      screen.getByRole('tablist', { name: '工作区标签' })
    ).toHaveStyle({
      height: 'calc(100% + 1px)',
      marginBottom: '-1px',
      paddingBottom: '1px',
    });
    expect(tab.parentElement).toHaveClass('border-b-0');
    expect(inactiveTab).not.toHaveAttribute('title');
    expect(inactiveTab.parentElement).toHaveClass(
      'border-transparent',
      'hover:bg-white/5',
      'hover:text-slate-200'
    );
    expect(inactiveTab.parentElement).not.toHaveClass(
      'hover:border-white/5',
      'hover:border-white/10'
    );
    expect(tab.parentElement).toHaveStyle({
      background: '#07111f',
      borderColor: '#22364d',
      borderTopLeftRadius: '8px',
      borderTopRightRadius: '8px',
      height: '44px',
      maxWidth: 'min(13rem, 100%)',
      minWidth: '6.5rem',
      width: 'fit-content',
      zIndex: 10,
    });
    expect(screen.getByTestId('studio-workspace-tab-connector')).toHaveStyle({
      background: '#07111f',
    });
    const leftShoulder = screen.getByTestId(
      'studio-workspace-tab-shoulder-left'
    );
    const rightShoulder = screen.getByTestId(
      'studio-workspace-tab-shoulder-right'
    );
    expect(leftShoulder).toHaveStyle({
      background: '#040b15',
      borderBottom: '1px solid #22364d',
      borderBottomRightRadius: '12px',
      borderRight: '1px solid #22364d',
      bottom: '-1px',
      boxShadow: '6px 6px 0 6px #07111f',
      height: '13px',
      left: '-12px',
      width: '13px',
      zIndex: 11,
    });
    expect(rightShoulder).toHaveStyle({
      background: '#040b15',
      borderBottom: '1px solid #22364d',
      borderBottomLeftRadius: '12px',
      borderLeft: '1px solid #22364d',
      bottom: '-1px',
      boxShadow: '-6px 6px 0 6px #07111f',
      height: '13px',
      right: '-12px',
      width: '13px',
      zIndex: 11,
    });
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
