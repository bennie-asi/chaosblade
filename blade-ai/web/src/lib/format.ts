/**
 * Small pure formatters shared across routes (chat messages AND the
 * tasks table). Lives in lib/ so app-layer pages don't import the chat
 * component module just for a duration string.
 */

/** "61s" → "1m01s"; sub-second → "<1s". Matches the TUI's compact
 *  duration style used in thinking chips and task rows. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return "<1s";
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m${s.toString().padStart(2, "0")}s`;
}

/** Compact token count — ≥1000 collapses to ``X.Yk`` so a chunky
 *  ``"6273 tokens"`` shrinks to ``"6.3k tokens"``; sub-1000 stays
 *  exact. Mirrors the TUI TurnUsageMessage convention so the two
 *  hosts' metadata rows read identically. */
export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(1)}k`;
}

/** Local-timezone ``MM-DD HH:MM:SS`` for the turn-usage row's commit
 *  stamp. ``formatToParts`` + manual assembly pins the part order
 *  (Intl locales disagree on ordering); built once at module level. */
const LOCAL_TIME_FMT = new Intl.DateTimeFormat("en-GB", {
  hour12: false,
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

export function formatLocalTime(ms: number): string {
  const parts: Record<string, string> = {};
  for (const p of LOCAL_TIME_FMT.formatToParts(ms)) {
    parts[p.type] = p.value;
  }
  return `${parts["month"]}-${parts["day"]} ${parts["hour"]}:${parts["minute"]}:${parts["second"]}`;
}

/** ISO-8601 → local ``HH:MM:SS`` — the boot doctor card's header
 *  tail (boot snapshot, same-day so the date would be noise).
 *  Unparseable input falls back to the raw string, mirroring the
 *  TUI's formatter. */
export function formatTimeOfDay(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** ISO-8601 → local ``YYYY-MM-DD HH:MM:SS`` — the header tail of the
 *  on-demand info cards (/doctor, /help, /session, /experiments,
 *  /model, /memory): the date prefix is locale-neutral and sortable
 *  so stacked cards across days read in obvious chronological order.
 *  Unparseable input falls back to the raw string. */
export function formatDateTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** "2026-08-18 10:11:12" / ISO → "08-18 10:11". Slice-based, not
 *  Date-parsed: the server's gmt_create is already local wall time.
 *  Shared by the tasks table and the trace page's task list. */
export function formatCreated(raw: string): string {
  if (!raw) return "—";
  const norm = raw.replace("T", " ");
  return norm.length >= 16 ? norm.slice(5, 16) : raw;
}
