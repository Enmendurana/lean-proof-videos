import {lazy, Suspense, useCallback, useEffect, useState} from 'react';
import {api, subscribe} from './api';
import type {Artifact, Job, ProgressEvent, Project, Revision, Source} from './types';

const terminal = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);
const LeanEditor = lazy(() => import('./LeanEditor'));

function elapsed(seconds?: number | null) {
  if (seconds == null) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

function App() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [source, setSource] = useState<Source | null>(null);
  const [draft, setDraft] = useState('');
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeJob, setActiveJob] = useState<string>('');
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [showImport, setShowImport] = useState(false);
  const [fileSearch, setFileSearch] = useState('');
  const [quality, setQuality] = useState('high');
  const [hardware, setHardware] = useState('auto');
  const [concurrency, setConcurrency] = useState('auto');
  const [noAudio, setNoAudio] = useState(false);
  const [audio, setAudio] = useState<{path: string; name: string} | null>(null);
  const [rebuildTrace, setRebuildTrace] = useState(false);
  const currentProject = projects.find((project) => project.id === selected);
  const currentJob = jobs.find((job) => job.id === activeJob);
  const dirty = source != null && draft !== source.content;

  const refresh = useCallback(async () => {
    const [projectRows, fileRows, jobRows] = await Promise.all([
      api.projects(), api.files(), api.jobs(),
    ]);
    setProjects(projectRows);
    setFiles(fileRows.files);
    setJobs(jobRows);
    if (!selected && projectRows[0]) setSelected(projectRows[0].id);
    if (!activeJob && jobRows[0]) setActiveJob(jobRows[0].id);
  }, [selected, activeJob]);

  useEffect(() => {
    (async () => {
      try {
        const params = new URLSearchParams(location.search);
        const token = params.get('token');
        if (token) {
          await api.bootstrap(token);
          history.replaceState({}, '', location.pathname);
        }
        await refresh();
        setReady(true);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
  }, []); // bootstrap exactly once

  useEffect(() => {
    if (!selected || !ready) return;
    Promise.all([api.source(selected), api.revisions(selected), api.jobs(selected)])
      .then(([nextSource, nextRevisions, nextJobs]) => {
        setSource(nextSource); setDraft(nextSource.content);
        setRevisions(nextRevisions); setJobs((all) => [
          ...nextJobs, ...all.filter((job) => job.projectId !== selected),
        ]);
        setActiveJob(nextJobs[0]?.id ?? '');
      })
      .catch((reason) => setError(String(reason)));
  }, [selected, ready]);

  useEffect(() => {
    if (!activeJob) return;
    setEvents([]);
    api.artifacts(activeJob).then(setArtifacts).catch(() => setArtifacts([]));
    const close = subscribe(activeJob, (event) => {
      setEvents((old) => [...old.slice(-299), event]);
      if (event.kind === 'completed' || event.kind === 'failed' || event.kind === 'cancelled') {
        api.jobs().then(setJobs);
        api.artifacts(activeJob).then(setArtifacts);
      }
    });
    const poll = window.setInterval(() => api.jobs().then(setJobs), 1000);
    return () => { close(); clearInterval(poll); };
  }, [activeJob]);

  async function save() {
    if (!source) return;
    try {
      const saved = await api.saveSource(source.projectId, draft, source.sha256);
      setSource({...source, ...saved});
      setRevisions(await api.revisions(source.projectId));
      await refresh();
    } catch (reason) {
      setError(String(reason));
      throw reason;
    }
  }

  async function launch(kind: string) {
    if (!selected) return;
    if (dirty) await save();
    try {
      const job = await api.createJob(selected, kind, {
        quality, renderHardware: hardware, renderConcurrency: concurrency,
        noAudio, audio: audio?.path, rebuildTrace, resume: true, useCache: true,
        toolchainBackend: 'auto', traceMode: 'auto',
      });
      setJobs((old) => [job, ...old]); setActiveJob(job.id);
    } catch (reason) { setError(String(reason)); }
  }

  async function importFile(path: string) {
    try {
      const project = await api.createProject(path);
      setProjects((old) => [project, ...old]); setSelected(project.id); setShowImport(false);
    } catch (reason) { setError(String(reason)); }
  }

  async function restore(revision: Revision) {
    if (!source || !confirm('Obnovim to različico? Trenutna vsebina bo prav tako arhivirana.')) return;
    try {
      const restored = await api.restore(source.projectId, revision.id, source.sha256);
      setSource({...source, ...restored}); setDraft(restored.content);
      setRevisions(await api.revisions(source.projectId));
    } catch (reason) { setError(String(reason)); }
  }

  const latest = events.at(-1);
  const video = artifacts.find((artifact) => artifact.mediaType.startsWith('video/'));
  const audit = artifacts.find((artifact) => artifact.name.endsWith('.audit.json'));
  const qa = artifacts.find((artifact) => artifact.name.endsWith('.qa.html') || artifact.name.endsWith('.qa.json'));
  const image = artifacts.find((artifact) => artifact.mediaType.startsWith('image/'));
  const projectJobs = jobs.filter((job) => !selected || job.projectId === selected);
  const matchingFiles = files.filter((file) => file.toLowerCase().includes(fileSearch.toLowerCase()));
  const percent = Math.round((currentJob?.progress ?? latest?.progress ?? 0) * 100);

  if (!ready) return <main className="gate"><div className="mark">∴</div><h1>Lean Proof Studio</h1><p>{error || 'Odpiram lokalni studio…'}</p></main>;

  return <div className="app-shell">
    <header>
      <div className="brand"><span className="brand-mark">∴</span><div><strong>Lean Proof Studio</strong><small>verified motion</small></div></div>
      <div className="header-state"><span className="dot" /> lokalno · Lean 4.32 <button className="ghost" onClick={() => api.stop()}>Ustavi studio</button></div>
    </header>

    {error && <div className="error-banner"><span>{error}</span><button onClick={() => setError('')}>×</button></div>}

    <div className="workspace">
      <aside className="projects panel">
        <div className="panel-title"><span>Dokazi</span><button onClick={() => setShowImport(true)}>＋</button></div>
        <div className="project-list">{projects.map((project) => <button key={project.id} className={project.id === selected ? 'project active' : 'project'} onClick={() => setSelected(project.id)}>
          <span className="project-symbol">⊢</span><span><strong>{project.name}</strong><small>{project.theorem}</small></span>
        </button>)}</div>
        <div className="history-title">Zgodovina</div>
        <div className="job-list">{projectJobs.map((job) => <button key={job.id} className={job.id === activeJob ? 'job active' : 'job'} onClick={() => setActiveJob(job.id)}>
          <span className={`status ${job.status}`} />
          <span><strong>{job.kind.replace('-', ' ')}</strong><small>{new Date(job.createdAt).toLocaleString('sl-SI')}</small></span>
          <em>{job.progress == null ? '' : `${Math.round(job.progress * 100)}%`}</em>
        </button>)}</div>
      </aside>

      <section className="editor-panel panel">
        <div className="editor-head">
          <div><small>{currentProject?.entryPath ?? 'Izberi dokaz'}</small><strong>{source?.theorem ?? '—'}</strong></div>
          <div className="save-state"><span className={dirty ? 'dirty' : ''}>{dirty ? 'neshranjeno' : 'shranjeno'}</span><button disabled={!dirty} onClick={save}>Shrani</button></div>
        </div>
        <div className="editor-wrap"><Suspense fallback={<div className="editor-loading">Nalagam Lean editor…</div>}><LeanEditor value={draft} onChange={setDraft} /></Suspense></div>
        <details className="revisions"><summary>Različice <span>{revisions.length}</span></summary>{revisions.map((revision) => <button key={revision.id} onClick={() => restore(revision)}><span>{new Date(revision.createdAt).toLocaleString('sl-SI')}</span><code>{revision.sha256.slice(0, 10)}</code></button>)}</details>
      </section>

      <aside className="controls panel">
        <div className="panel-title">Render</div>
        <label>Kakovost<select value={quality} onChange={(event) => setQuality(event.target.value)}><option>high</option><option>medium</option><option>low</option></select></label>
        <label>Strojna oprema<select value={hardware} onChange={(event) => setHardware(event.target.value)}><option value="auto">Samodejno</option><option value="cpu">CPU</option><option value="gpu-required">GPU obvezno</option></select></label>
        <label>Chromium zavihki<input value={concurrency} onChange={(event) => setConcurrency(event.target.value)} /></label>
        <label className="check"><input type="checkbox" checked={noAudio} onChange={(event) => setNoAudio(event.target.checked)} /> Brez glasbe</label>
        <label className="audio-picker">Glasba<input type="file" accept="audio/*" onChange={async (event) => { const file = event.target.files?.[0]; if (!file) return; try { setAudio(await api.uploadAudio(file)); setNoAudio(false); } catch (reason) { setError(String(reason)); } }} /><span>{audio?.name ?? 'privzeta glasba'}</span></label>
        <label className="check"><input type="checkbox" checked={rebuildTrace} onChange={(event) => setRebuildTrace(event.target.checked)} /> Ponovno izračunaj trace</label>
        <div className="action-grid">
          <button onClick={() => launch('validate')}>Preveri</button>
          <button onClick={() => launch('preview-head')}>Prvih 20 s</button>
          <button onClick={() => launch('preview-tail')}>Zadnjih 20 s</button>
          <button className="primary" onClick={() => launch('render-full')}>Renderaj MP4 <span>→</span></button>
        </div>
        <p className="hint">Job uporabi nespremenljiv posnetek shranjene revizije. Brskalnik lahko varno zapreš.</p>
      </aside>
    </div>

    <section className="job-drawer">
      <div className="job-summary">
        <div><span className={`status large ${currentJob?.status ?? ''}`} /><div><small>{currentJob?.status ?? 'čakalna vrsta'}</small><strong>{latest?.phase ?? currentJob?.phase ?? 'Ni aktivnega joba'}</strong></div></div>
        <div className="metric"><small>napredek</small><strong>{percent}%</strong></div>
        <div className="metric"><small>pretečeno</small><strong>{elapsed(latest?.elapsed_seconds)}</strong></div>
        <div className="metric"><small>ETA</small><strong>{latest?.eta_low_seconds == null ? '—' : latest.eta_low_seconds === latest.eta_high_seconds ? elapsed(latest.eta_low_seconds) : `${elapsed(latest.eta_low_seconds)}–${elapsed(latest.eta_high_seconds)}`}</strong></div>
        <div className="job-buttons">{currentJob?.status === 'running' && <button onClick={() => api.cancel(currentJob.id)}>Prekliči</button>}{currentJob && ['failed', 'cancelled', 'interrupted'].includes(currentJob.status) && <button onClick={() => api.resume(currentJob.id)}>Nadaljuj</button>}{currentJob && terminal.has(currentJob.status) && <button onClick={() => api.openFolder(currentJob.id)}>Odpri mapo</button>}</div>
      </div>
      <div className="progress-track"><span style={{width: `${percent}%`}} /></div>
      <div className="result-grid">
        <div className="log"><div className="result-title">Dogodki <span>{events.length}</span></div>{events.slice(-8).map((event) => <div key={`${event.sequence}-${event.timestamp}`}><time>{new Date(event.timestamp).toLocaleTimeString('sl-SI')}</time><span>{event.message}</span></div>)}</div>
        <div className="preview"><div className="result-title">Rezultat <span>{currentJob?.status === 'succeeded' ? 'preverjeno' : '—'}</span></div>{video ? <video controls src={video.url} /> : image ? <img src={image.url} alt="Kontaktna slika renderja" /> : <div className="empty-preview"><span>□</span><p>MP4 se bo prikazal tukaj</p></div>}<div className="artifact-links">{video && <a href={video.url} download>Download MP4</a>}{audit && <a href={audit.url}>Audit</a>}{qa && <a href={qa.url}>QA</a>}</div></div>
      </div>
    </section>

    {showImport && <div className="modal-backdrop" onMouseDown={() => setShowImport(false)}><div className="modal" onMouseDown={(event) => event.stopPropagation()}><div className="panel-title"><span>Dodaj Lean datoteko</span><button onClick={() => setShowImport(false)}>×</button></div><input className="search" placeholder="Poišči .lean datoteko…" autoFocus value={fileSearch} onChange={(event) => setFileSearch(event.target.value)} /><div className="file-list">{matchingFiles.map((file) => <button key={file} onClick={() => importFile(file)}><span>λ</span>{file}</button>)}</div></div></div>}
  </div>;
}

export default App;
