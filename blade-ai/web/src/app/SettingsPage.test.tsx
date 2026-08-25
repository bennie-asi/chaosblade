/**
 * SettingsPage tests (design §7.5).
 *
 * Two layers:
 *
 * 1. The page itself, over the shared mock backend (whose config/model
 *    branches mirror routes/config.py + routes/model.py: POST writes
 *    mutate a table that GET reads back, ``coldKeys`` answers
 *    hot_reload=false). Covers group rendering, the read-only masked
 *    credentials block, the llm_enable_thinking exclusion, toggle /
 *    draft / select write paths, the amber restart marker, the model
 *    hot-swap card, and the error state.
 *
 * 2. The i18n-setup resolution chain as pure functions (localStorage
 *    explicit choice > navigator.language > "en"). The full round trip
 *    — settings button → RootLayout.setLangChoice → localStorage +
 *    configureI18n + tree re-render — is pinned in router.test.tsx
 *    (only the real BootContext owns that wiring).
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BladeClient, configureI18n } from "@blade-ai/core";
import { BootContext } from "./bootContext";
import { SettingsPage } from "./SettingsPage";
import { installMockBackend, jsonResponse } from "../test/mockBackend";
import {
  LANG_STORAGE_KEY,
  readLangChoice,
  resolveLang,
} from "../i18n-setup";
import { ui } from "../lib/uiText";

configureI18n("en");

const realFetch = globalThis.fetch;

afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
  localStorage.clear();
});

function renderPage(opts: {
  setLangChoice?: (choice: "browser" | "zh" | "en") => void;
} = {}) {
  const client = new BladeClient("");
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <BootContext.Provider
        value={{
          client,
          activeSessionId: "s",
          resetSession: async () => {},
          switchSession: async () => {},
          langChoice: "browser",
          setLangChoice: opts.setLangChoice ?? (() => {}),
        }}
      >
        <SettingsPage />
      </BootContext.Provider>
    </QueryClientProvider>,
  );
}

/** The fetch spy sees every call — find the write for ``key``. */
function findConfigWrite(spy: ReturnType<typeof vi.fn>, key: string) {
  return spy.mock.calls.find(
    ([url, init]) =>
      String(url).includes(`/api/v1/config/${key}`) &&
      (init as RequestInit | undefined)?.method === "POST",
  );
}

