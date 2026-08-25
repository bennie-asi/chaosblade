/**
 * MessageView memo regression test.
 *
 * Why this exists: HistoryList re-renders on every streamed token
 * (``pending`` changes), and MessageView used to be an un-memoised
 * function component — every committed AgentMessage re-parsed its
 * markdown at token rate (O(transcript) work per token). The memo is
 * the fix; its correctness rests on the core reducer's immutability
 * contract (untouched items keep their reference). This file pins the
 * memo itself: same item reference → NO re-render; replaced item
 * object → re-render.
 *
 * The Markdown module is mocked with a render counter so the expensive
 * parse is directly observable.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { configureI18n, type AgentItem } from "@blade-ai/core";

const { markdownSpy } = vi.hoisted(() => ({ markdownSpy: vi.fn() }));

vi.mock("./Markdown", () => ({
  Markdown: (props: { text: string }) => {
    markdownSpy(props.text);
    return null;
  },
}));

import { MessageView } from "./messages";

configureI18n("en");

afterEach(() => {
  cleanup();
  markdownSpy.mockClear();
});

describe("MessageView memoisation", () => {
  it("skips re-render (and the markdown re-parse) when the item reference is unchanged", () => {
    const item: AgentItem = { kind: "agent", id: "a1", text: "hello" };
    const { rerender } = render(<MessageView item={item} />);
    expect(markdownSpy).toHaveBeenCalledTimes(1);

    // The token-stream scenario: the parent re-renders because some
    // OTHER pending item grew; this committed item's reference is
    // untouched and its render must be a no-op.
    rerender(<MessageView item={item} />);
    expect(markdownSpy).toHaveBeenCalledTimes(1);

    // A replaced item object (the reducer's spread-copy update) must
    // still re-render — memo must never go stale.
    rerender(<MessageView item={{ ...item, text: "hello world" }} />);
    expect(markdownSpy).toHaveBeenCalledTimes(2);
    expect(markdownSpy).toHaveBeenLastCalledWith("hello world");
  });
});
