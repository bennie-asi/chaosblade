/**
 * RailNav tests — the app-wide icon rail (design §6, mock col-1).
 * Pins the five entries' presence and the active-highlight rule:
 * exact match for chat, fuzzy for tasks/trace so the detail routes
 * (/trace/$taskId) keep their parent lit, settings sunk to the
 * bottom. Icon-only rail — the label rides aria-label, which is what
 * these queries target.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { configureI18n } from "@blade-ai/core";
import { RailNav } from "./RailNav";
import { ui } from "../lib/uiText";

configureI18n("en");

afterEach(cleanup);

function renderAt(path: string) {
  const rootRoute = createRootRoute({
    component: () => (
      <>
        <RailNav />
        <Outlet />
      </>
    ),
  });
  const routes = ["/", "/tasks", "/trace", "/trace/$taskId", "/charts", "/settings"].map(
    (p) =>
      createRoute({
        getParentRoute: () => rootRoute,
        path: p,
        component: () => null,
      }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(routes),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}

/** The active rail item is accent text + accent-soft wash (computed
 *  class list, never a filled pill). Assertions must AWAIT the item:
 *  RouterProvider's first paint is async (router.load), so a sync
 *  getByLabelText right after render() sees an empty tree. */
function isActive(el: HTMLElement): boolean {
  return el.className.includes("text-forge-accent");
}

describe("RailNav", () => {
  it("renders all five entries with their text labels", async () => {
    renderAt("/");
    for (const label of [
      ui().navChat,
      ui().navTasks,
      ui().navTrace,
      ui().navCharts,
      ui().navSettings,
    ]) {
      expect(await screen.findByLabelText(label)).toBeInTheDocument();
    }
  });

  it("lights chat on / and nothing else", async () => {
    renderAt("/");
    expect(isActive(await screen.findByLabelText(ui().navChat))).toBe(true);
    expect(isActive(screen.getByLabelText(ui().navTasks))).toBe(false);
    expect(isActive(screen.getByLabelText(ui().navTrace))).toBe(false);
    expect(isActive(screen.getByLabelText(ui().navSettings))).toBe(false);
  });

  it("keeps the trace entry lit on the detail route (/trace/$taskId)", async () => {
    renderAt("/trace/inject-abc-123");
    expect(isActive(await screen.findByLabelText(ui().navTrace))).toBe(true);
    expect(isActive(screen.getByLabelText(ui().navTasks))).toBe(false);
    expect(isActive(screen.getByLabelText(ui().navChat))).toBe(false);
  });

  it("lights settings on /settings", async () => {
    renderAt("/settings");
    expect(isActive(await screen.findByLabelText(ui().navSettings))).toBe(true);
    expect(isActive(screen.getByLabelText(ui().navChat))).toBe(false);
  });
});
