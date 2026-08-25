/**
 * Environment-neutral action-recorder sink (the overflow-probe hook).
 *
 * The store calls ``recordAction`` on every dispatch so a host-side
 * diagnostic probe (the TUI's ``utils/overflowProbe.ts``, gated by
 * ``BLADE_AI_DEBUG_OVERFLOW=1``) can correlate reducer work with frame
 * overflow. Core ships a no-op default; the host installs its recorder
 * via ``setActionRecorder`` at module load.
 */

export type ActionRecorder = (action: { type: string }) => void;

let recorder: ActionRecorder = () => undefined;

/** Install (or clear, with ``null``) the host's action recorder. */
export function setActionRecorder(next: ActionRecorder | null): void {
  recorder = next ?? (() => undefined);
}

/** Called by the store on every dispatch. No-op unless a host recorder
 *  is installed. */
export function recordAction(action: { type: string }): void {
  recorder(action);
}
