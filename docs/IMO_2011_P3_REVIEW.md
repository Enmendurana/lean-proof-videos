# IMO 2011 P3 semantic-core review

This document records the mathematical and visual acceptance review for
`Imo2011P3.imo2011_p3`. It deliberately separates four different artifacts:

1. the preserved proof-term baseline from commit `8520d62`;
2. the first source-action trace, before the Lean proof was made explicit;
3. the first full semantic-core render, which exposed two critical presentation
   defects despite passing the machine audits;
4. the accepted final-v3 candidate, rendered from a fresh ABI 5 trace and
   reviewed at every transition plus a regular half-second cadence.

Passing Lean, the strict trace audit, or presentation QA is necessary but is
not by itself sufficient for release. The final video must also show every
mathematically necessary dependency, close the last goal before QED, and
preserve only identities certified by Lean.

## Evidence and preserved baseline

The immutable comparison package is outside the worktree at
`D:\chatGPT\semantic-core-baseline`. Its principal records are:

- `BASELINE.md` — build and media facts for commit `8520d62`;
- `IMO_MATH_REVIEW_PREP.md` — an independent audit of every selected baseline
  state `V0`--`V70` and kernel row `K5`--`K734`;
- `IMO_SOURCE_ACTION_AUDIT.md` — comparison of the coarse source trace with the
  explicit 53-action proof;
- `IMO_VISUAL_REVIEW_NOTES.md` — independent review of every endpoint and
  midpoint in the first semantic-core full render.

### Proof-term baseline (`8520d62`)

| Item | Preserved value |
|---|---|
| Source | `Input/Imo2011P3.lean` |
| Theorem | `Imo2011P3.imo2011_p3` |
| Public command | `./render-proof.cmd ./Input/Imo2011P3.lean ./output/imo2011p3-full.mp4` |
| Trace | schema 2.2; 735 kernel rows projected to 71 visible states |
| Checked transitions | 70 |
| Video | 1,482 frames, 30 fps, 49.400 s, 1920 x 1080 |
| Video codec | H.264 High, 1,074,054 bit/s |
| Audio | AAC LC stereo, 48 kHz, 132,493 bit/s |
| File size | 7,504,323 bytes |
| Strict QA | valid, no warnings |
| Build baseline | full `lake build`, 8,136 jobs |

The baseline is useful as an independent mathematical and visual reference. It
is not canonical evidence for ABI 5, because it is a selected projection of a
proof term rather than an ordered source-action frontier.

One known baseline omission is important: the branch fact
`0 \le f(0)` exists in kernel rows `K716`--`K718`, but the old presentation
selected three administrative zero-simplification lemmas instead. The new
video must show the mathematical fact and may hide those administrative rows.

## Independent mathematical audit

The preserved audit checks the entire proof, not only frames that happened to
look plausible. The theorem assumes

\[
H(x,y): f(x+y) \le y f(x) + f(f(x))
\]

and proves `f(x) = 0` for every `x \le 0`. The required mathematical route is:

1. reparameterize `H` to
   \[
   H'(x,t): f(t) \le t f(x)-x f(x)+f(f(x));
   \]
2. for `x < 0`, combine two concrete instances of `H'` to obtain
   `x f(x) \le 0`, hence `0 \le f(x)`;
3. prove `f(x) \le 0` for all `x` by contradiction, using
   \[
   s := \frac{x f(x)-f(f(x))}{f(x)},
   \qquad a := \min(0,s)-1;
   \]
4. combine the two inequalities by antisymmetry to obtain `f(x)=0` for
   `x<0`;
5. split `x \le 0` into `x<0` and `x=0`; in the zero branch derive
   `0 \le f(0)` from `H'(-1,-1)` and `f(-1)=0`, combine it with
   `f(0) \le 0`, and close the theorem.

