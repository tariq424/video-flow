import { Composition } from "remotion";
import { Storyboard } from "./Storyboard";
import { Film, filmFrames } from "./Film";

export const totalFrames = (props) => {
  const { scenes = [] } = props;
  if (!scenes.length) return 1;
  // scenes sit at their FULL length; crossfades come from each scene's
  // extended tail overlapping the next one, so the total duration stays
  // exactly the sum (== the narration length when aligned)
  return Math.max(1, scenes.reduce((acc, s) => acc + s.durationInFrames, 0));
};

export const RemotionRoot = () => (
  <>
  <Composition
    id="Film"
    component={Film}
    durationInFrames={1}
    fps={30}
    width={1920}
    height={1080}
    defaultProps={{ items: [], overlays: [], total: 1 }}
    calculateMetadata={({ props }) => ({ durationInFrames: filmFrames(props) })}
  />
  <Composition
    id="Storyboard"
    component={Storyboard}
    durationInFrames={1}
    fps={30}
    width={1920}
    height={1080}
    defaultProps={{ scenes: [], fadeFrames: 15 }}
    calculateMetadata={({ props }) => ({
      durationInFrames: totalFrames(props),
    })}
  />
  </>
);
