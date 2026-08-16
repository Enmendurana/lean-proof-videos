import React from 'react';
import katex from 'katex';
import type {
  LayoutBoxData,
  LayoutStateData,
  ProofRow,
  ProofState,
} from './types-semantic';
import {sanitizeKatexTextCommands} from './latex-display';

export const chalk = '#f1f0e8';
export const dimChalk = '#aaa99f';

export type FlatToken = {
  index: number;
  latex: string;
  row: ProofRow;
  rowIndex: number;
  visualRowIndex: number;
};

export type TokenBox = FlatToken & LayoutBoxData;

export type StateLayout = {
  tokens: FlatToken[];
  visualRows: FlatToken[][];
};

const renderedMath = new Map<string, string>();
export const MathHtmlContext = React.createContext<Record<string, string> | null>(null);

export const independentlyRenderableToken = (latex: string): string => {
  const delimiter = latex.replace(
    /^\\(?:left|right|big|Big|bigg|Bigg|bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr)(?=[()[\]{}|.]|\\)/,
    '',
  );
  return delimiter === '.' ? '' : delimiter;
};

export const renderMath = (latex: string): string => {
  const cached = renderedMath.get(latex);
  if (cached !== undefined) return cached;
  let html: string;
  try {
    html = katex.renderToString(sanitizeKatexTextCommands(latex), {
      displayMode: false,
      throwOnError: false,
      strict: false,
      trust: false,
      output: 'html',
    });
  } catch {
    html = katex.renderToString(String.raw`\text{?}`, {throwOnError: false});
  }
  renderedMath.set(latex, html);
  return html;
};

export const visibleMathUnits = (latex: string): number => {
  const compact = latex
    .replace(/\\(?:mathbb|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}/g, '$1')
    .replace(/\\(?:left|right|quad|qquad)\b|\\[,;!]/g, '')
    .replace(/\\[A-Za-z]+/g, 'x')
    .replace(/[{}]/g, '');
  return Math.max(1, Array.from(compact).length);
};

const preferredLineBreaks = new Set([
  ',', ';', String.raw`\implies`, String.raw`\Rightarrow`, String.raw`\iff`,
  String.raw`\Longleftrightarrow`, '⇔', '↔', String.raw`\land`, String.raw`\lor`,
  String.raw`\vdash`,
]);

export const layoutState = (state: ProofState): StateLayout => {
  const tokens: FlatToken[] = [];
  const visualRows: FlatToken[][] = [];
  let globalIndex = 0;
  const maximumUnits = 68;
  const minimumBreakUnits = 24;
  for (let rowIndex = 0; rowIndex < state.rows.length; rowIndex++) {
    const row = state.rows[rowIndex];
    const pending = row.tokens.map(([latex]) => ({latex, index: globalIndex++}));
    let offset = 0;
    while (offset < pending.length) {
      let units = 0;
      let cursor = offset;
      let preferred = -1;
      while (cursor < pending.length) {
        const nextUnits = visibleMathUnits(pending[cursor].latex);
        if (cursor > offset && units + nextUnits > maximumUnits) break;
        units += nextUnits;
        cursor += 1;
        if (units >= minimumBreakUnits && preferredLineBreaks.has(pending[cursor - 1].latex)) {
          preferred = cursor;
        }
      }
      if (cursor < pending.length && preferred > offset) cursor = preferred;
      if (cursor === offset) cursor += 1;
      const visualRowIndex = visualRows.length;
      const line = pending.slice(offset, cursor).map(({latex, index}) => ({
        index, latex, row, rowIndex, visualRowIndex,
      }));
      visualRows.push(line);
      tokens.push(...line);
      offset = cursor;
    }
  }
  return {tokens: tokens.sort((a, b) => a.index - b.index), visualRows};
};

export const fontSizeFor = (layout: StateLayout, width: number, height: number): number => {
  const longest = Math.max(
    1,
    ...layout.visualRows.map((line) => line.reduce(
      (units, token) => units + visibleMathUnits(token.latex), 0,
    )),
  );
  const byWidth = (width * 0.84) / (longest * 0.58);
  const byHeight = (height * 0.84) / Math.max(3, layout.visualRows.length * 1.3);
  return Math.max(2, Math.min(height * 0.094, byWidth, byHeight));
};

export const centeredTopFor = (
  layout: StateLayout,
  fontSize: number,
  height: number,
): number => {
  const rowHeight = Math.max(fontSize * 1.34, 13);
  const visibleHeight = layout.visualRows.length <= 1
    ? fontSize * 1.22
    : (layout.visualRows.length - 1) * rowHeight + fontSize * 1.22;
  return (height - visibleHeight) / 2;
};

