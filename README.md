# Lean Proof Videos

`Lean Proof Videos` turns a formally verified Lean 4 theorem into an MP4 animation. Its default hybrid trace uses Lean's source-level `InfoTree` tactic actions as readable moves and independently kernel-checks every source-local theorem chapter. Imported library theorems remain cited atomic steps. Unbounded proof-term tracing based on [Mathlib's `#explode`](https://github.com/leanprover-community/mathlib4/blob/master/Mathlib/Tactic/Explode.lean) and the original tactic-state format from [dwrensha/animate-lean-proofs](https://github.com/dwrensha/animate-lean-proofs) remain explicit compatibility modes. Blender and syntax-highlighted Lean source are replaced with [Manim](https://github.com/3b1b/manim) and LaTeX-rendered mathematics.

Mathematical notation is generated semantically from elaborated `Lean.Expr` values by [LeanTeX](https://github.com/kmill/LeanTeX). The older string converter remains for traces created before semantic LaTeX fields were added and as a fallback for expression kinds that LeanTeX explicitly marks as unhandled.

The default is a standard 1920×1080, 30 fps, 16:9 YouTube video rendered by Remotion. The Manim compatibility renderer uses OpenGL when available and falls back to Cairo automatically. Every hybrid tactic action retains its checked proof-assignment fingerprint and semantic transition; every chapter retains a whole-declaration kernel certificate. A second validator rejects invalid fingerprints, dependencies or semantic edges before rendering starts; the renderer never invents proof steps.

Long Lean runs now produce `command-profile.json` beside their chapter
checkpoints. While a command is active, terminal progress includes its exact
source line and elapsed time; after extraction it prints the five slowest
commands. This makes a slow `aesop`, `simp` or large declaration visible instead
of leaving the process at an unexplained percentage. Once enough completed
work exists to estimate a rate, the ETA is shown as a narrowing interval rather
than a falsely precise timestamp.

## Pipeline

```text
theorem.lean
    │  explicit toolchain backend + validated incremental snapshot
    ▼
kernel/proof index + selective source InfoTree + kernel chapter checks
    ▼
Hybrid trace v3.1/v4 manifest + content-addressed theorem objects
    │  independent validation + certified live-sequent projection
    ▼
one evolving proof timeline
    │  Remotion (default) or Manim compatibility renderer
    ▼
silent MP4 on the infinite blackboard
    │  optional licensed audio muxed afterwards with FFmpeg
    ▼
MP4 (unlimited duration by default)
```

The reader also accepts trace-v4 content-addressed manifests. Existing v3.0
and v3.1 evidence remains readable and is not deleted during the migration.

## Python architecture

The supported public surface is the CLI, the proof-trace schema, and the two
renderers. Matching details are private implementation modules, separated so
proof logic never depends on Manim or browser geometry:

```text
proof_video/
├── proof/
│   ├── schema.py       immutable JSON/domain data contracts
│   ├── trace.py        certified ProofTrace presentation timeline
│   ├── explainers.py   certified tactic-evidence adapters
│   ├── matching.py     expression-path and rendered-span primitives
│   ├── branch_provenance.py  multi-premise proof-DAG composition
│   └── semantics.py    sequent construction and rule-level AST edges
├── animation/
│   ├── semantic.py     renderer-neutral token correspondence planning
│   ├── latex.py        LeanTeX cleanup and LaTeX tokenization
│   └── scene_helpers.py low-level Manim row/transition primitives
├── rendering/
│   ├── planning.py     duration, renderer safety, and cache identities
│   ├── manim_backend.py isolated worker processes and scene execution
│   ├── types.py        renderer result contracts
│   └── ffmpeg.py       atomic MP4 assembly and optional audio muxing
├── evidence_cache.py   durable source/toolchain-keyed Lean evidence
├── toolchains.py       isolated 4.28/4.32 workspace and qualification metadata
├── backend_policy.py   shared 4.32-first, 4.28-fallback execution policy
├── incremental_snapshot.py  strict snapshot/dependency integrity envelope
├── snapshot_runtime.py official Lean 4.32 full/header snapshot orchestration
├── snapshot_worker.py  authenticated 15-minute in-memory snapshot lease
├── commands/
│   ├── render_proof.py two-path automatic-theorem command
│   └── cache.py        manual unbounded-cache status/prune commands
├── studio/
│   ├── app.py          localhost FastAPI API, SSE and artifact streaming
│   ├── store.py        SQLite projects, revisions, jobs and artifact registry
│   ├── sources.py      path allowlist, SHA conflicts and immutable snapshots
│   ├── jobs.py         persistent single-heavy-job scheduler
│   ├── worker.py       isolated, cancellable render process
│   ├── security.py     one-time bootstrap and strict local session
│   └── launcher.py     hidden Windows/browser launcher
├── render_service.py   shared typed CLI/web request and progress boundary
├── models.py           trace-to-movie orchestration
├── lean_export.py      Lean worker process, progress, ETA and checkpoints
├── trace_store.py      streaming SHA-256 theorem-object storage
├── quality.py          pre-render semantic fail-closed audit
├── visual_quality.py   post-render media audit and contact sheet
├── scene.py            compact Manim scene orchestration
├── render.py           public render orchestration
├── lean_sources.py     shared extractor-module inventory for cache/toolchains
└── remotion_export.py  compact browser-renderer timeline export
```

The dependency direction is `proof → animation → renderer`: proof modules do
not import Manim or Remotion, and the Remotion exporter no longer imports the
Manim scene merely to tokenize formulas.

The Lean implementation follows the same boundary discipline. `Animate.lean`
is a compatibility façade over `Animate.Config`, `Animate.Schema`,
`Animate.TacticTrace`, `Animate.Hybrid`, and `Animate.Frontend`. `ProofTrace.lean`
similarly exposes the smaller `ProofTrace.Schema`, `ProofTrace.Dependencies`,
and `ProofTrace.Extraction` modules. Existing imports and terminal commands do
not change. The shared `proof_video/lean_sources.py` inventory ensures that a
new nested Lean module is included in both cache identities and the isolated
Lean 4.32 workspace.

## Requirements

- Windows, macOS, or Linux
- Lean 4 through `elan` (the repository pins the exact version in `lean-toolchain`)
- Python 3.11–3.13 (3.12 recommended)
- A LaTeX distribution with `latex` and `dvisvgm` (MiKTeX works on Windows)
- FFmpeg available on `PATH`
- Node.js 20 or newer for the default Remotion renderer

## Setup on this computer

PowerShell:

```powershell
cd D:\chatGPT\lean-proof-videos
.\setup.ps1
```

PowerShell activation is optional. All examples below call the virtual
environment executable directly, so they also work when script execution is
disabled by the system policy.

If FFmpeg is missing, install it once (for example `winget install Gyan.FFmpeg`) and open a new terminal.

## Lean Proof Studio (brez terminala)

`setup.ps1` namesti tudi lokalni FastAPI/React studio, zgradi njegov frontend in
na namizju ustvari bližnjico **Lean Proof Studio**. Po namestitvi ga odpreš z
dvoklikom. Studio posluša izključno na `127.0.0.1`; prvi kratek bootstrap token
se zamenja za `HttpOnly`, `SameSite=Strict` sejo in se ne more uporabiti drugič.

Studio omogoča:

- izbiro `.lean` datoteke iz tega repozitorija in urejanje v Monaco editorju;
- obnovljive, vsebinsko naslovljene revizije ter zaščito pred prepisom zunanje
  spremembe datoteke;
- preverjanje, prvih ali zadnjih 20 sekund in celotni MP4;
- trajno čakalno vrsto z enim težkim Lean/Remotion jobom, SSE napredkom, ETA,
  preklicem in nadaljevanjem iz veljavnih checkpointov;
- predvajanje MP4, download, audit, QA, loge in kontaktne slike iz istega
  pogleda.

Job vedno uporablja nespremenljiv posnetek izbrane revizije. Zaprtje brskalnika
ga ne prekine. Če se studio med renderjem ustavi, izolirani worker ostane živ;
ob naslednjem zagonu se UI ponovno priklopi na njegov PID in journal. CLI ostane
združljiv, ker Studio, `lean-proof-video` in `render-proof` uporabljajo isti
tipizirani `RenderService` ter isti strict audit/QA cevovod.

Studio lahko po želji zaženeš tudi ročno:

```powershell
.\proof-studio.cmd
```

Stanje je v `.lean-proof-video-web/studio.db`, revizije in job artefakti pa v
istem ignoriranem imeniku. Brisanje tega imenika ponastavi samo Studio; Lean
trace in render cache ostaneta ločena in nedotaknjena.

## Render the IMO 2011 Problem 3 demo

IMO 2011 Problem 3 is the canonical demo for this project. After renderer or pacing changes, use this proof for the representative preview and end-to-end render.

```powershell
.\render-proof.cmd .\Input\Imo2011P3.lean .\output\imo2011p3-full.mp4
```

The command detects `Imo2011P3.imo2011_p3` from the Lean source and performs a
complete Remotion render; it never enables an opening or tail preview. For a
file containing helper lemmas, the final theorem/lemma declaration is the
default. Add `-- proof-video: theorem Namespace.name` anywhere in the Lean file
to select a different declaration while keeping the same two-argument command.
The equivalent installed command is `.\.venv\Scripts\render-proof.exe`.

The two-path command deliberately chooses the strict fine-grained proof-term
profile for an ordinary proof. `auto` runs the shared extractor over the Lean
4.32 incremental snapshot and falls back to Lean 4.28/legacy if that operation
fails. Both backends contract version-specific kernel plumbing to the same 71
human-visible IMO states instead of the 30 source-tactic states in the scalable
hybrid trace. This is a presentation-quality choice, not a weaker audit; all
states remain kernel-derived and strict-audited. Pass
`--trace-granularity scalable` only when you explicitly want the smaller
source-tactic trace.

## Long proofs and resumable rendering

`Input/Erdos38.lean` selects its final theorem and scalable profile with source
markers. Render it with the same
two-path command:

```powershell
.\render-proof.cmd .\Input\Erdos38.lean .\output\erdos38.mp4
```

Its `-- proof-video: resumable` marker automatically enables persistent cache
reuse, theorem-chapter trace checkpoints, and semantic Remotion checkpoints
cut at proof-transition boundaries (normally 5--15 seconds).
The default hybrid extractor first discovers the source-local theorem dependency
DAG. Every reachable local lemma becomes one topologically ordered chapter;
imported Mathlib lemmas remain atomic. Inside a chapter, the visible moves are
the actual source-level tactic actions recorded by Lean's `InfoTree`, rather
than the often enormous implementation term generated by a tactic. Each action
retains the proof assignment fingerprint and descendants produced by Lean, and
the complete declaration is independently kernel-checked with `sorryAx`
forbidden. The strict audit verifies both layers before rendering.

There is no maximum lemma length and no 12,000-row cutoff. During hybrid
elaboration each completed theorem command is immediately converted from its
large `InfoTree` into a compact movie; the `InfoTree` is released before the
next command. Elaboration stops as soon as the selected theorem has been
accepted, because a declaration cannot depend on later source commands. A
completed chapter is written atomically as `source-chapter-N.json`; an unfinished chapter is
journaled every 128 actions under `source-chapter-N.parts/actions-K.json`.
Those fixed-size files are storage checkpoints, not a limit on the number of
actions. Restarting reuses compatible action fragments and continues at the
first missing action. When extractor code changes, atomic theorem-command
captures from an older namespace are seeded into the new namespace only for
the same canonical source. They remain mere candidates: freshly elaborated
Lean must match both the theorem name and proof-term fingerprint before reuse,
so an upgrade preserves expensive presentation work without weakening the
kernel audit. On NTFS these candidates use hardlinks and do not duplicate large
JSON payloads.
A small `progress.json` reports the active theorem, exact
source-action count, overall percentage, elapsed time and estimated time
remaining. The source byte position supplies real command-level progress and an
ETA during initial elaboration; action-level progress takes over during
extraction. Compact command captures are proof-fingerprint checked and reusable
after edits elsewhere in the file. Lean 4.28 still commits a source command
atomically, so interruption in the middle of one command restarts that command;
completed theorem commands, action chunks and chapters remain reusable.

After the sequential source environment is complete, independent chapter
kernel certificates use up to four bounded workers by default. Override this
with `--lean-workers N`. Chapter hashing and object validation are also bounded
and parallel while preserving source order; set
`LEAN_PROOF_POSTPROCESS_WORKERS=N` when a different I/O parallelism is useful.

### Modular long proofs

Lean's reusable compilation boundary is a module. A large development can
therefore add a companion `Proof.proof-video.json` and put stable groups of
helper lemmas in imported `.lean` files. The ordinary two-path command detects
the companion automatically:

```powershell
.\render-proof.cmd .\Path\Main.lean .\output\main.mp4
```

The schema is intentionally small:

```json
{
  "schemaVersion": 1,
  "units": [
    {"leanFile": "Foundation.lean", "theorem": "Demo.foundation"},
    {"leanFile": "Main.lean", "theorem": "Demo.main"}
  ]
}
```

Every unit is elaborated, kernel-audited and cached independently. The final
unit must be the file passed on the command line. Certified chapter manifests
are merged in the declared order; the last chapter alone is marked as the main
theorem. Editing a late module therefore reuses evidence for unchanged earlier
modules. See `examples/modular/Main.proof-video.json` for a complete miniature
layout. This remains the rollback interruption boundary on Lean 4.28. The
project also contains an isolated Lean/Mathlib 4.32.1 snapshot backend. It has
its own workspace, `.lake`, snapshots, evidence and qualification record under
`.lean-proof-video-cache/toolchains/lean-4.32.1/`; artifacts from the two
toolchains are never mixed. `auto` first tries the isolated 4.32 snapshot
backend and, if its workspace preparation, snapshot, or Lean extraction fails,
retries that complete Lean phase with 4.28/legacy. Checked evidence from one
toolchain can never satisfy the cache identity of the other.

Lean 4.32 snapshots use the official `--incr-save`, `--incr-load`, and
`--incr-header-save` frontend. A metadata envelope is committed only after the
snapshot and its `.deps` file are complete **and** the certificate command in
that same official Lean process has published a matching kernel/no-`sorry`
sidecar. It records the exact Lean and
Mathlib versions, Lake manifest, source/header hashes, extractor ABI, snapshot
hash, dependency-list hash, and hashes of imported `.olean`/`.ir` artifacts.
An import/header/toolchain mismatch rejects the snapshot. A later command edit
is passed to Lean as partial reuse; Lean itself stops reuse at the first changed
syntax node. Snapshot reuse never bypasses chapter kernel certification or the
strict audit. The snapshot reader is a Lean module executed inside the official
`lean.exe`, not a second native executable, which avoids Windows PE export
limits and keeps compacted-region ownership in the process that understands it.
After its first request, a source-local worker lease keeps that deserialized
snapshot tree and imported environment in memory for 15 minutes. Its localhost
protocol uses an unguessable per-process token and an exact header/extractor
identity; an expired, changed, or failed worker falls back to the verified
one-shot reader. The worker is never an evidence cache and cannot bypass the
snapshot envelope, kernel certificate, or strict audit.
When two trees are available in that worker, it compares Lean's actual parsed
command syntax and reports exact prefix reuse, for example
`reused 41/48 | re-elaborated 7/48`; it never estimates this from source bytes.
See the official [Lean 4.32 release notes](https://lean-lang.org/doc/reference/latest/releases/v4.32.0/)
and [incremental snapshot implementation](https://github.com/leanprover/lean4/pull/13965).

Force the isolated backend without automatic rollback with:

```powershell
.\render-proof.cmd .\Input\Tutorial.lean .\output\tutorial-432.mp4 `
  --toolchain-backend lean-4.32 --trace-backend snapshot
```

An explicit `lean-4.32` or explicit `snapshot` request is fail-fast. The
qualification record remains a diagnostic report for build, no-`sorry`,
type/axiom, strict-audit, cold/warm/late-edit and peak-memory gates; it no longer
controls `auto`. `render-proof-cache status` reports whether that record is
absent, stale, or fully qualified.

A single readable source can use the same backend without being manually
copied into files. Put shared `open`/`set_option` commands between
`proof-video: shared-preamble-begin` and `shared-preamble-end`, then add a
comment such as `-- proof-video: module-end helper_theorem` only after a fully
closed top-level section. `render-proof` generates a stable import chain under
the ignored `GeneratedProofs/` directory; content-addressed evidence is stored
separately, so editing a late chunk does not rename unchanged early modules.
The extractor publishes
each generated unit's `.olean` from the same elaboration that creates its trace,
then records a source/toolchain identity and SHA-256 envelope beside it. A stale
or damaged `.olean` is rebuilt instead of silently imported, while a compatible
one skips elaboration. Recreating a missing module bypasses the completed trace
only for that export; compatible command/action/chapter checkpoints remain
enabled, unlike an explicit user-requested `--rebuild-trace`.
`Input/Erdos38.lean` uses five
such units. Boundaries are never inferred automatically: an explicit marker is
required so the generator cannot cut an open declaration or namespace.

Each theorem chapter is stored as an immutable SHA-256 object. The small,
portable `erdos38.json` manifest uses relative references into
`erdos38.trace/objects/` instead of duplicating the complete trace. The strict audit is
`erdos38.audit.json`. The renderer stores
each completed silent MP4 chunk under
`.lean-proof-video-cache/remotion-checkpoints/`. If the computer is shut down,
run the identical command again: completed chunks are checked and skipped, and
rendering continues at the first missing chunk. The chunks are concatenated
losslessly and the soundtrack is muxed only after all chunks exist.

Completed Lean evidence is persistent by default for every proof, even when
renderer caching is disabled. Its stable key contains only the selected theorem,
trace mode, local Lean import closure, and pinned Lean/Mathlib environment.
Changes to Python, Remotion, Manim, pacing, LaTeX cleanup, or project layout do
not invalidate it. Comment-only Lean edits also retain the v2 evidence key;
existing v1 evidence is hash-checked, strictly audited, and migrated instead of
being deleted. Every reuse still passes the current strict audit. Use
`--rebuild-trace` only when the Lean evidence itself must be regenerated; the
simple wrapper also accepts
`render-proof.cmd Proof.lean proof.mp4 --rebuild-trace`. The
internal in-progress chapter checkpoints remain extractor-versioned because
their private representation is not a public compatibility contract.

The production 4.28 extractor deliberately keeps `ProofLatex` and Mathlib out of its own import
graph. They are loaded once into the input proof environment, which avoids the
Windows PE limit of 65,535 exported symbols. The native extractor is copied to
a content-addressed cache path with a hash-checked sidecar. If an older
`Animate.exe` is still running and Windows locks Lake's canonical output, the
current object files are linked directly to the new versioned path instead of
overwriting that process. Lake is therefore needed only when extractor sources
or the pinned environment change, not once per proof unit.

Hybrid runs additionally write `proof-index.json` beside chapter checkpoints.
It contains every reachable local declaration's proof fingerprint,
dependencies, axioms and source range. The action journal remains split into
atomic 128-action objects. Use `--rebuild-chapter THEOREM` to recompute one
chapter while retaining all compatible sibling command/action/chapter
checkpoints; the selected declaration is still kernel-certified and strictly
audited.

Cache size eviction is deliberately disabled. Inspect it or prune only
explicitly selected data with:

```powershell
.\render-proof-cache.cmd status
.\render-proof-cache.cmd prune
.\render-proof-cache.cmd prune --scope render
```

The default prune removes only interrupted `.tmp`, `.writing`, and `.syncing`
files. `--scope render` keeps Lean evidence and snapshots. The destructive
`--scope all` is never run automatically.

For any other long proof, add `-- proof-video: resumable` to its Lean source or
invoke the general command with `--resume`. Use `--render-chunking N` only when
you explicitly need fixed-size MP4 checkpoints; semantic `auto` chunks normally
give cleaner transition boundaries. Smaller chunks lose less rendering work
after an interruption but have slightly more encoding/assembly overhead. After an
interrupted trace extraction Lean must still elaborate the source to rebuild
its environment, but completed local-theorem chapters are reused rather than
semantically rendered again. Once the combined JSON trace exists, unchanged
source and toolchain inputs skip Lean entirely.

If you provide a licensed track at `assets/background-music.mp3`, it is mixed
into renders by default at background volume. The audio file itself is not
distributed with this repository. Use `--no-audio` with the general
`lean-proof-video` command for a silent export, or `--audio PATH` to select
another licensed soundtrack.

Remotion is the default renderer. Install its pinned JavaScript dependencies
once:

```powershell
cd .\remotion
npm install
cd ..
.\lean-proof-video.cmd .\Input\Imo2011P3.lean Imo2011P3.imo2011_p3 `
  --render-concurrency auto `
  --output .\output\imo-2011-p3-remotion.mp4
```

Remotion consumes the same strict audited timeline and produces one MP4. Before
the main render, one browser pass creates a content-addressed `LayoutManifest`:
each unique KaTeX state is laid out once and subsequent frames read prepared
coordinates without DOM measurement. The content-addressed webpack bundle and
layout manifest persist across commands. One global scheduler reuses one Chrome
process and renders semantic checkpoints sequentially, while calibrated browser
tabs provide frame-level parallelism without oversubscribing the machine. It consumes
the same validated `TransitionPlan` as Manim:
certified tokens stay in place or slide to their new logical occurrence,
certified premise copies may travel between different rows, and unresolved
tokens are recreated instead of being matched by text or shape. Audio is muxed
after the silent master, so changing it does not invalidate proof rendering.
On the first run for a new hardware, Chromium, renderer and resolution
fingerprint, a 120-frame calibration compares 3, 4, 6 and 8 tabs. GPU
composition is accepted only after pixel-equivalent output and at least a 10%
throughput improvement. `auto` also tests RTX/NVENC bitrates against the CPU
reference with SSIM >= 0.995, and falls back to `x264 veryfast` if the encoder
is missing, fails, or misses the quality threshold. The measured plan is stored
under `.lean-proof-video-cache/render-profiles/`; each video writes a detailed
`*.render-profile.json` report.
`--preview` renders only the first 20 seconds with
Remotion, so renderer iteration never compiles the full IMO demo by accident.
Use `--preview-tail` to render only the final 20 seconds, including the real
QED square, without compiling the full video.

Proof actions use a cinematic pacing curve. The first ten seconds of proof
actions keep one fixed slow cadence; only after that plateau do they begin a
smooth ten-second acceleration towards the middle pace. The ending mirrors this
schedule: deceleration finishes before the last ten seconds, which then remain
at one fixed slow cadence through the final inference. Short proofs overlap
these regions instead of being padded artificially.

Temporal visibility is the hard constraint: the maximum accepted pace is now
three times the former ceiling (180 glyphs/second at 30 FPS), while every proof
action still receives at least three frames. Remotion does not lengthen a step merely because
it contains many new glyphs; writing density may become arbitrarily high, while
the logical action itself remains visible. An explicit duration limit that
would violate the action-frame floor is rejected instead of silently producing
a one-frame proof step. Exact-duration previews keep their requested wall-clock
length.

`--trace-mode hybrid` is the default. It combines human-scale source tactics
with kernel certificates: local theorems are dependency-ordered chapters, every
tactic action carries Lean's elaborated proof assignment, and every completed
declaration is checked again as a whole. This avoids expanding typeclass search,
normalizer certificates and equality transports into thousands of presentation
rows without weakening verification. The CLI writes `<video>.audit.json` and
refuses to render a trace whose chapter certificate, dependency order, action
fingerprint or semantic edge is invalid.

The explanation layer is extensible without weakening that rule. Built-in
adapters for `rw`/`subst`/`change`, `simp`, `ring` and
`linarith`/`nlinarith` expose the exact premise identities, supporting
constants, proof kind and assignment fingerprint produced by Lean. They never
manufacture a plausible rewrite sequence from repeated symbols. An unknown
tactic remains one kernel-certified source move until a tactic-specific
extractor can provide equally strong evidence.

Every run writes timing and cache data to `<video>.metrics.json`. Before
rendering, `<video>.qa.json` and `.qa.html` check semantic persistence,
certificates and unhandled implementation notation. After rendering,
`<video>.visual-qa.json` and `.visual-qa.html` verify resolution, duration and
prolonged nearly-empty blackboard intervals; an eight-frame
`<video>.visual-qa.png` contact sheet makes regressions quick to inspect. Hard
violations fail the command but leave the MP4 intact for diagnosis.

`--trace-mode proof-term` remains available for diagnostics that deliberately
need every kernel proof-term construction. It is also unbounded and uses
append-only 128-row fragments, but can be much larger and less readable than the
source-tactic presentation. `--trace-mode tactic` is the legacy single-theorem
compatibility format.

There are no step labels. The board shows one live sequent: active assumptions and local definitions above, and exactly one current conclusion after `\vdash`. A new assumption is written glyph by glyph; an existing conclusion is rearranged in place using proof-expression identity. Identical context rows remain the same settled objects and never blink merely because the conclusion changes. Every formula uses one fixed chalk font size, while the camera dynamically zooms to fit the sequent. Entering or leaving a quantified scope expands or contracts the same block. The final mathematical conclusion ends with a deliberately paced QED square.

The legacy tactic-state animation is still useful when comparing the new architecture with the original project. Invoke it explicitly:

```powershell
.\lean-proof-video.cmd .\Input\Tutorial.lean tutorial `
  --trace-mode tactic --output .\output\tutorial-tactic-fallback.mp4
```

In this fallback mode, the Manim timeline follows upstream `goalActions`, and the semantic transition machinery below controls in-place transformations of goal rows.

State changes are driven by elaborated `Lean.Expr` occurrences rather than repeated characters. Each occurrence has an expression-tree path, an `FVarId`/constant identity or normalized fingerprint, and its exact span in the unchanged LeanTeX output. Certified rule adapters transform those paths (for example the body path of `forall` elimination and introduction). Local `x : A` declarations have explicit binder/name/colon/type nodes, so closing a scope moves that same declaration into `\forall x : A` instead of manufacturing a second `x`. Complete applications such as `f(x)` are indivisible transition candidates; a bare function head can never move independently.

Source actions additionally use Lean's own `TacticInfo` before/after
metavariable contexts.  Parentage is recovered with `Meta.getMVars`, matching
the infoview's goal-parent algorithm, and changed expression paths come from
the public `Lean.Widget.diffInteractiveGoals` implementation.  Consequently a
transition is attached to the exact `sourceGoalId -> targetGoalId` pair.  A
second `calc` branch or another sibling may share a parent, but it can never
reuse the previous sibling's visual edge.  If the widget diff cannot describe
an exotic goal, extraction continues and the renderer conservatively writes
the unresolved material.

Proof-term presentation uses a separate immutable proof-DAG projection.  A
completed derived fact remains one stable context object while its certified
descendants use it; it is not hidden and reconstructed through synthetic
`forall` staging rows.  Certified instantiation values stay in the strict
audit evidence and animate in place instead of appearing as administrative
`x := value` lines.  This keeps source-action lifecycle, kernel certificates,
and visual presentation independent without losing any proof obligation.

The strict renderer compiles Lean edges into a `TransitionPlan` containing only `preserve`, `copy`, `rewrite`, `create` and `delete` operations. Google OR-Tools CP-SAT selects a globally non-overlapping set of maximal AST hyperedges across all visible rows. A second validator checks the solver result before Manim sees it. Equal LaTeX, equal SVG shapes, screen position, generic fingerprints and SymPy are not proof of identity and cannot produce strict moves. Anything unresolved becomes `delete + create` (visually, a new write) rather than a guessed transform. This is deliberately conservative: a transition may be less elegant, but it cannot permute two equal-looking `f`, parentheses, relations or binders without a certified edge. Legacy `latexIndexMaps`, shape matching and optional SymPy analysis remain isolated to old tactic traces that do not claim strict ProofTrace semantics.

For old non-strict tactic traces only, a secondary [SymPy AST matcher](https://docs.sympy.org/latest/modules/parsing.html) can still analyze bounded algebraic subexpressions. Strict ProofTrace rendering never promotes a SymPy or textual proposal to a physical move; algebraic preservation must be exported as a certified Lean congruence/rewrite path or it is written anew.

New rows use Manim's real stroke-drawing `Write` animation, one visible mathematical glyph after another. Letters are traced onto the board instead of merely becoming visible. The first render is therefore more expensive; pass `--cache` when you want Manim animations and segmented transition files to remain reusable.

Add a soundtrack that you have permission to use:

```powershell
.\lean-proof-video.cmd .\Input\Imo2011P3.lean Imo2011P3.imo2011_p3 `
  --engine manim `
  --audio C:\path\to\licensed-track.mp3 `
  -o .\output\imo-2011-p3-demo.mp4
```

The CLI always saves the intermediate trace beside the video as `.json`. To inspect extraction without rendering:

```powershell
.\lean-proof-video.cmd .\examples\Cinematic.lean cinematic_square `
  --json-only -o .\output\cinematic-square.mp4
```

To audit why formulas transform, fall back to shape matching, or enter as new
blocks, export a transition map without starting Manim:

```powershell
.\lean-proof-video.cmd .\Input\Imo2011P3.lean Imo2011P3.imo2011_p3 `
  --json-only --dump-transition-map .\output\imo-2011-p3-transitions.json `
  -o .\output\imo-2011-p3-demo.mp4
```

The diagnostic follows the rendered semantic timeline and its first focused
goal. `semantic_transition` edges come directly from Lean expression identities
and retain their `reason`, `confidence`, `proofKind`, and `adapter`. Nodes absent
from those edges are listed as unmapped; the tool never fills semantic gaps with
an older character map. `legacy_character_map` identifies old
`latexIndexMaps`, while `legacy_shape_fallback` means Manim must choose matching
glyphs at render time and the diagnostic deliberately emits no invented edges.
Dormant branches also report their row-similarity confidence and the reason for
remaining inside one visual block. Formula references separately identify
semantic LeanTeX, per-expression legacy fallback, and old traces rendered from
Lean's pretty-printed state.

Useful options:

- `--engine remotion|manim`: use the faster one-render Remotion path (default), or explicitly select the legacy Manim path
- `--render-concurrency auto|N`: calibrate Chromium tabs for this machine (default), or force a number; `--remotion-concurrency` remains an alias
- `--render-hardware auto|cpu|gpu-required`: use quality-validated NVENC when available, force x264, or fail unless GPU encoding succeeds
- `--render-chunking auto|SECONDS|off`: use semantic 5--15 second checkpoints, fixed-size checkpoints, or one full render
- `--recalibrate-renderer`: discard the stored concurrency/GPU decision and benchmark it again
- `--render-profile-report PATH`: choose the detailed renderer timing and hardware report path
- `--trace-mode hybrid|proof-term|tactic`: use source-tactic chapters plus kernel certificates (default), the unbounded expanded proof-term trace, or the legacy single-theorem tactic extractor
- `--lean-workers N`: bounded parallel kernel certification after sequential source elaboration (default: at most 4)
- strict hybrid and ProofTrace runs always emit `<video>.audit.json`; there is no flag to bypass their fail-closed validators
- `--render-mode full|segmented`: render the complete proof as one Manim scene (`full`, default), or render and assemble independently cached transitions (`segmented`)
- `--quality low`: fast 854×480 development render
- `--quality medium`: 1280×720 landscape
- `--quality high`: 1920×1080 at 30 fps (default)
- `--quality high60`: 1920×1080 at 60 fps
- `--quality shorts`: 1080×1920 vertical at 30 fps
- `--quality shorts60`: 1080×1920 vertical at 60 fps
- `--fps 60`: override the selected profile's frame rate
- `--renderer auto|opengl|cairo`: select GPU rendering or the CPU fallback; `auto` avoids dense scenes or segments known to stall some Windows OpenGL drivers and remembers timed-out renders for future Cairo runs
- `--preview`: render the first 20 seconds with Remotion; with `--engine manim`, render representative first, middle, and final transitions
- verified Lean evidence is reused by default and is independent of renderer caching
- `--rebuild-trace`: ignore durable evidence and elaborate Lean again
- `--rebuild-chapter THEOREM`: rebuild one theorem chapter while reusing compatible siblings
- `--toolchain-backend auto|lean-4.32|lean-4.28`: `auto` first uses the isolated Lean 4.32.1 snapshot backend and automatically retries the complete Lean phase with 4.28/legacy if 4.32 fails; the explicit choices are fail-fast
- `--trace-backend snapshot|legacy`: use the validated 4.32 incremental prefrontend or the legacy frontend (`snapshot` requires Lean 4.32)
- `render-proof --trace-granularity auto|fine|scalable`: ordinary two-path renders default to the fine proof-term profile; resumable sources default to scalable hybrid chapters
- `--cache`: additionally reuse Manim/Remotion rendering artifacts
- `--no-cache`: disable renderer reuse; durable Lean evidence remains enabled
- `--write-speed 48`: set the middle proof-animation pace. In Remotion, movement and writing share the complete duration of each step; the first and final step retain the same fixed absolute speed, while the edge curves adapt smoothly to the selected middle pace. The `--chars-per-second` alias remains for compatibility.
- `--max-duration SECONDS`: optional duration ceiling; without it, no proof steps are compressed to meet a time limit
- `--trace proof.json`: rerender an existing trace without invoking Lean
- `--dump-transition-map proof-transitions.json`: export mapping edges, reasons, confidence, and semantic/legacy fallback decisions

### Full and segmented rendering

The default `--render-mode full` constructs one `ProofScene`, calls Manim once,
and renders the proof's complete timeline into one silent video. This avoids a
separate Manim scene startup for every proof transition and is the recommended
mode for a final production render.

Manim may still create temporary `partial_movie_files` for individual `play()` calls while
rendering this single scene. Those are Manim's internal working files and do
not mean that the proof-video pipeline launched a separate scene or output video
for every proof state.

Use `--render-mode segmented` for quick previews, incremental iteration, or as a
fallback when a long full-scene render is unreliable on the selected renderer.
It renders transitions independently, keeps their content-addressed MP4 files,
and stream-copies them into the silent result. Editing one proof state can then
invalidate only its nearby transition windows instead of the complete video.

Both modes mux optional audio with FFmpeg only after the silent mathematical
video is complete. With `--cache`, changing a soundtrack therefore does not
require Manim to render the proof again.

The command always reuses compatible, strictly audited Lean evidence. With
`--cache`, it additionally reuses:

- Manim's generated LaTeX/SVG artifacts;
- Manim's internal animation files for a full-scene render;
- in segmented mode, each transition MP4, keyed by its nearby proof states, timing, style, renderer, resolution, and fps;
- the completed silent video or segmented FFmpeg assembly.

An unchanged render can therefore reuse the cached silent result. For the most
fine-grained cache reuse while editing a proof, select segmented mode.

Before a scene starts, the renderer deterministically collects the exact
content-addressed TeX expressions it will need and compiles missing SVGs in
parallel.  The default is at most eight TeX workers and can be changed with
`LEAN_PROOF_TEX_WORKERS`. A warm repeat still validates every key, but typically
finishes this stage in well under a second.

For a long Cairo proof, `--render-mode segmented` renders independent verified
transitions concurrently and concatenates them losslessly into the requested
MP4. It defaults to four processes; set `LEAN_PROOF_RENDER_WORKERS` to match the
number of physical CPU cores. Each completed transition is content-addressed,
so an interrupted cached render resumes instead of restarting the proof.

There is no fixed pause between steps. One continuous master clock controls the
opening/glide/closing curve, with a constant middle cadence. Each end has a
ten-second constant-speed plateau followed or preceded by a ten-second smooth
ramp. These intervals are integrated in real time and quantized without
breaking their monotone speed change. Movement, deletion, and writing all use the same
`smootherstep` progress over the complete duration of their step, so they finish
together without a settled gap. New symbols are still revealed in visual
reading order, regardless of their density. There is no default video-duration
ceiling.

## What is rendered

- One left-aligned live sequent is shown: active assumptions/definitions and one current conclusion. Previous states are not accumulated below it.
- Introducing a binder adds a context row; discharging it removes that row and transforms the conclusion. A mathematical `let` such as `s := …` is shown, while proof-valued implementation lets remain only in the strict trace.
- Unchanged context rows are held stationary. In strict hybrid or ProofTrace mode, changed subexpressions move only through certified Lean identities; bounded SymPy matching is available only to legacy tactic traces.
- Formula glyphs keep one uniform world-space size. The camera alone zooms in or out as the live block grows, contracts, or wraps.
- Movement and writing use the duration of their current step, so their speed follows the same continuous opening/middle/closing curve and they finish together. Formula length changes the writing density, not the step duration. The final mathematical state ends with a deliberately paced `\square` QED mark on its right.
- Common Lean notation (`∀`, `∃`, `→`, `ℕ`, `Nat.Prime`, and similar) is converted to LaTeX. Unknown declarations remain mathematical `\operatorname{...}` expressions instead of source-code text.
- Each JSON goal includes `latexTarget`, structured `latexContext`, and semantic expression nodes; every successor includes proof-aware `semanticTransition` edges.
- `MathlibLatex.lean` is the central notation dictionary. It adapts the Apache-2.0 `LeanTeX-mathlib` rules to this project's Mathlib version and adds readable notation for every mathematical definition introduced by `Input/Erdos38.lean`. `ProofLatex.lean` is the stable import facade used by the extractor.

## Implementation plan / roadmap

The first usable milestone in this repository covers the complete local path from `.lean` to `.mp4`:

1. **Verified extraction — implemented.** The default hybrid trace records source-level `InfoTree` tactic actions, their elaborated assignments and independently kernel-checked local-theorem chapters. Unbounded ProofTrace v2 and the legacy tactic trace remain explicit alternatives.
2. **Renderer replacement — implemented.** Remotion/KaTeX is the optimized default; Manim/`MathTex` remains the compatibility renderer.
3. **CLI and duration control — implemented.** One command exports JSON, renders an unlimited-duration MP4 by default, supports local audio, and optionally accepts an explicit duration ceiling.
4. **Semantic notation — implemented.** LeanTeX renders elaborated expression trees; the legacy string converter is only a fallback.
5. **Full and incremental rendering — implemented.** Remotion renders bounded resumable ranges concurrently and losslessly assembles them; one-scene and segmented Manim paths remain available. OpenGL is isolated behind a timeout and Cairo fallback.
6. **Proof-semantic transformations — implemented.** Lean exports elaborated expression occurrences, proof-assignment classification, tactic adapters, LaTeX spans, and deterministic logical edges. Character maps are legacy-only.
7. **Live-sequent presentation — implemented.** The renderer keeps one conclusion and its active assumptions in a persistent block; unchanged rows are never retyped or whole-board faded.
8. **Editorial automation — next.** Add hook selection for long proofs, automatic title cards, and batch rendering.
9. **Channel production — next.** Add reusable licensed soundtrack profiles, thumbnail generation, subtitle/metadata export, and a render queue.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
.\lean-proof-video.cmd .\examples\Cinematic.lean cinematic_square --quality low -o .\output\smoke.mp4
```

For repeatable performance comparisons, keep the generated
`<video>.metrics.json` files. They distinguish Lean export/cache time, renderer
wall time, mathematical video duration, state count, and rendered versus reused
checkpoints; this avoids making speed claims from videos with different proof
lengths or cache warmth.

## Origin and license

The Lean extractor is derived from `animate-lean-proofs`; see the repository history and `LICENSE` (Apache-2.0). The new Manim renderer lives in `proof_video/`.
