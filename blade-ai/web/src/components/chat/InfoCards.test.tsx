/**
 * Info-card family tests — the web renderers for the nine card kinds
 * produced by core's shared slash commands and the boot seeds.
 * Contracts pinned here (each mirrors the TUI card of the same name):
 *
 *   - one shared frame grammar (icon + title + dim timestamp tail)
 *   - doctor cards: status glyph rows + a suggested-fixes block for
 *     failing rows; passing rows fade their message
 *   - pending tasks: per-state glyph/colour ladder, ``rejected`` is
 *     the ONLY grey state
 *   - platform deltas: NO Shift+Tab tip on the welcome card, NO
 *     terminal-background row on the runtime doctor, and the client
 *     version row reads "client version" (not "tui version")
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type {
  BootDoctorCardItem,
  ExperimentsCardItem,
  HelpCardItem,
  MemoryCardItem,
  ModelCardItem,
  RuntimeDoctorCardItem,
  SessionCardItem,
  WelcomeCardItem,
} from "@blade-ai/core";
import { configureI18n } from "@blade-ai/core";
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

configureI18n("en");

afterEach(() => {
  cleanup();
});

// ── welcome_card ────────────────────────────────────────────────────

describe("WelcomeCardView", () => {
  const item: WelcomeCardItem = {
    kind: "welcome_card",
    id: "w1",
    modelName: "qwen3.6-max-preview",
    permissionMode: "confirm",
    kubeconfig: "/very/long/path/to/some/cluster/config/admin.conf",
    namespace: "demo",
    version: "0.1.0",
  };

  it("renders the banner, model, mode and runtime rows", () => {
    render(<WelcomeCardView item={item} />);
    expect(screen.getByText("Blade-ai")).toBeInTheDocument();
    expect(screen.getByText(/v0\.1\.0/)).toBeInTheDocument();
    expect(screen.getByText("Welcome back!")).toBeInTheDocument();
    expect(screen.getByText("qwen3.6-max-preview")).toBeInTheDocument();
    expect(screen.getByText(/confirm/)).toBeInTheDocument();
    expect(screen.getByText("demo")).toBeInTheDocument();
    // Overlong kubeconfig collapses to .../<basename> (no $HOME in a
    // browser, so the TUI's ~-collapse doesn't apply).
    expect(screen.getByText(".../admin.conf")).toBeInTheDocument();
  });

  it("drops the TUI-only Shift+Tab tip but keeps the rest", () => {
    render(<WelcomeCardView item={item} />);
    expect(
      screen.getByText("Use /help to list all commands"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Use \/doctor/)).toBeInTheDocument();
    // The web has no permission-mode hotkey — the boot card must not
    // teach a gesture that doesn't exist.
    expect(screen.queryByText(/Shift\+Tab/)).not.toBeInTheDocument();
  });
});

// ── boot_doctor_card ────────────────────────────────────────────────

describe("BootDoctorCardView", () => {
  const item: BootDoctorCardItem = {
    kind: "boot_doctor_card",
    id: "bd1",
    passedCount: 2,
    totalCount: 3,
    capturedAt: "2026-08-20T14:30:05",
    checks: [
      {
        name: "llm_api_key",
        severity: "blocking",
        passed: true,
        message: "sk-…configured",
        fix: "",
      },
      {
        name: "kubeconfig",
        severity: "blocking",
        passed: false,
        message: "file not found: /root/.kube/config",
        fix: "Run /config to set the kubeconfig path",
      },
      {
        name: "skills",
        severity: "warning",
        passed: false,
        message: "no skill packs found",
        fix: "Install a skill pack",
      },
    ],
  };

  it("renders the summary, per-check rows and the fixes block", () => {
    render(<BootDoctorCardView item={item} />);
    expect(screen.getByText("Environment self-check")).toBeInTheDocument();
    expect(screen.getByText(/2\/3 passed/)).toBeInTheDocument();
    expect(screen.getByText(/captured at 14:30:05/)).toBeInTheDocument();
    expect(screen.getByText("llm_api_key")).toBeInTheDocument();
    expect(
      screen.getByText("file not found: /root/.kube/config"),
    ).toBeInTheDocument();
    expect(screen.getByText("Suggested fixes")).toBeInTheDocument();
    expect(
      screen.getByText("Run /config to set the kubeconfig path"),
    ).toBeInTheDocument();
    expect(screen.getByText("Install a skill pack")).toBeInTheDocument();
  });

  it("renders the unavailable variant when the probe endpoint failed", () => {
    render(
      <BootDoctorCardView
        item={{ ...item, unavailable: true, checks: [], passedCount: 0, totalCount: 0 }}
      />,
    );
    expect(
      screen.getByText(/preflight endpoint unavailable/),
    ).toBeInTheDocument();
  });
});

// ── pending_tasks_card ──────────────────────────────────────────────

describe("PendingTasksCardView", () => {
  it("renders the empty state", () => {
    render(
      <PendingTasksCardView
        item={{ kind: "pending_tasks_card", id: "pt0", tasks: [] }}
      />,
    );
    expect(screen.getByText("No unfinished tasks")).toBeInTheDocument();
  });

  it("renders rows with the per-state visual ladder", () => {
    const { container } = render(
      <PendingTasksCardView
        item={{
          kind: "pending_tasks_card",
          id: "pt1",
          tasks: [
            {
              taskId: "T-inj",
              faultType: "pod-cpu fullload",
              state: "injecting",
              createdAt: "",
            },
            {
              taskId: "T-fail",
              faultType: "pod-mem load",
              state: "failed",
              createdAt: "",
            },
            {
              taskId: "T-rej",
              faultType: "node-disk fill",
              state: "rejected",
              createdAt: "",
            },
          ],
        }}
      />,
    );
    // Active IO wears accent; failure wears danger; rejected is the
    // ONLY grey state (nothing to act on).
    const injecting = screen.getByText("injecting");
    expect(injecting.className).toContain("text-forge-accent");
    const failed = screen.getByText("failed");
    expect(failed.className).toContain("text-danger");
    const rejected = screen.getByText("rejected");
    expect(rejected.className).toContain("text-forge-text-faint");
    expect(screen.getByText("T-inj")).toBeInTheDocument();
    expect(screen.getByText("pod-cpu fullload")).toBeInTheDocument();
    // The ladder is exercised — no row silently fell to the fallback.
    expect(container.querySelectorAll(".text-forge-text-faint").length)
      .toBeGreaterThan(0);
  });
});

// ── runtime_doctor_card ─────────────────────────────────────────────

describe("RuntimeDoctorCardView", () => {
  const base: RuntimeDoctorCardItem = {
    kind: "runtime_doctor_card",
    id: "rd1",
    reachable: true,
    serverUrl: "http://127.0.0.1:8642",
    cluster: "test-cluster",
    tuiVersion: "0.1.0",
    serverVersion: "0.1.0",
    tuiProtocol: "1",
    serverProtocol: "1",
    lang: "en",
    mode: "confirm",
    capturedAt: "2026-08-20T14:31:40",
    checks: [
      {
        name: "llm_api_key",
        severity: "blocking",
        passed: true,
        message: "configured",
        fix: "",
      },
    ],
    passedCount: 1,
    totalCount: 1,
    preflightUnavailable: false,
  };

  it("renders the unified row list with the client-version label", () => {
    render(<RuntimeDoctorCardView item={base} />);
    expect(screen.getByText("Diagnostics")).toBeInTheDocument();
    expect(screen.getByText("http://127.0.0.1:8642")).toBeInTheDocument();
    expect(screen.getByText("test-cluster")).toBeInTheDocument();
    // The field carries THIS host's build — "client version", not the
    // TUI's "tui version".
    expect(screen.getByText("client version")).toBeInTheDocument();
    expect(screen.queryByText("tui version")).not.toBeInTheDocument();
    expect(screen.getByText("llm_api_key")).toBeInTheDocument();
    // The terminal-background probe is a TUI-only concern.
    expect(screen.queryByText("terminal background")).not.toBeInTheDocument();
  });

  it("flags an unreachable server with its fix", () => {
    render(<RuntimeDoctorCardView item={{ ...base, reachable: false }} />);
    expect(screen.getByText("(unreachable)")).toBeInTheDocument();
    expect(
      screen.getByText(/Check the blade-ai server is running/),
    ).toBeInTheDocument();
  });

  it("flags a protocol mismatch as a warning row", () => {
    render(<RuntimeDoctorCardView item={{ ...base, serverProtocol: "2" }} />);
    expect(screen.getByText(/→/)).toBeInTheDocument();
    expect(
      screen.getByText(/Restart the server or upgrade/),
    ).toBeInTheDocument();
  });

  it("surfaces preflight-unavailable as its own warn row when reachable", () => {
    render(
      <RuntimeDoctorCardView
        item={{ ...base, preflightUnavailable: true, checks: [] }}
      />,
    );
    // The warn row's name AND the fixes block's name column both
    // carry the label — hence getAllByText.
    expect(
      screen.getAllByText("environment self-check").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(/Preflight probe didn't respond/),
    ).toBeInTheDocument();
  });
});

// ── memory_card ─────────────────────────────────────────────────────

describe("MemoryCardView", () => {
  it("renders the session snapshot rows", () => {
    const item: MemoryCardItem = {
      kind: "memory_card",
      id: "mc1",
      sessionId: "sess_abc123",
      startedAt: "2026-08-20T10:00:00",
      status: "active",
      cluster: "",
      namespace: "demo",
      recentTasks: ["T1", "T2"],
      totalTasks: 5,
      stats: {
        message_count: 12,
        injection_count: 3,
        injection_success: 2,
        injection_fail: 1,
        recovery_count: 2,
      },
      memoryDir: "/tmp/mem",
      capturedAt: "2026-08-20T14:32:00",
    };
    render(<MemoryCardView item={item} />);
    expect(screen.getByText("Session memory")).toBeInTheDocument();
    expect(screen.getByText("sess_abc123")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
    // Empty cluster renders the dim placeholder, not a blank value.
    expect(screen.getByText("(unset)")).toBeInTheDocument();
    expect(screen.getByText("Tasks (2/5)")).toBeInTheDocument();
    expect(screen.getByText("T2")).toBeInTheDocument();
    expect(screen.getByText("T1")).toBeInTheDocument();
    expect(screen.getByText("Messages")).toBeInTheDocument();
    expect(screen.getByText("✓ 2")).toBeInTheDocument();
    expect(screen.getByText("✗ 1")).toBeInTheDocument();
    expect(screen.getByText("/tmp/mem")).toBeInTheDocument();
  });
});

// ── help_card ───────────────────────────────────────────────────────

describe("HelpCardView", () => {
  it("renders sections, top/sub rows and the tip", () => {
    const item: HelpCardItem = {
      kind: "help_card",
      id: "hc1",
      capturedAt: "2026-08-20T14:33:00",
      sections: [
        {
          heading: "General",
          rows: [
            { kind: "top", name: "/help", description: "Show this help" },
            { kind: "top", name: "/model", description: "Manage models" },
            { kind: "sub", name: "list", description: "List candidates" },
          ],
        },
      ],
      tip: "Tip: type / then TAB to autocomplete",
    };
    render(<HelpCardView item={item} />);
    expect(screen.getByText("Commands")).toBeInTheDocument();
    expect(screen.getByText(/── General/)).toBeInTheDocument();
    expect(screen.getByText("/help")).toBeInTheDocument();
    expect(screen.getByText("Show this help")).toBeInTheDocument();
    // Subcommands indent under their parent.
    expect(screen.getByText("list").className).toContain("pl-4");
    expect(
      screen.getByText("Tip: type / then TAB to autocomplete"),
    ).toBeInTheDocument();
  });
});

// ── session_card ────────────────────────────────────────────────────

describe("SessionCardView", () => {
  it("renders the row table with dim placeholders", () => {
    const item: SessionCardItem = {
      kind: "session_card",
      id: "sc1",
      capturedAt: "2026-08-20T14:34:00",
      rows: [
        { label: "session id", value: "sess_abc" },
        { label: "cluster", value: "(none)", dim: true },
      ],
    };
    render(<SessionCardView item={item} />);
    expect(screen.getByText("Session")).toBeInTheDocument();
    expect(screen.getByText("sess_abc")).toBeInTheDocument();
    const placeholder = screen.getByText("(none)");
    expect(placeholder.className).toContain("text-forge-text-faint");
  });
});

// ── experiments_card ────────────────────────────────────────────────

describe("ExperimentsCardView", () => {
  it("renders the catalog rows with the count tail", () => {
    const item: ExperimentsCardItem = {
      kind: "experiments_card",
      id: "ec1",
      capturedAt: "2026-08-20T14:35:00",
      totalCount: 2,
      rows: [
        { useCaseName: "Pod_OOM", faultSymptom: "内存接近 Limit 上限" },
        { useCaseName: "Node_CPU", faultSymptom: "" },
      ],
    };
    render(<ExperimentsCardView item={item} />);
    expect(screen.getByText("Experiments")).toBeInTheDocument();
    expect(screen.getByText(/2 cases/)).toBeInTheDocument();
    expect(screen.getByText("Pod_OOM")).toBeInTheDocument();
    expect(screen.getByText("内存接近 Limit 上限")).toBeInTheDocument();
    // Empty symptom falls back to the placeholder.
    expect(screen.getByText("(no symptom)")).toBeInTheDocument();
  });
});

// ── model_card ──────────────────────────────────────────────────────

describe("ModelCardView", () => {
  it("renders provider sections with the active row highlighted", () => {
    const item: ModelCardItem = {
      kind: "model_card",
      id: "mc1",
      capturedAt: "2026-08-20T14:36:00",
      activeModel: "qwen-max",
      apiBaseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      totalCount: 2,
      sections: [
        {
          provider: "qwen",
          rows: [
            { id: "qwen-max", active: true },
            { id: "qwen-plus", active: false, note: "" },
          ],
        },
      ],
    };
    render(<ModelCardView item={item} />);
    expect(screen.getByText("Models")).toBeInTheDocument();
    expect(screen.getByText(/2 models/)).toBeInTheDocument();
    expect(screen.getByText(/── qwen/)).toBeInTheDocument();
    expect(
      screen.getByText("https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ).toBeInTheDocument();
    const active = screen.getByText("qwen-max");
    expect(active.className).toContain("text-forge-accent");
    // Filled vs empty glyph carries the selection.
    expect(screen.getByText("●")).toBeInTheDocument();
    expect(screen.getByText("○")).toBeInTheDocument();
    expect(screen.getByText(/Tip: \/model set/)).toBeInTheDocument();
  });
});
