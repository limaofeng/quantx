import { fireEvent, render, screen } from '@testing-library/react';
import { useEffect } from 'react';
import { createPortal } from 'react-dom';

import { buildStudioWorkspaceTab } from '@/components/studio-workspace';
import { UiDensityProvider } from '@/components/UiDensityProvider';
import { initializeUiDensity, UI_DENSITY_STORAGE_KEY } from '@/core/ui-density';
import { AppearanceSettingsPanel } from '@/features/settings/components/AppearanceSettingsPanel';
import { findRoute } from '@/router';

describe('appearance density preference', () => {
  let saved: Map<string, string>;

  beforeEach(() => {
    saved = new Map();
    vi.mocked(localStorage.getItem).mockImplementation(
      key => saved.get(key) ?? null
    );
    vi.mocked(localStorage.setItem).mockImplementation((key, value) => {
      saved.set(key, value);
    });
    document.documentElement.removeAttribute('data-ui-density');
  });

  afterEach(() => {
    document.documentElement.removeAttribute('data-ui-density');
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
  });

  function renderSettings() {
    return render(
      <UiDensityProvider>
        <AppearanceSettingsPanel />
      </UiDensityProvider>
    );
  }

  it('defaults to standard and restores the selected density on the next mount', () => {
    const view = renderSettings();
    expect(screen.getByRole('radio', { name: '标准' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: '紧凑' }));
    expect(document.documentElement.dataset.uiDensity).toBe('compact');
    expect(saved.get(UI_DENSITY_STORAGE_KEY)).toBe('compact');
    view.unmount();

    renderSettings();
    expect(screen.getByRole('radio', { name: '紧凑' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: '标准' }));
    expect(document.documentElement.dataset.uiDensity).toBe('standard');
    expect(saved.get(UI_DENSITY_STORAGE_KEY)).toBe('standard');
  });

  it('applies a saved preference before React is mounted', () => {
    saved.set(UI_DENSITY_STORAGE_KEY, 'compact');
    expect(initializeUiDensity()).toBe('compact');
    expect(document.documentElement.dataset.uiDensity).toBe('compact');
  });

  it('uses standard for invalid data and supports switching when storage is blocked', () => {
    saved.set(UI_DENSITY_STORAGE_KEY, 'invalid');
    expect(initializeUiDensity()).toBe('standard');
    vi.mocked(localStorage.getItem).mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.mocked(localStorage.setItem).mockImplementation(() => {
      throw new Error('blocked');
    });
    renderSettings();
    fireEvent.click(screen.getByRole('radio', { name: '紧凑' }));
    expect(screen.getByRole('radio', { name: '紧凑' })).toBeChecked();
    expect(document.documentElement.dataset.uiDensity).toBe('compact');
  });

  it('keeps form state, portal content and unrelated sidebar preferences intact', () => {
    const mounted = vi.fn();
    const unmounted = vi.fn();
    saved.set(
      'quantx-studio-workbench',
      '{"sidebarWidths":{"studio-workspace-sidebar":312}}'
    );
    function Workspace() {
      useEffect(() => {
        mounted();
        return unmounted;
      }, []);
      return (
        <>
          <input aria-label="未保存的草稿" defaultValue="草稿" />
          {createPortal(
            <div role="dialog" aria-label="已打开的弹窗">
              弹窗内容
            </div>,
            document.body
          )}
        </>
      );
    }
    render(
      <UiDensityProvider>
        <AppearanceSettingsPanel />
        <Workspace />
      </UiDensityProvider>
    );
    const dialog = screen.getByRole('dialog');
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '修改后的草稿' } });
    fireEvent.click(screen.getByRole('radio', { name: '紧凑' }));
    expect(screen.getByRole('textbox')).toBe(input);
    expect(input).toHaveValue('修改后的草稿');
    expect(screen.getByRole('dialog')).toBe(dialog);
    expect(dialog.closest('html')).toHaveAttribute(
      'data-ui-density',
      'compact'
    );
    expect(mounted).toHaveBeenCalledTimes(1);
    expect(unmounted).not.toHaveBeenCalled();
    expect(saved.get('quantx-studio-workbench')).toBe(
      '{"sidebarWidths":{"studio-workspace-sidebar":312}}'
    );
  });

  it('opens appearance inside the existing settings workspace tab', () => {
    expect(findRoute('/settings/appearance')?.path).toBe(
      '/settings/appearance'
    );
    expect(buildStudioWorkspaceTab('/settings/appearance')).toMatchObject({
      id: 'settings',
      name: '系统设置',
    });
  });
});
