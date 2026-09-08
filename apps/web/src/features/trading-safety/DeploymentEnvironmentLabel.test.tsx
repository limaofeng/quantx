import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { DeploymentEnvironmentLabel } from './DeploymentEnvironmentLabel';

afterEach(() => vi.unstubAllGlobals());

it('shows the server deployment and independent quote source', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        environment: 'production',
        mode: 'live',
        marketSource: 'qmt',
      }),
    })
  );
  render(<DeploymentEnvironmentLabel />);
  expect(
    await screen.findByText('生产／真实交易 · QMT 行情')
  ).toBeInTheDocument();
});

it('does not guess an environment from an invalid response', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
  );
  render(<DeploymentEnvironmentLabel />);
  expect(await screen.findByText('环境信息未确认')).toBeInTheDocument();
});
