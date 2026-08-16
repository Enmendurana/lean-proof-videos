import React from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  useDelayRender,
  useCurrentFrame,
} from 'remotion';
import type {ProofRow, ProofState, ProofTimeline, ProofTransition} from './types';

const chalk = '#f1f0e8';
const dimChalk = '#aaa99f';

const renderMath = (latex: string): string => {
  try {
    return katex.renderToString(latex, {
      displayMode: false,
      throwOnError: false,
      strict: false,
      trust: false,
      output: 'html',
    });
  } catch {
    return katex.renderToString(String.raw`\text{unrenderable expression}`, {
      throwOnError: false,
    });
  }
};

const Row: React.FC<{
  row: ProofRow;
  y: number;
  opacity: number;
  scale: number;
  blur: number;
  reveal: number;
  fontSize: number;
  animated?: boolean;
}> = ({row, y, opacity, scale, blur, reveal, fontSize, animated = true}) => (
  <div
    data-proof-row={row.key}
    style={{
      position: 'absolute',
      left: 0,
      top: y,
      color: row.kind === 'context' ? dimChalk : chalk,
      fontSize,
      lineHeight: 1.22,
      whiteSpace: 'nowrap',
      width: 'max-content',
      height: fontSize * 1.28,
      contain: 'layout paint',
      opacity,
      ...(animated ? {
        filter: `blur(${blur}px)`,
        transform: `scale(${scale})`,
        transformOrigin: 'left center',
        clipPath: `inset(0 ${Math.max(0, (1 - reveal) * 100)}% 0 0)`,
        willChange: 'transform, opacity, filter, clip-path',
      } : {}),
    }}
    dangerouslySetInnerHTML={{__html: renderMath(row.latex)}}
  />
);

const rowMap = (state: ProofState): Map<string, ProofRow> =>
  new Map(state.rows.map((row) => [row.key, row]));

const activeTransition = (
  timeline: ProofTimeline,
  frame: number,
): {transition: ProofTransition | null; progress: number} => {
  const transition = [...timeline.transitions]
    .reverse()
    .find((item) => frame >= item.startFrame);
  if (!transition) return {transition: null, progress: 0};
  return {
    transition,
    progress: interpolate(
      frame,
      [transition.startFrame, transition.startFrame + transition.durationFrames],
      [0, 1],
      {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.inOut(Easing.cubic)},
    ),
  };
};

const visibleMathUnits = (latex: string): number => {
  // Count rendered mathematical units rather than LaTeX source bytes.  A
  // command such as `\mathbb{R}` occupies roughly one glyph, not ten.
  const compact = latex
    .replace(/\\(?:mathbb|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}/g, '$1')
    .replace(/\\(?:left|right|quad|qquad)\b|\\[,;!]/g, '')
    .replace(/\\[A-Za-z]+/g, 'x')
    .replace(/[{}]/g, '');
  return Math.max(1, Array.from(compact).length);
};

const fontSizeFor = (state: ProofState, width: number, height: number): number => {
  const longest = Math.max(1, ...state.rows.map((row) => visibleMathUnits(row.latex)));
  const byWidth = (width * 0.78) / (longest * 0.58);
  const byHeight = (height * 0.75) / Math.max(4, state.rows.length * 1.3);
  const cameraMaximum = height * 0.055;
  // Never crop a certified formula.  Very long implementation-level terms
  // may force an extreme camera pull-back; notation cleanup belongs upstream.
  return Math.max(2, Math.min(cameraMaximum, byWidth, byHeight));
};

