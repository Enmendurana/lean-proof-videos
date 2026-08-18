export type Project = {
  id: string; name: string; entryPath: string; theorem: string;
  createdAt: string; updatedAt: string;
};

export type Source = {
  projectId: string; path: string; content: string; sha256: string; theorem: string;
};

export type Revision = {id: string; sha256: string; createdAt: string};

export type JobStatus = 'queued' | 'running' | 'cancelling' | 'interrupted' |
  'cancelled' | 'failed' | 'succeeded';

export type Job = {
  id: string; projectId: string; revisionId: string; kind: string;
  status: JobStatus; options: Record<string, unknown>; phase: string;
  progress: number | null; message: string; attempts: number;
  createdAt: string; startedAt: string | null; finishedAt: string | null;
  returnCode: number | null; error: string | null;
};

export type Artifact = {
  id: string; name: string; size: number; mediaType: string; url: string;
};

export type ProgressEvent = {
  sequence: number; timestamp: string; kind: string; phase: string; message: string;
  progress: number | null; elapsed_seconds?: number | null;
  eta_low_seconds?: number | null; eta_high_seconds?: number | null;
  cached?: boolean | null;
};
