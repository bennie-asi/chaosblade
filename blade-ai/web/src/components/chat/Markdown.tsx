/**
 * Markdown — GFM rendering for agent-authored prose (P2).
 *
 * react-markdown + remark-gfm. NO rehype-raw: raw HTML from the LLM
 * stays inert text — the agent channel is untrusted input, and the
 * component allowlist below is the full render surface.
 *
 * Forge mapping (design doc §5.3):
 *   - code blocks use the LIGHT gray well (bg-forge-code) — dark code
 *     blocks in a light UI read as "visual black holes"
 *   - links are forge-accent + open in a new tab (noopener)
 *   - headings are modest (h3 ceiling) — agent prose is chat, not a
 *     document; oversized headings shout over the conversation
 *   - everything else stays at body size with restrained spacing
 *
 * Streaming: the agent message re-renders per token; react-markdown
 * re-parses each time. That's the same trade every chat UI makes and
 * is fine at our message sizes (KBs, not MBs).
 */
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

const components: Components = {
  // Code: inline spans get the gray well chip; fenced blocks get the
  // well card via <pre>. The ``[&_code]:`` resets strip the inline
  // chip styling when a <code> lands inside a <pre> (react-markdown
  // nests them), so block code never shows double backgrounds.
  code: ({ children }) => (
    <code className="rounded bg-forge-code px-1 py-0.5 font-mono text-[0.85em] text-forge-text">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-card border border-forge-border bg-forge-code p-3 font-mono text-xs leading-5 [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit">
      {children}
    </pre>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-forge-accent underline underline-offset-2"
    >
      {children}
    </a>
  ),
  p: ({ children }) => <p className="my-2 first:my-0 last:my-0">{children}</p>,
  ul: ({ children }) => (
    <ul className="my-2 list-disc pl-5 first:my-0 last:my-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2 list-decimal pl-5 first:my-0 last:my-0">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="my-0.5">{children}</li>,
  h1: ({ children }) => (
    <h3 className="mt-3 mb-1 text-base font-semibold first:mt-0">
      {children}
    </h3>
  ),
  h2: ({ children }) => (
    <h3 className="mt-3 mb-1 text-base font-semibold first:mt-0">
      {children}
    </h3>
  ),
  h3: ({ children }) => (
    <h3 className="mt-3 mb-1 text-sm font-semibold first:mt-0">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="mt-2 mb-1 text-sm font-semibold first:mt-0">
      {children}
    </h4>
  ),
  h5: ({ children }) => (
    <h5 className="mt-2 mb-1 text-sm font-semibold first:mt-0">
      {children}
    </h5>
  ),
  h6: ({ children }) => (
    <h6 className="mt-2 mb-1 text-sm font-semibold first:mt-0">
      {children}
    </h6>
  ),
  // Agent-supplied images must never blow out the message column.
  img: ({ src, alt }) => (
    <img src={src} alt={alt} className="my-2 max-w-full rounded-card" />
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-forge-border pl-3 text-forge-text-secondary">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-forge-border" />,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-forge-border bg-forge-code px-2 py-1 text-left font-medium">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-forge-border px-2 py-1 align-top">
      {children}
    </td>
  ),
};

export function Markdown({ text }: { text: string }) {
  return (
    // remark-breaks: single newlines become <br>. Without it, plain
    // multi-line agent text ("集群: x\n命名空间: y") collapses onto
    // one line — a regression vs the pre-markdown whitespace-pre-wrap
    // renderer this component replaced.
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      components={components}
    >
      {text}
    </ReactMarkdown>
  );
}
