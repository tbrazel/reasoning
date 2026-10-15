/** Convert conventional LaTeX delimiters to the dollar delimiters understood
 * by remark-math, without changing inline or fenced code. */
export function normalizeMathDelimiters(text: string): string {
  const parts = text.split(/(```[\s\S]*?```|`[^`]*`)/g);
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part;
      return part
        .replace(/\\\(/g, () => "$")
        .replace(/\\\)/g, () => "$")
        .replace(/\\\[/g, () => "\n$$\n")
        .replace(/\\\]/g, () => "\n$$\n");
    })
    .join("");
}
