import process from 'node:process';

import type { CodegenConfig } from '@graphql-codegen/cli';

const endpoint =
  process.env.CODEGEN_GRAPHQL_ENDPOINT ??
  process.env.GRAPHQL_ENDPOINT ??
  process.env.VITE_GRAPHQL_ENDPOINT ??
  'http://127.0.0.1:8080/graphql';

/**
 * Focused generation for the market workbench. The repository-wide config
 * remains authoritative for the full app; this config isolates the approved
 * market operations while unrelated portfolio documents migrate schemas.
 *
 * Regenerate from apps/web with:
 *   CODEGEN_GRAPHQL_ENDPOINT=http://127.0.0.1:8080/graphql
 *   npm exec -- graphql-codegen --config codegen.market-index.ts
 */
const config: CodegenConfig = {
  schema: endpoint,
  documents: ['src/features/dashboard/graphql/*.graphql'],
  generates: {
    'src/features/dashboard/graphql/__generated__/': {
      preset: 'client',
      plugins: [],
      presetConfig: {
        gqlTagName: 'graphql',
      },
      config: {
        onlyOperationTypes: true,
        scalars: {
          DateTime: 'string',
          Date: 'string',
          Decimal: 'number',
          JSON: 'any',
          Long: 'number',
        },
      },
    },
  },
};

export default config;
