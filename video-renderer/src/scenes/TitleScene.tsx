import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { BRAND } from "../brand";

type Props = {
  title: string;
  heroImageSrc: string | null;
};

export const TitleScene: React.FC<Props> = ({ title, heroImageSrc }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Ken-Burns: slow zoom + slight pan over the hero.
  const zoom = interpolate(frame, [0, durationInFrames], [1.0, 1.12]);
  const panX = interpolate(frame, [0, durationInFrames], [-12, 12]);

  // Title slides up + fades in via a soft spring.
  const enter = spring({
    frame,
    fps,
    config: { damping: 200, stiffness: 80, mass: 0.6 },
    durationInFrames: Math.round(fps * 0.9),
  });
  const titleY = interpolate(enter, [0, 1], [40, 0]);
  const titleOpacity = interpolate(enter, [0, 1], [0, 1]);

  return (
    <AbsoluteFill>
      {heroImageSrc ? (
        <AbsoluteFill
          style={{
            transform: `scale(${zoom}) translateX(${panX}px)`,
            transformOrigin: "center",
          }}
        >
          <Img
            src={heroImageSrc.startsWith("http") ? heroImageSrc : staticFile(heroImageSrc)}
            style={{ width: "100%", height: "100%", objectFit: "cover", opacity: 0.55 }}
          />
        </AbsoluteFill>
      ) : null}
      <AbsoluteFill
        style={{
          background: heroImageSrc
            ? "linear-gradient(180deg, rgba(15,30,26,0.25) 0%, rgba(15,30,26,0.85) 100%)"
            : "transparent",
        }}
      />
      <AbsoluteFill
        style={{
          justifyContent: "center",
          alignItems: "center",
          padding: 80,
          textAlign: "center",
        }}
      >
        <div
          style={{
            transform: `translateY(${titleY}px)`,
            opacity: titleOpacity,
            color: BRAND.fgPrimary,
            fontWeight: 700,
            fontSize: 80,
            lineHeight: 1.05,
            letterSpacing: -1,
            maxWidth: 900,
          }}
        >
          {title}
        </div>
        <div
          style={{
            marginTop: 28,
            opacity: titleOpacity * 0.85,
            color: BRAND.accent,
            fontWeight: 600,
            fontSize: 22,
            letterSpacing: 6,
            textTransform: "uppercase",
          }}
        >
          The research, briefly
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
