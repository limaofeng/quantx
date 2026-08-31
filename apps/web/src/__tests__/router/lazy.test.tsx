import { act, render, screen } from '@testing-library/react';
import { vi } from 'vitest';

import { logger } from '@/core/errors/logger';
import {
  createLazyRoute,
  preloadImporter,
  type RouteImporter,
} from '@/router/lazy';

vi.mock('@/core/errors/logger', () => ({
  logger: { warn: vi.fn(), info: vi.fn(), error: vi.fn(), debug: vi.fn() },
}));

function LoadedPage() {
  return <h1>页面已就绪</h1>;
}

describe('route module loading', () => {
  it('shares the pending module between preloads and navigation', async () => {
    let finish!: (module: Awaited<ReturnType<RouteImporter>>) => void;
    const importer = vi.fn<RouteImporter>(
      () =>
        new Promise(resolve => {
          finish = resolve;
        })
    );
    const preload = preloadImporter(importer);
    const duplicate = preloadImporter(importer);
    const Route = createLazyRoute(importer, '示例页');

    render(<Route params={{}} />);
    expect(screen.getByText('页面加载中')).toBeInTheDocument();
    await act(async () => {
      await Promise.resolve();
      finish({ default: LoadedPage });
      await Promise.all([preload, duplicate]);
    });

    expect(
      await screen.findByRole('heading', { name: '页面已就绪' })
    ).toBeVisible();
    expect(importer).toHaveBeenCalledTimes(1);
  });

  it('handles speculative failure and retries when the user navigates', async () => {
    const importer = vi
      .fn<RouteImporter>()
      .mockRejectedValueOnce(new Error('Module temporarily unavailable'))
      .mockResolvedValue({ default: LoadedPage });

    await expect(preloadImporter(importer)).resolves.toBeUndefined();
    expect(logger.warn).toHaveBeenCalledWith('路由预加载失败，将在访问时重试', {
      message: 'Module temporarily unavailable',
    });
    const Route = createLazyRoute(importer, '示例页');
    render(<Route params={{}} />);

    expect(
      await screen.findByRole('heading', { name: '页面已就绪' })
    ).toBeVisible();
    expect(importer).toHaveBeenCalledTimes(2);
  });

  it('does not load a route merely because it was registered', () => {
    const importer = vi
      .fn<RouteImporter>()
      .mockResolvedValue({ default: LoadedPage });
    createLazyRoute(importer, '示例页');
    expect(importer).not.toHaveBeenCalled();
  });
});
