/**
 * Message stream: history (committed) + pending (in-flight turn items),
 * rendered through the kind dispatcher. Auto-scrolls to the bottom on
 * new content, but only while the user is already near the bottom —
 * scrolling up to read history must not yank them back down on every
 * streaming token.
 *
 * Node-progress lines (tagged logs) are filtered out while the right
 * rail owns them (ProgressRailContext = drill live + expanded + wide
 * viewport); the rail timeline renders them instead. When the rail is
 * away, the lines stay here — the LivePhaseStepper fallback rule,
 * generalised.
 *
 * Empty state (mock v2 scene 1): while the session has neither
 * committed history nor pending items, the hero (greeting + starter
 * chips) renders instead of a blank column. Pending counts too — a
 * turn whose user echo has landed is already a conversation.
 */
import { useEffect, useRef } from "react";
import { useAppSelector } from "@blade-ai/core";
import { useProgressRailVisible } from "../rail/ProgressRailContext";
import { isNodeProgressItem } from "../../lib/nodeProgress";
import { Hero } from "./Hero";
import { MessageView } from "./messages";
import { LiveThinkingPanel } from "./ThinkingPanel";

const NEAR_BOTTOM_PX = 80;

export interface HistoryListProps {
  /** Hero chip click → fill the Composer draft (never auto-send). */
  onSuggest?: (text: string) => void;
}

export function HistoryList({ onSuggest }: HistoryListProps = {}) {
  const history = useAppSelector((s) => s.history);
  const pending = useAppSelector((s) => s.pending);
  const railVisible = useProgressRailVisible();
  const scrollRef = useRef<HTMLDivElement>(null);
  const nearBottomRef = useRef(true);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    nearBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  };

  useEffect(() => {
    const el = scrollRef.current;
    if (el && nearBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [history, pending]);

  const items = [...history, ...pending].filter(
    (item) => !(railVisible && isNodeProgressItem(item)),
  );

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      className="min-h-0 flex-1 overflow-y-auto"
    >
      <div
        className={`mx-auto flex flex-col gap-3 px-4 py-6 ${
          railVisible ? "max-w-[780px]" : "max-w-[980px]"
        } ${items.length === 0 ? "h-full" : ""}`}
      >
        {items.length === 0 ? (
          <Hero onSuggest={onSuggest} />
        ) : (
          <>
            {items.map((item) => (
              <MessageView key={item.id} item={item} />
            ))}
            {/* Live CoT fold — sits at the tail of the stream, exactly
                where the committed duration chip will land when the
                thinking session ends. Renders nothing when idle. */}
            <LiveThinkingPanel />
          </>
        )}
      </div>
    </div>
  );
}
