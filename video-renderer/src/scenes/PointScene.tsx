import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { BRAND } from "../brand";

type Props = {
  index: number;
  total: number;
  headline: string;
  body: string;
};

export const PointScene: React.FC<Props> = ({ index, total, headline, body }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Number badge pops in first, then headline, then body.
  const badge = spring({ frame, fps, config: { damping: 12, stiffness: 220, mass: 0.5 } });
  const headlineSpring = spring({
    frame: frame - Math.round(fps * 0.12),
    fps,
    config: { damping: 14, stiffness: 160, mass: 0.7 },
  });
  const bodySpring = spring({
    frame: frame - Math.round(fps * 0.28),
    fps,
    config: { damping: 16, stiffness: 140, mass: 0.7 },
  });

  const badgeScale = interpolate(badge, [0, 1], [0.6, 1]);
  const headlineX = interpolate(headlineSpring, [0, 1], [-60, 0]);
  const headlineOpacity = interpolate(headlineSpring, [0, 1], [0, 1]);
  const bodyY = interpolate(bodySpring, [0, 1], [24, 0]);
  const bodyOpacity = interpolate(bodySpring, [0, 1], [0, 1]);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        alignItems: "flex-start",
        padding: 96,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 18,
          marginBottom: 28,
          transform: `scale(${badgeScale})`,
          transformOrigin: "left center",
        }}
      >
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: 14,
            background: BRAND.accent,
            color: BRAND.bgGradient[0],
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            fontWeight: 700,
            fontSize: 28,
          }}
        >
          {index + 1}
        </div>
        <div
          style={{
            color: BRAND.fgSecondary,
            fontWeight: 600,
            letterSpacing: 4,
            textTransform: "uppercase",
            fontSize: 18,
          }}
        >
          Point {index + 1} of {total}
        </div>
      </div>

      <div
        style={{
          transform: `translateX(${headlineX}px)`,
          opacity: headlineOpacity,
          color: BRAND.fgPrimary,
          fontWeight: 700,
          fontSize: 78,
          lineHeight: 1.05,
          letterSpacing: -1,
          maxWidth: 900,
        }}
      >
        {headline}
      </div>

      <div
        style={{
          marginTop: 28,
          transform: `translateY(${bodyY}px)`,
          opacity: bodyOpacity * 0.9,
          color: BRAND.fgPrimary,
          fontWeight: 400,
          fontSize: 34,
          lineHeight: 1.35,
          maxWidth: 880,
        }}
      >
        {body}
      </div>
    </AbsoluteFill>
  );
};
