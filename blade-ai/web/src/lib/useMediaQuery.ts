/**
 * Live media-query hook. jsdom ships no ``window.matchMedia`` — the
 * hook reports ``false`` there (and on ancient engines), so gating
 * logic falls back to the narrow-viewport behaviour in tests unless
 * they stub matchMedia.
 */
import { useCallback, useSyncExternalStore } from "react";

export function useMediaQuery(query: string): boolean {
  // useSyncExternalStore re-subscribes whenever the subscribe fn's
  // identity changes — keep it stable per query value, or every
  // consumer render (HistoryList re-renders per streamed token)
  // churns a matchMedia listener pair for nothing.
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window.matchMedia !== "function") return () => {};
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () =>
      typeof window.matchMedia === "function"
        ? window.matchMedia(query).matches
        : false,
    () => false,
  );
}
