/**
 * HistoryItem renderers.
 *
 * Core kinds (user / agent / thinking / tool / tool_group / log /
 * error / system / result), the confirm-gate family (confirm_context /
 * confirm_prompt — see ConfirmMessage.tsx), the turn-metadata rows
 * (turn_usage / memory_compaction / phase_stepper), and the nine
 * info cards (welcome / boot doctor / pending tasks / runtime doctor
 * / memory / help / session / experiments / model — see
 * InfoCards.tsx). Anything still unknown falls through to
 * ``UnknownMessage`` so nothing is silently dropped.
 *
 * Forge discipline: status is always "small dot + text" (never a
 * filled colour pill), restrained radii, single light shadow.
 */
import { memo } from "react";
import type {
  AgentItem,
  ConfirmContextItem,
  ConfirmPromptItem,
  ErrorItem,
  HistoryItem,
  LogItem,
  MemoryCompactionItem,
  PhaseStepperItem,
  ResultItem,
  SystemItem,
  ThinkingItem,
  ToolGroupItem,
  ToolItem,
  ToolStatus,
  TurnUsageItem,
  UserItem,
} from "@blade-ai/core";
import { t } from "@blade-ai/core";
import { ConfirmContextView, ConfirmPromptView } from "./ConfirmMessage";
import {
  BootDoctorCardView,
  ExperimentsCardView,
  HelpCardView,
  MemoryCardView,
  ModelCardView,
  PendingTasksCardView,
  RuntimeDoctorCardView,
  SessionCardView,
  WelcomeCardView,
} from "./InfoCards";
import { Markdown } from "./Markdown";
import { PhaseStepperStrip } from "./PhaseStepper";
import { formatDuration, formatLocalTime, formatTokens } from "../../lib/format";

// ── shared bits ─────────────────────────────────────────────────────

function StatusDot({ className }: { className: string }) {
  return (
    <span
      className={`inline-block size-2 shrink-0 rounded-full ${className}`}
    />
  );
}

const TOOL_STATUS_DOT: Record<ToolStatus, string> = {
  running: "bg-warning-dot",
  success: "bg-success-dot",
  error: "bg-danger",
  canceled: "bg-forge-text-faint",
};

const LOG_LEVEL_DOT: Record<LogItem["level"], string> = {
  info: "bg-forge-text-faint",
  warn: "bg-warning-dot",
  ok: "bg-success-dot",
};

// ── user / agent ────────────────────────────────────────────────────

/** User message — right-aligned bubble (Forge chat convention). */
function UserMessage({ item }: { item: UserItem }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[75%] rounded-card bg-forge-code px-4 py-2 text-sm whitespace-pre-wrap text-forge-text">
        {item.text}
      </div>
    </div>
  );
}

/** Agent reply — left-aligned GFM. Markdown blocks supply their own
 *  spacing; the wrapper keeps body size + line height. Mid-stream
 *  fragments (the reducer's tail splits) may carry an unclosed fence
 *  — react-markdown renders those gracefully as-is. */
function AgentMessage({ item }: { item: AgentItem }) {
  return (
    <div className="max-w-[85%] text-sm leading-6 text-forge-text">
      <Markdown text={item.text} />
    </div>
  );
}

// ── thinking / system / log / error ─────────────────────────────────

function ThinkingMessage({ item }: { item: ThinkingItem }) {
  return (
    <div className="text-xs text-forge-text-faint italic">
      ▸ {t("thinking.collapsed", { duration: formatDuration(item.durationMs) })}
    </div>
  );
}

function SystemMessage({ item }: { item: SystemItem }) {
  return (
    <div className="text-xs whitespace-pre-wrap text-forge-text-faint">
      {item.text}
    </div>
  );
}

function LogMessage({ item }: { item: LogItem }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <StatusDot className={`mt-1.5 ${LOG_LEVEL_DOT[item.level]}`} />
      <div className="min-w-0 text-forge-text-secondary">
        {item.tag ? (
          <span className="text-forge-text-faint">{item.tag} · </span>
        ) : null}
        <span className="whitespace-pre-wrap">{item.text}</span>
      </div>
    </div>
  );
}

function ErrorMessage({ item }: { item: ErrorItem }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <StatusDot className="mt-1.5 bg-danger" />
      <div className="min-w-0 whitespace-pre-wrap text-danger">
        {item.text}
      </div>
    </div>
  );
}

// ── tool ────────────────────────────────────────────────────────────

