import React from "react";
import {
  AbsoluteFill,
  Easing,
  Freeze,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

/* The "Film" composition: a cinematic timeline of
   - depth-layer stills (2.5D parallax: nearer layers move further),
   - video clips (muted here; their sound is mixed by ffmpeg),
   - overlays: chapter cards and location lower-thirds.
   Props come from film_timeline.py; every time is in frames. */

const ease = Easing.inOut(Easing.sin);
const prog = (f, d) => interpolate(f, [0, Math.max(1, d)], [0, 1], { easing: ease, extrapolateLeft: "clamp", extrapolateRight: "clamp" });

// camera moves: x/y in % of frame, s = scale; depth multiplies the motion per layer
const MOVES = {
  "push":       { x: [0, 0],    y: [0, -0.6], s: [1.06, 1.16] },
  "pull":       { x: [0, 0],    y: [-0.6, 0], s: [1.16, 1.06] },
  "pan-left":   { x: [2.2, -2.2], y: [0, 0],  s: [1.12, 1.12] },
  "pan-right":  { x: [-2.2, 2.2], y: [0, 0],  s: [1.12, 1.12] },
  "rise":       { x: [0, 0],    y: [1.6, -1.6], s: [1.12, 1.14] },
};
const DEPTH = [0.35, 1.0, 1.5];   // back layer moves least

const LayersShot = ({ layers, dur, move }) => {
  const f = useCurrentFrame();
  const p = prog(f, dur);
  const m = MOVES[move] || MOVES.push;
  return (
    <AbsoluteFill style={{ overflow: "hidden", background: "#000" }}>
      {layers.map((src, i) => {
        const k = DEPTH[i] ?? 1;
        const x = interpolate(p, [0, 1], m.x) * k;
        const y = interpolate(p, [0, 1], m.y) * k;
        const s = 1 + (interpolate(p, [0, 1], m.s) - 1) * (0.7 + 0.3 * k);
        return (
          <Img key={i} src={staticFile(src)} style={{
            position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover",
            transform: `translate(${x}%, ${y}%) scale(${s})`,
          }} />
        );
      })}
    </AbsoluteFill>
  );
};

const Clip = ({ src, dur, clipFrames }) => {
  const f = useCurrentFrame();
  const v = <OffthreadVideo src={staticFile(src)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />;
  // a clip shorter than its slot holds its last frame
  if (clipFrames && f >= clipFrames - 1) return <AbsoluteFill><Freeze frame={clipFrames - 2}>{v}</Freeze></AbsoluteFill>;
  return <AbsoluteFill>{v}</AbsoluteFill>;
};

const SERIF = "Georgia, 'Times New Roman', serif";
const SANS = "'Segoe UI', Arial, sans-serif";

const ChapterCard = ({ num, title, dur }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = interpolate(f, [0, fps * 0.9, dur - fps * 0.9, dur], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const rule = interpolate(f, [fps * 0.3, fps * 1.6], [0, 260], { easing: ease, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const lift = interpolate(f, [0, fps * 1.2], [14, 0], { easing: ease, extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ opacity: a, alignItems: "center", justifyContent: "center",
      background: "radial-gradient(ellipse at center, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0.25) 55%, rgba(0,0,0,0) 80%)" }}>
      <div style={{ textAlign: "center", color: "#fff", transform: `translateY(${lift}px)`,
        textShadow: "0 2px 18px rgba(0,0,0,0.8)" }}>
        <div style={{ fontFamily: SANS, fontSize: 26, letterSpacing: 12, fontWeight: 600, opacity: 0.85 }}>{num}</div>
        <div style={{ height: 2, width: rule, background: "#E9C46A", margin: "22px auto 24px" }} />
        <div style={{ fontFamily: SERIF, fontSize: 92, fontWeight: 400, letterSpacing: 2 }}>{title}</div>
      </div>
    </AbsoluteFill>
  );
};

const LowerThird = ({ text, sub, dur }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = interpolate(f, [0, fps * 0.6, dur - fps * 0.6, dur], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const bar = interpolate(f, [0, fps * 0.8], [0, 1], { easing: ease, extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ opacity: a }}>
      <div style={{ position: "absolute", left: 110, bottom: 120, color: "#fff", textShadow: "0 2px 12px rgba(0,0,0,0.85)" }}>
        <div style={{ height: 3, width: 70 * bar, background: "#E9C46A", marginBottom: 16 }} />
        <div style={{ fontFamily: SANS, fontSize: 38, fontWeight: 700, letterSpacing: 6 }}>{text}</div>
        {sub ? <div style={{ fontFamily: SANS, fontSize: 24, letterSpacing: 8, opacity: 0.8, marginTop: 8 }}>{sub}</div> : null}
      </div>
    </AbsoluteFill>
  );
};

const Caption = ({ text, dur }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = interpolate(f, [0, fps * 0.4, dur - fps * 0.4, dur], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ opacity: a, justifyContent: "flex-end", alignItems: "center", paddingBottom: 110 }}>
      <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 40, letterSpacing: 4, color: "#fff", padding: "16px 34px",
        background: "rgba(8,10,16,0.62)", borderLeft: "4px solid #E9C46A", textShadow: "0 1px 6px rgba(0,0,0,0.8)" }}>{text}</div>
    </AbsoluteFill>
  );
};

