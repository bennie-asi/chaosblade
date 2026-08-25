/**
 * Root layout — owns the app-lifetime resources (boot, session, store)
 * and the two-region chrome (sidebar + routed content).
 *
 * Why boot lives here and not in the chat route: the conversation must
 * survive route navigation. Booting inside ChatPage would re-create
 * the session (and a remounted StoreProvider would drop the history)
 * on every leave-and-return; at the root layout both live exactly as
 * long as the app. The SSE stream is safe across navigation too —
 * useStream is driven by the fetch reader promise chain, not an
 * effect, so unmounting the chat page never aborts an in-flight turn
 * (verified at the source: core/src/hooks/useStream.ts has no
 * useEffect cleanup).
 *
 * Boot runs once per mount under a ref guard (StrictMode double-invokes
 * effects in dev; without the guard we'd create two sessions).
 *
 * Session lifecycle owned here, in both directions: pagehide deletes
 * the live session on real unload (bfcache-exempt), and
 * ``resetSession`` (exposed via BootContext, wired to the session
 * panel's new-session button) swaps in a fresh session — create first,
 * then fire-and-forget delete of the old id, wipe history, re-populate
 * session context. ``switchSession`` re-points the active id at an
 * EXISTING server-side session (session panel click) without deleting
 * anything — the previous session stays in the list for a later
 * switch-back, and the embedded server's exit reaps it eventually.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
} from "react";
import { Outlet } from "@tanstack/react-router";
import {
  StoreProvider,
  useAppDispatch,
  configureI18n,
  type BladeClient,
  type Action,
} from "@blade-ai/core";
import { bootSession, type BootResult } from "../lib/boot";
import { ui } from "../lib/uiText";
import { asString } from "../lib/utils";
import {
  LANG_STORAGE_KEY,
  readLangChoice,
  resolveLang,
  type LangChoice,
} from "../i18n-setup";
import { BootContext } from "./bootContext";
import { RailNav } from "./RailNav";

/** Populate the store's session context (cluster/namespace/model) for
 *  the status bar. Best-effort: a state-endpoint failure must not
 *  block the chat surface — fall back to empty fields. Shared by the
 *  boot path, the new-session reset path and the session-switch path
 *  (which passes its already-fetched state as ``prefetched`` to skip
 *  a second round-trip).
 */
async function dispatchSessionContext(
  dispatch: Dispatch<Action>,
  client: BladeClient,
  sessionId: string,
  prefetched?: Record<string, unknown>,
): Promise<void> {
  try {
    const sessionState = prefetched ?? (await client.getSessionState(sessionId));
    dispatch({
      type: "SESSION_INITIALIZED",
      session: {
        id: sessionId,
        cluster: asString(sessionState["cluster"]),
        namespace: asString(sessionState["namespace"]) || "default",
        modelName: asString(sessionState["model_name"]),
      },
    });
  } catch {
    dispatch({
      type: "SESSION_INITIALIZED",
      session: { id: sessionId, cluster: "", namespace: "", modelName: "" },
    });
  }
}

/** Boots the session, then renders the gated app shell. Kept as an
 *  inner component because the boot effect dispatches into the store —
 *  StoreProvider must already be above it. */
