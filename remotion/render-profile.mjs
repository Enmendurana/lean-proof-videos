import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {renderFrames} from '@remotion/renderer';

export const readJson = async (file) => {
  try {
    return JSON.parse(await fs.readFile(file, 'utf8'));
  } catch {
    return null;
  }
};

export const writeJsonAtomic = async (file, value) => {
  await fs.mkdir(path.dirname(file), {recursive: true});
  const temporary = `${file}.${process.pid}.tmp`;
  await fs.writeFile(temporary, JSON.stringify(value, null, 2), 'utf8');
  await fs.rename(temporary, file);
};

export const parseRenderPlan = async (file) => {
  const plan = await readJson(file);
  if (plan?.schemaVersion !== 1) throw new Error('Unsupported or missing render plan.');
  return plan;
};

export const parseRequestedConcurrency = (raw, logicalCpus) => {
  const value = String(raw).trim();
  if (value === 'auto') return null;
  if (/^[1-9]\d*$/.test(value)) return Number.parseInt(value, 10);
  const percentage = /^(\d{1,3})%$/.exec(value);
  if (percentage) {
    const amount = Number.parseInt(percentage[1], 10);
    if (amount >= 1 && amount <= 100) {
      return Math.max(1, Math.floor(logicalCpus * amount / 100));
    }
  }
  throw new Error(`Invalid render concurrency ${JSON.stringify(raw)}.`);
};

export const representativeFrames = (duration, requested) => {
  const count = Math.max(1, Math.min(duration, requested));
  const start = Math.max(0, Math.floor((duration - count) / 2));
  return Array.from({length: count}, (_unused, index) => start + index);
};

const percentile = (values, fraction) => {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * fraction))];
};

export const benchmarkFrames = async ({
  serveUrl,
  composition,
  timeline,
  puppeteerInstance,
  frames,
  concurrency,
  captureHashes = false,
}) => {
  const frameTimes = [];
  const hashes = {};
  const interesting = new Set([
    frames[0],
    frames[Math.floor(frames.length / 2)],
    frames[frames.length - 1],
  ]);
  const started = Date.now();
  const memoryBefore = {free: os.freemem(), processRss: process.memoryUsage().rss};
  await renderFrames({
    serveUrl,
    composition,
    inputProps: timeline,
    puppeteerInstance,
    outputDir: null,
    frames,
    onFrameBuffer: (buffer, frame) => {
      if (captureHashes && interesting.has(frame)) {
        hashes[frame] = crypto.createHash('sha256').update(buffer).digest('hex');
      }
    },
    imageFormat: 'jpeg',
    jpegQuality: 92,
    muted: true,
    concurrency,
    logLevel: 'warn',
    onStart: () => undefined,
    onFrameUpdate: (_rendered, _frame, milliseconds) => frameTimes.push(milliseconds),
  });
  const wallMilliseconds = Date.now() - started;
  const memoryAfter = {free: os.freemem(), processRss: process.memoryUsage().rss};
  const p50Milliseconds = percentile(frameTimes, 0.50);
  const p95Milliseconds = percentile(frameTimes, 0.95);
  return {
    concurrency,
    wallMilliseconds,
    framesPerSecond: frames.length / Math.max(0.001, wallMilliseconds / 1000),
    p50Milliseconds,
    p95Milliseconds,
    stabilityRatio: p50Milliseconds > 0 ? p95Milliseconds / p50Milliseconds : 1,
    memory: {before: memoryBefore, after: memoryAfter},
    hashes,
  };
};

export const calibrateConcurrency = async ({
  serveUrl,
  composition,
  timeline,
  puppeteerInstance,
  frames,
  candidates,
}) => {
  const results = [];
  for (const concurrency of candidates) {
    console.log(`Renderer calibration: ${concurrency} Chromium tabs...`);
    try {
      results.push(await benchmarkFrames({
        serveUrl,
        composition,
        timeline,
        puppeteerInstance,
        frames,
        concurrency,
        captureHashes: true,
      }));
    } catch (error) {
      results.push({concurrency, error: String(error), framesPerSecond: 0});
    }
  }
  const successful = results.filter((result) => (
    !result.error && Number.isFinite(result.framesPerSecond) && result.framesPerSecond > 0
  ));
  const stable = successful.filter((result) => (
    result.p95Milliseconds <= Math.max(1000, result.p50Milliseconds * 6)
  ));
  const eligible = stable.length ? stable : successful;
  if (!eligible.length) throw new Error('No renderer concurrency candidate completed.');
  const best = [...eligible].sort((left, right) => (
    right.framesPerSecond - left.framesPerSecond || left.concurrency - right.concurrency
  ))[0];
  return {best, candidates: results, stableCandidates: stable.map((item) => item.concurrency)};
};

