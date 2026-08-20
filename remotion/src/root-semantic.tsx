import React from 'react';
import {Composition} from 'remotion';
import {SemanticProofVideo} from './video-semantic';
import {LayoutProbe} from './layout-probe';
import type {ProofTimeline} from './types-semantic';

const emptyTimeline: ProofTimeline = {
  schemaVersion: 1,
  rendererContract: 'strict-proof-transition-v16-goal-forest',
  theorem: 'proof',
  width: 1280,
  height: 720,
  fps: 30,
  durationInFrames: 180,
  initialFrames: 30,
  transitionFrames: 20,
  writeSpeed: 24,
  celebrationFrames: 60,
  completionHoldFrames: 90,
  terminalCompletion: {
    status: 'unknown', source: 'empty-composition', actionIndex: null,
    remainingGoalIds: [], certifiedClosed: false,
  },
  showQed: false,
  edgeReasons: [],
  states: [{
    id: 'empty', proofFrameIndex: 0, tactic: '', lineageId: '', rows: [],
    goalForest: {
      id: 'empty', stateFingerprint: '', rootCardIds: [], focusCardIds: [],
      activeCardId: null, introducedCardIds: [], retiredCardIds: [],
      closedCardIds: [], cards: [],
    },
  }],
  transitions: [],
};

export const RemotionSemanticRoot: React.FC = () => (
  <>
    <Composition
      id="ProofVideo"
      component={SemanticProofVideo}
      width={1280}
      height={720}
      fps={30}
      durationInFrames={180}
      defaultProps={emptyTimeline}
      calculateMetadata={({props}) => ({
        width: props.width,
        height: props.height,
        fps: props.fps,
        durationInFrames: props.durationInFrames,
      })}
    />
    <Composition
      id="ProofLayoutProbe"
      component={LayoutProbe}
      width={1280}
      height={720}
      fps={30}
      durationInFrames={1}
      defaultProps={emptyTimeline}
      calculateMetadata={({props}) => ({
        width: props.width,
        height: props.height,
        fps: props.fps,
        durationInFrames: Math.max(1, props.states.length),
      })}
    />
  </>
);
