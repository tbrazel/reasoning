export function formatModelPrice(value: number | null, suffix = ""): string {
  if (value === null) return "—";
  if (value === 0) return `free${suffix}`;
  const digits = value < 0.01 ? 4 : value < 1 ? 3 : value < 100 ? 2 : 0;
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: digits })}${suffix}`;
}
