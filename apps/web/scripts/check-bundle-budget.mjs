import { readdir, stat } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const DIST = path.resolve('dist');
const BUDGETS = {
  '.js': 500 * 1024,
  '.css': 230 * 1024,
};

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map(async entry => {
      const target = path.join(directory, entry.name);
      return entry.isDirectory() ? filesUnder(target) : [target];
    })
  );
  return nested.flat();
}

const violations = [];
for (const file of await filesUnder(DIST)) {
  const extension = path.extname(file);
  const budget = BUDGETS[extension];
  if (!budget || file.endsWith('.map')) continue;
  const { size } = await stat(file);
  if (size > budget) {
    violations.push(
      `${path.relative(DIST, file)}: ${(size / 1024).toFixed(1)} KiB > ` +
        `${(budget / 1024).toFixed(0)} KiB`
    );
  }
}

if (violations.length > 0) {
  process.stderr.write(
    `Bundle budget exceeded:\n${violations.map(item => `- ${item}`).join('\n')}\n`
  );
  process.exit(1);
}

process.stdout.write(
  'Bundle budget passed (JavaScript <= 500 KiB, CSS <= 230 KiB per file).\n'
);
