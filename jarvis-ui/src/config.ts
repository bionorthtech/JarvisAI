/**
 * B2 — runtime configuration constants.
 *
 * Extracted from App.tsx so per-mode files can import these without
 * pulling the whole 5K-line monolith. Keep this file purely
 * declarative — no React, no side effects.
 */
export const BACKEND     = "http://127.0.0.1:8000";
export const WS_BACKEND  = "ws://127.0.0.1:8000";
export const POLL_MS     = 12_000;
