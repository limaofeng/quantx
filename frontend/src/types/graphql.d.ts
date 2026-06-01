// GraphQL 文件类型声明
declare module '*.gql' {
  import { type DocumentNode } from 'graphql';

  const Schema: DocumentNode;
  export = Schema;
}

declare module '*.graphql' {
  import { type DocumentNode } from 'graphql';

  const Schema: DocumentNode;
  export = Schema;
}
