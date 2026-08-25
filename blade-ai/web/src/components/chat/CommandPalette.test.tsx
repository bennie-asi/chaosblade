/**
 * CommandPalette tests — the ⌘K command palette (web counterpart of
 * the TUI SlashMenu).
 *
 * Pinned behaviours:
 *   - ⌘K AND Ctrl+K both toggle; Esc and backdrop click close
 *   - filter ranks prefix > substring; hidden commands stay hidden;
 *     subcommands appear as flattened ``/root sub`` rows
 *   - Enter FILLS via onPick (never executes) and closes the palette
 *   - ↑↓ wrap-around navigation moves the selection
 *   - focus is trapped: Tab bounces back to the search input and row
 *     buttons stay out of the tab order (arrows, not focus, select)
 *
 * Reducer/registry semantics (streamSafe gating, alias resolution at
 * dispatch) are covered in core tests; here we only pin the palette's
 * own interaction contract.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildRegistry, configureI18n } from "@blade-ai/core";
import { ui } from "../../lib/uiText";
import { CommandPalette } from "./CommandPalette";

configureI18n("en");

afterEach(() => {
  cleanup();
});

function Harness({ onPick }: { onPick: (fill: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <CommandPalette
      open={open}
      onOpenChange={setOpen}
      registry={buildRegistry()}
      onPick={onPick}
    />
  );
}

function openPalette() {
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  return screen.getByRole("dialog", { name: ui().paletteKbdLabel });
}

/** The search input inside an open palette. */
function searchInput(): HTMLInputElement {
  return screen.getByPlaceholderText(ui().palettePlaceholder);
}

describe("CommandPalette", () => {
  it("⌘K opens the palette with the search input focused; Esc closes", () => {
    render(<Harness onPick={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();

    openPalette();
    expect(document.activeElement).toBe(searchInput());

    fireEvent.keyDown(searchInput(), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Ctrl+K opens as well (win/linux path)", () => {
    render(<Harness onPick={() => {}} />);
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(
      screen.getByRole("dialog", { name: ui().paletteKbdLabel }),
    ).toBeTruthy();
  });

  it("clicking the backdrop closes the palette", () => {
    render(<Harness onPick={() => {}} />);
    const dialog = openPalette();
    // The backdrop is the absolutely-positioned div behind the panel.
    const backdrop = dialog.querySelector(".absolute.inset-0");
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop!);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lists subcommands as flattened rows and keeps hidden commands out", () => {
    render(<Harness onPick={() => {}} />);
    openPalette();
    // /skills list — a flattened subcommand row.
    expect(
      screen.getByRole("button", { name: /\/skills list/ }),
    ).toBeTruthy();
    // /status is hidden — callable but never advertised.
    expect(screen.queryByRole("button", { name: /\/status/ })).toBeNull();
  });

  it("filters by prefix, then Enter fills the composer and closes", () => {
    const onPick = vi.fn();
    render(<Harness onPick={onPick} />);
    openPalette();

    fireEvent.change(searchInput(), { target: { value: "hel" } });
    // /help is the only name-prefix match and must be the top row.
    const help = screen.getByRole("button", { name: /\/help/ });
    expect(help.className).toContain("bg-forge-accent-soft");

    fireEvent.keyDown(searchInput(), { key: "Enter" });
    // /help takes no arguments → fill without a trailing space.
    expect(onPick).toHaveBeenCalledWith("/help");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("fills with a trailing space for commands that expect more input", () => {
    const onPick = vi.fn();
    render(<Harness onPick={onPick} />);
    openPalette();

    fireEvent.change(searchInput(), { target: { value: "skills l" } });
    fireEvent.keyDown(searchInput(), { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith("/skills list ");
  });

  it("↑↓ wrap-around navigation moves the selection", () => {
    const onPick = vi.fn();
    render(<Harness onPick={onPick} />);
    openPalette();

    const input = searchInput();
    const buttons = screen.getAllByRole("button");
    // Wrap from the first row to the LAST row with a single ↑.
    fireEvent.keyDown(input, { key: "ArrowUp" });
    const last = buttons[buttons.length - 1]!;
    expect(last.className).toContain("bg-forge-accent-soft");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    // Wraps back to the first row.
    expect(
      screen.getAllByRole("button")[0]!.className,
    ).toContain("bg-forge-accent-soft");
  });

  it("shows the empty state when nothing matches", () => {
    render(<Harness onPick={() => {}} />);
    openPalette();
    fireEvent.change(searchInput(), { target: { value: "zzz-no-match" } });
    expect(screen.getByText(ui().paletteEmpty)).toBeTruthy();
  });

  it("ignores key-repeat ⌘K (holding the chord must not strobe)", () => {
    render(<Harness onPick={() => {}} />);
    fireEvent.keyDown(window, { key: "k", metaKey: true, repeat: true });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("returns focus to the previously focused element on dismiss", () => {
    render(
      <>
        <textarea data-testid="composer" />
        <Harness onPick={() => {}} />
      </>,
    );
    const textarea = screen.getByTestId("composer");
    textarea.focus();

    openPalette();
    expect(document.activeElement).toBe(searchInput());

    fireEvent.keyDown(searchInput(), { key: "Escape" });
    expect(document.activeElement).toBe(textarea);
  });

  it("traps Tab focus on the search input (Shift+Tab too)", () => {
    render(<Harness onPick={() => {}} />);
    openPalette();
    const input = searchInput();
    expect(document.activeElement).toBe(input);

    // jsdom never moves focus on Tab natively, so asserting
    // activeElement alone would pass vacuously. The non-vacuous pin:
    // fireEvent returns false exactly when a handler called
    // preventDefault — proof the trap actually intercepted the key.
    expect(fireEvent.keyDown(input, { key: "Tab" })).toBe(false);
    expect(document.activeElement).toBe(input);
    expect(fireEvent.keyDown(input, { key: "Tab", shiftKey: true })).toBe(
      false,
    );
    expect(document.activeElement).toBe(input);
  });

  it("keeps row buttons out of the tab order (arrows select, not focus)", () => {
    render(<Harness onPick={() => {}} />);
    openPalette();
    for (const button of screen.getAllByRole("button")) {
      expect(button.tabIndex).toBe(-1);
    }
  });

  it("a fresh open resets the query and selection", () => {
    render(<Harness onPick={() => {}} />);
    openPalette();
    fireEvent.change(searchInput(), { target: { value: "zzz-no-match" } });
    fireEvent.keyDown(searchInput(), { key: "Escape" });

    openPalette();
    expect(searchInput().value).toBe("");
    expect(screen.queryByText(ui().paletteEmpty)).toBeNull();
  });
});
