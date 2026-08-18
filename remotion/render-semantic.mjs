import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {openBrowser, renderStill, selectComposition} from '@remotion/renderer';
import {ensureBundle} from './bundle-cache.mjs';
import {renderChunks, renderFull} from './chunk-scheduler.mjs';
import {ensureLayoutManifest} from './layout-prepass.mjs';
import {
  benchmarkFrames,
  calibrateConcurrency,
  calibrateEncoding,
  captureFrameSequence,
  parseRenderPlan,
  parseRequestedConcurrency,
  readJson,
  representativeFrames,
  writeJsonAtomic,
} from './render-profile.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const value = (flag, fallback = null) => {
  const index = args.indexOf(flag);
  return index >= 0 && index + 1 < args.length ? args[index + 1] : fallback;
};
const has = (flag) => args.includes(flag);

const timelinePath = path.resolve(value('--timeline', path.join(here, '..', 'output', 'remotion-timeline.json')));
const output = path.resolve(value('--output', path.join(here, '..', 'output', 'remotion-proof.mp4')));
const renderPlanPath = value('--render-plan');
if (!renderPlanPath) throw new Error('--render-plan is required.');
const plan = await parseRenderPlan(path.resolve(renderPlanPath));
const layoutCacheDir = path.resolve(value('--layout-cache-dir', path.join(here, '..', '.lean-proof-video-cache', 'remotion-layouts')));
const chunkManifestPath = value('--chunk-manifest');
const stillFrameValue = value('--still');
const recalibrate = has('--recalibrate-renderer');
const timeline = JSON.parse(await fs.readFile(timelinePath, 'utf8'));

const supportedContracts = new Set([
  'strict-proof-transition-v1',
  'strict-proof-transition-v2-stable-rows',
  'strict-proof-transition-v3-mandatory-stable-rows',
  'strict-proof-transition-v4-pinned-premises',
  'strict-proof-transition-v5-carried-conclusions',
  'strict-proof-transition-v6-certified-current-context',
  'strict-proof-transition-v7-in-place-instantiation',
  'strict-proof-transition-v8-staged-instantiation-context',
  'strict-proof-transition-v9-temporal-dedup-multisource',
  'strict-proof-transition-v10-advancing-stored-conclusion',
  'strict-proof-transition-v11-split-forall-specialization',
  'strict-proof-transition-v12-consumed-forall-row',
  'strict-proof-transition-v13-action-lineage',
  'strict-proof-transition-v14-staged-proof-use',
  'strict-proof-transition-v15-overlapped-proof-use',
]);
if (timeline.schemaVersion !== 1 || !supportedContracts.has(timeline.rendererContract)) {
  throw new Error(
    `Unsupported strict timeline contract: schema=${timeline.schemaVersion}, contract=${timeline.rendererContract}`,
  );
}

const timings = {};
const startedAt = Date.now();
const systemSnapshot = () => {
  let gpu = null;
  try {
    gpu = execFileSync('nvidia-smi', [
      '--query-gpu=utilization.gpu,utilization.encoder,memory.used',
      '--format=csv,noheader,nounits',
    ], {encoding: 'utf8', windowsHide: true, timeout: 3000}).trim();
  } catch {
    // A portable CPU-only renderer must not depend on nvidia-smi.
  }
  return {
    timestamp: new Date().toISOString(),
    freeMemory: os.freemem(),
    totalMemory: os.totalmem(),
    loadAverage: os.loadavg(),
    processMemory: process.memoryUsage(),
    processCpu: process.cpuUsage(),
    gpu,
  };
};
const systemBefore = systemSnapshot();
const systemSamples = [];
let currentStage = 'bundle';
const sampler = setInterval(() => {
  systemSamples.push({...systemSnapshot(), stage: currentStage});
}, 5000);
sampler.unref();

console.log('Remotion semantic: bundling renderer...');
const bundleResult = await ensureBundle({
  entryPoint: path.join(here, 'src', 'index-semantic.ts'),
  cacheRoot: path.join(path.dirname(layoutCacheDir), 'remotion-bundles'),
  rendererFingerprint: plan.rendererFingerprint,
});
const serveUrl = bundleResult.serveUrl;
timings.bundleMilliseconds = bundleResult.wallMilliseconds;
timings.bundleCacheHit = bundleResult.cacheHit;

const stored = recalibrate ? null : await readJson(plan.profileStore);
const profileMatches = stored
  && stored.schemaVersion === 1
  && stored.rendererFingerprint === plan.rendererFingerprint
  && stored.hardwareFingerprint === plan.hardware.fingerprint
  && stored.width === timeline.width
  && stored.height === timeline.height
  && stored.fps === timeline.fps;
