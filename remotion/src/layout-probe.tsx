import React from 'react';
import {
  AbsoluteFill,
  Artifact,
  useCurrentFrame,
  useDelayRender,
} from 'remotion';
import {
  centeredTopFor,
  fontSizeFor,
  independentlyRenderableToken,
  layoutState,
  MeasurementState,
  measureLayout,
  renderMath,
} from './layout-engine';
import type {LayoutStateData, ProofTimeline} from './types-semantic';

/** Measure one proof state per frame and emit its exact browser geometry.
 *
 * This short preflight composition is rendered once per unique state.  The
 * main composition then consumes the emitted manifest and never performs DOM
 * queries or font-dependent layout during ordinary video frames.
 */
export const LayoutProbe: React.FC<ProofTimeline> = (timeline) => {
  const frame = useCurrentFrame();
  const stateIndex = Math.min(Math.max(0, frame), timeline.states.length - 1);
  const state = timeline.states[stateIndex];
  const layout = React.useMemo(() => layoutState(state), [state]);
  const fontSize = fontSizeFor(layout, timeline.width, timeline.height);
  const top = centeredTopFor(layout, fontSize, timeline.height);
  const mathHtml = React.useMemo(() => Object.fromEntries(
    layout.tokens.map((token) => [
      token.latex,
      renderMath(independentlyRenderableToken(token.latex)),
    ]),
  ), [layout.tokens]);
  const boardRef = React.useRef<HTMLDivElement>(null);
  const [measurement, setMeasurement] = React.useState<{
    stateId: string;
    data: LayoutStateData;
  } | null>(null);
  const {delayRender, continueRender, cancelRender} = useDelayRender();
  const handle = React.useMemo(
    () => delayRender(`Measuring proof state ${state.id}`, {timeoutInMilliseconds: 30000}),
    [delayRender, state.id],
  );

  React.useLayoutEffect(() => {
    let active = true;
    document.fonts.ready.then(() => {
      if (!active || !boardRef.current) return;
      try {
        const data = measureLayout(
          boardRef.current,
          'probe',
          layout,
          fontSize,
          top,
          timeline.height,
        );
        setMeasurement({stateId: state.id, data});
        continueRender(handle);
      } catch (error: unknown) {
        cancelRender(error instanceof Error ? error : new Error(String(error)));
      }
    }).catch((error: unknown) => {
      cancelRender(error instanceof Error ? error : new Error(String(error)));
    });
    return () => { active = false; };
  }, [cancelRender, continueRender, fontSize, handle, layout, state.id, timeline.height, top]);

  return <AbsoluteFill style={{backgroundColor: '#000', overflow: 'hidden'}}>
    <div ref={boardRef} style={{
      position: 'absolute', left: timeline.width * 0.07, top: 0,
      width: timeline.width * 0.88, height: timeline.height,
    }}>
      <MeasurementState layout={layout} side="probe" fontSize={fontSize} top={top} />
    </div>
    {measurement?.stateId === state.id ? <Artifact
      filename={`layout-${String(stateIndex).padStart(8, '0')}.json`}
      content={JSON.stringify({
        stateIndex,
        stateId: state.id,
        data: measurement.data,
        mathHtml,
      })}
    /> : null}
  </AbsoluteFill>;
};
