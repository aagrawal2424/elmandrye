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
  handle: string;
};

export const CtaScene: React.FC<Props> = ({ handle: _handle }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 140, mass: 0.7 },
  });
  const lineEnter = spring({
    frame: frame - Math.round(fps * 0.2),
    fps,
    config: { damping: 20, stiffness: 130, mass: 0.7 },
  });

  const topOpacity = interpolate(enter, [0, 1], [0, 1]);
  const topY = interpolate(enter, [0, 1], [16, 0]);
  const lineWidth = interpolate(lineEnter, [0, 1], [0, 360]);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        alignItems: "center",
        textAlign: "center",
        padding: 96,
      }}
    >
      <div
        style={{
          opacity: topOpacity,
          transform: `translateY(${topY}px)`,
          color: BRAND.accent,
          fontWeight: 600,
          fontSize: 22,
          letterSpacing: 6,
          textTransform: "uppercase",
        }}
      >
        Keep reading
      </div>

      <div
        style={{
          opacity: topOpacity,
          transform: `translateY(${topY}px)`,
          color: BRAND.fgPrimary,
          fontWeight: 700,
          fontSize: 72,
          lineHeight: 1.05,
          letterSpacing: -1,
          marginTop: 22,
          maxWidth: 920,
        }}
      >
        Full breakdown on elmandrye.com
      </div>

      <div
        style={{
          marginTop: 40,
          width: lineWidth,
          height: 4,
          background: BRAND.accent,
          borderRadius: 2,
        }}
      />

      <div
        style={{
          opacity: topOpacity * 0.85,
          color: BRAND.fgSecondary,
          fontWeight: 400,
          fontSize: 28,
          lineHeight: 1.4,
          marginTop: 40,
          maxWidth: 820,
        }}
      >
        Read the full study notes, dosages, and what to look for on a label.
      </div>
    </AbsoluteFill>
  );
};
