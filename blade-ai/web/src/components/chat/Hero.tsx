/**
 * Empty-conversation hero (mock v2 scene 1): time-of-day greeting +
 * one-line sub + starter chips, rendered by HistoryList while the
 * session has neither committed history nor pending items — the
 * alternative to mock parity here is a blank conversation column,
 * which reads as "broken" rather than "fresh".
 *
 * Chips FILL the Composer draft (``onSuggest``), never send: the user
 * keeps edit/send control, same affordance as mock v2's suggestion
 * row.
 *
 * Mock parity note: the mock's hero also carries a centred input box.
 * The implementation keeps the single bottom Composer instead — its
 * useStream instance owns the SSE lifecycle and must never unmount
 * mid-turn, so the hero covers greeting + chips only and the input
 * stays where the turn machinery lives.
 */
import { ui } from "../../lib/uiText";

const CHIP_KEYS = [
  "heroChipCpu",
  "heroChipNetwork",
  "heroChipRecent",
  "heroChipWhat",
] as const;

/** Time-of-day greeting key — hour bucketing matches common mail-app
 *  convention (<12 morning, <18 afternoon, else evening). Local time:
 *  the greeting is a social nicety, not a cluster fact. */
function greetingKey(): string {
  const h = new Date().getHours();
  if (h < 12) return ui().heroGreetingMorning;
  if (h < 18) return ui().heroGreetingAfternoon;
  return ui().heroGreetingEvening;
}

export function Hero({ onSuggest }: { onSuggest?: (text: string) => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 px-6 py-10 text-center">
      <div className="flex flex-col gap-2">
        <h2 className="text-2xl font-semibold tracking-tight text-forge-text">
          {greetingKey()}
        </h2>
        <p className="text-sm text-forge-text-secondary">{ui().heroSub}</p>
      </div>
      {/* Chips row — capped with a PRESET token, never the
          ``max-w-[Npx]`` arbitrary syntax: that syntax is the
          dedicated carrier of the "chat column follows the rail"
          width rule (780/980), and ChatPage's width tests scan for
          exactly that pattern. Hero content is an inner-layout
          constraint, not a column-width carrier. */}
      <div className="flex max-w-3xl flex-wrap items-center justify-center gap-2">
        {CHIP_KEYS.map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => onSuggest?.(ui()[key])}
            className="rounded-full border border-forge-border bg-forge-card px-3.5 py-1.5 text-xs text-forge-text-secondary transition-colors hover:border-forge-text-faint hover:text-forge-text"
          >
            {ui()[key]}
          </button>
        ))}
      </div>
    </div>
  );
}
