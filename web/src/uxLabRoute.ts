export function isUxLabPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, "") === "/ux-lab";
}
