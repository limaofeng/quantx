import {
  clearGraphqlRequestTimings,
  getGraphqlRequestTimings,
  parseQuantxGraphqlTiming,
  recordGraphqlRequestTiming,
  type GraphqlRequestTiming,
} from '@/core/performance/graphql-timing';

describe('GraphQL performance timing', () => {
  afterEach(() => {
    clearGraphqlRequestTimings();
  });

  it('parses bounded field and SQL timing details', () => {
    const timing = parseQuantxGraphqlTiming({
      requestId: 'req-1',
      operationName: 'TTradeGlobalMonitor',
      operationType: 'Query',
      totalMs: 1250.5,
      phases: { parseMs: 1.2, executeMs: 1200 },
      fieldInvocations: 4,
      fieldResolverMs: 1190,
      fieldCount: 2,
      fieldsTruncated: false,
      fields: [
        {
          parentType: 'Query',
          field: 'tTradeGlobalMonitor',
          paths: ['tTradeGlobalMonitor'],
          count: 1,
          totalMs: 1180,
          maxMs: 1180,
          errors: 0,
        },
      ],
      sql: {
        count: 3,
        totalMs: 900,
        maxMs: 500,
        errors: 0,
        statementCount: 2,
        statementsTruncated: false,
        statements: [
          {
            kind: 'SELECT',
            fingerprint: '1234567890abcdef',
            count: 2,
            totalMs: 800,
            maxMs: 500,
            errors: 0,
          },
        ],
      },
    });

    expect(timing).not.toBeNull();
    expect(timing?.fields[0]).toMatchObject({
      parentType: 'Query',
      field: 'tTradeGlobalMonitor',
      totalMs: 1180,
    });
    expect(timing?.sql.statements[0]).toMatchObject({
      kind: 'SELECT',
      fingerprint: '1234567890abcdef',
    });
    expect(timing?.phases.executeMs).toBe(1200);
  });

  it('rejects invalid payloads and clamps invalid durations', () => {
    expect(parseQuantxGraphqlTiming(null)).toBeNull();
    const timing = parseQuantxGraphqlTiming({
      totalMs: -5,
      phases: { executeMs: Number.NaN, parseMs: -1 },
      fields: [],
      sql: {},
    });

    expect(timing?.totalMs).toBe(0);
    expect(timing?.phases).toEqual({ parseMs: 0 });
  });

  it('keeps a 200-request ring buffer', () => {
    for (let index = 0; index < 205; index += 1) {
      recordGraphqlRequestTiming(createRequestTiming(index));
    }

    const timings = getGraphqlRequestTimings();
    expect(timings).toHaveLength(200);
    expect(timings[0].requestId).toBe('req-5');
    expect(timings[199].requestId).toBe('req-204');
  });
});

function createRequestTiming(index: number): GraphqlRequestTiming {
  return {
    requestId: `req-${index}`,
    operationName: 'TimingQuery',
    operationType: 'Query',
    route: '/t-trade',
    startedAt: index,
    receivedAt: index + 1,
    clientMs: 1,
    graphqlMs: null,
    outsideGraphqlMs: null,
    hasError: false,
    server: null,
  };
}
