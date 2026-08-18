import type {Artifact, Job, ProgressEvent, Project, Revision, Source} from './types';

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {'Content-Type': 'application/json', ...(init.headers ?? {})},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({detail: response.statusText}));
    throw new Error(body.detail ?? response.statusText);
  }
  return response.json() as Promise<T>;
}

export const api = {
  bootstrap: (token: string) => request<{authenticated: boolean}>('/api/session', {
    method: 'POST', body: JSON.stringify({token}),
  }),
  files: () => request<{files: string[]}>('/api/files'),
  projects: () => request<Project[]>('/api/projects'),
  createProject: (path: string) => request<Project>('/api/projects', {
    method: 'POST', body: JSON.stringify({path}),
  }),
  source: (id: string) => request<Source>(`/api/projects/${id}/source`),
  saveSource: (id: string, content: string, baseSha256: string) => request<Source>(
    `/api/projects/${id}/source`, {
      method: 'PUT', body: JSON.stringify({content, baseSha256}),
    }),
  revisions: (id: string) => request<Revision[]>(`/api/projects/${id}/revisions`),
  restore: (projectId: string, revisionId: string, baseSha256: string) => request<Source>(
    `/api/projects/${projectId}/revisions/${revisionId}/restore`, {
      method: 'POST', body: JSON.stringify({baseSha256}),
    }),
  jobs: (projectId?: string) => request<Job[]>(
    `/api/jobs${projectId ? `?project_id=${projectId}` : ''}`,
  ),
  createJob: (projectId: string, kind: string, options: Record<string, unknown>) =>
    request<Job>('/api/jobs', {method: 'POST', body: JSON.stringify({projectId, kind, options})}),
  cancel: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, {method: 'POST'}),
  resume: (id: string) => request<Job>(`/api/jobs/${id}/resume`, {method: 'POST'}),
  retry: (id: string) => request<Job>(`/api/jobs/${id}/retry`, {method: 'POST'}),
  artifacts: (id: string) => request<Artifact[]>(`/api/jobs/${id}/artifacts`),
  openFolder: (id: string) => request(`/api/jobs/${id}/open-folder`, {method: 'POST'}),
  uploadAudio: async (file: File) => {
    const response = await fetch('/api/audio', {
      method: 'POST', headers: {'X-Filename': file.name}, body: file,
    });
    if (!response.ok) throw new Error((await response.json()).detail ?? response.statusText);
    return response.json() as Promise<{path: string; name: string}>;
  },
  stop: () => request('/api/studio/stop', {method: 'POST'}),
};

export function subscribe(jobId: string, onEvent: (event: ProgressEvent) => void) {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  source.addEventListener('progress', (raw) => onEvent(JSON.parse((raw as MessageEvent).data)));
  return () => source.close();
}
