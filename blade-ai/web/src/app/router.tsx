/**
 * Route table, as a factory.
 *
 * ``createAppRouter`` takes an optional history so tests can drive the
 * real route table (RootLayout included) under a memory history and
 * pin layout-level behaviour — chiefly "boot happens once across
 * navigation" (router.test.tsx). Production uses the default browser
 * history via the module-level ``router`` singleton below.
 *
 * The route TREE is module-level and shared: it is plain config, safe
 * to hand to any number of router instances, and keeping it concrete
 * is what lets ``Register.router`` name a single type (a factory-
 * inferred return type breaks the declaration merging).
 */
import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  type RouterHistory,
} from "@tanstack/react-router";
import { lazy, Suspense } from "react";
import { RootLayout } from "./RootLayout";
import { ChatPage } from "./ChatPage";
import { TasksPage } from "./TasksPage";
import { TracePage } from "./TracePage";
// Settings is a STATIC import — no heavy deps (no recharts), so a lazy
// chunk would buy nothing; the charts route stays the only lazy one.
import { SettingsPage } from "./SettingsPage";
import { ui } from "../lib/uiText";

// Route-level lazy import: recharts (and everything else this page
// pulls in) stays OUT of the first-paint bundle — design §7.4's
// “图表库路由级懒加载”. Vite emits the page as its own chunk,
// fetched only when /charts is first navigated to.
const ChartsPage = lazy(() => import("./ChartsPage"));

const rootRoute = createRootRoute({ component: RootLayout });
const chatRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: ChatPage,
});
const tasksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tasks",
  component: TasksPage,
});
// Trace lives at two paths: /trace (list + auto-select newest) and
// /trace/$taskId (deep link). The old /tasks/$taskId detail route is
// kept as a bare redirect so existing bookmarks keep working — the
// audit view's only home is now the trace page (2026-08-20 decision:
// the trace page replaced the replay page AND absorbed task detail).
const traceRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/trace",
  component: TracePage,
});
const traceDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/trace/$taskId",
  component: TracePage,
});
const taskDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/tasks/$taskId",
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/trace/$taskId",
      params: { taskId: params.taskId },
      replace: true,
    });
  },
});

// The replay page is retired (2026-08-20 decision: the trace page
// replaced it AND absorbed task detail). /replay stays a bare redirect
// so existing bookmarks land on the audit surface instead of 404ing;
// the recording endpoints + core replay machinery remain for the TUI's
// /replay command, just with no web UI mounted.
const replayRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/replay",
  beforeLoad: () => {
    throw redirect({ to: "/trace", replace: true });
  },
});

const chartsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/charts",
  component: () => (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center text-sm text-forge-text-faint">
          {ui().chartsLoading}
        </div>
      }
    >
      <ChartsPage />
    </Suspense>
  ),
});

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: SettingsPage,
});

const routeTree = rootRoute.addChildren([
  chatRoute,
  tasksRoute,
  traceRoute,
  traceDetailRoute,
  taskDetailRoute,
  replayRoute,
  chartsRoute,
  settingsRoute,
]);

export function createAppRouter(history?: RouterHistory) {
  return createRouter({ routeTree, history });
}

export const router = createAppRouter();

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
