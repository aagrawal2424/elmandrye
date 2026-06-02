/**
 * Brand tokens for the elmandrye animated video summary.
 * Kept in one file so a future tenant-override system can swap them
 * per-shop without rewriting the scene components.
 */

export const BRAND = {
  // Dark olive/forest palette — matches the elmandrye storefront
  bgGradient: ["#0F1E1A", "#1A332C"] as const,
  fgPrimary: "#F4EEDD",
  fgSecondary: "#A8B5AC",
  accent: "#C9A961",
  /** Used for stats / numbers — high-contrast against the bg */
  statColor: "#F4EEDD",
  /** Used for the lower-third "elmandrye" tag */
  tagColor: "rgba(244, 238, 221, 0.45)",
} as const;

export const FONT = {
  // We use @remotion/google-fonts for headless-Chrome-safe font loading
  // (system fonts don't survive in Lambda / GH Actions runners).
  headingFamily: "Inter",
  bodyFamily: "Inter",
} as const;

export const VIDEO = {
  width: 1080,
  height: 1080,
  fps: 30,
  /** Cap; we clamp by actual audio duration at composition time. */
  maxDurationFrames: 30 * 90, // 90s max
} as const;
