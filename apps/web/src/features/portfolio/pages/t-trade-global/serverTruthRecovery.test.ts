import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it, vi } from 'vitest';

import {
  createTTradeServerTruthRefreshPolicy,
  T_TRADE_SERVER_TRUTH_AUDIT_INTERVAL_MS,
} from './serverTruthRecovery';

describe('T-trade server-truth recovery policy', () => {
  it('keeps the authoritative query on the active URQL query instance', () => {
    const page = readFileSync(
      resolve(__dirname, '../TTradeGlobalPage.tsx'),
      'utf8'
    );
    const refreshBlock = page
      .split('const runMonitorEpochRefresh = React.useCallback', 2)[1]
      .split('const [lastTrustedMonitor', 2)[0];

    expect(refreshBlock).toContain("requestPolicy: 'network-only'");
    expect(refreshBlock).not.toContain('_instance');
    expect(page).not.toContain('monitorRefreshSequenceRef');
  });

  it('keeps subscription and audit recovery scoped to the monitor projection', () => {
    const page = readFileSync(
      resolve(__dirname, '../TTradeGlobalPage.tsx'),
      'utf8'
    );
    const subscriptionBlock = page
      .split(
        'const version = tTradeUpdateResult.data?.tTradeUpdates.version',
        2
      )[1]
      .split('const errorKey = tTradeUpdateResult.error?.message', 2)[0];
    const auditBlock = page
      .split('const auditServerTruth = () =>', 2)[1]
      .split('const timer = window.setInterval', 2)[0];

    expect(subscriptionBlock).toContain('requestAuthoritativeMonitorRefresh()');
    expect(subscriptionBlock).not.toContain('requestAuthoritativeRefresh()');
    expect(auditBlock).toContain('requestAuthoritativeMonitorRefresh()');
    expect(auditBlock).not.toContain('requestAuthoritativeRefresh()');
  });

  it('preserves the last trusted snapshot while a background audit is in flight', () => {
    const page = readFileSync(
      resolve(__dirname, '../TTradeGlobalPage.tsx'),
      'utf8'
    );
    const refreshBlock = page
      .split(
        'const requestAuthoritativeMonitorRefresh = React.useCallback',
        2
      )[1]
      .split('const requestAuthoritativeRefresh = React.useCallback', 2)[0];

    expect(refreshBlock).toContain('preserveTrust: true');
    expect(refreshBlock).not.toContain('setTrustedSignalSnapshotEpoch(null)');
  });

  it('coalesces duplicate subscription versions but accepts a replay after reconnect', () => {
    const policy = createTTradeServerTruthRefreshPolicy();

    expect(policy.shouldRefreshForSubscriptionVersion('account-1', '42')).toBe(
      true
    );
    expect(policy.shouldRefreshForSubscriptionVersion('account-1', '42')).toBe(
      false
    );
    expect(policy.shouldRefreshForSubscriptionVersion('account-1', '43')).toBe(
      true
    );

    policy.resetForReconnect('account-1');
    expect(policy.shouldRefreshForSubscriptionVersion('account-1', '43')).toBe(
      true
    );
  });

  it('coalesces a repeated subscription failure until a terminal result clears it', () => {
    const policy = createTTradeServerTruthRefreshPolicy();

    expect(
      policy.shouldRefreshForSubscriptionError('account-1', 'stream closed')
    ).toBe(true);
    expect(
      policy.shouldRefreshForSubscriptionError('account-1', 'stream closed')
    ).toBe(false);

    policy.clearSubscriptionError('account-1');
    expect(
      policy.shouldRefreshForSubscriptionError('account-1', 'stream closed')
    ).toBe(true);
  });

  it('returns to the HTTP service truth when Redis drops every notification', async () => {
    const policy = createTTradeServerTruthRefreshPolicy({
      auditIntervalMs: 30_000,
    });
    const networkOnlyRefetch = vi.fn(async () => ({
      accountId: 'account-1',
      version: 'server-2',
      candidateCount: 3,
    }));
    let renderedSnapshot = {
      accountId: 'account-1',
      version: 'cached-1',
      candidateCount: 0,
    };

    policy.noteNetworkRequest('account-1', 10_000);
    expect(policy.shouldRunAudit('account-1', 'connected', 39_999)).toBe(false);
    expect(policy.shouldRunAudit('account-1', 'connected', 40_000)).toBe(true);

    // No subscription event is supplied here: the audit is the recovery path
    // for an interrupted Redis publish/subscribe delivery chain.
    renderedSnapshot = await networkOnlyRefetch();

    expect(networkOnlyRefetch).toHaveBeenCalledOnce();
    expect(renderedSnapshot).toEqual({
      accountId: 'account-1',
      version: 'server-2',
      candidateCount: 3,
    });
  });

  it('never treats a disconnected transport as an audit success and avoids a timer storm', () => {
    const policy = createTTradeServerTruthRefreshPolicy();
    const at = T_TRADE_SERVER_TRUTH_AUDIT_INTERVAL_MS;

    expect(policy.shouldRunAudit('account-1', 'closed', at)).toBe(false);
    expect(policy.shouldRunAudit('account-1', 'connected', at)).toBe(true);
    expect(policy.shouldRunAudit('account-1', 'connected', at)).toBe(false);
  });
});
