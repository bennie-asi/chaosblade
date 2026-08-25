/**
 * Markdown tests — GFM rendering for agent-authored prose.
 *
 * Pinned behaviours:
 *   - inline code chip + fenced block both use the LIGHT well
 *     (bg-forge-code) — dark code blocks are a banned pattern
 *   - links get target=_blank + noopener and the forge accent
 *   - GFM tables render (remark-gfm wired)
 *   - raw HTML from the LLM stays inert text (no rehype-raw)
 *   - headings cap at modest sizes (h3 ceiling for h1/h2)
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

afterEach(() => {
  cleanup();
});

describe("Markdown", () => {
  it("renders inline code with the light well chip", () => {
    render(<Markdown text={"run `kubectl get pods` first"} />);
    const code = screen.getByText("kubectl get pods");
    expect(code.tagName).toBe("CODE");
    expect(code.className).toContain("bg-forge-code");
  });

  it("renders fenced blocks in the light well card (never dark)", () => {
    const { container } = render(
      <Markdown text={"```bash\nblade create k8s pod-cpu fullload\n```"} />,
    );
    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre!.className).toContain("bg-forge-code");
    // The nested <code> must not double up the chip background.
    const code = pre!.querySelector("code");
    expect(code).not.toBeNull();
  });

  it("links open in a new tab with noopener and the forge accent", () => {
    render(<Markdown text={"see [docs](https://chaosblade.io)"} />);
    const link = screen.getByRole("link", { name: "docs" });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(link.className).toContain("text-forge-accent");
  });

  it("renders GFM tables", () => {
    const { container } = render(
      <Markdown text={"| pod | state |\n| --- | --- |\n| web | Running |"} />,
    );
    expect(container.querySelector("table")).not.toBeNull();
    expect(screen.getByText("Running")).toBeTruthy();
  });

  it("keeps raw HTML inert (no rehype-raw)", () => {
    const { container } = render(
      <Markdown text={'payload: <img src=x onerror=alert(1)>'} />,
    );
    // The tag must NOT become a live element — it renders as text.
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x");
  });

  it("caps h1/h2 at modest heading size", () => {
    render(<Markdown text={"# Big title"} />);
    const heading = screen.getByText("Big title");
    // h1 source maps to an h3-sized element — chat prose, not a doc.
    expect(heading.tagName).toBe("H3");
    expect(heading.className).toContain("text-base");
  });

  it("preserves single newlines as <br> (remark-breaks)", () => {
    // Regression guard: the pre-markdown renderer was
    // whitespace-pre-wrap plain text — soft newlines were visible.
    // Without remark-breaks, GFM collapses them onto one line.
    const { container } = render(
      <Markdown text={"集群: prod\n命名空间: default"} />,
    );
    expect(container.querySelector("br")).not.toBeNull();
  });

  it("constrains agent-supplied images to the column width", () => {
    const { container } = render(
      <Markdown text={"![diagram](https://example.com/a.png)"} />,
    );
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img!.className).toContain("max-w-full");
  });

  it("renders lists with restrained spacing", () => {
    const { container } = render(<Markdown text={"- one\n- two"} />);
    const ul = container.querySelector("ul");
    expect(ul).not.toBeNull();
    expect(ul!.className).toContain("list-disc");
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });
});
