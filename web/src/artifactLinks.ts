/** Artifact-link helpers shared by the chat, ledger, and artifact readers.
 *
 * Existing Markdown links must remain opaque: Codex uses angle-bracketed
 * destinations for absolute paths containing spaces. Bare-path discovery is
 * therefore performed on parsed Markdown text nodes, after links and code have
 * already become distinct AST nodes.
 */

export type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  title?: string | null;
  children?: MarkdownNode[];
};

const ARTIFACT_BARE = /\S*\bartifacts\/[\w-](?:[\w.\-/]*[\w/])?/g;

function decoded(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/** The project-relative artifact name referred to by a path, or null.
 *
 * React Markdown percent-encodes spaces in angle-bracketed destinations, so
 * decoding happens only after the stable `/artifacts/` boundary is found.
 * Arbitrary scheme URLs remain external. `file:` and `sandbox:` are accepted
 * only when they still contain that explicit project-artifact boundary.
 */
export function artifactNameFromPath(path: string): string | null {
  const trimmed = path.trim();
  const scheme = trimmed.match(/^([a-z][a-z0-9+.-]*):/i)?.[1]?.toLowerCase();
  if (scheme && scheme !== "file" && scheme !== "sandbox") return null;

  const match = trimmed.match(/(?:^|\/)artifacts\/(.+)$/);
  if (!match) return null;
  const name = decoded(match[1]).replace(/:\d+(?:-\d+)?$/, "");
  return name || null;
}

/** Decode the private fragment used by bare-path links, or resolve an
 * explicit absolute/relative artifact destination. */
export function artifactNameFromHref(href: string): string | null {
  return href.startsWith("#art-")
    ? decoded(href.slice(5)) || null
    : artifactNameFromPath(href);
}

function artifactFragment(name: string): string {
  return `#art-${encodeURIComponent(name).replace(/%2F/gi, "/")}`;
}

/** Split one ordinary Markdown text node into text and artifact-link nodes. */
export function artifactNodesFromText(value: string): MarkdownNode[] {
  const nodes: MarkdownNode[] = [];
  let cursor = 0;
  ARTIFACT_BARE.lastIndex = 0;

  for (const match of value.matchAll(ARTIFACT_BARE)) {
    const path = match[0];
    const start = match.index;
    const name = artifactNameFromPath(path);
    if (!name) continue;
    if (start > cursor) nodes.push({ type: "text", value: value.slice(cursor, start) });
    const normalized = name.replace(/\/+$/, "");
    const base = normalized.split("/").pop() ?? normalized;
    nodes.push({
      type: "link",
      url: artifactFragment(name),
      title: `artifacts/${name}`,
      children: [{ type: "text", value: base }],
    });
    cursor = start + path.length;
  }

  if (cursor < value.length) nodes.push({ type: "text", value: value.slice(cursor) });
  return nodes.length ? nodes : [{ type: "text", value }];
}

function linkArtifactText(node: MarkdownNode): void {
  if (!node.children || node.type === "link" || node.type === "linkReference") return;

  node.children = node.children.flatMap((child) => {
    if (child.type === "text" && typeof child.value === "string") {
      return artifactNodesFromText(child.value);
    }
    linkArtifactText(child);
    return [child];
  });
}

/** Remark plugin: link bare project-artifact paths without ever rewriting
 * explicit link destinations, inline/fenced code, or math nodes. */
export function remarkArtifactLinks() {
  return (tree: MarkdownNode) => linkArtifactText(tree);
}