const relationTokens = new Set([
  '=', '<', '>', String.raw`\le`, String.raw`\leq`, String.raw`\ge`, String.raw`\geq`,
  String.raw`\ne`, String.raw`\to`, String.raw`\Rightarrow`, String.raw`\implies`,
  String.raw`\iff`, String.raw`\Longleftrightarrow`, '⇔', '↔', String.raw`\vdash`,
  String.raw`\in`, String.raw`\mid`,
]);
const binaryTokens = new Set([
  '+', '-', String.raw`\cdot`, String.raw`\times`, String.raw`\land`, String.raw`\lor`,
]);

const tokenSpacing = (latex: string): React.CSSProperties => {
  if (relationTokens.has(latex)) return {paddingLeft: '0.25em', paddingRight: '0.25em'};
  if (binaryTokens.has(latex)) return {paddingLeft: '0.22em', paddingRight: '0.22em'};
  if (latex === ':' || latex === String.raw`\colon`) {
    return {paddingLeft: '0.12em', paddingRight: '0.18em'};
  }
  if (latex === ',') return {paddingRight: '0.22em'};
  if (latex === ';') return {paddingRight: '0.3em'};
  if (latex === String.raw`\forall` || latex === String.raw`\exists`) {
    return {paddingRight: '0.08em'};
  }
  return {};
};

export const TokenHtml = React.memo<{latex: string}>(({latex}) => {
  const manifestHtml = React.useContext(MathHtmlContext);
  const html = manifestHtml?.[latex]
    ?? renderMath(independentlyRenderableToken(latex));
  return <span
      style={{display: 'inline-block', whiteSpace: 'nowrap', ...tokenSpacing(latex)}}
      dangerouslySetInnerHTML={{__html: html}}
    />;
});
TokenHtml.displayName = 'TokenHtml';

export const MeasurementState: React.FC<{
  layout: StateLayout;
  side: 'source' | 'target' | 'probe';
  fontSize: number;
  top: number;
}> = ({layout, side, fontSize, top}) => {
  const rowHeight = Math.max(fontSize * 1.34, 13);
  return <div style={{position: 'absolute', inset: 0, opacity: 0, pointerEvents: 'none'}}>
    {layout.visualRows.map((line, visualRowIndex) => (
      <div
        key={`${side}-${visualRowIndex}`}
        style={{
          position: 'absolute', left: 0, top: top + visualRowIndex * rowHeight,
          display: 'flex', alignItems: 'baseline', width: 'max-content',
          color: line[0]?.row.kind === 'context' ? dimChalk : chalk,
          fontSize, lineHeight: 1.22, whiteSpace: 'nowrap',
        }}
      >
        {line.map((token) => (
          <span key={token.index} data-layout-side={side} data-token-index={token.index}>
            <TokenHtml latex={token.latex} />
          </span>
        ))}
      </div>
    ))}
  </div>;
};

export const measureLayout = (
  board: HTMLDivElement,
  side: 'source' | 'target' | 'probe',
  layout: StateLayout,
  fontSize: number,
  layoutTop: number,
  height: number,
): LayoutStateData => {
  const boardRect = board.getBoundingClientRect();
  const measured: LayoutBoxData[] = layout.tokens.map((token) => {
    const element = board.querySelector<HTMLElement>(
      `[data-layout-side="${side}"][data-token-index="${token.index}"]`,
    );
    if (!element) throw new Error(`Missing ${side} token ${token.index}`);
    const rect = element.getBoundingClientRect();
    return {
      index: token.index,
      rowIndex: token.rowIndex,
      visualRowIndex: token.visualRowIndex,
      left: rect.left - boardRect.left,
      top: rect.top - boardRect.top,
      width: rect.width,
      height: rect.height,
      fontSize,
    };
  });
  const rowRights = new Map<number, number>();
  for (const box of measured) {
    rowRights.set(
      box.visualRowIndex,
      Math.max(rowRights.get(box.visualRowIndex) ?? 0, box.left + box.width),
    );
  }
  const widest = Math.max(1, ...rowRights.values());
  const scale = Math.min(1, (boardRect.width * 0.94) / widest);
  if (scale >= 0.999) return {boxes: measured};
  const fittedFontSize = fontSize * scale;
  const fittedTop = centeredTopFor(layout, fittedFontSize, height);
  return {
    boxes: measured.map((box) => ({
      ...box,
      left: box.left * scale,
      top: fittedTop + (box.top - layoutTop) * scale,
      width: box.width * scale,
      height: box.height * scale,
      fontSize: fittedFontSize,
    })),
  };
};

export const materializeBoxes = (
  layout: StateLayout,
  data: LayoutStateData | undefined,
): TokenBox[] | null => {
  if (!data || data.boxes.length !== layout.tokens.length) return null;
  const tokens = new Map(layout.tokens.map((token) => [token.index, token]));
  const boxes: TokenBox[] = [];
  for (const box of data.boxes) {
    const token = tokens.get(box.index);
    if (!token) return null;
    boxes.push({...token, ...box});
  }
  return boxes.sort((left, right) => left.index - right.index);
};
