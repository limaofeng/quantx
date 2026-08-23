import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createClient, Provider, cacheExchange, fetchExchange } from 'urql';

import { MarketIndexCustomizer } from '@/features/dashboard/components/MarketIndexCustomizer';
import {
  CORE_MARKET_INDICES,
  type MarketIndexPreferenceItem,
} from '@/features/dashboard/marketWorkbench';

const client = createClient({
  url: '/graphql',
  exchanges: [cacheExchange, fetchExchange],
});

describe('MarketIndexCustomizer', () => {
  it('supports keyboard-visible configuration changes and returns focus after save', async () => {
    const user = userEvent.setup();
    const items: MarketIndexPreferenceItem[] = CORE_MARKET_INDICES.slice(
      0,
      2
    ).map(definition => ({ ...definition, visible: true }));
    const onSave = vi.fn(() => true);

    render(
      <Provider value={client}>
        <MarketIndexCustomizer
          items={items}
          onSave={onSave}
          storageStatus="available"
        />
      </Provider>
    );

    const trigger = screen.getByRole('button', { name: '定制行情指数' });
    await user.click(trigger);

    const first = items[0];
    expect(screen.getByText('定制行情指数')).toBeVisible();
    await user.click(screen.getByRole('button', { name: `隐藏${first.name}` }));

    const save = screen.getByRole('button', { name: '保存配置' });
    expect(save).toBeEnabled();
    await user.click(save);

    expect(onSave).toHaveBeenCalledWith([
      { ...first, visible: false },
      items[1],
    ]);
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
