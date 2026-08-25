/**
 * Input area: textarea + send button, NL turns and slash commands.
 *
 * Slash dispatch mirrors the TUI Composer's contract (the reducer and
 * command registry are shared, so the semantics must be identical):
 *   - parseSlashLine pre-check → parseSlashCommand registry resolution
 *   - unknown root → echo + warn (never silently sent to the agent)
 *   - mid-stream gate → only streamSafe commands run while busy
 *   - dispatchesOwnTurn commands (``/run`` …) skip the synthetic echo
 *     because their handler's submitTurn fires the real TURN_STARTED
 *   - handler errors land as a warn log, never an unhandled rejection
 *
 * Web-specifics:
 *   - Enter sends, Shift+Enter newlines, and ``isComposing`` guards
 *     CJK IME confirmation keystrokes (a terminal never sees these;
 *     browsers do).
 *   - Typing stays enabled while busy (queue the next message); only
 *     sending is blocked.
 *   - ``exit`` surfaces a visible hint, then window.close() — browsers
 *     silently ignore close() for tabs they didn't open.
 *   - ``saveTextFile`` triggers a download (no FS access in a browser).
 *   - Confirm decisions: ConfirmPromptView dispatches
 *     CONFIRM_USER_DECIDED into ``state.pendingDecision``; the effect
 *     below picks it up and runs the network calls on this Composer's
 *     useStream instance (same handoff contract as the TUI).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BladeClient, SlashCommandContext } from "@blade-ai/core";
import {
  buildRegistry,
  parseSlashCommand,
  parseSlashLine,
  t,
  useAppDispatch,
  useAppSelector,
  useAppStateGetter,
  useStream,
} from "@blade-ai/core";
import { saveTextFile } from "../../lib/saveTextFile";
import { ui } from "../../lib/uiText";
import { useProgressRailVisible } from "../rail/ProgressRailContext";
import { CommandPalette } from "./CommandPalette";
import { LivePhaseStepper } from "./PhaseStepper";

export interface ComposerProps {
  client: BladeClient;
  sessionId: string;
  /** Imperative draft injection — hero chip clicks land here (fill,
   *  never send). Counter-based: the parent bumps it after
   *  ``setSuggest`` so repeated clicks on the SAME chip text still
   *  re-fill (a plain value prop would dedupe against unchanged
   *  state and drop the second click). */
  draftSeed?: { text: string; seq: number };
}

