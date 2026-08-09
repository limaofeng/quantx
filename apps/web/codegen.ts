import type { CodegenConfig } from '@graphql-codegen/cli';

const graphqlSchemaEndpoint =
  process.env.CODEGEN_GRAPHQL_ENDPOINT ??
  process.env.GRAPHQL_ENDPOINT ??
  process.env.VITE_GRAPHQL_ENDPOINT ??
  'http://localhost:8080/graphql';
const graphqlSchemaToken = process.env.CODEGEN_GRAPHQL_TOKEN?.trim();

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
  // 扫描所有 ts/tsx 文件中的 gql 标签
  documents: [
    'src/**/*.tsx',
    'src/**/*.ts',
    'src/**/*.gql',
    '!src/generated/**/*',
  ],
  generates: {
    // 统一生成到 src/generated/gql/ 目录下
    'src/generated/gql/': {
      preset: 'client',
      plugins: [],
      presetConfig: {
        gqlTagName: 'gql',
      },
      config: {
        scalars: {
          DateTime: 'string',
          Date: 'string',
          Decimal: 'number',
          JSON: 'any',
          Long: 'number',
        },
      },
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
  allowPartialOutputs: true,
};

export default config;
