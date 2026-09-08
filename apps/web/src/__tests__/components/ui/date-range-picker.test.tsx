import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import type { DateRange } from 'react-day-picker';
import { describe, expect, it, vi } from 'vitest';

import { DateRangePicker } from '@/components/ui/date-range-picker';

function setup() {
  const onChange = vi.fn();
  function Example() {
    const [value, setValue] = useState<DateRange | undefined>({
      from: new Date(2026, 8, 8),
      to: new Date(2026, 8, 10),
    });
    return (
      <DateRangePicker
        value={value}
        onChange={range => {
          onChange(range);
          setValue(range);
        }}
      />
    );
  }
  render(<Example />);
  return { user: userEvent.setup(), onChange };
}

describe('DateRangePicker', () => {
  it('commits edited dates only on confirmation and closes the calendar', async () => {
    const { user, onChange } = setup();
    await user.click(screen.getByLabelText('结束日期'));
    fireEvent.change(screen.getByLabelText('结束日期'), {
      target: { value: '2026-09-15' },
    });
    expect(onChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认' }));
    expect(onChange).toHaveBeenCalledWith({
      from: new Date(2026, 8, 8),
      to: new Date(2026, 8, 15),
    });
    expect(
      screen.queryByRole('button', { name: '确认' })
    ).not.toBeInTheDocument();
  });

  it('discards unconfirmed edits on Escape and clears the committed range', async () => {
    const { user, onChange } = setup();
    await user.click(screen.getByLabelText('结束日期'));
    fireEvent.change(screen.getByLabelText('结束日期'), {
      target: { value: '2026-09-15' },
    });
    await user.keyboard('{Escape}');
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByLabelText('结束日期')).toHaveValue('2026-09-10');
    await user.click(screen.getByLabelText('结束日期'));
    await user.click(screen.getByRole('button', { name: '清空', exact: true }));
    expect(onChange).toHaveBeenCalledWith(undefined);
    expect(screen.getByLabelText('开始日期')).toHaveValue('');
    expect(screen.getByLabelText('结束日期')).toHaveValue('');
  });

  it('blocks incomplete, invalid and reversed date ranges', async () => {
    const { user, onChange } = setup();
    await user.click(screen.getByLabelText('结束日期'));
    for (const value of ['', '2026-02-30', '2026-09-07']) {
      fireEvent.change(screen.getByLabelText('结束日期'), {
        target: { value },
      });
      expect(screen.getByRole('button', { name: '确认' })).toBeDisabled();
    }
    expect(onChange).not.toHaveBeenCalled();
  });
});
