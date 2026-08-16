import fs from 'node:fs/promises';
import path from 'node:path';
import {renderMedia} from '@remotion/renderer';

export const nonEmptyFile = async (file) => {
  try {
    return (await fs.stat(file)).size > 0;
  } catch {
    return false;
  }
};

const encodingIdentity = (encoding) => ({
  hardwareAcceleration: encoding.hardwareAcceleration,
  bitrate: encoding.bitrate ?? null,
});

const chunkMetadataPath = (output) => `${output}.render.json`;

const cachedChunkMatches = async (chunk, encoding, plan) => {
  if (!(await nonEmptyFile(chunk.output))) return false;
  try {
    const metadata = JSON.parse(await fs.readFile(chunkMetadataPath(chunk.output), 'utf8'));
    return metadata.schemaVersion === 1
      && metadata.key === chunk.key
      && metadata.rendererFingerprint === plan.rendererFingerprint
      && JSON.stringify(metadata.encoding) === JSON.stringify(encodingIdentity(encoding));
  } catch {
    return false;
  }
};

const writeChunkMetadata = async (chunk, measurement, plan) => {
  const file = chunkMetadataPath(chunk.output);
  const temporary = `${file}.${process.pid}.tmp`;
  await fs.writeFile(temporary, JSON.stringify({
    schemaVersion: 1,
    key: chunk.key,
    rendererFingerprint: plan.rendererFingerprint,
    start: chunk.start,
    end: chunk.end,
    encoding: encodingIdentity(measurement),
  }, null, 2), 'utf8');
  await fs.rm(file, {force: true});
  await fs.rename(temporary, file);
};

const mediaOptions = ({
  serveUrl,
  composition,
  timeline,
  puppeteerInstance,
  output,
  frameRange,
  concurrency,
  encoding,
  plan,
  onProgress,
  onStart,
}) => {
  const accelerated = encoding.hardwareAcceleration !== 'disable';
  return {
    serveUrl,
    composition,
    codec: 'h264',
    outputLocation: output,
    inputProps: timeline,
    puppeteerInstance,
    concurrency,
    imageFormat: 'jpeg',
    jpegQuality: 92,
    ...(accelerated ? {
      hardwareAcceleration: encoding.hardwareAcceleration,
      videoBitrate: encoding.bitrate,
      binariesDirectory: plan.hardware.ffmpegDirectory,
    } : {
      hardwareAcceleration: 'disable',
      x264Preset: 'veryfast',
    }),
    muted: true,
    logLevel: 'warn',
    overwrite: true,
    ...(frameRange ? {frameRange} : {}),
    onProgress,
    onStart,
  };
};

const renderAtomic = async (options, {allowHardwareFallback}) => {
  const output = options.output;
  await fs.mkdir(path.dirname(output), {recursive: true});
  const temporary = `${output}.${process.pid}.rendering.mp4`;
  await fs.rm(temporary, {force: true});
  const started = Date.now();
  let renderedDoneIn = null;
  let encodedDoneIn = null;
  let startData = null;
  let lastProgress = -1;
  const attempt = async (encoding) => renderMedia(mediaOptions({
    ...options,
    encoding,
    output: temporary,
    onStart: (data) => { startData = data; },
    onProgress: (progress) => {
      renderedDoneIn = progress.renderedDoneIn ?? renderedDoneIn;
      encodedDoneIn = progress.encodedDoneIn ?? encodedDoneIn;
      const percent = Math.max(0, Math.min(100, Math.floor(progress.progress * 100)));
      if (percent >= lastProgress + 5 || percent === 100) {
        lastProgress = percent;
        options.reportProgress?.(percent, progress);
      }
    },
  }));
  let usedEncoding = options.encoding;
  let hardwareFallback = false;
  try {
    await attempt(usedEncoding);
  } catch (error) {
    if (!allowHardwareFallback || usedEncoding.hardwareAcceleration === 'disable') throw error;
    hardwareFallback = true;
    usedEncoding = {hardwareAcceleration: 'disable', bitrate: null};
    await fs.rm(temporary, {force: true});
    await attempt(usedEncoding);
  }
  if (!(await nonEmptyFile(temporary))) {
    throw new Error(`Renderer did not produce a non-empty file: ${temporary}`);
  }
  await fs.rm(output, {force: true});
  await fs.rename(temporary, output);
  const frameCount = options.frameRange
    ? options.frameRange[1] - options.frameRange[0] + 1
    : options.composition.durationInFrames;
  return {
    output,
    wallMilliseconds: Date.now() - started,
    renderedDoneIn,
    encodedDoneIn,
    frameCount,
    frameRenderFramesPerSecond: renderedDoneIn
      ? frameCount / (renderedDoneIn / 1000)
      : null,
    encodingFramesPerSecond: encodedDoneIn
      ? frameCount / (encodedDoneIn / 1000)
      : null,
    resolvedConcurrency: startData?.resolvedConcurrency ?? options.concurrency,
    parallelEncoding: startData?.parallelEncoding ?? null,
    hardwareAcceleration: usedEncoding.hardwareAcceleration,
    bitrate: usedEncoding.bitrate,
    hardwareFallback,
  };
};

export const renderChunks = async ({
  chunks,
  serveUrl,
  composition,
  timeline,
  puppeteerInstance,
  concurrency,
  encoding,
  plan,
}) => {
  let cached = 0;
  let rendered = 0;
  let activeEncoding = encoding;
  const measurements = [];
  for (const [index, chunk] of chunks.entries()) {
    if (await cachedChunkMatches(chunk, activeEncoding, plan)) {
      cached += 1;
      console.log(`Checkpoint ${index + 1}/${chunks.length}: cached frames ${chunk.start}-${chunk.end}.`);
      continue;
    }
    console.log(`Checkpoint ${index + 1}/${chunks.length}: rendering frames ${chunk.start}-${chunk.end}...`);
    const measurement = await renderAtomic({
      output: chunk.output,
      serveUrl,
      composition,
      timeline,
      puppeteerInstance,
      frameRange: [chunk.start, chunk.end],
      concurrency,
      encoding: activeEncoding,
      plan,
      reportProgress: (percent) => {
        console.log(`Checkpoint ${index + 1}/${chunks.length}: ${String(percent).padStart(3, ' ')}%`);
      },
    }, {allowHardwareFallback: plan.hardwarePolicy === 'auto'});
    await writeChunkMetadata(chunk, measurement, plan);
    if (measurement.hardwareFallback) {
      console.log(
        'NVENC failed during a checkpoint; restarting the checkpoint plan with x264 so segments never mix encoders.',
      );
      const restarted = await renderChunks({
        chunks,
        serveUrl,
        composition,
        timeline,
        puppeteerInstance,
        concurrency,
        encoding: {hardwareAcceleration: 'disable', bitrate: null},
        plan,
      });
      return {...restarted, hardwareFallback: true};
    }
    measurements.push({...measurement, start: chunk.start, end: chunk.end, key: chunk.key});
    rendered += 1;
  }
  return {cached, rendered, measurements, effectiveEncoding: activeEncoding};
};

export const renderFull = async (options) => renderAtomic({
  ...options,
  frameRange: null,
  reportProgress: (percent, progress) => {
    const renderedFrames = progress.renderedFrames ?? 0;
    const encodedFrames = progress.encodedFrames ?? 0;
    console.log(
      `Progress: ${String(percent).padStart(3, ' ')}% | rendered ${renderedFrames} | encoded ${encodedFrames}`,
    );
  },
}, {allowHardwareFallback: options.plan.hardwarePolicy === 'auto'});
