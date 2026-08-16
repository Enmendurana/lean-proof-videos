import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const value = (flag, fallback) => {
  const index = args.indexOf(flag);
  return index >= 0 && index + 1 < args.length ? args[index + 1] : fallback;
};
const timelinePath = path.resolve(value('--timeline', path.join(here, '..', 'output', 'remotion-timeline.json')));
const output = path.resolve(value('--output', path.join(here, '..', 'output', 'remotion-proof.mp4')));
const concurrency = value('--concurrency', '75%');
const stillFrameValue = value('--still', null);
const timeline = JSON.parse(await fs.readFile(timelinePath, 'utf8'));

if (timeline.schemaVersion !== 1 || timeline.rendererContract !== 'strict-proof-transition-v1') {
  throw new Error(`Unsupported strict timeline schema: ${timeline.schemaVersion}`);
}

console.log('Remotion: bundling renderer...');
const serveUrl = await bundle({
  entryPoint: path.join(here, 'src', 'index.ts'),
  enableCaching: false,
  // Webpack's persistent pack cache is both unnecessary for this tiny bundle
  // and prone to slow EPERM retries on Windows when renders overlap.
  webpackOverride: (config) => ({...config, cache: false}),
});
console.log('Remotion: renderer bundle ready.');
const composition = await selectComposition({
  serveUrl,
  id: 'ProofVideo',
  inputProps: timeline,
});
console.log(`Remotion: composition ${composition.durationInFrames} frames at ${composition.fps} fps.`);

if (stillFrameValue !== null) {
  const frame = Number.parseInt(stillFrameValue, 10);
  if (!Number.isInteger(frame) || frame < 0 || frame >= composition.durationInFrames) {
    throw new Error(`--still must be between 0 and ${composition.durationInFrames - 1}`);
  }
  await renderStill({
    serveUrl,
    composition,
    output: output,
    inputProps: timeline,
    frame,
    imageFormat: 'png',
    logLevel: 'warn',
  });
  console.log(output);
  process.exit(0);
}

await renderMedia({
  serveUrl,
  composition,
  codec: 'h264',
  outputLocation: output,
  inputProps: timeline,
  concurrency,
  imageFormat: 'jpeg',
  jpegQuality: 92,
  x264Preset: 'veryfast',
  muted: true,
  logLevel: 'info',
  overwrite: true,
});

console.log(output);
