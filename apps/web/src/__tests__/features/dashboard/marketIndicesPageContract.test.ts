import { readFileSync } from 'node:fs';

function readPageSource() {
  const relativePath =
    '../../../features/dashboard/pages/MarketIndicesPage.tsx';
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

describe('all-indices page layout contract', () => {
  it('keeps page scrolling vertical and confines horizontal overflow to the table', () => {
    const source = readPageSource();
    const mainClassName = source.match(/<main className="([^"]+)"/)?.[1] ?? '';

    expect(mainClassName).toContain('overflow-x-hidden');
    expect(mainClassName).not.toContain('overflow-x-auto');
    expect(source).toMatch(/<div className="overflow-x-auto">/);
  });
});
