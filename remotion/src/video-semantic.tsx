import React from 'react';
import 'katex/dist/katex.min.css';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  useCurrentFrame,
  useDelayRender,
} from 'remotion';
import type {ProofRow, ProofState, ProofTimeline, ProofTransition} from './types-semantic';
import {transitionAtFrame} from './semantic-timeline';
import {
  chalk,
  centeredTopFor,
  dimChalk,
  fontSizeFor,
  layoutState,
  MathHtmlContext,
  materializeBoxes,
  MeasurementState,
  measureLayout,
  TokenHtml,
  type TokenBox,
  visibleMathUnits,
} from './layout-engine';

const smootherstep = (value: number): number => {
  const clamped = Math.max(0, Math.min(1, value));
  return clamped ** 3 * (clamped * (clamped * 6 - 15) + 10);
};

const activeTransition = (
  timeline: ProofTimeline,
  frame: number,
): {transition: ProofTransition | null; progress: number} => {
  const transition = transitionAtFrame(timeline.transitions, frame);
  if (!transition) return {transition: null, progress: 0};
  const linearProgress = interpolate(
    frame,
    [transition.startFrame, transition.startFrame + transition.durationFrames],
    [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  const activeProgress = interpolate(
    linearProgress,
    [0, Math.max(0.0001, transition.moveEnd ?? 1)],
    [0, 1],
    {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    },
  );
  return {
    transition,
    progress: smootherstep(activeProgress),
  };
};

const AnimatedToken: React.FC<{
  box: {left: number; top: number; fontSize: number};
  latex: string;
  color: string;
  opacity: number;
  blur?: number;
  scale?: number;
  reveal?: number;
}> = ({box, latex, color, opacity, blur = 0, scale = 1, reveal = 1}) => (
  <div style={{
    position: 'absolute', left: box.left, top: box.top, width: 'max-content',
    color, fontSize: box.fontSize, lineHeight: 1.22, whiteSpace: 'nowrap',
    opacity, filter: blur ? `blur(${blur}px)` : undefined,
    transform: scale === 1 ? undefined : `scale(${scale})`,
    transformOrigin: 'left center', contain: 'layout paint',
    clipPath: reveal >= 1
      ? undefined
      : `inset(-0.25em ${(1 - Math.max(0, reveal)) * 100}% -0.25em -0.12em)`,
  }}>
    <TokenHtml latex={latex} />
  </div>
);

export const SemanticProofVideo: React.FC<ProofTimeline> = (timeline) => {
  const frame = useCurrentFrame();
  const {delayRender, continueRender, cancelRender} = useDelayRender();
  const [fontsReady, setFontsReady] = React.useState(false);
  const continuedHandles = React.useRef(new Set<number>());
  React.useEffect(() => {
    let active = true;
    document.fonts.ready.then(() => active && setFontsReady(true)).catch((error: unknown) => {
      if (active) cancelRender(error instanceof Error ? error : new Error(String(error)));
    });
    return () => { active = false; };
  }, [cancelRender]);

  const {transition, progress} = activeTransition(timeline, frame);
  const source = transition ? timeline.states[transition.fromState] : timeline.states[0];
  const target = transition ? timeline.states[transition.toState] : source;
  const sourceLayout = React.useMemo(() => layoutState(source), [source]);
  const targetLayout = React.useMemo(() => layoutState(target), [target]);
  const sourceTokens = sourceLayout.tokens;
  const targetTokens = targetLayout.tokens;
  const sourceFontSize = fontSizeFor(sourceLayout, timeline.width, timeline.height);
  const targetFontSize = fontSizeFor(targetLayout, timeline.width, timeline.height);
  const finalVisualRowIndex = targetLayout.visualRows.length - 1;
  const layoutKey = `${source.id}->${target.id}@${sourceFontSize}:${targetFontSize}`;
  const layoutHandle = React.useMemo(
    () => delayRender(`Measuring semantic transition ${layoutKey}`, {timeoutInMilliseconds: 30000}),
    [delayRender, layoutKey],
  );
  const sourceTop = centeredTopFor(sourceLayout, sourceFontSize, timeline.height);
  const targetTop = centeredTopFor(targetLayout, targetFontSize, timeline.height);
  const boardRef = React.useRef<HTMLDivElement>(null);
  const [boxes, setBoxes] = React.useState<{
    key: string;
    source: TokenBox[];
    target: TokenBox[];
  } | null>(null);
  const precomputedBoxes = React.useMemo(() => {
    const manifest = timeline.layoutManifest;
    if (!manifest || manifest.width !== timeline.width || manifest.height !== timeline.height) {
      return null;
    }
    const sourceBoxes = materializeBoxes(sourceLayout, manifest.states[source.id]);
    const targetBoxes = materializeBoxes(targetLayout, manifest.states[target.id]);
    if (!sourceBoxes || !targetBoxes) return null;
    return {key: layoutKey, source: sourceBoxes, target: targetBoxes};
  }, [
    layoutKey, source.id, sourceLayout, target.id, targetLayout,
    timeline.height, timeline.layoutManifest, timeline.width,
  ]);
  const resolvedBoxes = precomputedBoxes ?? boxes;

  React.useLayoutEffect(() => {
    if (precomputedBoxes) return;
    if (!fontsReady) return;
    const board = boardRef.current;
    if (!board) return;
    setBoxes({
      key: layoutKey,
      source: materializeBoxes(
        sourceLayout,
        measureLayout(board, 'source', sourceLayout, sourceFontSize, sourceTop, timeline.height),
      ) ?? [],
      target: materializeBoxes(
        targetLayout,
        measureLayout(board, 'target', targetLayout, targetFontSize, targetTop, timeline.height),
      ) ?? [],
    });
  }, [
    fontsReady, layoutKey, precomputedBoxes, sourceLayout, targetLayout,
    sourceFontSize, targetFontSize, sourceTop, targetTop, timeline.height,
  ]);

  React.useEffect(() => {
    if (resolvedBoxes?.key === layoutKey && !continuedHandles.current.has(layoutHandle)) {
      continuedHandles.current.add(layoutHandle);
      continueRender(layoutHandle);
    }
  }, [continueRender, layoutHandle, layoutKey, resolvedBoxes]);

  const finalRowBoxes = resolvedBoxes?.key === layoutKey
    ? resolvedBoxes.target.filter((box) => box.visualRowIndex === finalVisualRowIndex)
    : [];
  const finalWaveRows = new Map<number, {left: number; right: number}>();
  if (resolvedBoxes?.key === layoutKey) {
    for (const box of resolvedBoxes.target) {
      const bounds = finalWaveRows.get(box.visualRowIndex);
      finalWaveRows.set(box.visualRowIndex, {
        left: bounds ? Math.min(bounds.left, box.left) : box.left,
        right: bounds ? Math.max(bounds.right, box.left + box.width) : box.left + box.width,
      });
    }
  }
  const celebrationFrames = timeline.celebrationFrames ?? Math.round(2 * timeline.fps);
  const finalHoldStart = timeline.durationInFrames
    - (timeline.completionHoldFrames ?? 0)
    - celebrationFrames;
  const qedStart = finalHoldStart + Math.round(0.15 * timeline.fps);
  const qedEnd = qedStart + Math.round(0.65 * timeline.fps);
  const waveStart = qedEnd;
  // Preserve the established 1.2-second overlap into the final hold while
  // allowing the timeline to lengthen the visible celebration itself.
  const waveEnd = finalHoldStart + celebrationFrames + Math.round(1.2 * timeline.fps);
  const qed = interpolate(frame, [qedStart, qedEnd], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const waveOffset = (box: TokenBox): number => {
    if (
      !timeline.showQed
      || frame < waveStart
      || frame > waveEnd
    ) return 0;
    const rowBounds = finalWaveRows.get(box.visualRowIndex);
    if (!rowBounds) return 0;
    const rowWidth = Math.max(1, rowBounds.right - rowBounds.left);
    const horizontalPosition = Math.max(
      0,
      Math.min(1, (box.left + box.width / 2 - rowBounds.left) / rowWidth),
    );
    const globalProgress = (frame - waveStart) / Math.max(1, waveEnd - waveStart);
    // Every displayed row receives the same left-to-right crest, so a
    // multi-line final statement celebrates as one proof rather than moving
    // only its last line. A squared sine envelope makes both velocity and
    // displacement vanish at the endpoints, avoiding a visible jerk.
    const localProgress = (globalProgress - horizontalPosition * 0.46) / 0.54;
    if (localProgress < 0 || localProgress > 1) return 0;
    const envelope = Math.sin(localProgress * Math.PI) ** 2;
    const oscillation = Math.sin(localProgress * Math.PI * 2);
    return -oscillation * envelope * targetFontSize * 0.18;
  };
  const sequentialWriteReveal = (
    unitOffset: number,
    unitWidth: number,
    totalUnits: number,
    globalProgress: number,
    start: number,
    end: number,
  ): number => {
    if (totalUnits <= 0 || end <= start) return 1;
    const sweep = interpolate(globalProgress, [start, end], [0, 1], {
      extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    });
    // Adjacent symbols overlap slightly, like continuous handwriting, while
    // retaining a deterministic left-to-right (and then row-to-row) order.
    const tokenStart = unitOffset / totalUnits;
    const tokenEnd = Math.min(
      1,
      (unitOffset + Math.max(unitWidth, 1.8)) / totalUnits,
    );
    return interpolate(sweep, [tokenStart, tokenEnd], [0, 1], {
      extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
      easing: Easing.inOut(Easing.quad),
    });
  };

  const visible: React.ReactNode[] = [];
  if (resolvedBoxes?.key === layoutKey) {
    if (!transition) {
      const initialUnits = resolvedBoxes.source.map((token) => visibleMathUnits(token.latex));
      let initialTotalUnits = 0;
      const initialOffsets = initialUnits.map((units) => {
        const offset = initialTotalUnits;
        initialTotalUnits += units;
        return offset;
      });
      for (const token of resolvedBoxes.source) {
        const initialWrite = sequentialWriteReveal(
          initialOffsets[token.index],
          initialUnits[token.index],
          initialTotalUnits,
          frame / Math.max(1, timeline.initialFrames),
          0,
          1,
        );
        visible.push(<AnimatedToken key={`initial-${token.index}`}
          box={{...token, top: token.top + waveOffset(token)}} latex={token.latex}
          color={token.row.kind === 'context' ? dimChalk : chalk}
          opacity={initialWrite > 0 ? 1 : 0} reveal={initialWrite} />);
      }
    } else {
      const plan = transition.plan ?? {
        pairs: [],
        created: targetTokens.map((token) => token.index),
        deleted: sourceTokens.map((token) => token.index),
      };
      plan.pairs.forEach(([sourceIndex, targetIndex], pairIndex) => {
        const from = resolvedBoxes.source[sourceIndex];
        const to = resolvedBoxes.target[targetIndex];
        if (!from || !to) return;
        const changed = from.latex !== to.latex;
        const currentLatex = changed && progress >= 0.5 ? to.latex : from.latex;
        const rewriteOpacity = changed
          ? interpolate(Math.abs(progress - 0.5), [0, 0.12], [0, 1], {extrapolateRight: 'clamp'})
          : 1;
        const rewriteReveal = changed && progress >= 0.5
          ? interpolate(progress, [0.5, 0.94], [0, 1], {
              extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
              easing: Easing.inOut(Easing.quad),
            })
          : 1;
        visible.push(<AnimatedToken
          key={`pair-${pairIndex}-${sourceIndex}-${targetIndex}`}
          box={{
            left: interpolate(progress, [0, 1], [from.left, to.left]),
            top: interpolate(progress, [0, 1], [from.top, to.top]) + waveOffset(to),
            fontSize: interpolate(progress, [0, 1], [from.fontSize, to.fontSize]),
          }}
          latex={currentLatex}
          color={to.row.kind === 'context' ? dimChalk : chalk}
          // A certified COPY starts exactly on top of its persistent source.
          // Fading the clone in made short transitions look like fresh
          // handwriting near the destination, even though its geometry was
          // correct. Keep every mapped proof object opaque from the first
          // frame: the clone then visibly separates from the premise and
          // slides to its result, while genuinely new tokens continue through
          // the sequential handwriting path below.
          opacity={rewriteOpacity}
          reveal={rewriteReveal}
        />);
      });
      for (const sourceIndex of plan.deleted) {
        const token = resolvedBoxes.source[sourceIndex];
        if (!token) continue;
        visible.push(<AnimatedToken key={`deleted-${sourceIndex}`} box={token} latex={token.latex}
          color={token.row.kind === 'context' ? dimChalk : chalk}
          opacity={interpolate(progress, [0, 0.55], [1, 0], {extrapolateRight: 'clamp'})} />);
      }
      const createdInWritingOrder = [...plan.created].sort((left, right) => left - right);
      const createdUnits = createdInWritingOrder.map(
        (targetIndex) => visibleMathUnits(resolvedBoxes.target[targetIndex]?.latex ?? ''),
      );
      let createdTotalUnits = 0;
      const createdOffsets = createdUnits.map((units) => {
        const offset = createdTotalUnits;
        createdTotalUnits += units;
        return offset;
      });
      for (const [createdOrder, targetIndex] of createdInWritingOrder.entries()) {
        const token = resolvedBoxes.target[targetIndex];
        if (!token) continue;
        const written = sequentialWriteReveal(
          createdOffsets[createdOrder],
          createdUnits[createdOrder],
          createdTotalUnits,
          progress,
          transition.writeStart ?? 0.34,
          transition.writeEnd ?? 0.98,
        );
        visible.push(<AnimatedToken key={`created-${targetIndex}`}
          box={{
            ...token,
            top: token.top + waveOffset(token),
          }}
          latex={token.latex} color={token.row.kind === 'context' ? dimChalk : chalk}
          opacity={written > 0 ? 1 : 0} reveal={written} />);
      }
    }
  }

  const currentFontSize = interpolate(progress, [0, 1], [sourceFontSize, targetFontSize]);
  const qedSize = currentFontSize * 1.02;
  const qedPosition = finalRowBoxes.length
    ? {
        left: timeline.width * 0.88 - qedSize,
        top: (() => {
          const rowTop = Math.min(...finalRowBoxes.map((box) => box.top));
          const rowBottom = Math.max(...finalRowBoxes.map((box) => box.top + box.height));
          return rowTop + (rowBottom - rowTop - qedSize) / 2;
        })(),
      }
    : null;

  return <MathHtmlContext.Provider value={timeline.layoutManifest?.mathHtml ?? null}>
  <AbsoluteFill style={{backgroundColor: '#000', overflow: 'hidden'}}>
    <div ref={boardRef} style={{
      position: 'absolute', left: timeline.width * 0.07, top: 0,
      width: timeline.width * 0.88, height: timeline.height,
    }}>
      {!precomputedBoxes ? <MeasurementState layout={sourceLayout} side="source" fontSize={sourceFontSize} top={sourceTop} /> : null}
      {!precomputedBoxes ? <MeasurementState layout={targetLayout} side="target" fontSize={targetFontSize} top={targetTop} /> : null}
      {visible}
      {timeline.showQed && qedPosition ? <div style={{
        position: 'absolute', left: qedPosition.left, top: qedPosition.top,
        width: qedSize, height: qedSize,
        boxSizing: 'border-box', border: `${Math.max(2, qedSize * 0.055)}px solid ${chalk}`, opacity: qed,
        transform: `scale(${interpolate(qed, [0, 1], [1.5, 1])})`,
        transformOrigin: 'center',
      }} /> : null}
    </div>
  </AbsoluteFill>
  </MathHtmlContext.Provider>;
};
