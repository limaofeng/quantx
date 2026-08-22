import { readFileSync } from 'node:fs';
import path from 'node:path';

import { buildSchema, validate } from 'graphql';
import { describe, expect, it } from 'vitest';

import { StockWorkspaceFinancialsQuery } from '@/features/stocks/hooks/useStockWorkspaceFinancials';

describe('StockWorkspaceFinancials query', () => {
  it('matches the generated GraphQL schema', () => {
    const schemaSource = readFileSync(
      path.resolve(process.cwd(), 'src/generated/schema.graphql'),
      'utf8'
    );
    const errors = validate(
      buildSchema(schemaSource),
      StockWorkspaceFinancialsQuery
    );

    expect(errors.map(error => error.message)).toEqual([]);
  });
});
