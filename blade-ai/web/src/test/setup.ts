import "@testing-library/jest-dom/vitest";

// React's act() environment flag — required for direct act() calls in
// tests (e.g. advancing fake timers through a component's setInterval
// ticker). RTL's fireEvent wraps act internally, but manual
// ``vi.advanceTimersByTime`` stepping needs this opt-in or React logs
// "not configured to support act(...)".
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom never implemented window.scrollTo — calling it logs a noisy
// "Not implemented" jsdom error per call. TanStack Router scrolls to
// top on every navigation, so every routed test would spew it. Stub
// it to a no-op (scroll behaviour is not what these tests assert).
window.scrollTo = () => {};
