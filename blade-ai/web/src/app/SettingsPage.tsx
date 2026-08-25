/**
 * Settings route (design §7.5) — a FORM over the config API surface
 * that already exists for the TS TUI's /config and /model slashes.
 * Zero backend / core changes: getConfig / setConfig / unsetConfig /
 * getModel / setModel were all wrapped long ago.
 *
 * Whitelist-driven rendering: the editable set mirrors config.py's
 * _WRITABLE_KEYS (31 keys) MINUS two deliberate exclusions —
 *   · model_name lives in the dedicated model section (hot-swap via
 *     POST /api/v1/model, not the generic key/value row), and
 *   · llm_enable_thinking is never rendered: it stays in the HTTP
 *     whitelist for legacy clients, but a quality-assurance switch
 *     must not offer a "turn quality off" affordance in any UI
 *     (same principle as the wizard seed exclusion).
 * Secrets / connection strings (llm_api_key, server_token, DSNs)
 * are NOT editable here — the credentials section shows them masked
 * and points at the CLI wizard.
 *
 * Write path: inline-per-row (toggle click / select change / text
 * blur-or-Enter commits). The server reply's coerced `value` is what
 * gets rendered; `hot_reload=false` (or a rebuild_error) pins an
 * amber "restart required" marker on that row until the next write.
 * Text edits that would submit an empty string are discarded on blur
 * (revert to the current value) — unsetting keys is deliberately not
 * exposed in this UI.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { BladeClient } from "@blade-ai/core";
import { useBoot } from "./bootContext";
import { ui } from "../lib/uiText";
import type { LangChoice } from "../i18n-setup";

// ---------------------------------------------------------------------------
// Field metadata — mirrors config.py's _WRITABLE_KEYS grouping comments.
// Keys are shown verbatim (config-tool convention, same as /config list);
// only groups and chrome get translated.
// ---------------------------------------------------------------------------

type FieldType = "bool" | "number" | "text" | "select";

interface FieldDef {
  key: string;
  type: FieldType;
  options?: readonly string[];
}

interface SubGroup {
  id: string;
  labelKey: keyof ReturnType<typeof ui>;
  fields: FieldDef[];
}

const GENERATION_FIELDS: FieldDef[] = [
  { key: "api_base_url", type: "text" },
  { key: "llm_temperature", type: "number" },
  { key: "llm_max_retries", type: "number" },
  {
    // Enum, not free text — settings.py's _validate_llm_thinking_format
    // rejects anything outside ALLOWED_THINKING_FORMATS (a typo would
    // surface as a row-level error; the select prevents it outright).
    // A future server-side dialect degrades gracefully: the current
    // value still renders via the select's extra-option fallback.
    key: "llm_thinking_format",
    type: "select",
    options: [
      "auto",
      "qwen",
      "openai",
      "deepseek",
      "zai",
      "openrouter",
      "together",
      "qwen-chat-template",
      "none",
    ],
  },
  { key: "verifier_json_mode", type: "bool" },
];

const BEHAVIOR_FIELDS: FieldDef[] = [
  { key: "confirmation_required", type: "bool" },
  { key: "self_evolution", type: "bool" },
  {
    key: "log_level",
    type: "select",
    options: ["DEBUG", "INFO", "WARNING", "ERROR"],
  },
];

const CLUSTER_FIELDS: FieldDef[] = [
  { key: "kubeconfig_path", type: "text" },
  { key: "kube_context", type: "text" },
];

const ADVANCED_SUBGROUPS: SubGroup[] = [
  {
    id: "timeouts",
    labelKey: "settingsSubTimeouts",
    fields: [
      { key: "timeout_blade", type: "number" },
      { key: "timeout_kubectl", type: "number" },
      { key: "timeout_kubectl_exec", type: "number" },
      { key: "llm_connect_timeout", type: "number" },
      { key: "llm_read_timeout", type: "number" },
      { key: "timeout_default", type: "number" },
    ],
  },
  {
    id: "loops",
    labelKey: "settingsSubLoops",
    fields: [
      { key: "max_agent_loop", type: "number" },
      { key: "max_execute_loop", type: "number" },
      { key: "max_verifier_loop", type: "number" },
      { key: "max_recover_verifier_loop", type: "number" },
      { key: "recursion_limit", type: "number" },
    ],
  },
  {
    id: "replan",
    labelKey: "settingsSubReplan",
    fields: [
      { key: "max_replan_count", type: "number" },
      { key: "max_verify_replan_count", type: "number" },
      { key: "replan_auto_trigger", type: "bool" },
      { key: "replan_reset_execute_count", type: "number" },
    ],
  },
  {
    id: "detection",
    labelKey: "settingsSubDetection",
    fields: [
      { key: "loop_detection_window", type: "number" },
      { key: "loop_detection_turns", type: "number" },
      { key: "loop_detection_threshold", type: "number" },
      { key: "idle_turn_threshold", type: "number" },
    ],
  },
];

// ---------------------------------------------------------------------------
// Row-level write state machine
// ---------------------------------------------------------------------------

type RowPhase = "idle" | "saving" | "saved" | "error";

interface RowState {
  phase: RowPhase;
  error?: string;
  /** Pinned when the server answered hot_reload=false / rebuild failed.
   *  Cleared by the next successful write on the same row. */
  restartRequired: boolean;
}