function ToolMessage({ item }: { item: ToolItem }) {
  // Full output first — same contract as the TUI card (``raw ||
  // resultPreview``). The single-line ``resultPreview`` is only a
  // fallback for legacy items that never captured ``raw``; showing it
  // first reduced every finished tool to an 80-char teaser. CSS owns
  // the truncation (max-h + overflow), not the field pick.
  const body = item.raw || item.resultPreview;
  return (
    <div className="rounded-card border border-forge-border bg-forge-card px-3 py-2 shadow-card">
      <div className="flex items-center gap-2 text-xs">
        {/* The cancelled right-rail "tool queue" panel's one real
            increment — the alive feel of a spinner — lives here as the
            running dot's pulse (zero-duplication rule kept the chat
            card, which carries strictly more information). */}
        <StatusDot
          className={`${TOOL_STATUS_DOT[item.status]}${
            item.status === "running"
              ? " animate-pulse motion-reduce:animate-none"
              : ""
          }`}
        />
        <span className="font-mono font-medium text-forge-text">
          {item.name}
        </span>
        {item.status === "running" ? (
          <span className="text-forge-text-faint">{t("tool.running")}</span>
        ) : null}
        {item.elapsedMs !== undefined ? (
          <span className="text-forge-text-faint">
            {(item.elapsedMs / 1000).toFixed(1)}s
          </span>
        ) : null}
        {item.locator ? (
          <span className="ml-auto font-mono text-forge-text-faint">
            {item.locator}
          </span>
        ) : null}
      </div>
      {/* Placeholder suppression mirrors the TUI exactly: ``running``
          is carried by the pulsing dot, ``canceled`` by the status
          glyph — an empty body there is expected, not missing data. */}
      {item.status !== "running" && item.status !== "canceled" ? (
        body ? (
          <pre className="mt-2 max-h-64 overflow-auto rounded-input bg-forge-code p-2 font-mono text-xs whitespace-pre-wrap text-forge-text-secondary">
            {body}
          </pre>
        ) : (
          <div className="mt-1 text-xs text-forge-text-faint">
            {t(item.placeholderKey ?? "tool.no_output")}
          </div>
        )
      ) : null}
    </div>
  );
}

function ToolGroupMessage({ item }: { item: ToolGroupItem }) {
  return (
    <div className="flex flex-col gap-2">
      {item.tools.map((tool) => (
        <ToolMessage key={tool.id} item={tool} />
      ))}
    </div>
  );
}

// ── result ──────────────────────────────────────────────────────────

const RESULT_STATUS_DOT: Record<ResultItem["status"], string> = {
  success: "bg-success-dot",
  partial: "bg-warning-dot",
  failed: "bg-danger",
  unknown: "bg-forge-text-faint",
};

/** Section heading — mirrors the TUI's ``── label`` divider grammar. */
function SectionHeading({ label }: { label: string }) {
  return (
    <div className="mt-3 text-xs font-medium text-forge-text-faint">
      {label}
    </div>
  );
}

/** Result field row — SKIPS empty values (unlike the confirm card's
 *  always-render discipline: a result is a terminal record, absence
 *  means the section doesn't apply). */
function ResultField({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "warn" | "danger";
}) {
  if (!value) return null;
  const labelCls =
    tone === "warn"
      ? "text-warning-dot"
      : tone === "danger"
        ? "text-danger"
        : "text-forge-text-faint";
  const valueCls =
    tone === "danger" ? "text-danger" : "text-forge-text-secondary";
  return (
    <>
      <dt className={labelCls}>{label}</dt>
      <dd className={`whitespace-pre-wrap break-words ${valueCls}`}>{value}</dd>
    </>
  );
}

/**
 * Result card — full counterpart of the TUI's ResultCard. Sections:
 * Outcome (fault/target/uid/duration/attempts) → Effect verified →
 * Side effects (success/partial) → Recovery notes (partial) →
 * Failure analysis (failed) → postmortem / alternatives → replay hint.
 * Forge discipline: status rides on the small dot + title, the frame
 * stays neutral (no status-coloured border/pill).
 */
