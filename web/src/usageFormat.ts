export function estimateLabel(value: number | null | undefined): string {
  return value == null ? "—" : value === 0 ? "0" : value.toLocaleString(undefined, { maximumSignificantDigits: 3 });
}
