import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import {bundle} from '@remotion/bundler';

const exists = async (file) => {
  try {
    await fs.access(file);
    return true;
  } catch {
    return false;
  }
};

const validBundle = async (directory, marker, rendererFingerprint) => {
  try {
    const metadata = JSON.parse(await fs.readFile(marker, 'utf8'));
    const index = await fs.stat(path.join(directory, 'index.html'));
    return metadata.schemaVersion === 1
      && metadata.rendererFingerprint === rendererFingerprint
      && index.isFile()
      && index.size > 0;
  } catch {
    return false;
  }
};

/**
 * Build each content-addressed Remotion bundle once.
 *
 * The renderer fingerprint includes every local Remotion source and package
 * file.  A completed marker is written only after webpack has returned, so an
 * interrupted build can never be mistaken for a reusable bundle.
 */
export const ensureBundle = async ({entryPoint, cacheRoot, rendererFingerprint}) => {
  const directory = path.join(cacheRoot, rendererFingerprint);
  const marker = path.join(directory, '.proof-video-bundle.json');
  if (await exists(marker) && await validBundle(directory, marker, rendererFingerprint)) {
    return {serveUrl: directory, cacheHit: true, wallMilliseconds: 0};
  }

  await fs.mkdir(cacheRoot, {recursive: true});
  const temporary = `${directory}.${process.pid}.writing`;
  await fs.rm(temporary, {recursive: true, force: true});
  const started = Date.now();
  try {
    const serveUrl = await bundle({
      entryPoint,
      outDir: temporary,
      enableCaching: true,
    });
    await fs.writeFile(
      path.join(serveUrl, '.proof-video-bundle.json'),
      JSON.stringify({schemaVersion: 1, rendererFingerprint}, null, 2),
      'utf8',
    );
    await fs.rm(directory, {recursive: true, force: true});
    await fs.rename(temporary, directory);
  } catch (error) {
    await fs.rm(temporary, {recursive: true, force: true});
    throw error;
  }
  return {
    serveUrl: directory,
    cacheHit: false,
    wallMilliseconds: Date.now() - started,
  };
};
