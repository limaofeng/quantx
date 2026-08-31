import { render, screen } from '@testing-library/react';

import { Input } from '@/components/ui/input';
import {
  StudioPageDescription,
  StudioPageTitle,
} from '@/components/ui/studio-layout';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

describe('shared UI density overrides', () => {
  it('keeps heading and caption sizes alongside their text colors', () => {
    render(
      <>
        <StudioPageTitle>外观</StudioPageTitle>
        <StudioPageDescription>界面密度</StudioPageDescription>
      </>
    );

    expect(screen.getByRole('heading', { name: '外观' })).toHaveClass(
      'text-ui-page-title',
      'text-slate-100'
    );
    expect(screen.getByText('界面密度')).toHaveClass(
      'text-ui-caption',
      'text-slate-500'
    );
  });

  it('lets table consumers override header height, empty height and cell padding', () => {
    render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="h-9">行业</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell className="py-0">银行</TableCell>
          </TableRow>
          <TableRow>
            <TableCell className="h-[300px] p-0">暂无数据</TableCell>
          </TableRow>
        </TableBody>
      </Table>
    );

    const header = screen.getByRole('columnheader', { name: '行业' });
    expect(header).toHaveClass('h-9');
    expect(header).not.toHaveClass('h-ui-table-header');

    const row = screen.getByRole('cell', { name: '银行' });
    expect(row).toHaveClass('h-ui-table-row', 'px-3', 'py-0');
    expect(row).not.toHaveClass('py-ui-table-cell-y');

    const empty = screen.getByRole('cell', { name: '暂无数据' });
    expect(empty).toHaveClass('h-[300px]', 'p-0');
    for (const defaultClass of [
      'h-ui-table-row',
      'px-3',
      'py-ui-table-cell-y',
    ]) {
      expect(empty).not.toHaveClass(defaultClass);
    }
  });

  it('keeps input and file text sizes while allowing a caller height override', () => {
    render(<Input aria-label="搜索" className="h-8" />);

    const input = screen.getByRole('textbox', { name: '搜索' });
    expect(input).toHaveClass(
      'h-8',
      'text-ui-body',
      'text-slate-900',
      'file:text-ui-body',
      'file:text-foreground'
    );
    expect(input).not.toHaveClass('h-control-default');
  });
});
