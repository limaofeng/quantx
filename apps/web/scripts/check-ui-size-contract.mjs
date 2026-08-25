import { promises as fs } from 'node:fs';
import path from 'node:path';

const sourceRoot = path.resolve('src');
const violations = [];
const numericControlHeightPattern =
  /(?<![A-Za-z0-9_-])(?:min-)?h-(?:7|8|9|10|11|12|\[(?:32|36|40)px\])(?![A-Za-z0-9_-])/;

const rules = [
  {
    name: 'arbitrary font size',
    pattern: /text-\[(?:\d+(?:\.\d+)?(?:px|rem))\]/g,
  },
  {
    name: 'display radius outside the density contract',
    pattern:
      /rounded-(?:xl|2xl|3xl)|rounded-\[(?:1\.2rem|1\.25rem|2rem|3rem|20px|24px|32px)\]/g,
  },
  {
    name: 'legacy large spacing utility',
    pattern:
      /(?<![A-Za-z0-9_-])(?:p|px|py|gap|space-[xy])-(?:4|5|6|8|10|12)(?![A-Za-z0-9_-])/g,
  },
];

async function collectFiles(directory) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await collectFiles(entryPath)));
    } else if (entry.isFile() && entry.name.endsWith('.tsx')) {
      files.push(entryPath);
    }
  }
  return files;
}

function shouldCheck(filePath) {
  const relativePath = path
    .relative(sourceRoot, filePath)
    .split(path.sep)
    .join('/');
  if (
    relativePath.includes('/__tests__/') ||
    relativePath.endsWith('.test.tsx')
  ) {
    return false;
  }
  if (relativePath.startsWith('features/auth/')) return false;
  return (
    relativePath.startsWith('features/') ||
    relativePath.startsWith('components/studio-workbench/') ||
    relativePath.startsWith('components/studio-workspace/')
  );
}

for (const filePath of await collectFiles(sourceRoot)) {
  if (!shouldCheck(filePath)) continue;
  const source = await fs.readFile(filePath, 'utf8');
  const lines = source.split(/\r?\n/);
  for (const rule of rules) {
    for (let index = 0; index < lines.length; index += 1) {
      const matches = lines[index].match(rule.pattern);
      if (!matches) continue;
      violations.push(
        `${path.relative(process.cwd(), filePath)}:${index + 1} ${rule.name}: ${matches.join(', ')}`
      );
    }
  }

  const controlTagPattern =
    /<(?:Button|Input|NativeSelect|SelectTrigger)\b[\s\S]*?>/g;
  for (const match of source.matchAll(controlTagPattern)) {
    if (!numericControlHeightPattern.test(match[0])) continue;
    const line = source.slice(0, match.index).split(/\r?\n/).length;
    violations.push(
      `${path.relative(process.cwd(), filePath)}:${line} shared control uses a numeric height instead of a control-height Token`
    );
  }

  const nativeControlPattern = /<(?:input|select|textarea)\b[\s\S]*?\/>/g;
  for (const match of source.matchAll(nativeControlPattern)) {
    const tag = match[0];
    const isAllowedChoiceInput =
      tag.startsWith('<input') &&
      /type\s*=\s*["'](?:checkbox|radio|range|file|hidden|color)["']/.test(tag);
    if (isAllowedChoiceInput) continue;
    const line = source.slice(0, match.index).split(/\r?\n/).length;
    violations.push(
      `${path.relative(process.cwd(), filePath)}:${line} native form control must use a shared UI primitive`
    );
  }
}

const styleSheet = await fs.readFile(
  path.join(sourceRoot, 'index.css'),
  'utf8'
);
const densityOverridePattern =
  /\[data-studio-workbench\][^{]*\{[^}]*(?:padding|gap|border-radius|font-size|height|width):[^;]*!important|\[data-studio-workbench\][^{]*\.(?:p|px|py|gap|rounded)-/gs;
if (densityOverridePattern.test(styleSheet)) {
  violations.push(
    'src/index.css contains a host-scoped child size override; components must consume size tokens directly'
  );
}

if (violations.length > 0) {
  process.stderr.write(`UI size contract violations (${violations.length}):\n`);
  process.stderr.write(`${violations.join('\n')}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write('UI size contract passed.\n');
}
