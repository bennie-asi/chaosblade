/**
 * Progress-rail visibility, distributed from ChatPage to the chat
 * column. One boolean answers "is the right progress rail actually on
 * screen" (drill live AND expanded AND viewport ≥1250px); consumers
 * use it for the two §7.2 零重复 fallback rules — HistoryList keeps
 * node-progress lines only when the rail is away, LivePhaseStepper
 * surfaces only when the rail is away — and for the chat column's
 * 980px ↔ 780px width switch (mock v2 layout rule).
 */
import { createContext, useContext } from "react";

export const ProgressRailContext = createContext(false);

export function useProgressRailVisible(): boolean {
  return useContext(ProgressRailContext);
}
