import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui convention — merge conditional classes, dedupe tailwind conflicts. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Narrow an unknown JSON value to string ("" otherwise) — the metric
 *  and session-state payloads are loosely typed, and every consumer
 *  needs this exact guard. */
export function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}