// Aris's camera: viewfinder corners, blinking REC, running timecode
const Viewfinder = ({ dur, tc = 0 }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = interpolate(f, [0, 6, dur - 6, dur], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const t = tc + f / fps;
  const pad = (n) => String(Math.floor(n)).padStart(2, "0");
  const c = { position: "absolute", width: 90, height: 90, borderColor: "rgba(255,255,255,0.9)", borderStyle: "solid", borderWidth: 0 };
  return (
    <AbsoluteFill style={{ opacity: a, fontFamily: "Consolas, monospace", color: "#fff", textShadow: "0 0 6px rgba(0,0,0,0.9)" }}>
      <div style={{ ...c, left: 70, top: 60, borderLeftWidth: 4, borderTopWidth: 4 }} />
      <div style={{ ...c, right: 70, top: 60, borderRightWidth: 4, borderTopWidth: 4 }} />
      <div style={{ ...c, left: 70, bottom: 60, borderLeftWidth: 4, borderBottomWidth: 4 }} />
      <div style={{ ...c, right: 70, bottom: 60, borderRightWidth: 4, borderBottomWidth: 4 }} />
      <div style={{ position: "absolute", left: 110, top: 90, fontSize: 40, fontWeight: 700, display: "flex", alignItems: "center", gap: 14 }}>
        <span style={{ width: 26, height: 26, borderRadius: 13, background: "#e53935", opacity: Math.floor(f / 15) % 2 ? 0.25 : 1 }} />REC
      </div>
      <div style={{ position: "absolute", right: 110, top: 90, fontSize: 36 }}>{`00:${pad(t / 60)}:${pad(t % 60)}:${pad((f % fps) * 25 / fps)}`}</div>
      <div style={{ position: "absolute", left: 110, bottom: 90, fontSize: 30, opacity: 0.85 }}>SUA PAN  05:42  4K</div>
      <div style={{ position: "absolute", left: "50%", top: "50%", width: 60, height: 60, marginLeft: -30, marginTop: -30,
        border: "2px solid rgba(255,255,255,0.7)" }} />
    </AbsoluteFill>
  );
};

// TV news headline card
const Headline = ({ text, sub, dur, tag = "BREAKING NEWS" }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a = interpolate(f, [0, fps * 0.35, dur - fps * 0.35, dur], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const slide = interpolate(f, [0, fps * 0.5], [-80, 0], { easing: ease, extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ opacity: a, justifyContent: "flex-end", paddingBottom: 120 }}>
      <div style={{ transform: `translateX(${slide}px)`, marginLeft: 110, display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
        <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: 30, letterSpacing: 4, color: "#fff", background: "#c62828", padding: "8px 22px" }}>{tag}</div>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 56, color: "#111", background: "rgba(255,255,255,0.96)", padding: "14px 30px", maxWidth: 1500 }}>{text}</div>
        {sub ? <div style={{ fontFamily: SANS, fontSize: 30, color: "#fff", background: "rgba(10,20,40,0.85)", padding: "10px 30px" }}>{sub}</div> : null}
      </div>
    </AbsoluteFill>
  );
};

const Fade = ({ xf, children }) => {
  const f = useCurrentFrame();
  const o = xf ? interpolate(f, [0, xf], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 1;
  return <AbsoluteFill style={{ opacity: o }}>{children}</AbsoluteFill>;
};

export const filmFrames = (props) => Math.max(1, props.total || 1);

export const Film = ({ items = [], overlays = [] }) => (
  <AbsoluteFill style={{ background: "#000" }}>
    {items.map((it, i) => (
      <Sequence key={"i" + i} from={it.from} durationInFrames={it.dur}>
        <Fade xf={it.xf}>
          {it.kind === "video"
            ? <Clip src={it.src} dur={it.dur} clipFrames={it.clipFrames} />
            : <LayersShot layers={it.layers} dur={it.dur} move={it.move} />}
        </Fade>
      </Sequence>
    ))}
    {overlays.map((o, i) => (
      <Sequence key={"o" + i} from={o.from} durationInFrames={o.dur}>
        {o.kind === "chapter" ? <ChapterCard num={o.num} title={o.title} dur={o.dur} />
          : o.kind === "lower" ? <LowerThird text={o.text} sub={o.sub} dur={o.dur} />
          : o.kind === "rec" ? <Viewfinder dur={o.dur} tc={o.tc || 0} />
          : o.kind === "headline" ? <Headline text={o.text} sub={o.sub} tag={o.tag} dur={o.dur} />
          : <Caption text={o.text} dur={o.dur} />}
      </Sequence>
    ))}
  </AbsoluteFill>
);