export function Composer({ client, sessionId, draftSeed }: ComposerProps) {
  const [text, setText] = useState("");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dispatch = useAppDispatch();
  const getAppState = useAppStateGetter();
  // Chat column width follows the rail (mock v2: 980px free,
  // 780px when the rail is on screen).
  const railVisible = useProgressRailVisible();
  const {
    submitTurn,
    submitRecover,
    cancelTurn,
    cancelReplay,
    cancelManualCompact,
    resolveConfirm,
    beginReplay,
    beginManualCompact,
    busy,
    awaitingConfirmation,
  } = useStream(client, sessionId);
  const registry = useMemo(() => buildRegistry(), []);
  const pendingDecision = useAppSelector((s) => s.pendingDecision);

  // ConfirmPromptView's buttons dispatch CONFIRM_USER_DECIDED into
  // ``state.pendingDecision``. We pick it up here, run the network
  // side-effects on this useStream instance, and clear the slot.
  // Two-step for the feedback case: first ``resolveConfirm`` closes
  // the confirm gate (server resumes graph with rejected), then
  // ``submitTurn`` fires a fresh user turn with the typed text.
  // ``supersedePrevious: true`` — commits the resolved confirm card
  // to history before TURN_STARTED clears pending, and lets the old
  // SSE abort silently (see the TUI Composer for the full rationale).
  useEffect(() => {
    if (!pendingDecision) return;
    const { taskId, answer, feedback } = pendingDecision;
    let cancelled = false;
    void (async () => {
      try {
        await resolveConfirm(taskId, answer);
        if (cancelled) return;
        if (feedback != null && feedback.trim().length > 0) {
          await submitTurn(feedback, { supersedePrevious: true });
        }
      } finally {
        if (!cancelled) {
          dispatch({ type: "CONFIRM_DECISION_CONSUMED" });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pendingDecision, dispatch, resolveConfirm, submitTurn]);

  // Interrupt the in-flight turn / replay / manual compact — all
  // three cancel calls are idempotent (no-ops when their source is
  // inactive), so one handler can fire them blindly, same as the TUI
  // Composer's Esc contract.
  const cancelInFlight = useCallback(() => {
    cancelReplay();
    cancelManualCompact();
    cancelTurn();
  }, [cancelReplay, cancelManualCompact, cancelTurn]);

  // Manual /compact runs while streamState is idle (its own busy gate
  // is ``currentManualCompact !== null``), so the busy-derived Esc
  // condition alone would leave it uninterruptible — the TUI keeps a
  // separate Esc handler for exactly this reason.
  const manualCompactActive = useAppSelector(
    (s) => s.currentManualCompact != null,
  );

  // Esc interrupts the in-flight turn / replay / manual compact — the
  // web counterpart of the TUI Esc handler. Same activation contract:
  // NOT while parked on a confirm gate (there Esc means "reject the
  // gate", owned by ConfirmPromptView), NOT while the ⌘K palette owns
  // the keyboard (its Esc closes the palette and is already
  // preventDefault'd — the defaultPrevented check is the
  // order-independent belt to this state check's suspenders).
  useEffect(() => {
    if (paletteOpen) return;
    if ((!busy || awaitingConfirmation) && !manualCompactActive) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.repeat || e.defaultPrevented) return;
      cancelInFlight();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, awaitingConfirmation, manualCompactActive, paletteOpen, cancelInFlight]);

  const handleSubmit = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setText("");

    const slashRaw = parseSlashLine(trimmed);
    if (slashRaw) {
      const parsed = parseSlashCommand(trimmed, registry);
      // Synthetic echo — see the TUI Composer for why this exists and
      // why dispatchesOwnTurn commands skip it.
      const echoSlashLine = () => {
        dispatch({ type: "TURN_STARTED", input: trimmed });
        dispatch({ type: "TURN_DONE" });
      };

      if (!parsed) {
        echoSlashLine();
        dispatch({
          type: "LOG_APPENDED",
          level: "warn",
          text: t("replay.unknown_command", { name: slashRaw.name }),
        });
        return;
      }

      const cmd = registry.get(parsed.root)!;
      const matchedSub = parsed.sub
        ? cmd.subcommands?.[parsed.sub]
        : undefined;

      // Stream-safe gate (per-sub strict semantics — see TUI Composer).
      const stateSnapshot = getAppState();
      if (stateSnapshot.streamState !== "idle") {
        const allowed = parsed.sub
          ? !!matchedSub?.streamSafe
          : !!cmd.streamSafe;
        if (!allowed) {
          echoSlashLine();
          dispatch({
            type: "LOG_APPENDED",
            level: "warn",
            text: t("command.busy_block"),
          });
          return;
        }
      }

      const ownTurn = matchedSub
        ? !!matchedSub.dispatchesOwnTurn
        : !!cmd.dispatchesOwnTurn;
      if (!ownTurn) {
        echoSlashLine();
      }

      const ctx: SlashCommandContext = {
        client,
        sessionId,
        state: stateSnapshot,
        registry,
        dispatch,
        exit: () => {
          // window.close() is silently ignored for tabs the script
          // didn't open (i.e. every normal tab) — without a visible
          // note, /exit would look like a no-op. Hand-rolled id per
          // the HISTORY_APPENDED convention (see reducer).
          dispatch({
            type: "HISTORY_APPENDED",
            item: {
              kind: "system",
              id: `exit-${Date.now()}`,
              text: ui().exitHint,
            },
          });
          window.close();
        },
        beginReplay,
        beginManualCompact,
        submitTurn,
        submitRecover,
        hostVersion: __BLADE_WEB_VERSION__,
        saveTextFile,
      };

      const handler = matchedSub ? matchedSub.handler : cmd.handler;
      handler(ctx, parsed.args).catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        const nameForLog = parsed.sub
          ? `${parsed.root} ${parsed.sub}`
          : parsed.root;
        dispatch({
          type: "LOG_APPENDED",
          level: "warn",
          text: t("command.handler_failed", { name: nameForLog, msg }),
        });
      });
      return;
    }

    void submitTurn(trimmed);
  }, [
    text,
    busy,
    registry,
    dispatch,
    getAppState,
    client,
    sessionId,
    submitTurn,
    submitRecover,
    beginReplay,
    beginManualCompact,
  ]);

  // ⌘K palette pick → fill the composer, never execute (same
  // "apply" semantics as the TUI SlashMenu). Focus returns to the
  // textarea once the palette has unmounted.
  const handlePalettePick = useCallback((fill: string) => {
    setText(fill);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  }, []);

  // Hero chip → draft injection. Same fill-only semantics as the
  // palette pick above; keyed on the seed's seq so every click (even
  // a repeat of the same chip) is one fill. Skips the first render
  // where seq is still 0 — nothing to inject yet.
  useEffect(() => {
    if (!draftSeed || draftSeed.seq === 0) return;
    handlePalettePick(draftSeed.text);
  }, [draftSeed, handlePalettePick]);

  return (
    <div className="shrink-0 border-t border-forge-border bg-forge-card">
      {/* Live pipeline strip — pinned directly above the input row so
          it stays visible regardless of the output streaming above it
          (mirrors the TUI Composer's strip → InputPrompt anchor).
          Renders nothing when no inject/recover turn is in flight, and
          yields to the DAG rail when the rail is visible — the strip
          itself owns that fallback logic (see PhaseStepper.tsx). */}
      <LivePhaseStepper />
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        registry={registry}
        onPick={handlePalettePick}
      />
      <div
        className={`mx-auto flex items-end gap-2 px-4 pt-3 pb-1 ${
          railVisible ? "max-w-[780px]" : "max-w-[980px]"
        }`}
      >
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              handleSubmit();
            }
          }}
          rows={Math.min(8, text.split("\n").length || 1)}
          placeholder={ui().inputPlaceholder}
          className="max-h-48 flex-1 resize-none rounded-input border border-forge-border bg-forge-bg px-3 py-2 text-sm text-forge-text outline-none placeholder:text-forge-text-faint focus:border-forge-accent"
        />
        {busy && !awaitingConfirmation ? (
          <button
            type="button"
            onClick={cancelInFlight}
            title={ui().stopHint}
            className="rounded-button bg-danger px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
          >
            ■ {ui().stop}
          </button>
        ) : (
          <button
            type="button"
            onClick={handleSubmit}
            disabled={busy || !text.trim()}
            className="rounded-button bg-forge-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-forge-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
          >
            {ui().send}
          </button>
        )}
      </div>
      {/* Hint bar — the mock's composer-bar affordance (design doc
          §6 mock: ⌘K 命令面板 chip under the input). Clicking the
          chip opens the palette for mouse-first users. */}
      <div
        className={`mx-auto flex items-center px-4 pb-2 ${
          railVisible ? "max-w-[780px]" : "max-w-[980px]"
        }`}
      >
        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className="flex items-center gap-1.5 text-[11px] text-forge-text-faint transition-colors hover:text-forge-text-secondary"
        >
          <kbd className="rounded border border-forge-border bg-forge-bg px-1 py-px font-mono text-[10px]">
            {/mac/i.test(navigator.platform) ? "⌘K" : "Ctrl K"}
          </kbd>
          {ui().paletteKbdLabel}
        </button>
      </div>
    </div>
  );
}
