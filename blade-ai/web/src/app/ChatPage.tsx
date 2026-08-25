/**
 * Chat route — the conversation surface: session panel (column 2),
 * conversation column (history stream, composer, status bar) and the
 * right progress rail.
 *
 * Boot, session and store all live in the root layout (see
 * RootLayout's docstring for the why); this page only consumes the
 * booted client via useBoot(). Navigating away and back remounts this
 * component but resumes the same session and the same conversation.
 *
 * Rail visibility is decided HERE and distributed through
 * ProgressRailContext (design §7.2 零重复 + mock v2 width rule):
 * the rail is on screen only when a drill is live AND the user hasn't
 * collapsed it AND the viewport is ≥1250px. The same boolean gates
 * HistoryList's node-progress fallback, LivePhaseStepper's fallback,
 * and the chat column's 980px ↔ 780px width switch — one rule, three
 * consumers, so the fallbacks can never disagree with what's actually
 * rendered.
 */
import { useBoot } from "./bootContext";
import { HistoryList } from "../components/chat/HistoryList";
import { Composer } from "../components/chat/Composer";
import { SessionPanel } from "../components/chat/SessionPanel";
import { StatusBar } from "../components/StatusBar";
import { ProgressRail } from "../components/rail/ProgressRail";
import { ProgressRailContext } from "../components/rail/ProgressRailContext";
import { useRailCollapsed } from "../components/rail/railStore";
import { useActiveDrill } from "../components/rail/useActiveDrill";
import { RAIL_MIN_WIDTH_PX } from "../lib/nodeProgress";
import { useMediaQuery } from "../lib/useMediaQuery";
import { useCallback, useState } from "react";

export function ChatPage() {
  const { client, activeSessionId } = useBoot();
  const { isLive } = useActiveDrill(client);
  const [railCollapsed] = useRailCollapsed();
  const viewportWide = useMediaQuery(`(min-width: ${RAIL_MIN_WIDTH_PX}px)`);
  const railVisible = isLive && !railCollapsed && viewportWide;

  // Hero chip → Composer draft bridge. The chip lives in HistoryList
  // (empty state), the draft in the Composer — state here is the
  // shortest path that respects both components' lifecycles (the
  // Composer is keyed by session id and remounts on switch, which a
  // shared child-ref would not survive).
  const [draftSeed, setDraftSeed] = useState({ text: "", seq: 0 });
  const onSuggest = useCallback(
    (text: string) => setDraftSeed((prev) => ({ text, seq: prev.seq + 1 })),
    [],
  );

  return (
    <ProgressRailContext.Provider value={railVisible}>
      <div className="flex h-full">
        <SessionPanel />
        <div className="flex h-full min-w-0 flex-1 flex-col">
          <HistoryList onSuggest={onSuggest} />
          {/* key remounts the Composer on session switch (session panel
              "new session" / entry click): the draft is wiped and
              useStream re-binds to the new session id instead of keeping
              callbacks closed over the old one. */}
          <Composer
            key={activeSessionId}
            client={client}
            sessionId={activeSessionId}
            draftSeed={draftSeed}
          />
          <StatusBar />
        </div>
        {/* Right progress rail (design §7.2) — renders only while a
            drill is live; the viewport gate lives in railVisible, so
            the rail's own markup needs no responsive classes. */}
        {viewportWide && <ProgressRail client={client} />}
      </div>
    </ProgressRailContext.Provider>
  );
}
