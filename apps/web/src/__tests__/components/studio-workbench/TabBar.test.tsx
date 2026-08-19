import { fireEvent, render, screen } from '@testing-library/react';
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
    expect(screen.getByRole('tablist', { name: '工作区标签' })).toBeVisible();
    expect(firstTab).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(firstTab, { key: 'ArrowRight' });
    expect(onTabChange).toHaveBeenCalledWith('tab-2');
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