describe("SettingsPage", () => {
  it("renders all groups, masked credentials, and excludes llm_enable_thinking", async () => {
    installMockBackend({
      config: {
        llm_api_key: "sk-secret",
        server_token: "tok-secret",
        confirmation_required: true,
        llm_temperature: 0.7,
        // A quality-assurance switch the HTTP whitelist still carries
        // for legacy clients — the UI must never render an editor for
        // it (same principle as the wizard seed exclusion).
        llm_enable_thinking: true,
      },
    });
    renderPage();

    // Group headings (advanced sub-groups live behind <details>, but
    // jsdom renders details content regardless of the open attr).
    await screen.findByText(ui().settingsGroupModel);
    screen.getByText(ui().settingsGroupGeneration);
    screen.getByText(ui().settingsGroupBehavior);
    screen.getByText(ui().settingsGroupCluster);
    screen.getByText(ui().settingsGroupAdvanced);
    screen.getByText(ui().settingsLanguage);
    screen.getByText(ui().settingsCredentials);

    // Credentials block: masked presence, never an input — and the
    // plaintext must not leak as a text node either (queryByDisplayValue
    // alone would only catch an input-value leak).
    expect(screen.getAllByText("********")).toHaveLength(2);
    expect(screen.queryByDisplayValue("sk-secret")).toBeNull();
    expect(screen.queryByDisplayValue("tok-secret")).toBeNull();
    expect(screen.queryByText("sk-secret")).toBeNull();
    expect(screen.queryByText("tok-secret")).toBeNull();
    screen.getByText("/tmp/mock-blade-ai/config.json");

    // The quality switch is nowhere: no toggle, no input.
    expect(
      screen.queryByRole("switch", { name: "llm_enable_thinking" }),
    ).toBeNull();
    expect(screen.queryByLabelText("llm_enable_thinking")).toBeNull();

    // A whitelisted bool IS a switch with the fetched state.
    const toggle = screen.getByRole("switch", {
      name: "confirmation_required",
    });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });

  it("pins the full writable-key render domain: 30 editors, llm_enable_thinking nowhere", async () => {
    // Exhaustive pin of the field tables against config.py's
    // _WRITABLE_KEYS (31): 29 field rows + model_name (model section
    // select) + llm_enable_thinking (deliberately excluded). A typo'd
    // or dropped key would RENDER fine and only fail at write time on
    // the real server — this test makes the table itself the target.
    installMockBackend({ config: {} });
    renderPage();
    await screen.findByText(ui().settingsGroupAdvanced);

    const FIELD_KEYS = [
      // Generation (llm_thinking_format is a select of the server's
      // ALLOWED_THINKING_FORMATS dialects).
      "api_base_url",
      "llm_temperature",
      "llm_max_retries",
      "llm_thinking_format",
      "verifier_json_mode",
      // Behaviour.
      "confirmation_required",
      "self_evolution",
      "log_level",
      // Cluster targeting.
      "kubeconfig_path",
      "kube_context",
      // Timeouts.
      "timeout_blade",
      "timeout_kubectl",
      "timeout_kubectl_exec",
      "llm_connect_timeout",
      "llm_read_timeout",
      "timeout_default",
      // Loop budgets.
      "max_agent_loop",
      "max_execute_loop",
      "max_verifier_loop",
      "max_recover_verifier_loop",
      "recursion_limit",
      // Replan.
      "max_replan_count",
      "max_verify_replan_count",
      "replan_auto_trigger",
      "replan_reset_execute_count",
      // Loop detection.
      "loop_detection_window",
      "loop_detection_turns",
      "loop_detection_threshold",
      "idle_turn_threshold",
    ];
    // getByLabelText hits all three control kinds (aria-label lives on
    // the button / select / input alike). Filter-then-compare prints
    // the missing keys on failure instead of a generic not-found.
    const missing = FIELD_KEYS.filter((k) => !screen.queryByLabelText(k));
    expect(missing).toEqual([]);

    // model_name lives in the model section's select.
    expect(screen.queryByLabelText("model_name")).toBeInTheDocument();
    // The quality-assurance switch is deliberately never rendered.
    expect(screen.queryByLabelText("llm_enable_thinking")).toBeNull();
  });

  it("writes a toggle via setConfig and renders the coerced echo", async () => {
    const spy = installMockBackend({
      config: { confirmation_required: true },
    });
    renderPage();

    const toggle = await screen.findByRole("switch", {
      name: "confirmation_required",
    });
    fireEvent.click(toggle);

    // The server echo (value: false) replaces the row state.
    await waitFor(() =>
      expect(
        screen.getByRole("switch", { name: "confirmation_required" }),
      ).toHaveAttribute("aria-checked", "false"),
    );
    const call = findConfigWrite(
      spy as unknown as ReturnType<typeof vi.fn>,
      "confirmation_required",
    );
    expect(call).toBeDefined();
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({
      value: "false",
    });
  });

  it("pins the amber restart marker when the write answers hot_reload=false", async () => {
    installMockBackend({
      config: { llm_temperature: 0.7 },
      coldKeys: ["llm_temperature"],
    });
    renderPage();

    const input = await screen.findByLabelText("llm_temperature");
    fireEvent.change(input, { target: { value: "0.9" } });
    fireEvent.blur(input);

    await screen.findByText(ui().settingsRestart);
  });

  it("ignores the blur fired after an Enter commit — no double write", async () => {
    // Regression pin for the commit guard: Enter commits, the write's
    // re-render disables the focused input, and browsers fire blur when
    // that happens (a fast manual Tab lands in the same window). Without
    // the `if (disabled) return` guard the blur re-commits the same
    // draft against the still-stale echoed value — a redundant POST.
    const spy = installMockBackend({
      config: { llm_temperature: 0.7 },
    });
    renderPage();

    const input = await screen.findByLabelText("llm_temperature");
    fireEvent.change(input, { target: { value: "0.9" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);

    await waitFor(() => {
      const writes = (
        spy as unknown as ReturnType<typeof vi.fn>
      ).mock.calls.filter(
        ([url, init]) =>
          String(url).includes("/api/v1/config/llm_temperature") &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(writes).toHaveLength(1);
    });
    // Sanity: the single write did carry the new draft.
    expect(
      JSON.parse(
        String(
          (findConfigWrite(
            spy as unknown as ReturnType<typeof vi.fn>,
            "llm_temperature",
          )?.[1] as RequestInit).body,
        ),
      ),
    ).toEqual({ value: "0.9" });
  });

  it("discards an emptied text draft on blur — unset is not exposed", async () => {
    const spy = installMockBackend({
      config: { api_base_url: "https://example.test/v1" },
    });
    renderPage();

    const input = await screen.findByLabelText("api_base_url");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.blur(input);

    // No write fired, and the draft reverted to the current value.
    expect(findConfigWrite(spy as never, "api_base_url")).toBeUndefined();
    expect((input as HTMLInputElement).value).toBe(
      "https://example.test/v1",
    );
  });

  it("switches the model via POST /api/v1/model and refetches", async () => {
    const spy = installMockBackend();
    renderPage();

    // Wait for getModel to resolve: the select renders (disabled)
    // during pending with an empty option list, which jsdom displays
    // as the "__custom__" fallback — the findByLabelText alone would
    // catch that transient frame.
    await screen.findByRole("option", { name: "qwen-max" });
    const select = screen.getByLabelText("model_name");
    expect((select as HTMLSelectElement).value).toBe(
      "qwen3.6-max-preview",
    );
    fireEvent.change(select, { target: { value: "qwen-max" } });

    await waitFor(() =>
      expect((select as HTMLSelectElement).value).toBe("qwen-max"),
    );
    const posted = spy.mock.calls.some(
      ([url, init]) =>
        String(url).endsWith("/api/v1/model") &&
        (init as RequestInit | undefined)?.method === "POST" &&
        String((init as RequestInit).body).includes("qwen-max"),
    );
    expect(posted).toBe(true);
  });

  it("delegates language choices to the boot context", async () => {
    const setLangChoice = vi.fn();
    installMockBackend();
    renderPage({ setLangChoice });

    fireEvent.click(await screen.findByRole("button", { name: "中文" }));
    expect(setLangChoice).toHaveBeenCalledWith("zh");
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    expect(setLangChoice).toHaveBeenCalledWith("en");
    fireEvent.click(
      screen.getByRole("button", { name: ui().settingsLangBrowser }),
    );
    expect(setLangChoice).toHaveBeenCalledWith("browser");
  });

  it("shows the load-failed state when getConfig rejects", async () => {
    globalThis.fetch = vi.fn(async (url: string | URL | Request) => {
      if (String(url).endsWith("/api/v1/config")) {
        return jsonResponse({ status: "fail", message: "db down" });
      }
      return jsonResponse({});
    }) as unknown as typeof fetch;
    renderPage();

    await screen.findByText(ui().settingsLoadFailed);
    screen.getByText(/db down/);
  });
});

describe("i18n-setup language resolution", () => {
  it("falls back to the browser when nothing is stored", () => {
    expect(readLangChoice()).toBe("browser");
    // jsdom's navigator.language is en-US.
    expect(resolveLang("browser")).toBe("en");
  });

  it("honours a stored explicit choice", () => {
    localStorage.setItem(LANG_STORAGE_KEY, "zh");
    expect(readLangChoice()).toBe("zh");
    expect(resolveLang(readLangChoice())).toBe("zh");
  });

  it("ignores junk in storage", () => {
    localStorage.setItem(LANG_STORAGE_KEY, "klingon");
    expect(readLangChoice()).toBe("browser");
  });

  it("passes explicit choices through unchanged", () => {
    expect(resolveLang("zh")).toBe("zh");
    expect(resolveLang("en")).toBe("en");
  });
});
