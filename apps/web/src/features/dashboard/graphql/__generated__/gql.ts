/* eslint-disable */
import * as types from './graphql';
import { TypedDocumentNode as DocumentNode } from '@graphql-typed-document-node/core';

/**
 * Map of all GraphQL operations in the project.
 *
 * This map has several performance disadvantages:
 * 1. It is not tree-shakeable, so it will include all operations in the project.
 * 2. It is not minifiable, so the string of a GraphQL query will be multiple times inside the bundle.
 * 3. It does not support dead code elimination, so it will add unused operations.
 *
 * Therefore it is highly recommended to use the babel or swc plugin for production.
 * Learn more about it here: https://the-guild.dev/graphql/codegen/plugins/presets/preset-client#reducing-bundle-size
 */
type Documents = {
    "query Market_IndicesDirectory($first: Int!, $after: String, $where: InstrumentWhereInput, $orderBy: InstrumentOrder) {\n  instrumentsConnection(\n    first: $first\n    after: $after\n    where: $where\n    orderBy: $orderBy\n  ) {\n    totalCount\n    edges {\n      cursor\n      node {\n        id\n        instrumentId\n        name\n        market\n        type\n      }\n    }\n    pageInfo {\n      hasNextPage\n      endCursor\n    }\n  }\n}": typeof types.Market_IndicesDirectoryDocument,
    "query Dashboard_MarketIndexSnapshots($stockList: [String!]!) {\n  marketIndexSnapshots(stockList: $stockList) {\n    stockCode\n    quote {\n      stockCode\n      time\n      lastPrice\n      open\n      high\n      low\n      preClose\n      volume\n    }\n    dailyKline {\n      stockCode\n      time\n      open\n      high\n      low\n      close\n      preClose\n      volume\n    }\n  }\n}": typeof types.Dashboard_MarketIndexSnapshotsDocument,
};
const documents: Documents = {
    "query Market_IndicesDirectory($first: Int!, $after: String, $where: InstrumentWhereInput, $orderBy: InstrumentOrder) {\n  instrumentsConnection(\n    first: $first\n    after: $after\n    where: $where\n    orderBy: $orderBy\n  ) {\n    totalCount\n    edges {\n      cursor\n      node {\n        id\n        instrumentId\n        name\n        market\n        type\n      }\n    }\n    pageInfo {\n      hasNextPage\n      endCursor\n    }\n  }\n}": types.Market_IndicesDirectoryDocument,
    "query Dashboard_MarketIndexSnapshots($stockList: [String!]!) {\n  marketIndexSnapshots(stockList: $stockList) {\n    stockCode\n    quote {\n      stockCode\n      time\n      lastPrice\n      open\n      high\n      low\n      preClose\n      volume\n    }\n    dailyKline {\n      stockCode\n      time\n      open\n      high\n      low\n      close\n      preClose\n      volume\n    }\n  }\n}": types.Dashboard_MarketIndexSnapshotsDocument,
};

/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 *
 *
 * @example
 * ```ts
 * const query = graphql(`query GetUser($id: ID!) { user(id: $id) { name } }`);
 * ```
 *
 * The query argument is unknown!
 * Please regenerate the types.
 */
export function graphql(source: string): unknown;

/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "query Market_IndicesDirectory($first: Int!, $after: String, $where: InstrumentWhereInput, $orderBy: InstrumentOrder) {\n  instrumentsConnection(\n    first: $first\n    after: $after\n    where: $where\n    orderBy: $orderBy\n  ) {\n    totalCount\n    edges {\n      cursor\n      node {\n        id\n        instrumentId\n        name\n        market\n        type\n      }\n    }\n    pageInfo {\n      hasNextPage\n      endCursor\n    }\n  }\n}"): (typeof documents)["query Market_IndicesDirectory($first: Int!, $after: String, $where: InstrumentWhereInput, $orderBy: InstrumentOrder) {\n  instrumentsConnection(\n    first: $first\n    after: $after\n    where: $where\n    orderBy: $orderBy\n  ) {\n    totalCount\n    edges {\n      cursor\n      node {\n        id\n        instrumentId\n        name\n        market\n        type\n      }\n    }\n    pageInfo {\n      hasNextPage\n      endCursor\n    }\n  }\n}"];
/**
 * The graphql function is used to parse GraphQL queries into a document that can be used by GraphQL clients.
 */
export function graphql(source: "query Dashboard_MarketIndexSnapshots($stockList: [String!]!) {\n  marketIndexSnapshots(stockList: $stockList) {\n    stockCode\n    quote {\n      stockCode\n      time\n      lastPrice\n      open\n      high\n      low\n      preClose\n      volume\n    }\n    dailyKline {\n      stockCode\n      time\n      open\n      high\n      low\n      close\n      preClose\n      volume\n    }\n  }\n}"): (typeof documents)["query Dashboard_MarketIndexSnapshots($stockList: [String!]!) {\n  marketIndexSnapshots(stockList: $stockList) {\n    stockCode\n    quote {\n      stockCode\n      time\n      lastPrice\n      open\n      high\n      low\n      preClose\n      volume\n    }\n    dailyKline {\n      stockCode\n      time\n      open\n      high\n      low\n      close\n      preClose\n      volume\n    }\n  }\n}"];

export function graphql(source: string) {
  return (documents as any)[source] ?? {};
}

export type DocumentType<TDocumentNode extends DocumentNode<any, any>> = TDocumentNode extends DocumentNode<  infer TType,  any>  ? TType  : never;