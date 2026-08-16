import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import {renderFrames, selectComposition} from '@remotion/renderer';

const stableHash = (value) => crypto
  .createHash('sha256')
  .update(JSON.stringify(value))
  .digest('hex');

const validManifest = (manifest, timeline, rendererFingerprint) => {
  if (
    manifest?.schemaVersion !== 1
    || manifest.rendererFingerprint !== rendererFingerprint
    || manifest.width !== timeline.width
    || manifest.height !== timeline.height
  ) return false;
  const layoutsValid = timeline.states.every((state) => {
    const expected = state.rows.reduce((total, row) => total + row.tokens.length, 0);
    return manifest.states?.[state.id]?.boxes?.length === expected;
  });
  const htmlValid = timeline.states.every((state) => state.rows.every(
    (row) => row.tokens.every(([latex]) => typeof manifest.mathHtml?.[latex] === 'string'),
  ));
  return layoutsValid && htmlValid;
};

const readJson = async (file) => {
  try {
    return JSON.parse(await fs.readFile(file, 'utf8'));
  } catch {
    return null;
  }
};

const percentile = (values, fraction) => {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * fraction))];
};

export const ensureLayoutManifest = async ({
  timeline,
  serveUrl,
  puppeteerInstance,
  cacheDir,
  rendererFingerprint,
  force,
}) => {
  const key = stableHash({
    rendererFingerprint,
    width: timeline.width,
    height: timeline.height,
    states: timeline.states.map((state) => ({id: state.id, rows: state.rows})),
  });
  const cacheFile = path.join(cacheDir, key.slice(0, 2), `${key}.json`);
  if (!force) {
    const cached = await readJson(cacheFile);
    if (validManifest(cached, timeline, rendererFingerprint)) {
      return {manifest: cached, cacheHit: true, wallMilliseconds: 0, frameTimes: []};
    }
  }

  const composition = await selectComposition({
    serveUrl,
    id: 'ProofLayoutProbe',
    inputProps: timeline,
    puppeteerInstance,
  });
  const states = {};
  const mathHtml = {};
  const frameTimes = [];
  const started = Date.now();
  await renderFrames({
    serveUrl,
    composition,
    inputProps: timeline,
    puppeteerInstance,
    outputDir: null,
    onFrameBuffer: () => undefined,
    imageFormat: 'jpeg',
    jpegQuality: 70,
    muted: true,
    // One persistent page means the module-level KaTeX cache evaluates every
    // unique token once. Parallel tabs would each rebuild the same HTML cache.
    concurrency: 1,
    logLevel: 'warn',
    onStart: () => undefined,
    onFrameUpdate: (_rendered, _frame, milliseconds) => frameTimes.push(milliseconds),
    onArtifact: (artifact) => {
      const text = typeof artifact.content === 'string'
        ? artifact.content
        : new TextDecoder().decode(artifact.content);
      const entry = JSON.parse(text);
      states[entry.stateId] = entry.data;
      Object.assign(mathHtml, entry.mathHtml ?? {});
    },
  });
  const manifest = {
    schemaVersion: 1,
    rendererFingerprint,
    width: timeline.width,
    height: timeline.height,
    mathHtml,
    states,
  };
  if (!validManifest(manifest, timeline, rendererFingerprint)) {
    throw new Error('Layout preflight did not emit complete geometry for every proof state.');
  }
  await fs.mkdir(path.dirname(cacheFile), {recursive: true});
  const temporary = `${cacheFile}.${process.pid}.tmp`;
  await fs.writeFile(temporary, JSON.stringify(manifest), 'utf8');
  await fs.rename(temporary, cacheFile);
  return {
    manifest,
    cacheHit: false,
    wallMilliseconds: Date.now() - started,
    frameTimes,
    p50Milliseconds: percentile(frameTimes, 0.50),
    p95Milliseconds: percentile(frameTimes, 0.95),
  };
};
