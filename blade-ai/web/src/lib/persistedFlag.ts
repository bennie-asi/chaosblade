/**
 * localStorage-backed boolean flag with same-tab reactivity — the
 * collapse-state pattern shared by the session panel and the progress
 * rail (and formerly the sidebar / DAG rail, each of which used to
 * hand-roll its own copy).
 *
 * Three requirements drive the shape:
 *
 *  1. Reactivity across CONSUMERS in the same tab: the "storage" event
 *     only fires across tabs, so the writer dispatches a custom event
 *     that ``useSyncExternalStore`` subscribers listen to alongside it.
 *  2. Persistence is best-effort: private mode / sandboxed frames can
 *     throw on localStorage access, so an in-memory mirror keeps the
 *     toggle working (a present localStorage value stays authoritative
 *     for cross-tab sync).
 *  3. Test isolation: suites clean localStorage in afterEach but
 *     cannot reach the module-level mirror — ``resetForTests`` drops
 *     it. Production never needs it: nothing outside tests removes
 *     the key.
 */
import { useCallback, useSyncExternalStore } from "react";

export interface PersistedFlag {
  /** [value, toggle] — re-renders subscribers on every write. */
  (): [boolean, () => void];
  /** Test-only: drop the in-memory mirror (see module docstring). */
  resetForTests: () => void;
  /** The localStorage key, exposed for tests asserting persistence. */
  readonly key: string;
}

export function makePersistedFlag(key: string): PersistedFlag {
  const changeEvent = `${key}-changed`;
  let memory: boolean | null = null;

  function read(): boolean {
    try {
      const v = localStorage.getItem(key);
      if (v !== null) return v === "1";
    } catch {
      // Storage unavailable — fall through to the in-memory mirror.
    }
    return memory ?? false;
  }

  function write(next: boolean): void {
    memory = next;
    try {
      localStorage.setItem(key, next ? "1" : "0");
    } catch {
      // Best-effort persistence — the mirror already updated.
    }
    window.dispatchEvent(new Event(changeEvent));
  }

  function subscribe(onChange: () => void): () => void {
    window.addEventListener(changeEvent, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(changeEvent, onChange);
      window.removeEventListener("storage", onChange);
    };
  }

  const useFlag = () => {
    const value = useSyncExternalStore(subscribe, read, () => false);
    const toggle = useCallback(() => write(!read()), []);
    return [value, toggle] as [boolean, () => void];
  };
  useFlag.resetForTests = () => {
    memory = null;
  };
  useFlag.key = key;
  return useFlag;
}