The kernel audit records the axioms `propext`, `Classical.choice`, and
`Quot.sound`. No mathematical objection was found in the Lean proof itself.
The failures found below concern which certified actions were materialized and
how their logical identities were animated.

### Required visible derivation

| Segment | Certified facts that must be visible | Safe to combine or hide |
|---|---|---|
| Reparameterization | introduction of `x,t`; `x+(t-x)=t`; `hf x (t-x)`; distributivity; completed `H'` | proof-term congruence and discharge wrappers |
| Negative input | both instances `H'(2f(x),f(x))` and `H'(x,f(2f(x)))`; their combination to `x f(x)\le0`; sign argument | internal `nlinarith` certificate terms |
| Nonpositivity | definitions of `s,a`; `a<s`; `a<0`; `H'(x,a)`; multiplication by positive `f(x)`; common-tail inequality; zero identity; `f(a)<0`; `0\le f(a)` | typeclass arguments, `Eq.mp`, routine negation normalization |
| Antisymmetry | `f(x)\le0` and `0\le f(x)` feeding `f(x)=0` | final implication/forall discharge scaffolding |
| Final cases | real branch split; negative branch result; `f(-1)=0`; `H'(-1,-1)`; `0\le f(0)`; `f(0)\le0`; final equality | separate `mul_zero`, `sub_zero`, and `zero_add` rows |

## Why the original source-action trace was insufficient

The first fresh source-action extraction was formally valid but contained only
30 actions and 29 visible transitions. It gave small commands such as `intro`
their own states while collapsing inline `calc` edges, concrete theorem
specializations, `nlinarith` premises, and continuations into large jumps.
Consequently, the central mathematics was less rigorous on screen than the
71-state proof-term baseline.

The source proof was therefore refactored to name genuine mathematical
intermediates. A fresh hybrid trace then contained 53 certified actions and 52
adjacent displayed transitions, with strict audit and presentation QA both
passing. The refactor made the following dependencies explicit:

- `harg`, `hspec`, and `halg` for the three reparameterization edges;
- `h_outer`, `h_inner`, and `hprod` for the two specializations and their
  arithmetic consequence;
- `s`, `a`, `ha_lt_s`, `ha_neg`, `h_at_a`, `hmul`, `htail`, `hzero`,
  `hfa_neg`, and `hfa_nn` for the contradiction argument;
- `hle` and `hge` before antisymmetry;
- `hle0`, `hno`, `hp`, and `hge0` in the final zero branch.

The current names are intentionally shorter than the first candidate's
`hspecialized_outer`, `hspecialized_inner`, and similar labels. This reduces
line wrapping without removing any proof dependency.

### Grouped action coverage

This is the expected semantic sequence. It is grouped for review; it is not a
request to recreate every administrative tactic as a separate video row.

| Actions | Mathematical purpose | Review expectation |
|---|---|---|
| A00--A07 | replace `hf` by `H'` through `harg`, `hspec`, `halg` | old `hf` remains available while proving the replacement, then is replaced exactly once |
| A08--A14 | prove nonnegativity for negative arguments | two whole inequalities are instantiated before `hprod`; no isolated matching of repeated `f` glyphs |
| A15--A36 | prove global nonpositivity | values of `s` and `a` remain visible; every inequality has its actual premise; the contradiction closes its temporary goal |
| A37--A41 | strengthen the negative-input lemma to equality | both inequalities move into the antisymmetry result; the old and strengthened lemmas are not left duplicated |
| A42--A50 | introduce the final input and solve both cases | two goals have distinct identities; `0\le f(0)` is shown, not replaced by simplifier bureaucracy |
| terminal action | direct `exact hle0.antisymm hge0` | one live goal becomes an empty frontier; QED appears only after this close edge |

## First semantic-core full render: independent review

The first full candidate was inspected continuously and through all 13 contact
sheets. Every transition was sampled at its source endpoint, midpoint, and
target endpoint, with additional exact-frame inspection around the case split
and the last eight seconds.