export const ProofVideo: React.FC<ProofTimeline> = (timeline) => {
  const {delayRender, continueRender, cancelRender} = useDelayRender();
  const [fontHandle] = React.useState(() =>
    delayRender('Waiting for deterministic KaTeX fonts', {timeoutInMilliseconds: 30000}),
  );
  React.useEffect(() => {
    let active = true;
    document.fonts.ready.then(() => {
      if (active) continueRender(fontHandle);
    }).catch((error: unknown) => {
      if (active) cancelRender(error instanceof Error ? error : new Error(String(error)));
    });
    return () => {
      active = false;
    };
  }, [cancelRender, continueRender, fontHandle]);
  const frame = useCurrentFrame();
  if (!timeline.states.length) return <AbsoluteFill style={{backgroundColor: '#000'}} />;

  const {transition, progress} = activeTransition(timeline, frame);
  const source = transition
    ? timeline.states[transition.fromState]
    : timeline.states[0];
  const target = transition
    ? timeline.states[transition.toState]
    : timeline.states[0];
  const oldRows = rowMap(source);
  const newRows = rowMap(target);
  const sourceFontSize = fontSizeFor(source, timeline.width, timeline.height);
  const targetFontSize = fontSizeFor(target, timeline.width, timeline.height);
  const fontSize = interpolate(progress, [0, 1], [sourceFontSize, targetFontSize]);
  const sourceRowHeight = Math.max(sourceFontSize * 1.34, timeline.height * 0.018);
  const targetRowHeight = Math.max(targetFontSize * 1.34, timeline.height * 0.018);
  const top = timeline.height * 0.13;

  const allKeys = Array.from(new Set([...oldRows.keys(), ...newRows.keys()]));
  const rows: React.ReactNode[] = [];
  for (const key of allKeys) {
    const oldRow = oldRows.get(key);
    const newRow = newRows.get(key);
    const oldIndex = oldRow ? source.rows.findIndex((row) => row.key === key) : -1;
    const newIndex = newRow ? target.rows.findIndex((row) => row.key === key) : -1;
    const oldY = top + Math.max(0, oldIndex) * sourceRowHeight;
    const newY = top + Math.max(0, newIndex) * targetRowHeight;
    const y = oldIndex >= 0 && newIndex >= 0
      ? interpolate(progress, [0, 1], [oldY, newY])
      : oldIndex >= 0 ? oldY : newY;

    if (oldRow && newRow && oldRow.latex === newRow.latex) {
      rows.push(
        <Row key={key} row={newRow} y={y} opacity={1} scale={1} blur={0} reveal={1} fontSize={fontSize} animated={false} />,
      );
      continue;
    }
    if (oldRow) {
      const departure = interpolate(progress, [0, 0.45, 0.68, 1], [1, 1, 0, 0]);
      rows.push(
        <Row key={`${key}-old`} row={oldRow} y={oldY} opacity={departure} scale={1} blur={(1 - departure) * 1.5} reveal={1} fontSize={fontSize} />,
      );
    }
    if (newRow) {
      const entrance = interpolate(
        progress,
        oldRow ? [0.48, 1] : [0, 1],
        [0, 1],
        {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
      );
      rows.push(
        <Row
          key={`${key}-new`}
          row={newRow}
          y={newY}
          opacity={entrance}
          scale={interpolate(entrance, [0, 1], [1.18, 1])}
          blur={interpolate(entrance, [0, 1], [6, 0])}
          reveal={oldRow ? 1 : entrance}
          fontSize={fontSize}
        />,
      );
    }
  }

  const finalStart = timeline.durationInFrames - Math.round(1.6 * timeline.fps);
  const qed = interpolate(frame, [finalStart, finalStart + timeline.fps], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill style={{backgroundColor: '#000', overflow: 'hidden'}}>
      <div
        style={{
          position: 'absolute',
          left: timeline.width * 0.07,
          top: 0,
          width: timeline.width * 0.88,
          height: timeline.height,
        }}
      >
        {rows}
        <div
          style={{
            position: 'absolute',
            right: timeline.width * 0.015,
            bottom: timeline.height * 0.1,
            width: fontSize * 0.55,
            height: fontSize * 0.55,
            border: `2px solid ${chalk}`,
            opacity: qed,
            transform: `scale(${interpolate(qed, [0, 1], [1.5, 1])})`,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