function ResultMessage({ item }: { item: ResultItem }) {
  const titleKey =
    item.operation === "recover"
      ? item.status === "success"
        ? "result.status.success.recover"
        : item.status === "failed"
          ? "result.status.failed.recover"
          : "result.status.unknown"
      : `result.status.${item.status}`;

  let targetStr = "";
  if (item.target) {
    const ns = item.target.namespace || "";
    const namesStr = (item.target.names ?? []).join(", ");
    if (ns && namesStr) targetStr = `${ns} · ${namesStr}`;
    else if (ns) targetStr = ns;
    else if (namesStr) targetStr = namesStr;
  }
  const replanCount = item.replanCount ?? 0;
  const hasOutcome = Boolean(
    item.faultType || item.experimentUid || item.duration || targetStr || replanCount > 0,
  );
  const isFailed = item.status === "failed";
  const isPartial = item.status === "partial";
  const showSideEffects = item.status === "success" || isPartial;
  const hasSideEffects = !!item.sideEffects && item.sideEffects.length > 0;

  return (
    <div className="flex flex-col gap-1">
      <div className="rounded-card border border-forge-border bg-forge-card px-4 py-3 shadow-card">
        <div className="flex items-center gap-2 text-sm font-medium text-forge-text">
          <StatusDot className={RESULT_STATUS_DOT[item.status]} />
          {t(titleKey)}
          {item.taskId ? (
            <span className="font-mono text-xs font-normal text-forge-text-faint">
              · {item.taskId}
            </span>
          ) : null}
          {item.locator ? (
            <span className="ml-auto font-mono text-xs font-normal text-forge-text-faint">
              {item.locator}
            </span>
          ) : null}
        </div>

        {hasOutcome ? (
          <>
            <SectionHeading label={t("result.section.outcome")} />
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <ResultField label={t("result.label.fault")} value={item.faultType} />
              <ResultField label={t("result.label.target")} value={targetStr} />
              <ResultField label={t("result.label.uid")} value={item.experimentUid} />
              <ResultField label={t("result.label.duration")} value={item.duration} />
              {replanCount > 0 ? (
                <ResultField
                  label={t("result.label.attempts")}
                  value={t("result.attempts.label", { n: replanCount })}
                  tone="warn"
                />
              ) : null}
            </dl>
          </>
        ) : null}

        {item.summary ? (
          <>
            <SectionHeading label={t("result.section.effect")} />
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <ResultField label={t("result.label.summary")} value={item.summary} />
            </dl>
          </>
        ) : null}

        {showSideEffects ? (
          <>
            <SectionHeading label={t("result.section.side_effects")} />
            {hasSideEffects ? (
              <ul className="mt-1 flex flex-col gap-0.5 text-xs text-forge-text-secondary">
                {item.sideEffects!.map((effect, i) => (
                  <li key={i}>{effect}</li>
                ))}
              </ul>
            ) : (
              <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <ResultField
                  label={t("result.label.side_effect_item")}
                  value={item.sideEffectsSummary || t("result.side_effects_none")}
                />
              </dl>
            )}
          </>
        ) : null}

        {isPartial && item.cause ? (
          <>
            <SectionHeading label={t("result.section.recovery_notes")} />
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <ResultField
                label={t("result.label.why_partial")}
                value={item.cause}
                tone="warn"
              />
              <ResultField label={t("result.label.hint")} value={item.hint ?? ""} />
            </dl>
          </>
        ) : null}

        {isFailed && item.cause ? (
          <>
            <SectionHeading label={t("result.section.failure_analysis")} />
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <ResultField
                label={t("result.label.cause")}
                value={item.cause}
                tone="danger"
              />
              <ResultField label={t("result.label.hint")} value={item.hint ?? ""} />
            </dl>
          </>
        ) : null}
      </div>

      {/* Postmortem / alternatives bodies are markdown (the backend
       *  writes GFM) — render through the same Markdown pipeline as
       *  agent replies; the scroll cap lives on the wrapper. */}
      {item.postmortem ? (
        <div className="rounded-card border border-forge-border bg-forge-card px-4 py-3 shadow-card">
          <div className="text-xs font-medium text-forge-text-faint">
            {t("postmortem.title")}
          </div>
          <div className="mt-1 max-h-64 overflow-auto text-xs text-forge-text-secondary">
            <Markdown
              text={item.postmortem.markdown || item.postmortem.summary}
            />
          </div>
          <div className="mt-1 font-mono text-xs text-forge-text-faint">
            {t("postmortem.saved_at", { path: item.postmortem.path })}
          </div>
        </div>
      ) : null}

      {item.alternatives ? (
        <div className="rounded-card border border-forge-border bg-forge-card px-4 py-3 shadow-card">
          <div className="text-xs font-medium text-forge-text-faint">
            {t("plan_preview.alternatives_title")}
          </div>
          <div className="mt-1 max-h-64 overflow-auto text-xs text-forge-text-secondary">
            <Markdown text={item.alternatives} />
          </div>
        </div>
      ) : null}

      {item.taskId ? (
        <div className="text-xs text-forge-text-faint">
          {t("result.show_for_timeline", { id: item.taskId })}
        </div>
      ) : null}
    </div>
  );
}

// ── turn metadata rows (usage / compaction) ─────────────────────────

/** Turn-end token usage — single dim row, sister of the TUI's
 *  TurnUsageMessage: ``⚡ turn used 287 tokens (in 198, out 89,
 *  08-05 14:32:07)``. Authoritative figures from server ``usage``
 *  events; the item is never created when both counts are 0. */
function TurnUsageMessage({ item }: { item: TurnUsageItem }) {
  const total = formatTokens(item.inputTokens + item.outputTokens);
  const input = formatTokens(item.inputTokens);
  const output = formatTokens(item.outputTokens);
  const time = formatLocalTime(item.endedAt);
  return (
    <div className="text-xs text-forge-text-faint">
      ⚡ {t("turn.usage", { total, input, output, time })}
    </div>
  );
}