| Item | Reviewed value |
|---|---|
| MP4 | `output/imo2011p3-semantic-core-final.mp4` |
| MP4 SHA-256 | `4771EA7E80432F598C93D426E280E3B561206BE1BDC83F499358541E1CAEA191` |
| Video | 1920 x 1080, 30 fps, 1,228 frames, 40.933 s, H.264 + AAC |
| Timeline SHA-256 | `0090441729C862A5E27086B1D21F7F75E4155CF9C2E0CAF5AFE25DCC0E72B4EE` |
| Timeline | 53 displayed states, 52 transitions |
| Strict audit | 53/53 certified source-tactic actions; 9,247 semantic edges |
| Semantic/presentation QA | no warnings |
| Visual QA | near-empty intervals at 0.000--0.933 s and 6.000--7.833 s |

Machine QA passed, but the independent review rejected this render for release.

### Critical: local definition values were dropped

The video displayed only `s : \mathbb R` and `a : \mathbb R`, not their values.
Later `dsimp` and rewrite steps therefore appeared to introduce expressions
without a premise. This made the central contradiction argument incomplete for
the viewer even though Lean's hidden state remained correct.

### Critical: QED was shown while the goal was still open

The trace contained the final `exact hzero` action with one goal before it and
no goals afterward, but the timeline stopped before that action. The last
frame consequently showed both `hzero : f(0)=0` and the open target
`\vdash f(0)=0`, alongside QED. This was simultaneously a temporal omission,
a duplicated equation, and a false visual claim that the displayed frontier
was closed.

### Major: near-empty scope-return interval

From 6.000 to 7.833 seconds, the reparameterization scope returned and the
next subproof opened through middle frames with almost no stable content. The
mathematical endpoints were correct, but the viewer temporarily lost the proof
context. Preserved global rows and arriving local rows must be staged
concurrently so a scope return never resembles an empty blackboard.

### Minor observations

- The first 0.933 seconds were nearly black. This can be an intentional music
  lead-in, but it should be a deliberate editorial choice.
- Some midframes, especially the two-goal split, were dense. Endpoint layout
  was correct, but branch-card motion and branch-specific writing may benefit
  from a small stagger.
- The long first-candidate hypothesis labels caused avoidable line wrapping.
- Reusing the name `f_of_neg` for a strengthened lemma is valid Lean but needs
  a clear replacement transition so it does not look like an unrelated fact.

### Confirmed correct in that candidate

- No endpoint was clipped on any side at 1920 x 1080.
- Rows were left aligned and the dense contradiction segment fit the frame.
- Both specializations used in the `x f(x)\le0` argument were visible.
- The case split produced two distinct goal cards; repeated global context
  there was intentional per-goal context, not an accidental duplicate.
- The zero branch showed `f(0)\le0`, `f(-1)=0`, `H'(-1,-1)`, and
  `0\le f(0)`.
- The QED square was right aligned, and the terminal wave affected all proof
  rows while excluding the QED square itself.

## General corrections in the final-v3 candidate

These corrections are architectural invariants, not exceptions for this
theorem.

### Canonical local definitions

A local definition is represented from Lean's immutable local declaration as
one canonical object containing its name, type, and optional value. The shared
row projection renders, for example,

\[
s : \mathbb R := \frac{x f(x)-f(f(x))}{f(x)},
\qquad
a : \mathbb R := \min(0,s)-1.
\]

Both renderer adapters consume the same projected rows; neither renderer
reconstructs a definition by parsing display text. The definition value has
its own structural spans and identities, so `dsimp`, rewrite, replacement, and
`clear_value` remain typed transitions. Corpus and row tests require at least
one kernel-visible definition value and verify add/change/clear behavior.

### Direct final `exact` and an explicit terminal frontier

The current Lean source ends directly with

```lean
exact hle0.antisymm hge0
```

