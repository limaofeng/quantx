import type { CodegenConfig } from '@graphql-codegen/cli';

const graphqlSchemaEndpoint =
  process.env.CODEGEN_GRAPHQL_ENDPOINT ??
  process.env.GRAPHQL_ENDPOINT ??
  process.env.VITE_GRAPHQL_ENDPOINT ??
  'http://localhost:8080/graphql';
const graphqlSchemaToken = process.env.CODEGEN_GRAPHQL_TOKEN?.trim();
const paperDocuments = 'src/features/portfolio/hooks/tAssistantPaperQueries.gql';
const scalarTypes = {
  DateTime: 'string',
  Date: 'string',
  Decimal: 'number',
  JSON: 'any',
  Long: 'number',
};

const config: CodegenConfig = {
  // 从后端获取实时 Schema
  schema: graphqlSchemaToken
    ? {
        [graphqlSchemaEndpoint]: {
          headers: {
            Authorization: `Bearer ${graphqlSchemaToken}`,
          },
        },
      }
    : graphqlSchemaEndpoint,
  generates: {
    // 公共查询生成到 src/generated/gql/ 目录下
    'src/generated/gql/': {
      documents: [
        'src/**/*.tsx',
        'src/**/*.ts',
        'src/**/*.gql',
        '!src/generated/**/*',
        `!${paperDocuments}`,
      ],
      preset: 'client',
      plugins: [],
      presetConfig: {
        gqlTagName: 'gql',
      },
      config: {
        scalars: scalarTypes,
      },
    },
    // Keep this lazy workspace's operation AST out of the shared GraphQL chunk.
    'src/generated/paper/': {
      documents: [paperDocuments],
      preset: 'client',
      plugins: [],
      config: { scalars: scalarTypes },
    },
    // 生成完整的 Schema 文件供前端参考
    'src/generated/schema.graphql': {
      plugins: ['schema-ast'],
    },
    'src/generated/schema.json': {
      plugins: ['introspection'],
    },
  },
  ignoreNoDocuments: true,
  allowPartialOutputs: false,
};

export default config;
