import fs from 'node:fs/promises';
import path from 'node:path';

import {
  buildSchema,
  getNamedType,
  isIntrospectionType,
  isSpecifiedScalarType,
  printType,
} from 'graphql';

const docsRoot = path.resolve(import.meta.dirname, '..');
const contractRoot = path.join(docsRoot, 'public', 'contracts');
const outputRoot = path.join(
  docsRoot,
  'content',
  'reference',
  'graphql-api'
);

const schemaSource = await fs.readFile(
  path.join(contractRoot, 'graphql-schema.graphql'),
  'utf8'
);
const policyContract = JSON.parse(
  await fs.readFile(
    path.join(contractRoot, 'graphql-operation-policies.v2.json'),
    'utf8'
  )
);
if (policyContract.schemaVersion !== 2) {
  throw new Error('Unsupported GraphQL operation policy schema version');
}
const operationPolicies = policyContract.operations;
const schema = buildSchema(schemaSource);

await fs.rm(outputRoot, { recursive: true, force: true });
await fs.mkdir(path.join(outputRoot, 'type'), { recursive: true });

function frontmatter(title) {
  return `---\ntitle: ${JSON.stringify(title)}\noutline: [2, 3]\n---\n\n`;
}

function escapeTable(value) {
  return String(value ?? '')
    .replaceAll('|', '\\|')
    .replaceAll('\n', ' ');
}

function typeReference(type, prefix = './type/') {
  const namedType = getNamedType(type);
  const rendered = String(type);
  if (isSpecifiedScalarType(namedType)) return `\`${rendered}\``;
  return `[\`${rendered}\`](${prefix}${namedType.name})`;
}

function fieldSignature(field) {
  const args = field.args
    .map(argument => `${argument.name}: ${String(argument.type)}`)
    .join(', ');
  return `${field.name}${args ? `(${args})` : ''}: ${String(field.type)}`;
}

function renderOperation(operationName, type) {
  const lines = [
    frontmatter(operationName),
    `# ${operationName}`,
    '',
    '> 此页面由当前部署版本的 GraphQL Schema 与显式 operation policy 自动生成。',
    '',
  ];
  const policyMap = operationPolicies[operationName] ?? {};
  for (const field of Object.values(type.getFields()).sort((a, b) =>
    a.name.localeCompare(b.name)
  )) {
    const policy = policyMap[field.name];
    if (!policy) {
      throw new Error(`Missing operation policy: ${operationName}.${field.name}`);
    }
    lines.push(`## ${field.name}`, '');
    lines.push(
      `**所需权限：** ${policy.requiredPermissions
        .map(permission => `\`${permission}\``)
        .join(' + ')}`,
      '',
      `**适用端：** ${policy.audiences.map(value => `\`${value}\``).join('、')}`,
      '',
      `**稳定性 / 风险：** \`${policy.stability}\` / \`${policy.risk}\``,
      ''
    );
    if (field.description) lines.push(field.description, '');
    lines.push('```graphql', fieldSignature(field), '```', '');
    if (field.args.length > 0) {
      lines.push('| 参数 | 类型 | 默认值 | 说明 |', '| --- | --- | --- | --- |');
      for (const argument of field.args) {
        lines.push(
          `| \`${argument.name}\` | ${typeReference(argument.type)} | ${
            argument.defaultValue === undefined
              ? '—'
              : `\`${escapeTable(argument.defaultValue)}\``
          } | ${escapeTable(argument.description) || '—'} |`
        );
      }
      lines.push('');
    }
    lines.push(`**返回：** ${typeReference(field.type)}`, '');
    if (field.deprecationReason) {
      lines.push(`> 已弃用：${field.deprecationReason}`, '');
    }
  }
  return lines.join('\n');
}

const operationTypes = [
  ['Query', schema.getQueryType()],
  ['Mutation', schema.getMutationType()],
  ['Subscription', schema.getSubscriptionType()],
];
for (const [operationName, type] of operationTypes) {
  if (!type) continue;
  await fs.writeFile(
    path.join(outputRoot, `${operationName.toLowerCase()}.md`),
    renderOperation(operationName, type),
    'utf8'
  );
}

const rootTypeNames = new Set(
  operationTypes.map(([, type]) => type?.name).filter(Boolean)
);
const types = Object.values(schema.getTypeMap())
  .filter(
    type =>
      !isIntrospectionType(type) &&
      !isSpecifiedScalarType(type) &&
      !rootTypeNames.has(type.name)
  )
  .sort((a, b) => a.name.localeCompare(b.name));

const typeIndexLines = [
  frontmatter('GraphQL 类型索引'),
  '# GraphQL 类型索引',
  '',
  '> 类型定义由当前部署版本自动生成。业务语义以字段描述和客户端指南为准。',
  '',
];
for (const type of types) {
  typeIndexLines.push(`- [\`${type.name}\`](./type/${type.name})`);
  const body = [
    frontmatter(type.name),
    `# ${type.name}`,
    '',
    type.description || '当前 Schema 未提供额外类型说明。',
    '',
    '```graphql',
    printType(type),
    '```',
    '',
    '[返回类型索引](../types)',
    '',
  ].join('\n');
  await fs.writeFile(
    path.join(outputRoot, 'type', `${type.name}.md`),
    body,
    'utf8'
  );
}
await fs.writeFile(
  path.join(outputRoot, 'types.md'),
  typeIndexLines.join('\n'),
  'utf8'
);

const counts = Object.fromEntries(
  operationTypes.map(([name, type]) => [
    name,
    type ? Object.keys(type.getFields()).length : 0,
  ])
);
const index = [
  frontmatter('GraphQL Schema 参考'),
  '# GraphQL Schema 参考',
  '',
  '该参考由发布包中的 SDL 和 operation policy 自动生成，不依赖生产环境内省。',
  '',
  '| 操作 | 字段数 | 参考 |',
  '| --- | ---: | --- |',
  `| Query | ${counts.Query} | [查看 Query](./query) |`,
  `| Mutation | ${counts.Mutation} | [查看 Mutation](./mutation) |`,
  `| Subscription | ${counts.Subscription} | [查看 Subscription](./subscription) |`,
  `| Types | ${types.length} | [查看类型索引](./types) |`,
  '',
  '完整 SDL 与 v2 operation policy 可在[契约下载](../)页面获取。',
  '',
].join('\n');
await fs.writeFile(path.join(outputRoot, 'index.md'), index, 'utf8');
