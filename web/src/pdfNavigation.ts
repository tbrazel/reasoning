export type PdfPageBounds = {
  pageNumber: number;
  top: number;
  bottom: number;
};

/** Select the page containing the reading marker, or the nearest page when
 * the marker falls in the gap between pages. Page numbers are one-based. */
export function nearestPdfPage(bounds: PdfPageBounds[], marker: number): number {
  let selected = bounds[0]?.pageNumber ?? 1;
  let closest = Number.POSITIVE_INFINITY;

  for (let index = 0; index < bounds.length; index += 1) {
    const page = bounds[index];
    const distance = marker < page.top
      ? page.top - marker
      : marker > page.bottom
        ? marker - page.bottom
        : 0;
    if (distance < closest) {
      closest = distance;
      selected = page.pageNumber;
    }
    if (distance === 0 || (page.top > marker && closest < distance)) break;
  }
  return selected;
}
