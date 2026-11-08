import React from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
} from "remotion";

const ease = Easing.inOut(Easing.sin);

const clamped = (frame, [from, to], value) =>
  interpolate(frame, [from, to], value, {
    easing: ease,
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

/* Per-scene camera motion. p = 0..1 progress through the scene.
   "parallax" is a two-layer 2.5D fake: a blurred, slower-zooming back layer
   with a sharp, faster front layer clipped to a soft ellipse - enough depth
   for a still to feel alive. */
const layerStyle = (motion, p, layer) => {
  const zoom = (from, to) => clamped(p * 100, [0, 100], [from, to]);
  const shift = (dist) => clamped(p * 100, [0, 100], [-dist, dist]);
  switch (motion) {
    case "zoom-out":
      return { transform: `scale(${zoom(1.18, 1.02)})` };
    case "pan-right":
      return { transform: `scale(1.15) translateX(${shift(3)}%)` };
    case "pan-left":
      return { transform: `scale(1.15) translateX(${shift(-3)}%)` };
    case "parallax": {
      if (layer === "back") {
        return {
          transform: `scale(${zoom(1.25, 1.32)})`,
          filter: "blur(8px) brightness(0.75)",
        };
      }
      return { transform: `scale(${zoom(1.08, 1.2)}) translateX(${shift(-2.5)}%)` };
    }
    case "zoom-in":
    default:
      return { transform: `scale(${zoom(1.02, 1.18)})` };
  }
};

const Still = ({ src }) => (
  <Img
    src={staticFile(src)}
    style={{ width: "100%", height: "100%", objectFit: "cover" }}
  />
);

const MotionScene = ({ src, motion, durationInFrames, fadeFrames, first }) => {
  const frame = useCurrentFrame();
  const p = Math.min(1, frame / durationInFrames);
  const opacity = first
    ? 1
    : clamped(frame, [0, fadeFrames], [0, 1]);

  if (motion === "parallax") {
    return (
      <AbsoluteFill style={{ opacity }}>
        <AbsoluteFill style={layerStyle("parallax", p, "back")}>
          <Still src={src} />
        </AbsoluteFill>
        <AbsoluteFill style={layerStyle("parallax", p, "front")}>
          <div
            style={{
              width: "100%",
              height: "100%",
              overflow: "hidden",
              clipPath: "ellipse(75% 85% at 50% 45%)",
            }}
          >
            <Still src={src} />
          </div>
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill style={{ opacity }}>
      <AbsoluteFill style={layerStyle(motion, p)}>
        <Still src={src} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

export const Storyboard = ({ scenes, fadeFrames = 15 }) => {
  let cursor = 0;
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {scenes.map((s, i) => {
        const start = cursor;
        const last = i === scenes.length - 1;
        cursor += s.durationInFrames;
        return (
          <Sequence
            key={i}
            from={start}
            durationInFrames={s.durationInFrames + (last ? 0 : fadeFrames)}
          >
            <MotionScene
              src={s.src}
              motion={s.motion || "zoom-in"}
              durationInFrames={s.durationInFrames}
              fadeFrames={fadeFrames}
              first={i === 0}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
