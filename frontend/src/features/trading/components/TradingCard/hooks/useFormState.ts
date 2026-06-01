import { useState, useCallback, useMemo } from 'react';

/**
 * 表单基础状态管理
 */
export function useFormState() {
  const [tradeType, setTradeType] = useState<'buy' | 'sell'>('buy');
  const [orderType, setOrderType] = useState('limit');
  const [quantity, setQuantity] = useState('');
  const [price, setPrice] = useState('');

  const resetForm = useCallback(() => {
    setQuantity('');
    setPrice('');
  }, []);

  return useMemo(
    () => ({
      tradeType,
      setTradeType,
      orderType,
      setOrderType,
      quantity,
      setQuantity,
      price,
      setPrice,
      resetForm,
    }),
    [tradeType, orderType, quantity, price, resetForm]
  );
}