const run = (command, args) => new Promise((resolve, reject) => {
  const child = spawn(command, args, {windowsHide: true});
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
  child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
  child.on('error', reject);
  child.on('close', (code) => {
    if (code === 0) resolve({stdout, stderr});
    else reject(new Error(`${path.basename(command)} exited with ${code}: ${stderr}`));
  });
});

export const captureFrameSequence = async ({
  directory,
  serveUrl,
  composition,
  timeline,
  puppeteerInstance,
  frames,
  concurrency,
}) => {
  await fs.mkdir(directory, {recursive: true});
  const indexes = new Map(frames.map((frame, index) => [frame, index]));
  const writes = [];
  await renderFrames({
    serveUrl,
    composition,
    inputProps: timeline,
    puppeteerInstance,
    outputDir: null,
    frames,
    onFrameBuffer: (buffer, frame) => {
      const index = indexes.get(frame);
      if (index === undefined) return;
      writes.push(fs.writeFile(path.join(directory, `frame-${String(index).padStart(6, '0')}.jpeg`), buffer));
    },
    imageFormat: 'jpeg',
    jpegQuality: 92,
    muted: true,
    concurrency,
    logLevel: 'warn',
    onStart: () => undefined,
    onFrameUpdate: () => undefined,
  });
  await Promise.all(writes);
};

const encodeSequence = async ({ffmpeg, directory, fps, output, encoder, bitrate}) => {
  const args = [
    '-y', '-loglevel', 'error', '-framerate', String(fps),
    '-i', path.join(directory, 'frame-%06d.jpeg'),
    '-c:v', encoder,
  ];
  if (encoder === 'libx264') args.push('-preset', 'veryfast');
  if (bitrate) args.push('-b:v', bitrate);
  args.push('-pix_fmt', 'yuv420p', output);
  await run(ffmpeg, args);
};

const compareSsim = async (ffmpeg, reference, candidate) => {
  const result = await run(ffmpeg, [
    '-hide_banner', '-i', reference, '-i', candidate,
    '-lavfi', '[0:v][1:v]ssim', '-f', 'null', '-',
  ]);
  const match = /All:([0-9.]+)/.exec(`${result.stdout}\n${result.stderr}`);
  return match ? Number.parseFloat(match[1]) : 0;
};

export const calibrateEncoding = async ({
  plan,
  frameDirectory,
  workDirectory,
  fps,
}) => {
  if (plan.hardwarePolicy === 'cpu' || !plan.hardware.nvencAvailable) {
    return {hardwareAcceleration: 'disable', bitrate: null, ssim: 1, candidates: []};
  }
  const executable = process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg';
  const ffmpeg = path.join(plan.hardware.ffmpegDirectory, executable);
  const reference = path.join(workDirectory, 'software-reference.mp4');
  await encodeSequence({
    ffmpeg, directory: frameDirectory, fps, output: reference,
    encoder: 'libx264', bitrate: null,
  });
  const candidates = [];
  for (const bitrate of plan.nvencBitrates) {
    const candidate = path.join(workDirectory, `nvenc-${bitrate}.mp4`);
    try {
      await encodeSequence({
        ffmpeg, directory: frameDirectory, fps, output: candidate,
        encoder: 'h264_nvenc', bitrate,
      });
      const ssim = await compareSsim(ffmpeg, reference, candidate);
      candidates.push({bitrate, ssim});
    } catch (error) {
      candidates.push({bitrate, ssim: 0, error: String(error)});
    }
  }
  const accepted = candidates.find((candidate) => candidate.ssim >= plan.minimumSsim);
  if (!accepted && plan.hardwarePolicy === 'gpu-required') {
    throw new Error('NVENC did not satisfy the configured visual quality threshold.');
  }
  return accepted
    ? {hardwareAcceleration: 'if-possible', bitrate: accepted.bitrate, ssim: accepted.ssim, candidates}
    : {hardwareAcceleration: 'disable', bitrate: null, ssim: 1, candidates};
};
