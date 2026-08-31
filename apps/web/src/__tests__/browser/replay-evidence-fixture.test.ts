import { describe, expect, it, vi } from 'vitest';

import {
  fixtureAudit,
  fixtureFetch,
  fixtureSignals,
} from './replay-evidence-fixture';

const variables = {
  runId: 'browser-fixture',
  backtestId: 'sample-version-A',
  filters: {},
  first: 50,
  after: null,
};

describe('isolated browser evidence fixtures', () => {
  it('provides three disjoint signal pages and stops at the total', () => {
    const first = fixtureSignals(variables);
    const second = fixtureSignals({
      ...variables,
      after: first.pageInfo.endCursor,
    });
    const third = fixtureSignals({
      ...variables,
      after: second.pageInfo.endCursor,
    });
    expect([
      first.items.length,
      second.items.length,
      third.items.length,
    ]).toEqual([50, 50, 25]);
    expect(
      new Set(
        [...first.items, ...second.items, ...third.items].map(
          row => row.eventKey
        )
      ).size
    ).toBe(125);
    expect(third.pageInfo.hasNextPage).toBe(false);
    expect(third.pageInfo.endCursor).toBeNull();
  });

  it('places the trace target outside both first pages and resolves it exactly', () => {
    const eventKey = 'sample-A-event-075';
    expect(
      fixtureSignals(variables).items.some(row => row.eventKey === eventKey)
    ).toBe(false);
    expect(
      fixtureAudit(variables).items.some(row =>
        row.evaluationEventKeys.includes(eventKey)
      )
    ).toBe(false);
    const filtered = { ...variables, filters: { eventKey } };
    expect(fixtureSignals(filtered).items.map(row => row.eventKey)).toEqual([
      eventKey,
    ]);
    expect(
      fixtureAudit(filtered).items.map(row => row.evaluationEventKeys)
    ).toEqual([[eventKey]]);
  });

  it('keeps context separate from signals, including exact context lookup', () => {
    expect(fixtureSignals(variables).summary.eventCount).toBe(125);
    expect(
      fixtureSignals({ ...variables, filters: { includeContext: true } })
        .summary.eventCount
    ).toBe(126);
    const filters = { eventKey: 'sample-A-policy' };
    expect(fixtureSignals({ ...variables, filters }).items).toHaveLength(0);
    const context = fixtureSignals({
      ...variables,
      filters: { ...filters, includeContext: true },
    });
    expect(context.items).toHaveLength(1);
    expect(context.items[0].category).toBe('CONTEXT');
    expect(context.summary.suppressedCount).toBe(0);
  });

  it('uses distinct versions under the same run and supports a filtered second page', () => {
    const filtered = fixtureSignals({
      ...variables,
      filters: { search: '600000.SH' },
    });
    expect(filtered.summary.eventCount).toBe(63);
    expect(filtered.pageInfo.endCursor).toBe('sample-version-A:50');
    const versionB = { ...variables, backtestId: 'sample-version-B' };
    expect(fixtureSignals(versionB).summary.eventCount).toBe(17);
    expect(fixtureAudit(versionB).summary.decisionCount).toBe(18);
    expect(
      fixtureAudit(versionB).items.every(row =>
        row.decision.id.startsWith('decision-sample-B-')
      )
    ).toBe(true);
  });

  it('answers known evidence queries in memory and refuses unknown operations', async () => {
    const onRequest = vi.fn();
    const localFetch = fixtureFetch(onRequest);
    const response = await localFetch('/__browser_fixture_only__/graphql', {
      method: 'POST',
      body: JSON.stringify({
        query: 'query Portfolio_ReplaySignals',
        variables,
      }),
    });
    expect(await response.json()).toEqual({
      data: { tTradeReplaySignalEvaluations: fixtureSignals(variables) },
    });
    expect(onRequest).toHaveBeenCalledWith({
      kind: 'signal',
      version: 'sample-version-A',
      after: null,
      filters: {},
    });
    await expect(
      localFetch('/__browser_fixture_only__/graphql', {
        method: 'POST',
        body: JSON.stringify({ query: 'mutation NotAllowed', variables }),
      })
    ).rejects.toThrow('Browser fixture refuses unknown operations');
  });
});