/** Finalised memory-compaction row — same visual weight as the usage
 *  row (single dim line, no border), committed ahead of it. */
function MemoryCompactionMessage({ item }: { item: MemoryCompactionItem }) {
  const duration = formatDuration(item.durationMs);
  if (!item.succeeded) {
    const reason = item.errorMessage || t("compaction.failure_unknown");
    return (
      <div className="text-xs text-danger">
        {t("compaction.failure_line", { reason, duration })}
      </div>
    );
  }
  const saved = Math.max(0, item.tokensBefore - item.tokensAfter);
  const percent =
    item.tokensBefore > 0 ? Math.floor((saved * 100) / item.tokensBefore) : 0;
  return (
    <div className="text-xs text-forge-text-faint">
      {t("compaction.success_line", {
        before: formatTokens(item.tokensBefore),
        after: formatTokens(item.tokensAfter),
        saved: formatTokens(saved),
        percent,
        duration,
        messages: item.messagesCompacted,
      })}
    </div>
  );
}

// ── fallback ────────────────────────────────────────────────────────

/**
 * Fallback for kinds with no dedicated renderer yet — keeps the item
 * visible as a collapsed JSON block instead of disappearing (losing a
 * confirm prompt in P1 would have wedged the turn — even the fallback
 * must preserve the information).
 */
function UnknownMessage({ item }: { item: HistoryItem }) {
  return (
    <details className="rounded-card border border-forge-border bg-forge-code px-3 py-2 text-xs text-forge-text-faint">
      <summary className="cursor-pointer select-none">
        [{item.kind}] — no dedicated renderer
      </summary>
      <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap">
        {JSON.stringify(item, null, 2)}
      </pre>
    </details>
  );
}

// ── dispatcher ──────────────────────────────────────────────────────

/**
 * MessageView is memoised — load-bearing, not a micro-optimisation.
 * HistoryList re-renders on EVERY streamed token (``pending`` changes),
 * and without the memo each committed AgentMessage would re-parse its
 * markdown at token rate: O(transcript) work per token, which is the
 * long-session jank the core tail-split was designed to avoid.
 *
 * Correctness rests on the core reducer's immutability contract:
 * TOKEN_APPENDED / TOOL_ENDED replace only the items they touch and
 * keep every other item reference-stable (verified at the reducer —
 * spread-copy updates, never in-place mutation), so a shallow ``item``
 * compare is a sound change detector. A runtime language switch would
 * need a store-driven re-render signal with or without this memo
 * (``getActiveLang()`` is only read during render), so memo introduces
 * no new staleness constraint there either.
 */
export const MessageView = memo(function MessageView({
  item,
}: {
  item: HistoryItem;
}) {
  switch (item.kind) {
    case "user":
      return <UserMessage item={item} />;
    case "agent":
      return <AgentMessage item={item} />;
    case "thinking":
      return <ThinkingMessage item={item} />;
    case "tool":
      return <ToolMessage item={item} />;
    case "tool_group":
      return <ToolGroupMessage item={item} />;
    case "log":
      return <LogMessage item={item} />;
    case "error":
      return <ErrorMessage item={item} />;
    case "system":
      return <SystemMessage item={item} />;
    case "result":
      return <ResultMessage item={item} />;
    case "turn_usage":
      return <TurnUsageMessage item={item} />;
    case "memory_compaction":
      return <MemoryCompactionMessage item={item} />;
    case "confirm_context":
      return <ConfirmContextView item={item as ConfirmContextItem} />;
    case "confirm_prompt":
      return <ConfirmPromptView item={item as ConfirmPromptItem} />;
    case "phase_stepper":
      // Finalised pipeline strip committed at turn end — the same
      // component as the live Composer strip, minus the pulse.
      return (
        <div className="mt-2">
          <PhaseStepperStrip steps={(item as PhaseStepperItem).steps} />
        </div>
      );
    // — info-card family (core shared slash commands + boot seeds) —
    case "welcome_card":
      return <WelcomeCardView item={item} />;
    case "boot_doctor_card":
      return <BootDoctorCardView item={item} />;
    case "pending_tasks_card":
      return <PendingTasksCardView item={item} />;
    case "runtime_doctor_card":
      return <RuntimeDoctorCardView item={item} />;
    case "memory_card":
      return <MemoryCardView item={item} />;
    case "help_card":
      return <HelpCardView item={item} />;
    case "session_card":
      return <SessionCardView item={item} />;
    case "experiments_card":
      return <ExperimentsCardView item={item} />;
    case "model_card":
      return <ModelCardView item={item} />;
    default:
      return <UnknownMessage item={item} />;
  }
});
