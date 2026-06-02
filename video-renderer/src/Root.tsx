import React from "react";
import { Composition } from "remotion";
import { VideoSummary, videoSummarySchema, defaultVideoSummaryProps } from "./VideoSummary";
import { VIDEO } from "./brand";

export const Root: React.FC = () => {
  return (
    <Composition
      id="VideoSummary"
      component={VideoSummary}
      width={VIDEO.width}
      height={VIDEO.height}
      fps={VIDEO.fps}
      // Default duration; the actual render uses calculateMetadata to read
      // audioDurationSec from props and stretch the timeline to fit.
      durationInFrames={VIDEO.fps * 30}
      defaultProps={defaultVideoSummaryProps}
      schema={videoSummarySchema}
      calculateMetadata={({ props }) => {
        const seconds = Math.min(
          props.audioDurationSec || 30,
          VIDEO.maxDurationFrames / VIDEO.fps,
        );
        return {
          durationInFrames: Math.max(VIDEO.fps * 6, Math.ceil(seconds * VIDEO.fps)),
        };
      }}
    />
  );
};