const IDLE_ROW: RowState = { phase: "idle", restartRequired: false };

// ---------------------------------------------------------------------------
// Controls (Forge discipline: one accent hue, no filled pills)
// ---------------------------------------------------------------------------

function Toggle({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  label: string;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-40 ${
        checked ? "bg-forge-accent" : "bg-forge-border"
      }`}
    >
      <span
        className={`absolute top-0.5 size-4 rounded-full bg-white transition-transform ${
          checked ? "translate-x-[18px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

/** Small inline status cell to the right of every control: spinner
 *  while saving, a fading check on hot success, an amber restart
 *  marker while a cold change is pending, red error text on failure. */
function RowStatus({ state }: { state: RowState }) {
  if (state.phase === "saving") {
    return (
      <span
        aria-label="saving"
        className="inline-block size-3.5 animate-spin rounded-full border border-forge-border border-t-forge-accent"
      />
    );
  }
  return (
    <span className="flex items-center gap-2">
      {state.phase === "saved" ? (
        <span aria-label="saved" className="text-success">
          ✓
        </span>
      ) : null}
      {state.restartRequired ? (
        <span className="text-xs text-warning">{ui().settingsRestart}</span>
      ) : null}
      {state.phase === "error" ? (
        <span className="max-w-64 truncate text-xs text-danger" title={state.error}>
          {state.error}
        </span>
      ) : null}
    </span>
  );
}

/** Text/number input with draft semantics: blur or Enter commits when
 *  the draft differs, Escape reverts, empty text is never submitted
 *  (unset is deliberately not exposed — see the module docstring). */
function DraftInput({
  field,
  value,
  disabled,
  onCommit,
}: {
  field: FieldDef;
  value: string;
  disabled: boolean;
  onCommit: (raw: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  const commit = () => {
    // A write in flight disables the input; browsers fire blur when the
    // focused element becomes disabled (and the user can Tab away before
    // the echo lands). Without this guard the blur re-commits the same
    // draft against the still-stale `value` prop — a redundant POST.
    // Safe because the draft is frozen while disabled, and the echoed
    // value re-syncs it via the effect below.
    if (disabled) return;
    const v = draft.trim();
    if (v === "" || v === value) {
      setDraft(value);
      return;
    }
    onCommit(v);
  };

  return (
    <input
      type={field.type === "number" ? "number" : "text"}
      aria-label={field.key}
      value={draft}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setDraft(value);
      }}
      className={`rounded-input border border-forge-border bg-forge-card px-2 py-1 font-mono text-xs outline-none focus:border-forge-accent disabled:opacity-40 ${
        field.type === "number" ? "w-24" : "w-72 max-w-full"
      }`}
    />
  );
}

// ---------------------------------------------------------------------------
// Config field row
// ---------------------------------------------------------------------------

function FieldRow({
  field,
  value,
  state,
  onWrite,
}: {
  field: FieldDef;
  value: unknown;
  state: RowState;
  onWrite: (key: string, raw: string) => void;
}) {
  const busy = state.phase === "saving";
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <span className="font-mono text-xs text-forge-text-secondary">
        {field.key}
      </span>
      <span className="flex items-center gap-3">
        <RowStatus state={state} />
        {field.type === "bool" ? (
          <Toggle
            checked={value === true}
            disabled={busy}
            label={field.key}
            onChange={(next) => onWrite(field.key, next ? "true" : "false")}
          />
        ) : field.type === "select" ? (
          <select
            aria-label={field.key}
            value={String(value ?? "")}
            disabled={busy}
            onChange={(e) => onWrite(field.key, e.target.value)}
            className="rounded-input border border-forge-border bg-forge-card px-2 py-1 font-mono text-xs outline-none focus:border-forge-accent disabled:opacity-40"
          >
            {(field.options ?? []).map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
            {field.options && !field.options.includes(String(value ?? "")) ? (
              <option value={String(value ?? "")}>{String(value ?? "")}</option>
            ) : null}
          </select>
        ) : (
          <DraftInput
            field={field}
            value={String(value ?? "")}
            disabled={busy}
            onCommit={(raw) => onWrite(field.key, raw)}
          />
        )}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Model section — dedicated hot-swap card (POST /api/v1/model)
// ---------------------------------------------------------------------------

function ModelSection({ client }: { client: BladeClient }) {
  const modelQuery = useQuery({
    queryKey: ["model"],
    queryFn: () => client.getModel(),
  });
  const [state, setState] = useState<RowState>(IDLE_ROW);
  const [customOpen, setCustomOpen] = useState(false);
  const [customDraft, setCustomDraft] = useState("");
  const savedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      // Clear the fading-check timer on unmount — the page-level
      // savedTimers cleanup covers config rows, not this local one.
      if (savedTimer.current) clearTimeout(savedTimer.current);
    },
    [],
  );

  const data = modelQuery.data;
  const active = String(data?.["active"] ?? "");
  const candidatesRaw = Array.isArray(data?.["candidates"])
    ? (data["candidates"] as Record<string, unknown>[])
    : [];
  const candidateIds = candidatesRaw.map((c) => String(c["id"] ?? ""));
  // The active model is always selectable — synthesise an option when
  // it isn't in the curated list (same contract as the TS card builder).
  const options = candidateIds.includes(active)
    ? candidateIds
    : [...candidateIds, active].filter(Boolean);

  const apply = async (model: string) => {
    if (!model || model === active) return;
    setState({ phase: "saving", restartRequired: false });
    try {
      const resp = await client.setModel(model);
      const restartRequired = resp["restart_required"] === true;
      if (savedTimer.current) clearTimeout(savedTimer.current);
      setState({ phase: "saved", restartRequired });
      savedTimer.current = setTimeout(
        () => setState((s) => ({ ...s, phase: "idle" })),
        1500,
      );
      await modelQuery.refetch();
    } catch (e) {
      setState({
        phase: "error",
        restartRequired: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  };

  return (
    <section>
      <h2 className="text-sm font-medium">{ui().settingsGroupModel}</h2>
      <div className="mt-2 rounded-card border border-forge-border bg-forge-card px-4 py-1">
        <div className="flex items-center justify-between gap-4 py-2">
          <span className="font-mono text-xs text-forge-text-secondary">
            model_name
          </span>
          <span className="flex items-center gap-3">
            <RowStatus state={state} />
            {modelQuery.isError ? (
              <span className="text-xs text-danger">
                {ui().settingsLoadFailed}
              </span>
            ) : (
              <select
                aria-label="model_name"
                value={customOpen ? "__custom__" : active}
                disabled={state.phase === "saving" || modelQuery.isPending}
                onChange={(e) => {
                  if (e.target.value === "__custom__") {
                    setCustomOpen(true);
                    return;
                  }
                  setCustomOpen(false);
                  void apply(e.target.value);
                }}
                className="rounded-input border border-forge-border bg-forge-card px-2 py-1 font-mono text-xs outline-none focus:border-forge-accent disabled:opacity-40"
              >
                {options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
                <option value="__custom__">{ui().settingsModelCustom}</option>
              </select>
            )}
          </span>
        </div>
        {customOpen ? (
          <div className="flex items-center justify-end gap-2 pb-3">
            <input
              type="text"
              aria-label="custom model"
              placeholder={ui().settingsModelCustom}
              value={customDraft}
              onChange={(e) => setCustomDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && customDraft.trim()) {
                  setCustomOpen(false);
                  void apply(customDraft.trim());
                }
                if (e.key === "Escape") setCustomOpen(false);
              }}
              className="w-72 max-w-full rounded-input border border-forge-border bg-forge-card px-2 py-1 font-mono text-xs outline-none focus:border-forge-accent"
            />
            <button
              type="button"
              disabled={!customDraft.trim() || state.phase === "saving"}
              onClick={() => {
                setCustomOpen(false);
                void apply(customDraft.trim());
              }}
              className="rounded-button bg-forge-ink px-3 py-1 text-xs text-white disabled:opacity-40"
            >
              {ui().settingsModelApply}
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function SettingsPage() {
  const { client, langChoice, setLangChoice } = useBoot();
  const configQuery = useQuery({
    queryKey: ["config"],
    queryFn: () => client.getConfig(),
  });
  /** Server-coerced values from successful writes override the fetched
   *  snapshot row-locally (no full refetch for a single-key write). */
  const [overrides, setOverrides] = useState<Record<string, unknown>>({});
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const savedTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(
    () => () => {
      Object.values(savedTimers.current).forEach(clearTimeout);
    },
    [],
  );

  if (configQuery.isPending) {
    return (
      <p className="py-12 text-center text-sm text-forge-text-faint">
        {ui().settingsLoading}
      </p>
    );
  }
  if (configQuery.isError) {
    return (
      <div className="py-12 text-center">
        <p className="text-sm font-medium text-danger">
          {ui().settingsLoadFailed}
        </p>
        <p className="mt-1 font-mono text-xs text-forge-text-faint">
          {configQuery.error instanceof Error
            ? configQuery.error.message
            : String(configQuery.error)}
        </p>
      </div>
    );
  }

  const config = (configQuery.data["config"] as Record<string, unknown>) ?? {};
  const configPath = String(configQuery.data["config_path"] ?? "");
  const valueOf = (key: string) =>
    key in overrides ? overrides[key] : config[key];

  const write = async (key: string, raw: string) => {
    setRows((r) => ({
      ...r,
      [key]: { phase: "saving", restartRequired: false },
    }));
    try {
      const resp = await client.setConfig(key, raw);
      const restartRequired =
        resp["hot_reload"] === false || Boolean(resp["rebuild_error"]);
      setOverrides((o) => ({ ...o, [key]: resp["value"] }));
      if (savedTimers.current[key]) clearTimeout(savedTimers.current[key]);
      setRows((r) => ({ ...r, [key]: { phase: "saved", restartRequired } }));
      savedTimers.current[key] = setTimeout(
        () =>
          setRows((r) => ({
            ...r,
            [key]: { ...(r[key] ?? IDLE_ROW), phase: "idle" },
          })),
        1500,
      );
    } catch (e) {
      setRows((r) => ({
        ...r,
        [key]: {
          phase: "error",
          restartRequired: false,
          error: e instanceof Error ? e.message : String(e),
        },
      }));
    }
  };

  const renderFields = (fields: FieldDef[]) =>
    fields.map((f) => (
      <FieldRow
        key={f.key}
        field={f}
        value={valueOf(f.key)}
        state={rows[f.key] ?? IDLE_ROW}
        onWrite={write}
      />
    ));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-6 py-6">
        <h1 className="text-lg font-medium">{ui().settingsTitle}</h1>
        <p className="mt-0.5 text-xs text-forge-text-faint">
          {ui().settingsHint}
        </p>

        <div className="mt-5 space-y-6">
          <ModelSection client={client} />

          <section>
            <h2 className="text-sm font-medium">
              {ui().settingsGroupGeneration}
            </h2>
            <div className="mt-2 rounded-card border border-forge-border bg-forge-card px-4 py-1">
              {renderFields(GENERATION_FIELDS)}
            </div>
          </section>

          <section>
            <h2 className="text-sm font-medium">
              {ui().settingsGroupBehavior}
            </h2>
            <div className="mt-2 rounded-card border border-forge-border bg-forge-card px-4 py-1">
              {renderFields(BEHAVIOR_FIELDS)}
            </div>
          </section>

          <section>
            <h2 className="text-sm font-medium">{ui().settingsGroupCluster}</h2>
            <div className="mt-2 rounded-card border border-forge-border bg-forge-card px-4 py-1">
              {renderFields(CLUSTER_FIELDS)}
            </div>
          </section>

          <section>
            <h2 className="text-sm font-medium">
              {ui().settingsGroupAdvanced}
            </h2>
            <div className="mt-2 space-y-3">
              {ADVANCED_SUBGROUPS.map((sg) => (
                <details
                  key={sg.id}
                  className="rounded-card border border-forge-border bg-forge-card px-4 py-2"
                >
                  <summary className="cursor-pointer text-xs text-forge-text-secondary">
                    {ui()[sg.labelKey]}
                  </summary>
                  <div className="pt-1">{renderFields(sg.fields)}</div>
                </details>
              ))}
            </div>
          </section>

          <section>
            <h2 className="text-sm font-medium">{ui().settingsLanguage}</h2>
            <div className="mt-2 flex items-center gap-1 rounded-card border border-forge-border bg-forge-card p-1">
              {(
                [
                  ["browser", ui().settingsLangBrowser],
                  ["zh", "中文"],
                  ["en", "English"],
                ] as [LangChoice, string][]
              ).map(([choice, label]) => (
                <button
                  key={choice}
                  type="button"
                  aria-pressed={langChoice === choice}
                  onClick={() => setLangChoice(choice)}
                  className={`rounded-button px-3 py-1 text-xs ${
                    langChoice === choice
                      ? "bg-forge-accent-soft text-forge-accent"
                      : "text-forge-text-secondary hover:text-forge-text"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </section>

          <section>
            <h2 className="text-sm font-medium">{ui().settingsCredentials}</h2>
            <p className="mt-0.5 text-xs text-forge-text-faint">
              {ui().settingsCredentialsHint}
            </p>
            <div className="mt-2 rounded-card border border-forge-border bg-forge-card px-4 py-1">
              <div className="flex items-center justify-between gap-4 py-2">
                <span className="font-mono text-xs text-forge-text-secondary">
                  llm_api_key
                </span>
                <span className="font-mono text-xs text-forge-text">
                  {config["llm_api_key"] ? "********" : ui().settingsNotSet}
                </span>
              </div>
              <div className="flex items-center justify-between gap-4 py-2">
                <span className="font-mono text-xs text-forge-text-secondary">
                  server_token
                </span>
                <span className="font-mono text-xs text-forge-text">
                  {config["server_token"] ? "********" : ui().settingsNotSet}
                </span>
              </div>
              <div className="flex items-center justify-between gap-4 py-2">
                <span className="font-mono text-xs text-forge-text-secondary">
                  {ui().settingsConfigPath}
                </span>
                <span className="truncate font-mono text-xs text-forge-text">
                  {configPath || ui().settingsNotSet}
                </span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

export default SettingsPage;
