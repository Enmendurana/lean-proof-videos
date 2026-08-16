import React from 'react';
import {Composition} from 'remotion';
import {ProofVideo} from './video';
import type {ProofTimeline} from './types';

const emptyTimeline: ProofTimeline = {
  schemaVersion: 1,
  rendererContract: 'strict-proof-transition-v1',
  theorem: 'proof',
  width: 1280,
  height: 720,
  fps: 30,
  durationInFrames: 90,
  initialFrames: 30,
  transitionFrames: 20,
  edgeReasons: [],
  states: [{id: 'empty', proofFrameIndex: 0, tactic: '', lineageId: '', rows: []}],
  transitions: [],
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="ProofVideo"
    component={ProofVideo}
    width={1280}
    height={720}
    fps={30}
    durationInFrames={90}
    defaultProps={emptyTimeline}
    calculateMetadata={({props}) => ({
      width: props.width,
      height: props.height,
      fps: props.fps,
      durationInFrames: props.durationInFrames,
    })}
  />
);
