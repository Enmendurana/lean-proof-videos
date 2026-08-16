export type ProofRow = {
  key: string;
  kind: 'context' | 'target';
  latex: string;
};

export type ProofState = {
  id: string;
  proofFrameIndex: number;
  tactic: string;
  lineageId: string;
  rows: ProofRow[];
};

export type SemanticNode = [kind: string, spans: Array<[number, number]>];
export type SemanticEdge = [
  sourceNode: number,
  targetNode: number,
  copy: 0 | 1,
  reason: number,
];

export type ProofTransition = {
  fromState: number;
  toState: number;
  startFrame: number;
  durationFrames: number;
  semantic: null | {
    p: string;
    a: string;
    s: SemanticNode[];
    t: SemanticNode[];
    e: SemanticEdge[];
  };
};

export type ProofTimeline = {
  schemaVersion: 1;
  rendererContract: 'strict-proof-transition-v1';
  theorem: string;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  initialFrames: number;
  transitionFrames: number;
  edgeReasons: string[];
  states: ProofState[];
  transitions: ProofTransition[];
};
