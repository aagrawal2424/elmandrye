/**
 * Shape of the JSON payload Python writes to props.json
 * and passes via `--props=props.json` to `remotion render`.
 *
 * Keep in sync with scripts/content_engine/video_summary_remotion.py.
 */

export type VideoPoint = {
  /** ~3-6 words. Shown as the big slide headline. */
  headline: string;
  /** ~1 short sentence. Shown beneath the headline, smaller. */
  body: string;
};

export type VideoSummaryProps = {
  title: string;
  /** Same handle Shopify uses (used for the closing CTA copy). */
  handle: string;
  /** 3-5 points; we clamp to 5 in the composition. */
  points: VideoPoint[];
  /** Path to the narration mp3 *inside the bundle's public/ dir*. */
  audioSrc: string;
  /** Total narration length in seconds — drives composition duration. */
  audioDurationSec: number;
  /** Optional: path to a hero image (inside public/) for the title card. */
  heroImageSrc?: string | null;
};
