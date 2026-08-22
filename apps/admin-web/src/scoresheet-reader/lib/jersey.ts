export function isValidJerseyNumber(value: string, allowBlank = true): boolean {
  const normalized = value.trim();
  if (!normalized) return allowBlank;
  if (normalized === '0' || normalized === '00') return true;
  return /^[1-9]\d?$/.test(normalized) && Number(normalized) <= 99;
}
