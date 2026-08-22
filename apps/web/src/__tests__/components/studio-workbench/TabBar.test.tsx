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
    expect(
      screen.getByRole('tab', { name: '标签 2' }).parentElement
    ).toHaveClass('hover:border-white/5');
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

  it('keeps a fixed tab non-closable while exposing cleanup actions', () => {
    const onTabChange = vi.fn();
    const onTabClose = vi.fn();

    render(
      <TabBar
        activeTabId="tab-1"
        canCloseTab={tab => tab.id !== 'tab-1'}
        onTabChange={onTabChange}
        onTabClose={onTabClose}
        tabs={buildTabs(2)}
        themeColor="red"
        variant="workspace"
      />
    );

    const fixedTab = screen.getByRole('tab', { name: '标签 1' });
    expect(screen.getAllByRole('tab')[0]).toBe(fixedTab);
    expect(fixedTab.parentElement).toHaveAttribute(
      'data-studio-fixed-tab',
      'true'
    );
    expect(fixedTab.parentElement).toHaveStyle({
      left: '16px',
      position: 'sticky',
      zIndex: 20,
    });
    expect(
      screen.queryByRole('button', { name: '关闭 标签 1' })
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭 标签 2' })).toBeVisible();

    fireEvent.keyDown(fixedTab, { key: 'Delete' });
    expect(onTabClose).not.toHaveBeenCalled();

    fireEvent.keyDown(fixedTab, { key: 'ArrowRight' });
    expect(onTabChange).toHaveBeenCalledWith('tab-2');

    fireEvent.contextMenu(fixedTab);
    expect(screen.getByRole('menuitem', { name: '关闭' })).toBeDisabled();
    expect(screen.getByRole('menuitem', { name: '关闭其他' })).toBeEnabled();
    expect(screen.getByRole('menuitem', { name: '关闭右侧' })).toBeEnabled();
    expect(screen.getByRole('menuitem', { name: '关闭全部' })).toBeEnabled();

    fireEvent.click(screen.getByRole('menuitem', { name: '关闭右侧' }));
    expect(onTabClose).toHaveBeenCalledTimes(1);
    expect(onTabClose).toHaveBeenCalledWith('tab-2');
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
    expect(screen.getByRole('tablist', { name: '工作区标签' })).toHaveClass(
      'px-4'
    );
    expect(screen.getByRole('tablist', { name: '工作区标签' })).toHaveStyle({
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
    expect(leftShoulder).toHaveAttribute('viewBox', '0 0 9 9');
    expect(leftShoulder).toHaveAttribute(
      'shape-rendering',
      'geometricPrecision'
    );
    expect(leftShoulder).toHaveStyle({
      bottom: '-1px',
      height: '9px',
      left: '-9px',
      pointerEvents: 'none',
      width: '9px',
      zIndex: 11,
    });
    expect(leftShoulder.querySelectorAll('path')[0]).toHaveAttribute(
      'd',
      'M9 0 C9 4.97 4.97 9 0 9 H9 Z'
    );
    expect(leftShoulder.querySelectorAll('path')[0]).toHaveAttribute(
      'fill',
      '#07111f'
    );
    expect(leftShoulder.querySelectorAll('path')[1]).toHaveAttribute(
      'd',
      'M8.5 0 C8.5 4.69 4.69 8.5 0 8.5'
    );
    expect(leftShoulder.querySelectorAll('path')[1]).toHaveAttribute(
      'stroke',
      '#22364d'
    );
    expect(rightShoulder).toHaveAttribute('viewBox', '0 0 9 9');
    expect(rightShoulder).toHaveStyle({
      bottom: '-1px',
      height: '9px',
      pointerEvents: 'none',
      right: '-9px',
      width: '9px',
      zIndex: 11,
    });
    expect(rightShoulder.querySelectorAll('path')[0]).toHaveAttribute(
      'd',
      'M0 0 C0 4.97 4.03 9 9 9 H0 Z'
    );
    expect(rightShoulder.querySelectorAll('path')[1]).toHaveAttribute(
      'd',
      'M0.5 0 C0.5 4.69 4.31 8.5 9 8.5'
    );
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
      expect(tabList.scrollLeft).toBe(470);
    });
  });

  it('does not move the fixed workspace tab when the strip is scrolled', async () => {
    render(
      <TabBar
        activeTabId="tab-1"
        canCloseTab={tab => tab.id !== 'tab-1'}
        onTabChange={vi.fn()}
        onTabClose={vi.fn()}
        tabs={buildTabs(10)}
        themeColor="red"
        variant="workspace"
      />
    );

    const tabList = screen.getByRole('tablist', { name: '工作区标签' });
    const fixedTab = screen.getByRole('tab', { name: '标签 1' });
    tabList.scrollLeft = 360;

    fireEvent(window, new Event('resize'));

    await waitFor(() => {
      expect(tabList.scrollLeft).toBe(360);
    });
    expect(fixedTab.parentElement).toHaveStyle({
      left: '16px',
      position: 'sticky',
      zIndex: 20,
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
