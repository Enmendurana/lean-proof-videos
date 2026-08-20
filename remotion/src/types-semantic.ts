export type ProofToken = [latex: string, start: number, end: number];

export type ProofRow = {
  id?: string;
  key: string;
  kind: 'context' | 'annotation' | 'target';
  latex: string;
  globalStart: number;
  tokens: ProofToken[];
  goalCardId?: string;
  goalId?: string;
  goalDepth?: number;
  goalOrder?: number;
  goalActive?: boolean;
  goalFocused?: boolean;
  goalRelation?: string;
};

export type GoalCard = {
  id: string;
  goalId: string;
  lineageId: string;
  parentCardIds: string[];
  rootCardIds: string[];
  depth: number;
  order: number;
  siblingOrder: number;
  branchKind: string;
  branchIndex: number | null;
  focusRank: number | null;
  active: boolean;
  incomingRelation: string;
};

export type GoalForest = {
  id: string;
  stateFingerprint: string;
  rootCardIds: string[];
  focusCardIds: string[];
  activeCardId: string | null;
  introducedCardIds: string[];
  retiredCardIds: string[];
  closedCardIds: string[];
  cards: GoalCard[];
};

export type ProofState = {
  id: string;
  proofFrameIndex: number;
  tactic: string;
  lineageId: string;
  rows: ProofRow[];
  goalForest?: GoalForest;
};

export type LayoutBoxData = {
  index: number;
  rowIndex: number;
  visualRowIndex: number;
  left: number;
  top: number;
  width: number;
  height: number;
  fontSize: number;
};

export type LayoutStateData = {
  boxes: LayoutBoxData[];
};

export type LayoutManifest = {
  schemaVersion: 1;
  rendererFingerprint: string;
  width: number;
  height: number;
  mathHtml: Record<string, string>;
  states: Record<string, LayoutStateData>;
};

export type ProofTransition = {
  fromState: number;
  toState: number;
  startFrame: number;
  durationFrames: number;
  pacing?: 'opening' | 'accelerating' | 'cruise' | 'decelerating' | 'closing';
  moveEnd?: number;
  writeStart?: number;
  writeEnd?: number;
  semantic: unknown;
  plan: null | {
    pairs: Array<[source: number, target: number, copy: 0 | 1]>;
    created: number[];
    deleted: number[];
    source?: 'canonical-visual-plan' | 'legacy-semantic-transition';
    primitives?: Array<{
      id: string;
      kind:
        | 'keep'
        | 'move'
        | 'copy'
        | 'rewrite'
        | 'create'
        | 'remove'
        | 'split'
        | 'merge'
        | 'close'
        | 'focus'
        | 'reorder';
      sourceAnchors: string[];
      targetAnchors: string[];
      sourceSlots: unknown[][];
      targetSlots: unknown[][];
      persistentIds: string[];
      scope: string;
      fallback: string;
    }>;
    staging?: null | {
      phaseRanges: [
        storage: [number, number],
        derivation: [number, number],
        substitution: [number, number],
      ];
      pairPhases: Array<0 | 1 | 2>;
      pairViaTargets: Array<number | null>;
      createdPhases: Array<0 | 1 | 2>;
      deletedPhases: Array<0 | 1 | 2>;
      substitutionGhosts: Array<{
        source: number;
        viaTarget: number;
        targetIndices: number[];
      }>;
    };
  };
};

export type ProofTimeline = {
  schemaVersion: 1;
  rendererContract:
    | 'strict-proof-transition-v1'
    | 'strict-proof-transition-v2-stable-rows'
    | 'strict-proof-transition-v3-mandatory-stable-rows'
    | 'strict-proof-transition-v4-pinned-premises'
    | 'strict-proof-transition-v5-carried-conclusions'
    | 'strict-proof-transition-v6-certified-current-context'
    | 'strict-proof-transition-v7-in-place-instantiation'
    | 'strict-proof-transition-v8-staged-instantiation-context'
    | 'strict-proof-transition-v9-temporal-dedup-multisource'
    | 'strict-proof-transition-v10-advancing-stored-conclusion'
    | 'strict-proof-transition-v11-split-forall-specialization'
    | 'strict-proof-transition-v12-consumed-forall-row'
    | 'strict-proof-transition-v13-action-lineage'
    | 'strict-proof-transition-v14-staged-proof-use'
     | 'strict-proof-transition-v15-overlapped-proof-use'
     | 'strict-proof-transition-v16-goal-forest';
  theorem: string;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  initialFrames: number;
  transitionFrames: number;
  activeFrames?: number;
  writeSpeed: number;
  pacingProfile?: 'continuous-slow-glide-slow-v2' | 'continuous-typing-envelope-v3' | 'unified-global-step-clock-v4' | 'unified-global-step-clock-v5' | 'step-relative-motion-and-writing-v6' | 'step-relative-long-closing-v7' | 'ten-second-edges-step-relative-v8' | 'gradual-twenty-action-edges-v9' | 'natural-half-speed-edges-v10' | 'fixed-absolute-endpoint-speed-v11' | 'unified-continuous-master-clock-v12' | 'unified-continuous-master-clock-3x-cap-v13' | 'ten-second-endpoint-plateaus-v14';
  celebrationFrames?: number;
  completionHoldFrames: number;
  terminalCompletion: {
    status: 'certified-closed' | 'open' | 'unknown';
    source: string;
    actionIndex: number | null;
    remainingGoalIds: string[];
    certifiedClosed: boolean;
  };
  showQed: boolean;
  edgeReasons: string[];
  states: ProofState[];
  transitions: ProofTransition[];
  layoutManifest?: LayoutManifest;
};
