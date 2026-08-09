export function safeInternalPath(value: string | null | undefined): string {
  const normalized = (value || '').trim();
  if (
    !normalized.startsWith('/') ||
    normalized.startsWith('//') ||
    normalized.startsWith('/login')
  ) {
    return '/';
  }
  return normalized;
}
