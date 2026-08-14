export function createClientId(prefix = 'client') {
  const cryptoApi =
    typeof globalThis.crypto === 'undefined' ? undefined : globalThis.crypto;

  if (typeof cryptoApi?.randomUUID === 'function') {
    return cryptoApi.randomUUID();
  }

  if (typeof cryptoApi?.getRandomValues === 'function') {
    const values = cryptoApi.getRandomValues(new Uint32Array(4));
    const suffix = Array.from(values, value =>
      value.toString(16).padStart(8, '0')
    ).join('');
    return `${prefix}-${suffix}`;
  }

  return `${prefix}-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2)}`;
}
