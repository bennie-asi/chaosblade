/**
 * Rail navigation (design §6, mock col-1) — the app-wide icon rail.
 * Five entries: chat / tasks / trace / charts on top, settings sunk to
 * the bottom (VSCode convention). Icon-only at 60px; the label rides
 * the ``title``/``aria-label`` tooltip (spec: 图标 + 文字标签).
 *
 * Forge rules honoured here (same as the old Sidebar):
 * - no border between rail and content — the bg-forge-sidebar vs
 *   bg-forge-bg value step IS the separator (明度分层);
 * - the active item is accent text + accent-soft wash, never a filled
 *   colour-block pill.
 *
 * Session history is NOT this component's job — the chat route renders
 * the SessionPanel column itself (chat-scoped, collapsible), keeping
 * the rail identical on every page.
 */
import { Link, useMatchRoute, type LinkProps } from "@tanstack/react-router";
import {
  Activity,
  ChartColumn,
  ClipboardList,
  MessageSquare,
  Settings,
} from "lucide-react";
import { ui } from "../lib/uiText";

function RailItem({
  to,
  fuzzy,
  icon,
  label,
}: {
  to: LinkProps["to"];
  /** fuzzy keeps the item lit on the detail route (/trace/$taskId). */
  fuzzy?: boolean;
  icon: React.ReactNode;
  label: string;
}) {
  const matchRoute = useMatchRoute();
  // Class list is computed, not layered via activeProps: two competing
  // text-colour utilities in one class attribute resolve by stylesheet
  // order, not attribute order — computing keeps the winner explicit.
  const active = Boolean(matchRoute({ to, fuzzy }));
  return (
    <Link
      to={to}
      aria-label={label}
      title={label}
      className={`grid size-10 place-items-center rounded-button ${
        active
          ? "bg-forge-accent-soft text-forge-accent"
          : "text-forge-text-faint hover:bg-forge-border/40 hover:text-forge-text"
      }`}
    >
      {icon}
    </Link>
  );
}

export function RailNav() {
  return (
    <nav
      aria-label={ui().railNavLabel}
      className="flex w-[60px] shrink-0 flex-col items-center gap-0.5 bg-forge-sidebar py-3"
    >
      {/* Product glyph — static brand mark, not a link (mock col-1). */}
      <div className="mb-2 grid size-[34px] place-items-center rounded-[9px] bg-forge-accent text-[15px] font-bold text-white">
        B
      </div>
      <RailItem
        to="/"
        icon={<MessageSquare className="size-[18px]" />}
        label={ui().navChat}
      />
      <RailItem
        to="/tasks"
        fuzzy
        icon={<ClipboardList className="size-[18px]" />}
        label={ui().navTasks}
      />
      <RailItem
        to="/trace"
        fuzzy
        icon={<Activity className="size-[18px]" />}
        label={ui().navTrace}
      />
      <RailItem
        to="/charts"
        icon={<ChartColumn className="size-[18px]" />}
        label={ui().navCharts}
      />
      {/* Settings sinks to the bottom rail (VSCode convention). */}
      <div className="mt-auto">
        <RailItem
          to="/settings"
          icon={<Settings className="size-[18px]" />}
          label={ui().navSettings}
        />
      </div>
    </nav>
  );
}
