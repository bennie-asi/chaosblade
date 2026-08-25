/**
 * Rail-collapse persistence (design §7.2 可折叠) — a thin wrapper
 * over the shared ``makePersistedFlag`` (localStorage + in-memory
 * mirror + same-tab custom event; see lib/persistedFlag.ts). The key
 * is new for the v2 rail: the old dagRailCollapsed value governed a
 * different surface and is deliberately not carried over.
 *
 * Consumers: ProgressRail (owns the toggle) and ChatPage (feeds the
 * visibility rule that ProgressRailContext distributes).
 */
import { makePersistedFlag } from "../../lib/persistedFlag";

export const useRailCollapsed = makePersistedFlag("blade-ai.railCollapsed");
