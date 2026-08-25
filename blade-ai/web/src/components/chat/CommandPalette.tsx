/**
 * ⌘K command palette — the web counterpart of the TUI's SlashMenu.
 *
 * The TUI grows an inline dropdown under the InputPrompt while the
 * buffer starts with ``/``; the web maps that surface to a modal
 * palette (design doc §6: "SlashMenu → ⌘K 命令面板, commands.ts
 * 完整复用"). One shared registry feeds both hosts.
 *
 * Contract:
 *   - ⌘K (mac) / Ctrl+K (win/linux) toggles; Esc or a backdrop click
 *     closes. The global listener lives here so the palette works
 *     regardless of which element has focus.
 *   - Focus is TRAPPED inside the dialog: the search input is the
 *     only tabbable element (row buttons are ``tabIndex={-1}`` — the
 *     cmdk pattern: arrows move the SELECTION, never focus), and Tab
 *     bounces back to the input. Esc / arrows / Enter are handled at
 *     panel level, so they keep working wherever focus sits inside.
 *   - Enter FILLS the composer with ``/name `` and returns focus —
 *     it never executes. Same "apply" semantics as the TUI SlashMenu:
 *     most commands take arguments, and filling first keeps the
 *     destructive ones (/clear, /exit) behind an explicit send.
 *   - Subcommands are flattened into the same list (``/skills list``)
 *     because the web has no inline sub-menu to fall back to.
 *   - Flat group-ordered list, NO section headers — the TUI dropped
 *     them because header rows were visually indistinguishable from
 *     the focused row; the group ordering alone carries hierarchy.
 *   - Filter ranks name-prefix > alias-prefix > name-substring >
 *     description-substring; hidden commands stay hidden.
 *
 * Controlled component: the parent owns ``open`` so the composer's
 * ⌘K hint chip can open the palette too.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";
import {
  SLASH_GROUP_ORDER,
  type SlashCommandRegistry,
} from "@blade-ai/core";
import { ui } from "../../lib/uiText";

// ── row model ────────────────────────────────────────────────────────

interface PaletteRow {
  /** React key + identity — "skills" for roots, "skills/list" for subs. */
  key: string;
  /** Display text, e.g. ``/skills list``. */
  name: string;
  /** What Enter puts into the composer (trailing space when the
   *  command expects arguments or a subcommand pick). */
  fill: string;
  description: string;
  aliases: readonly string[];
  /** Index into SLASH_GROUP_ORDER of the ROOT command — keeps the
   *  flat list group-ordered without rendering headers. */
  groupRank: number;
}

/** Flatten the registry (roots + subs) in SLASH_GROUP_ORDER. Hidden
 *  commands are excluded by ``listByGroup()``'s default. */
function buildRows(registry: SlashCommandRegistry): PaletteRow[] {
  const groups = registry.listByGroup();
  const rows: PaletteRow[] = [];
  SLASH_GROUP_ORDER.forEach((group, groupRank) => {
    for (const cmd of groups[group]) {
      const expectsMore = !!cmd.usage || !!cmd.subcommands;
      rows.push({
        key: cmd.name,
        name: `/${cmd.name}`,
        fill: `/${cmd.name}${expectsMore ? " " : ""}`,
        description: cmd.description,
        aliases: cmd.aliases ?? [],
        groupRank,
      });
      if (cmd.subcommands) {
        const subs = Object.values(cmd.subcommands).sort((a, b) =>
          a.name.localeCompare(b.name),
        );
        for (const sub of subs) {
          rows.push({
            key: `${cmd.name}/${sub.name}`,
            name: `/${cmd.name} ${sub.name}`,
            // Subs always take a trailing space: either they take
            // args (``usage``) or the user is done picking and the
            // next keystroke starts the argument.
            fill: `/${cmd.name} ${sub.name} `,
            description: sub.description,
            aliases: [],
            groupRank,
          });
        }
      }
    }
  });
  return rows;
}

/** Lower rank = better match; -1 = filtered out. Prefix on the
 *  command path beats substring beats description substring, so
 *  typing "mo" puts ``/mode`` above a command that merely mentions
 *  "mode" in its description. */
function rankRow(row: PaletteRow, query: string): number {
  if (!query) return 0;
  const path = row.name.slice(1); // strip leading "/"
  if (path.startsWith(query)) return 0;
  if (row.aliases.some((a) => a.startsWith(query))) return 1;
  if (path.includes(query)) return 2;
  if (row.description.toLowerCase().includes(query)) return 3;
  return -1;
}

function filterRows(rows: PaletteRow[], query: string): PaletteRow[] {
  const q = query.trim().toLowerCase().replace(/^\//, "");
  return rows
    .map((row, index) => ({ row, index, rank: rankRow(row, q) }))
    .filter((e) => e.rank >= 0)
    // Stable sort: equal ranks keep group order then name order.
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((e) => e.row);
}

// ── component ────────────────────────────────────────────────────────

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  registry: SlashCommandRegistry;
  /** Called with the fill text when the user picks a row. The parent
   *  puts it into the composer input and returns focus there. */
  onPick: (fill: string) => void;
}

