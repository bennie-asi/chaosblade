/**
 * Boot context — the layout-level boot result (client + active session)
 * shared by every route.
 *
 * Why layout-level: the chat session must survive route navigation.
 * Booting inside the chat route would re-create the session (and drop
 * the conversation store) on every leave-and-return; booting at the
 * root layout means the session lives exactly as long as the app.
 * The SSE stream is safe across navigation too — useStream is driven
 * by the fetch reader promise chain, not an effect, so unmounting the
 * chat page never aborts an in-flight turn (verified at the source:
 * core/src/hooks/useStream.ts has no useEffect cleanup).
 *
 * Multi-session (design §6 会话历史面板): ``activeSessionId`` is the
 * session every turn is routed to; ``switchSession`` re-points it at an
 * existing server-side session, ``resetSession`` creates a fresh one.
 */
import { createContext, useContext } from "react";
import type { BladeClient } from "@blade-ai/core";
import type { LangChoice } from "../i18n-setup";

export interface BootContextValue {
  client: BladeClient;
  /**
   * The session every turn / slash command is routed to. Set by boot
   * (create), resetSession (create new, release old) or switchSession
   * (re-point at an existing one from the session panel).
   */
  activeSessionId: string;
  /**
   * Swap the live session for a fresh one (the "new session" button):
   * create → fire-and-forget delete of the old id → wipe history →
   * re-populate session context. Throws if the create call fails —
   * the old session and history are left untouched in that case.
   * Re-entrant calls NO-OP (resolve immediately) via an internal ref
   * guard in RootLayout — they do not reject.
   */
  resetSession: () => Promise<void>;
  /**
   * Re-point the active session at an EXISTING server-side session
   * (session panel click). Verifies the target exists first (state
   * endpoint), then wipes the conversation view and re-populates the
   * session context. The server holds no message history for the web
   * host, so the chat column reverts to its empty state while the
   * LLM thread continues server-side via the checkpointer.
   *
   * Throws when the target no longer exists (stale list row) — the
   * caller surfaces the failure and the current session stays active.
   * Switching to the already-active id is a no-op. Re-entrant calls
   * NO-OP via an internal ref guard. Mid-turn switches are blocked by
   * the panel's ``streamState !== "idle"`` gate, not here.
   */
  switchSession: (sid: string) => Promise<void>;
  /**
   * Interface-language choice (design §7.5). RootLayout owns the
   * state; changing it re-runs ``configureI18n`` and re-renders the
   * whole tree (the provider's value identity changes), so every
   * render-time ``ui()`` call re-resolves in the new language.
   */
  langChoice: LangChoice;
  setLangChoice: (choice: LangChoice) => void;
}

export const BootContext = createContext<BootContextValue | null>(null);

/** The booted client + active session id (+ reset/switch). Only
 *  callable below the layout's boot gate — the gate guarantees the
 *  value is non-null. */
export function useBoot(): BootContextValue {
  const boot = useContext(BootContext);
  if (!boot) {
    throw new Error("useBoot must be used under RootLayout's boot gate");
  }
  return boot;
}