function BootGate() {
  const [boot, setBoot] = useState<BootResult | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [langChoice, setLangChoiceState] = useState<LangChoice>(readLangChoice);
  const startedRef = useRef(false);
  const resettingRef = useRef(false);
  const switchingRef = useRef(false);
  const dispatch = useAppDispatch();

  // Interface-language switch (settings page, design §7.5): persist the
  // choice, re-run configureI18n, then setState so the provider value's
  // identity flips and the whole tree re-renders — every render-time
  // ui() call re-resolves in the new language. (Module-level snapshots,
  // if any, stay stale until reload; render-time calls dominate.)
  const setLangChoice = useCallback((choice: LangChoice) => {
    try {
      if (choice === "browser") {
        localStorage.removeItem(LANG_STORAGE_KEY);
      } else {
        localStorage.setItem(LANG_STORAGE_KEY, choice);
      }
    } catch {
      // Storage disabled — the in-memory switch below still applies.
    }
    configureI18n(resolveLang(choice));
    setLangChoiceState(choice);
  }, []);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    const ac = new AbortController();
    bootSession(ac.signal)
      .then(async (result) => {
        // Unmounted while booting (e.g. HMR): drop the result instead
        // of setState on a dead component. The catch branch below
        // already guards; keep the success path symmetric.
        if (ac.signal.aborted) return;
        await dispatchSessionContext(dispatch, result.client, result.sessionId);
        setBoot(result);
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setBootError(err instanceof Error ? err.message : String(err));
      });
    return () => ac.abort();
  }, [dispatch]);

  // New-session reset (sidebar "+" button). Create → wipe → swap:
  // the new id is created FIRST (failure leaves the old session and
  // its history untouched), then the conversation clears and the
  // boot context swaps to the fresh id. The OLD session is NOT
  // deleted — the web sidebar keeps every session for switch-back
  // (mock v2 semantics; the TUI "/new" drop-old contract does not
  // apply here). Cleanup of switched-away sessions is the user's
  // explicit per-entry delete in the session panel, plus the
  // pagehide beacon for the active one on unload.
  const resetSession = useCallback(async () => {
    if (!boot || resettingRef.current) return;
    resettingRef.current = true;
    try {
      const newId = await boot.client.createSession();
      dispatch({ type: "HISTORY_CLEARED" });
      await dispatchSessionContext(dispatch, boot.client, newId);
      setBoot({ client: boot.client, sessionId: newId });
    } finally {
      resettingRef.current = false;
    }
  }, [boot, dispatch]);

  // Session switch (session panel click). Unlike resetSession this
  // deletes NOTHING — the previous session stays server-side for a
  // later switch-back. The target's existence is verified BEFORE any
  // local state is torn down: the sessions list can be stale (another
  // tab deleted the row seconds ago), and discovering that after
  // HISTORY_CLEARED would leave the user in a wiped chat bound to a
  // dead id. A 404 therefore aborts the whole switch and the caller
  // (SessionPanel) surfaces the failure.
  const switchSession = useCallback(
    async (sid: string) => {
      if (!boot || sid === boot.sessionId || switchingRef.current) return;
      switchingRef.current = true;
      try {
        const sessionState = await boot.client.getSessionState(sid);
        dispatch({ type: "HISTORY_CLEARED" });
        await dispatchSessionContext(dispatch, boot.client, sid, sessionState);
        // The Composer is keyed by session id, so this remounts it:
        // the draft is wiped and useStream re-binds to the new id
        // instead of keeping callbacks closed over the old one.
        setBoot({ client: boot.client, sessionId: sid });
      } finally {
        switchingRef.current = false;
      }
    },
    [boot, dispatch],
  );

  // Session cleanup on page unload: without this, every refresh /
  // closed tab leaks the ACTIVE session into the embedded server's
  // memory. Sessions the user switched away from deliberately survive
  // (they remain in the panel for switch-back); the embedded server
  // dies with the CLI process anyway, so a missed beacon is harmless.
  // ``keepalive`` lets the DELETE outlive the unloading document.
  // NOTE: must stay ABOVE the conditional returns (rules of hooks).
  useEffect(() => {
    if (!boot) return;
    const { client, sessionId } = boot;
    const onPageHide = (event: PageTransitionEvent) => {
      // bfcache: navigating away via back/forward fires pagehide with
      // persisted=true and keeps the page FROZEN, not destroyed — if
      // the user comes back, the page resumes with all state intact.
      // Deleting the session here would orphan that restored page
      // (every subsequent turn would 404). Only delete on real unload.
      if (event.persisted) return;
      void client.deleteSession(sessionId, { keepalive: true });
    };
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, [boot]);

  if (bootError) {
    return (
      <div className="flex min-h-full flex-col items-center justify-center gap-3 p-8">
        <div className="flex items-center gap-2 text-sm font-medium text-danger">
          <span className="inline-block size-2 rounded-full bg-danger" />
          {ui().bootFailedTitle}
        </div>
        <p className="max-w-md text-center font-mono text-xs text-forge-text-secondary">
          {bootError}
        </p>
        <p className="max-w-md text-center text-xs text-forge-text-faint">
          {ui().bootFailedHint}
        </p>
      </div>
    );
  }

  if (!boot) {
    return (
      <div className="flex min-h-full items-center justify-center text-sm text-forge-text-faint">
        {ui().connecting}
      </div>
    );
  }

  return (
    // Value identity changes when ``boot`` or ``langChoice`` does —
    // the latter on purpose: a language switch must re-render the
    // whole tree so ui() calls re-resolve.
    <BootContext.Provider
      value={{
        client: boot.client,
        activeSessionId: boot.sessionId,
        resetSession,
        switchSession,
        langChoice,
        setLangChoice,
      }}
    >
      <div className="flex h-full">
        <RailNav />
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
    </BootContext.Provider>
  );
}

export function RootLayout() {
  return (
    <StoreProvider>
      <BootGate />
    </StoreProvider>
  );
}