export function CommandPalette({
  open,
  onOpenChange,
  registry,
  onPick,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const rows = useMemo(() => buildRows(registry), [registry]);
  const filtered = useMemo(() => filterRows(rows, query), [rows, query]);

  // Global toggle. ⌘K on macOS, Ctrl+K elsewhere — listen for both on
  // every platform so a remote-desktop mixup never strands the user.
  // ``e.repeat`` guard: holding the chord would otherwise strobe the
  // panel open/closed at key-repeat rate.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.repeat) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onOpenChange(!open);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  // Focus management, both directions. The previous element must be
  // captured BEFORE the search input takes focus — which is why the
  // input does NOT use the ``autoFocus`` attribute (it would steal
  // focus during the commit phase, before this passive effect runs,
  // and we'd capture the search input itself). Capture-then-focus in
  // one effect; on close (cleanup) the previous element — usually the
  // composer textarea — gets focus back on EVERY dismiss path (Esc /
  // backdrop / pick). The pick path's own focus+cursor placement runs
  // in a rAF after this cleanup, so it wins the final cursor position.
  const previousFocusRef = useRef<Element | null>(null);
  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement;
    inputRef.current?.focus();
    return () => {
      const prev = previousFocusRef.current;
      if (prev instanceof HTMLElement) prev.focus();
      previousFocusRef.current = null;
    };
  }, [open]);

  // Fresh state on every open — a palette that remembers a stale
  // query from three minutes ago reads as a bug, not a feature.
  useEffect(() => {
    if (open) {
      setQuery("");
      setSelected(0);
    }
  }, [open]);

  // Clamp the selection when filtering shrinks the list.
  useEffect(() => {
    if (selected >= filtered.length) {
      setSelected(Math.max(0, filtered.length - 1));
    }
  }, [filtered.length, selected]);

  // Keep the selected row visible while keyboard-navigating.
  // (Optional-call: jsdom doesn't implement scrollIntoView.)
  useEffect(() => {
    if (!open) return;
    itemRefs.current[selected]?.scrollIntoView?.({ block: "nearest" });
  }, [open, selected]);

  if (!open) return null;

  const pick = (row: PaletteRow) => {
    onOpenChange(false);
    onPick(row.fill);
  };

  // Panel-level key handling. Attached to the panel container (not
  // the input) so Esc / arrows / Enter keep working even if focus has
  // drifted onto a row button (mouse click can focus one despite
  // tabIndex=-1); events from the input bubble up identically.
  const onPanelKeyDown = (e: React.KeyboardEvent) => {
    // CJK IME confirmation keystrokes must not move the selection or
    // pick a row — same guard as the composer's Enter handling.
    if (e.nativeEvent.isComposing) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (filtered.length > 0) {
        setSelected((s) => (s + 1) % filtered.length);
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (filtered.length > 0) {
        setSelected((s) => (s - 1 + filtered.length) % filtered.length);
      }
    } else if (e.key === "Enter") {
      // A focused row button fires its own click on Enter — let the
      // native activation pick THAT row; handling it here too would
      // pick twice (and possibly the wrong row).
      if (e.target instanceof HTMLButtonElement) return;
      e.preventDefault();
      const row = filtered[selected];
      if (row) pick(row);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onOpenChange(false);
    } else if (e.key === "Tab") {
      // Focus trap. The search input is the ONLY tabbable element in
      // the dialog (row buttons are tabIndex=-1), so trapping reduces
      // to bouncing Tab / Shift+Tab back to it.
      e.preventDefault();
      inputRef.current?.focus();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]"
      role="dialog"
      aria-modal="true"
      aria-label={ui().paletteKbdLabel}
    >
      {/* Backdrop — click anywhere outside the panel to dismiss. */}
      <div
        className="absolute inset-0 bg-forge-ink/20"
        onClick={() => onOpenChange(false)}
      />
      <div
        className="palette-in relative flex max-h-[60vh] w-full max-w-xl flex-col overflow-hidden rounded-card border border-forge-border bg-forge-card shadow-float"
        onKeyDown={onPanelKeyDown}
      >
        <div className="flex items-center gap-2 border-b border-forge-border px-4">
          <Search className="size-4 shrink-0 text-forge-text-faint" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected(0);
            }}
            placeholder={ui().palettePlaceholder}
            className="w-full bg-transparent py-3 text-sm text-forge-text outline-none placeholder:text-forge-text-faint"
          />
        </div>
        <ul className="flex-1 overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <li className="px-4 py-6 text-center text-sm text-forge-text-faint">
              {ui().paletteEmpty}
            </li>
          ) : (
            filtered.map((row, i) => {
              const isSelected = i === selected;
              return (
                <li key={row.key}>
                  <button
                    type="button"
                    // Mouse-only target: keyboard users drive the
                    // selection with arrows (the cmdk pattern), and
                    // keeping rows out of the tab order is what makes
                    // the focus trap a one-liner.
                    tabIndex={-1}
                    ref={(el) => {
                      itemRefs.current[i] = el;
                    }}
                    onClick={() => pick(row)}
                    onMouseMove={() => {
                      if (!isSelected) setSelected(i);
                    }}
                    className={`flex w-full items-baseline gap-3 px-4 py-2 text-left ${
                      isSelected ? "bg-forge-accent-soft" : ""
                    }`}
                  >
                    <span
                      className={`shrink-0 font-mono text-sm ${
                        isSelected
                          ? "font-medium text-forge-accent"
                          : "text-forge-text"
                      }`}
                    >
                      {row.name}
                    </span>
                    <span className="truncate text-xs text-forge-text-secondary">
                      {row.description}
                    </span>
                  </button>
                </li>
              );
            })
          )}
        </ul>
        <div className="border-t border-forge-border px-4 py-2 text-[11px] text-forge-text-faint">
          {ui().paletteHint}
        </div>
      </div>
    </div>
  );
}