const priorProfile = profileMatches ? stored : null;
const requestedConcurrency = parseRequestedConcurrency(
  plan.requestedConcurrency,
  plan.hardware.logicalCpus,
);
let selectedGl = priorProfile?.gpuCompositing?.gl ?? null;
let browser = null;
let composition = null;
let layoutResult = null;
let concurrencyCalibration = priorProfile?.concurrencyCalibration ?? null;
let gpuCalibration = priorProfile?.gpuCalibration ?? null;
let encoding = priorProfile?.encoding ?? null;
let resolvedConcurrency = requestedConcurrency ?? priorProfile?.concurrency ?? null;

const open = async (gl) => {
  const openedAt = Date.now();
  const instance = await openBrowser('chrome', {
    logLevel: 'warn',
    ...(gl ? {chromiumOptions: {gl}} : {}),
  });
  timings.browserOpenMilliseconds = (timings.browserOpenMilliseconds ?? 0) + Date.now() - openedAt;
  return instance;
};

try {
  currentStage = 'browser-open';
  browser = await open(selectedGl);
  currentStage = 'layout';
  layoutResult = await ensureLayoutManifest({
    timeline,
    serveUrl,
    puppeteerInstance: browser,
    cacheDir: layoutCacheDir,
    rendererFingerprint: plan.rendererFingerprint,
    force: recalibrate,
  });
  timeline.layoutManifest = layoutResult.manifest;
  timings.layoutMilliseconds = layoutResult.wallMilliseconds;
  composition = await selectComposition({
    serveUrl,
    id: 'ProofVideo',
    inputProps: timeline,
    puppeteerInstance: browser,
  });
  console.log(`Remotion semantic: ${composition.durationInFrames} frames at ${composition.fps} fps.`);
  const sampleFrames = representativeFrames(
    composition.durationInFrames,
    plan.calibrationFrames,
  );

  if (resolvedConcurrency === null) {
    currentStage = 'concurrency-calibration';
    concurrencyCalibration = await calibrateConcurrency({
      serveUrl,
      composition,
      timeline,
      puppeteerInstance: browser,
      frames: sampleFrames,
      candidates: plan.calibrationCandidates,
    });
    resolvedConcurrency = concurrencyCalibration.best.concurrency;
  }

  if (!priorProfile || recalibrate) {
    currentStage = 'gpu-composition-calibration';
    const baseline = concurrencyCalibration?.best?.hashes
      ? concurrencyCalibration.best
      : await benchmarkFrames({
        serveUrl,
        composition,
        timeline,
        puppeteerInstance: browser,
        frames: sampleFrames,
        concurrency: resolvedConcurrency,
        captureHashes: true,
      });
    let angleBrowser = null;
    try {
      angleBrowser = await open('angle');
      const angleComposition = await selectComposition({
        serveUrl,
        id: 'ProofVideo',
        inputProps: timeline,
        puppeteerInstance: angleBrowser,
      });
      const angle = await benchmarkFrames({
        serveUrl,
        composition: angleComposition,
        timeline,
        puppeteerInstance: angleBrowser,
        frames: sampleFrames,
        concurrency: resolvedConcurrency,
        captureHashes: true,
      });
      const pixelsMatch = JSON.stringify(baseline.hashes) === JSON.stringify(angle.hashes);
      const improvement = baseline.wallMilliseconds > 0
        ? 1 - angle.wallMilliseconds / baseline.wallMilliseconds
        : 0;
      const accepted = pixelsMatch && improvement >= 0.10;
      gpuCalibration = {baseline, angle, pixelsMatch, improvement, accepted};
      if (accepted) {
        await browser.close({silent: true});
        browser = angleBrowser;
        angleBrowser = null;
        composition = angleComposition;
        selectedGl = 'angle';
      } else {
        selectedGl = null;
      }
    } catch (error) {
      gpuCalibration = {accepted: false, error: String(error)};
      selectedGl = null;
    } finally {
      if (angleBrowser) await angleBrowser.close({silent: true});
    }
  }

  if (!encoding || recalibrate) {
    currentStage = 'encoding-calibration';
    if (plan.hardwarePolicy === 'cpu' || !plan.hardware.nvencAvailable) {
      encoding = {hardwareAcceleration: 'disable', bitrate: null, ssim: 1, candidates: []};
    } else {
      const calibrationRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'proof-render-calibration-'));
      const framesDirectory = path.join(calibrationRoot, 'frames');
      try {
        try {
          await captureFrameSequence({
            directory: framesDirectory,
            serveUrl,
            composition,
            timeline,
            puppeteerInstance: browser,
            frames: sampleFrames,
            concurrency: resolvedConcurrency,
          });
          encoding = await calibrateEncoding({
            plan,
            frameDirectory: framesDirectory,
            workDirectory: calibrationRoot,
            fps: composition.fps,
          });
        } catch (error) {
          if (plan.hardwarePolicy === 'gpu-required') throw error;
          encoding = {
            hardwareAcceleration: 'disable',
            bitrate: null,
            ssim: 1,
            candidates: [],
            calibrationError: String(error),
          };
        }
      } finally {
        await fs.rm(calibrationRoot, {recursive: true, force: true});
      }
    }
  }

  const persistentProfile = {
    schemaVersion: 1,
    rendererFingerprint: plan.rendererFingerprint,
    hardwareFingerprint: plan.hardware.fingerprint,
    width: timeline.width,
    height: timeline.height,
    fps: timeline.fps,
    concurrency: resolvedConcurrency,
    concurrencyCalibration,
    gpuCompositing: {gl: selectedGl},
    gpuCalibration,
    encoding,
    updatedAt: new Date().toISOString(),
  };
  await writeJsonAtomic(plan.profileStore, persistentProfile);

  if (stillFrameValue !== null) {
    currentStage = 'still-render';
    const frame = Number.parseInt(stillFrameValue, 10);
    if (!Number.isInteger(frame) || frame < 0 || frame >= composition.durationInFrames) {
      throw new Error(`--still must be between 0 and ${composition.durationInFrames - 1}`);
    }
    await renderStill({
      serveUrl,
      composition,
      output,
      inputProps: timeline,
      puppeteerInstance: browser,
      frame,
      imageFormat: 'png',
      logLevel: 'warn',
    });
  } else if (chunkManifestPath) {
    currentStage = 'frame-render';
    const chunkManifest = await readJson(path.resolve(chunkManifestPath));
    if (chunkManifest?.schemaVersion !== 1 || !Array.isArray(chunkManifest.chunks)) {
      throw new Error('Invalid semantic chunk manifest.');
    }
    const chunkResult = await renderChunks({
      chunks: chunkManifest.chunks,
      serveUrl,
      composition,
      timeline,
      puppeteerInstance: browser,
      concurrency: resolvedConcurrency,
      encoding,
      plan,
    });
    timings.chunks = chunkResult.measurements;
    timings.chunkSummary = {
      cached: chunkResult.cached,
      rendered: chunkResult.rendered,
      total: chunkManifest.chunks.length,
    };
    if (chunkResult.effectiveEncoding) encoding = chunkResult.effectiveEncoding;
    console.log(
      `Checkpoints complete: ${chunkResult.cached} cached, ${chunkResult.rendered} rendered, ${chunkManifest.chunks.length} total.`,
    );
  } else {
    currentStage = 'frame-render';
    timings.fullRender = await renderFull({
      output,
      serveUrl,
      composition,
      timeline,
      puppeteerInstance: browser,
      concurrency: resolvedConcurrency,
      encoding,
      plan,
    });
    if (timings.fullRender.hardwareFallback) {
      encoding = {hardwareAcceleration: 'disable', bitrate: null};
    }
  }

  if (JSON.stringify(persistentProfile.encoding) !== JSON.stringify(encoding)) {
    await writeJsonAtomic(plan.profileStore, {
      ...persistentProfile,
      encoding,
      updatedAt: new Date().toISOString(),
    });
  }
} finally {
  clearInterval(sampler);
  if (browser) await browser.close({silent: true});
}

const report = {
  schemaVersion: 1,
  rendererFingerprint: plan.rendererFingerprint,
  dimensions: {width: timeline.width, height: timeline.height, fps: timeline.fps},
  frames: timeline.durationInFrames,
  layout: {
    cacheHit: layoutResult?.cacheHit ?? false,
    wallMilliseconds: layoutResult?.wallMilliseconds ?? null,
    p50Milliseconds: layoutResult?.p50Milliseconds ?? null,
    p95Milliseconds: layoutResult?.p95Milliseconds ?? null,
  },
  concurrency: resolvedConcurrency,
  concurrencyCalibration,
  gpuCompositing: {gl: selectedGl, calibration: gpuCalibration},
  encoding,
  timings: {...timings, totalMilliseconds: Date.now() - startedAt},
  system: {before: systemBefore, samples: systemSamples, after: systemSnapshot()},
};
await writeJsonAtomic(plan.profileReport, report);
console.log(`Render profile: ${plan.profileReport}`);
if (!chunkManifestPath) console.log(output);