rather than adding a redundant `hzero` local and then applying it. The action's
authoritative `afterState` is the empty goal frontier. The proof model and
visual vocabulary represent this as `GoalEffectKind.CLOSE`/`close`; QED is
permitted only after the empty frontier has been materialized. The final-v3
render demonstrates this invariant: the closing action has an explicit empty
`afterState`, the terminal certificate is carried onto the last visible frame,
and both renderers gate QED on that certificate.

### Explicit source steps with compact mathematical labels

Large opaque automation calls were split into named, kernel-checked
intermediates at the source. This preserves rigorous one-move-at-a-time
progress without asking the renderer to invent a preferred explanation of an
arbitrary `nlinarith` certificate. Short names such as `h_outer`, `h_inner`,
`h_at_a`, and `hfa_nn` retain the same dependencies while improving line
length and hanging indentation.

### No continuity from equal rendered text

Two equal LaTeX strings are not evidence that they are the same logical
object. Continuity now requires a Lean identity, definitional equality, alias,
certified source edge, or typed structural relation. If none exists, the
planner emits independent remove/create primitives and records
`uncertified-text-continuity-rejected`. It does not silently fall back to
glyph-, punctuation-, or token-equality matching. This rule prevents repeated
`f`, `x`, relations, parentheses, and commas from being permuted merely because
their rendered symbols happen to match.

### Required stable scope returns and branch staging

Closing a nested goal must not erase unchanged global context. The final-v3
candidate plans the outgoing goal, preserved rows, and incoming focus from
one observed before/after frontier, allowing movement and writing to overlap
without an intermediate empty-board state. A split or merge must retain
distinct goal IDs; duplicate global rows are acceptable only when they belong
to distinct live goal cards. The final contact sheets confirm both the scope
return and the two-card case split.

## Final-v3 render statistics

| Item | Final-v3 evidence |
|---|---|
| MP4 | `output/imo2011p3-semantic-core-final-v3.mp4` |
| MP4 SHA-256 | `BFCA70B74DDF3225F9902415C217FBBF30B024D8E6D1A7199EB91FA48CFAE18A` |
| Timeline | `output/imo2011p3-semantic-core-final-v3.timeline.json`; SHA-256 `E1F5530F600CAF77CDAEB1644D45F9307498DE1350975EEAA1558886D4BD13AF` |
| Trace | hybrid manifest schema 3.1; canonical ABI 5 |
| Capabilities | canonical proof state, ordered action frontiers, goal-lineage and entity hyperedges, local-definition values, presentation visibility, expression occurrences |
| Source actions | 52 kernel-certified actions |
| Displayed states | 52 |
| Transitions | 51 visible transitions plus the certified terminal `1 -> 0` close carried by the last visible frame |
| Semantic edges | 8,925 |
| Video | 1,216 frames, 30 fps, exactly 40.533333 s, 1920 x 1080 |
| Video codec | H.264 High, 833,039 bit/s |
| Audio | AAC LC stereo, 48 kHz, 132,436 bit/s |
| File | 4,937,529 bytes, total 974,512 bit/s |
| Strict audit | valid; 52/52 certified actions; no errors |
| Semantic/presentation QA | valid; 51/51 canonical transitions; 30,906 primitives; zero fallback primitives; no warnings |
| Visual QA | valid; heuristic flags low-density intervals at 0.000--0.933 s and 6.000--7.833 s |
| Environment | Lean 4.28.0, Python 3.12.4, Remotion 4.0.509, Node 24.16.0, FFmpeg 9.0, Manim 0.19.2 |
| Source | branch `semantic-core-rewrite`, based on `8520d62`; the pushed release commit contains this report |

The low-density intervals are not missing states. The first is the intentional
one-second opening; the second contains the short quantified goal followed by
the certified `intro x hx` sequence. Regular half-second samples show active
mathematical content throughout. The block is horizontally width-limited, so
additional vertical scaling would either change font size or clip the formula.

