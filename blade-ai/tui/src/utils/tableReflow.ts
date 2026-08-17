/**
 * Wide-table guard for terminal markdown rendering.
 *
 * marked-terminal renders GFM tables at their NATURAL width — the
 * ``width`` option only drives paragraph reflow, never tables. A
 * table whose longest cells push it past the terminal width (e.g.
 * the 云盘 drill's 70+ char PVC names produce a ~339-column table)
 * then hits ``<Text wrap="wrap">`` in AgentMessage, which hard-breaks
 * every over-long line mid-cell: the box-drawing frame shreds into
 * misaligned fragments (the "换行不对" bug).
 *
 * Fix strategy: BEFORE handing text to marked, detect each GFM table
 * and measure its natural width. Tables that fit render as real
 * boxes; tables that don't are rewritten as a vertical record list
 * (one bullet per row, one nested bullet per column — mysql ``\G``
 * spirit) which reflows cleanly at ANY width with zero information
 * loss.
 *
 * Streaming safety: the reducer keeps the RAW markdown in the
 * pending item and re-renders from scratch on every token, so this
 * transform is pure/stateless — a half-streamed table is simply
 * re-evaluated each frame. Fenced code blocks are skipped so a
 * table-shaped string inside ``` fences is never touched.
 */

/** Visual width of a string — CJK glyphs occupy 2 terminal cells
 *  but count as 1 JS character; without compensation the width
 *  heuristic underestimates tables with Chinese headers and leaves
 *  them unconverted. Coverage is the common East-Asian ranges;
 *  anything exotic falls back to 1 cell, which only makes the
 *  threshold slightly optimistic. Exported so the post-render
 *  shred guard in markdown.ts measures lines the same way. */
export function visualLen(s: string): number {
  let n = 0;
  for (const ch of s) {
    const code = ch.codePointAt(0) ?? 0;
    const wide =
      (code >= 0x2e80 && code <= 0x9fff) || // CJK radicals..unified
      (code >= 0xf900 && code <= 0xfaff) || // CJK compatibility
      (code >= 0xff00 && code <= 0xffef); // fullwidth forms
    n += wide ? 2 : 1;
  }
  return n;
}

interface ParsedTable {
  header: string[];
  rows: string[][];
  /** EXCLUSIVE end index into the source line array. */
  end: number;
}

/** Whether ``line`` looks like the ``| --- | :--: |`` separator that
 *  distinguishes a real GFM table from a stray line of prose that
 *  happens to contain pipes. Requires at least one dash. */
function isSeparatorLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("-")) return false;
  if (!trimmed.includes("|")) return false;
  return trimmed.replace(/[|\s:-]/g, "") === "";
}

/** Split a ``| a | b |`` row into trimmed cells. Strips the outer
 *  pipes and honours ``\|`` escapes inside cell text. */
function splitCells(line: string): string[] {
  let body = line.trim();
  if (body.startsWith("|")) body = body.slice(1);
  if (body.endsWith("|")) body = body.slice(0, -1);
  const cells: string[] = [];
  let current = "";
  for (let i = 0; i < body.length; i++) {
    const ch = body[i];
    if (ch === "\\" && body[i + 1] === "|") {
      current += "|";
      i++;
    } else if (ch === "|") {
      cells.push(current.trim());
      current = "";
    } else {
      current += ch;
    }
  }
  cells.push(current.trim());
  return cells;
}

/** Whether ``line`` is a table data row: pipes present, and it isn't
 *  the separator. A partial row still streaming (no trailing pipe,
 *  or mid-cell) simply fails this check and ends the table for the
 *  current frame — the next token re-evaluates. */
function isDataRow(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) return false;
  if (isSeparatorLine(trimmed)) return false;
  return splitCells(trimmed).length >= 1;
}

/** Attempt to parse a GFM table starting at ``lines[start]``.
 *  Requires the canonical header + separator pair, then greedily
 *  collects data rows. Returns null when no table starts here. */
function tryParseTable(lines: string[], start: number): ParsedTable | null {
  const headerLine = lines[start];
  const sepLine = lines[start + 1];
  if (headerLine === undefined || sepLine === undefined) return null;
  const headerTrim = headerLine.trim();
  if (!headerTrim.startsWith("|") || !headerTrim.endsWith("|")) return null;
  if (!isSeparatorLine(sepLine)) return null;

  const header = splitCells(headerTrim);
  const rows: string[][] = [];
  let end = start + 2;
  while (end < lines.length) {
    const rowLine = lines[end]!;
    if (!isDataRow(rowLine)) break;
    rows.push(splitCells(rowLine.trim()));
    end++;
  }
  return { header, rows, end };
}

/** Rendered width of the table as marked-terminal would draw it:
 *  per column ``max(cell) + 2`` padding (floor of 4 — the box style
 *  never narrows below ``│ x │``), plus one border glyph per column
 *  plus the closing border. */
function naturalWidth(header: string[], rows: string[][]): number {
  const ncols = header.length;
  const widths = new Array<number>(ncols).fill(0);
  for (const row of [header, ...rows]) {
    for (let c = 0; c < ncols; c++) {
      widths[c] = Math.max(widths[c]!, visualLen(row[c] ?? ""));
    }
  }
  return widths.reduce((sum, w) => sum + Math.max(w + 2, 4), 0) + ncols + 1;
}

/** Vertical record form: each data row becomes a plain paragraph of
 *  ``label: value`` lines indented with ideographic spaces (they
 *  survive reflow and keep the record block visually grouped).
 *  Paragraphs — NOT list items — because marked-terminal's
 *  ``reflowText`` only wraps plain paragraphs; list-item content
 *  stays on one line and a 90-char PVC name would overflow again.
 *  Header cells double as field labels; an empty header falls back
 *  to a positional label so no column goes unnamed. */
function toRecords(header: string[], rows: string[][]): string {
  const blocks = rows.map((row) => {
    const fields = header.map((h, idx) => {
      const label = h || `col ${idx + 1}`;
      const value = row[idx] ?? "";
      return `**${label}**: ${value}`;
    });
    const [first, ...rest] = fields;
    return ["　" + first, ...rest.map((f) => "　" + f)].join("  \n");
  });
  // Blank line between records so marked keeps them as separate
  // paragraphs (and the reducer's \n\n split point stays valid).
  return blocks.join("\n\n");
}

/** Rewrite every over-wide GFM table in ``text`` into record form.
 *  Tables at or under ``width`` pass through untouched, as does all
 *  content inside fenced code blocks. */
export function reflowWideTables(text: string, width: number): string {
  if (!text || !text.includes("|")) return text;
  const lines = text.split("\n");
  const out: string[] = [];
  let i = 0;
  let inFence = false;
  while (i < lines.length) {
    const line = lines[i]!;
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      out.push(line);
      i++;
      continue;
    }
    if (inFence) {
      out.push(line);
      i++;
      continue;
    }
    const table = tryParseTable(lines, i);
    if (table) {
      if (naturalWidth(table.header, table.rows) > width) {
        out.push(toRecords(table.header, table.rows));
      } else {
        out.push(...lines.slice(i, table.end));
      }
      i = table.end;
      continue;
    }
    out.push(line);
    i++;
  }
  return out.join("\n");
}
