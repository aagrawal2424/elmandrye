import React, { useEffect, useState } from "react";
import {
  AbsoluteFill,
  Audio,
  Sequence,
  cancelRender,
  continueRender,
  delayRender,
  staticFile,
  useVideoConfig,
} from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { z } from "remotion/zod";
import { loadFont } from "@remotion/google-fonts/Inter";
import { TitleScene } from "./scenes/TitleScene";
import { PointScene } from "./scenes/PointScene";
import { CtaScene } from "./scenes/CtaScene";
import { BRAND, VIDEO } from "./brand";
import type { VideoPoint } from "./types";

// Bundle Inter so headless Chrome doesn't fall back to a system font.
// waitUntilDone() returns a promise that resolves once all font weights
// have loaded — we block render until then to avoid the title-card flash
// of fallback font (FOUT). This is a documented Remotion gotcha and more
// visible during render than in studio.
const { fontFamily: interFamily, waitUntilDone } = loadFont();

export const videoSummarySchema = z.object({
  title: z.string(),
  handle: z.string(),
  points: z
    .array(
      z.object({
        headline: z.string(),
        body: z.string(),
      }),
    )
    .min(1)
    .max(5),
  audioSrc: z.string(),
  audioDurationSec: z.number().min(3).max(120),
  heroImageSrc: z.string().nullable().optional(),
});

export const defaultVideoSummaryProps: z.infer<typeof videoSummarySchema> = {
  title: "EPA vs DHA: what the research actually shows",
  handle: "epa-vs-dha-what-the-research-actually-shows-about-omega-3-ratios",
  points: [
    {
      headline: "The ratio matters",
      body: "Most studies use 2:1 EPA-to-DHA, not the random blends on shelves.",
    },
    {
      headline: "EPA leads on mood",
      body: "Trials targeting depressive symptoms favor EPA-dominant formulas.",
    },
    {
      headline: "DHA leads on brain",
      body: "Cognitive and visual endpoints in older adults respond to DHA load.",
    },
  ],
  audioSrc: "narration.mp3",
  audioDurationSec: 24,
  heroImageSrc: null,
};

export const VideoSummary: React.FC<z.infer<typeof videoSummarySchema>> = ({
  title,
  handle,
  points,
  audioSrc,
  audioDurationSec,
  heroImageSrc,
}) => {
  const { fps, durationInFrames } = useVideoConfig();

  // Block render until Inter is fully loaded — otherwise the title
  // card's first frames rasterize with a system fallback font and you
  // get a one-frame swap when Inter arrives mid-render.
  const [fontHandle] = useState(() => delayRender("loading-inter"));
  useEffect(() => {
    waitUntilDone()
      .then(() => continueRender(fontHandle))
      .catch((e) => cancelRender(e));
  }, [fontHandle]);

  // Split the timeline: 18% title, ~equal share for each point, 14% CTA.
  const totalSec = durationInFrames / fps;
  const titleSec = Math.max(2.5, totalSec * 0.18);
  const ctaSec = Math.max(2.5, totalSec * 0.14);
  const pointsTotalSec = Math.max(3, totalSec - titleSec - ctaSec);
  const perPointSec = pointsTotalSec / Math.max(1, points.length);

  const titleFrames = Math.round(titleSec * fps);
  const perPointFrames = Math.round(perPointSec * fps);
  const ctaFrames = Math.round(ctaSec * fps);

  const transitionFrames = Math.round(fps * 0.5);

  return (
    <AbsoluteFill style={{ backgroundColor: BRAND.bgGradient[0], fontFamily: interFamily }}>
      <AbsoluteFill
        style={{
          background: `linear-gradient(160deg, ${BRAND.bgGradient[0]} 0%, ${BRAND.bgGradient[1]} 100%)`,
        }}
      />
      <TransitionSeries>
        <TransitionSeries.Sequence durationInFrames={titleFrames}>
          <TitleScene title={title} heroImageSrc={heroImageSrc ?? null} />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          timing={linearTiming({ durationInFrames: transitionFrames })}
          presentation={fade()}
        />

        {points.flatMap((p: VideoPoint, i: number) => {
          const seqKey = `pt-${i}`;
          const isLast = i === points.length - 1;
          const seq = (
            <TransitionSeries.Sequence
              key={seqKey}
              durationInFrames={perPointFrames}
            >
              <PointScene index={i} total={points.length} headline={p.headline} body={p.body} />
            </TransitionSeries.Sequence>
          );
          if (isLast) return [seq];
          return [
            seq,
            <TransitionSeries.Transition
              key={`tr-${i}`}
              timing={linearTiming({ durationInFrames: transitionFrames })}
              presentation={fade()}
            />,
          ];
        })}

        <TransitionSeries.Transition
          timing={linearTiming({ durationInFrames: transitionFrames })}
          presentation={fade()}
        />

        <TransitionSeries.Sequence durationInFrames={ctaFrames}>
          <CtaScene handle={handle} />
        </TransitionSeries.Sequence>
      </TransitionSeries>

      <Sequence from={0}>
        <Audio
          src={audioSrc.startsWith("http") ? audioSrc : staticFile(audioSrc)}
          // Trim audio to the video length if narration runs slightly long.
          endAt={durationInFrames}
        />
      </Sequence>

      {/* Static brand lower-third */}
      <AbsoluteFill
        style={{
          justifyContent: "flex-end",
          alignItems: "center",
          paddingBottom: 36,
          pointerEvents: "none",
        }}
      >
        <div
          style={{
            fontFamily: interFamily,
            fontWeight: 500,
            fontSize: 22,
            letterSpacing: 4,
            textTransform: "uppercase",
            color: BRAND.tagColor,
          }}
        >
          elm & rye
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// Keep the VIDEO export referenced so tree-shaking doesn't drop the brand
// metadata at bundle time (Remotion's bundler is aggressive).
export const _videoMeta = VIDEO;