## Final independent visual review

Direct Windows playback was attempted, but the desktop-control safety layer
stopped when the local MP4 had to be opened through a browser because local
`file://` URL policy enforcement is unavailable. In accordance with the
fallback review protocol, the complete render was instead checked using:

- all 51 transitions at exact before, midpoint, and after frames (103 unique
  lossless PNGs across 13 contact sheets);
- an additional regular sample every 0.5 seconds from 0.0 through 40.5 seconds;
- the exact Remotion timing manifest and FFprobe media/audio metadata;
- the independent mathematical action audit of every source action.

The review confirmed:

- `s` and `a` appear with their exact definition values before use, and both
  `dsimp` transitions retain their canonical local-definition identity;
- every new displayed formula has a named, kernel-certified premise or source
  action; equal text never creates an uncertified physical continuity;
- the reparameterization scope return retains the global context and never
  shows an actually empty proof state;
- no local/global row is accidentally duplicated within one goal card;
- the case split creates two distinct cards, each with an independent goal ID;
  the negative branch closes before the zero branch is promoted;
- `f(0) \le 0` and `0 \le f(0)` are both visible before the final equality;
- the terminal action has one source goal and an explicit empty target
  frontier; QED appears only on the certified-closed final state;
- no endpoint clips the frame, and long rows remain left aligned;
- the final wave affects all proof rows while the enlarged, right-aligned QED
  square remains stationary.

The densest transition is the one-goal-to-two-goal split. At its midpoint the
two certified copies overlap while diverging from the common parent. This is a
brief, intentional representation of copying, not an identity error; the two
target cards are clean and separated. No critical or major finding remains.

**Independent decision:** `ACCEPT` based on complete frame-sequence, timing,
audio-metadata, and mathematical review. Direct real-time playback was not
claimed.

## Reproduction and review commands

The accepted final-v3 artifact was rendered from a fresh Lean 4.28 legacy
trace.  Its exact reproduction command is:

```powershell
.\render-proof.cmd .\Input\Imo2011P3.lean .\output\imo2011p3-semantic-core-final-v3.mp4 `
  --toolchain-backend lean-4.28 `
  --trace-backend legacy `
  --trace-granularity auto `
  --rebuild-trace
```

This exact audio-bearing artifact also requires the user's licensed, untracked
`assets/background-music.mp3`.  The repository intentionally does not
redistribute that file; a fresh clone can use the general `lean-proof-video`
command with `--no-audio` for a silent render.

After the render, generate deterministic endpoint/midpoint evidence with:

```powershell
review-proof-render `
  .\output\imo2011p3-semantic-core-final-v3.mp4 `
  .\output\imo2011p3-semantic-core-final-v3.timeline.json `
  --output-dir .\output\imo2011p3-semantic-core-final-v3-review
```

The review package must contain a manifest with source hashes and sampling
rules, a CSV row for every transition/sample pair, lossless numbered PNGs, and
contact sheets. If the generated timeline filename differs, record the actual
path in the Final-v3 render statistics section rather than silently reviewing
an older timeline.

## Remaining limitations and release boundary

- Explicit source intermediates are still needed when an automation
  certificate has no unique, readable mathematical decomposition. This is a
  source-authoring constraint, not permission for the renderer to invent an
  uncertified derivation.
- Text discontinuity may look less fluid than a guessed morph. Mathematical
  provenance takes priority; presentation can improve only after an identity
  edge is certified.
- Mid-transition density and pacing remain visual-review concerns even when
  endpoints and semantic edges are valid.
- The old proof-term baseline and first semantic-core candidate remain useful
  evidence, but neither can substitute for a fresh final-v3 trace, timeline,
  MP4, audit, and contact-sheet review.

The final-v3 candidate satisfies the release boundary: all machine audits pass,
the statistics are complete, the independent decision is `ACCEPT`, and no
unresolved critical or major finding remains.
