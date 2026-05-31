# lume-belt-live: session log and GUI plan

This file records what was set up in the most recent Claude Code session and the approved plan for the next piece of work (a PyQt5 GUI wrapper for `lume-belt-live-demo-lcls.py`). Implementation of the GUI is on hold — the user is making algorithmic changes to `lume-belt-live-demo-lcls.py` first and will signal when to start.

---

## Part 1 — What was done in this session

### 1. Pushed the repo to GitHub (slaclab/lume-belt-live)

- Original `origin` was HTTPS (`https://github.com/slaclab/lume-belt-live.git`); HTTPS auth is not configured on s3df, so push failed with `could not read Username for 'https://github.com'`.
- Verified SSH works as `jy-tang` (`ssh -T git@github.com`) — switched `origin` to SSH:
  ```
  git remote set-url origin git@github.com:slaclab/lume-belt-live.git
  ```
- Working tree contained ~70 GB of run data that must not be committed:
  - `archive/` (52 GB), `scan/` (14 GB), `test/` (670 MB), `plot/` (376 MB), `XTCAV_data/` (221 MB), `example/` (213 MB), `benchmark_with_Litrack/` (198 MB), `example_lcls/` (152 MB), `test_50pC/` (127 MB), and many `*.h5` files >100 MB which GitHub hard-rejects.
- Created `.gitignore` excluding all data directories, `*.h5`, `__pycache__/`, and `.ipynb_checkpoints/`.
- Committed the code/notebooks/CSVs/`templete/belt.in` plus the new `.gitignore`:
  ```
  10586c4  Add LCLS notebooks, scripts, and .gitignore for data dirs
  ```
- Pushed `60a018a..10586c4` to `main`.

**Follow-up cleanup not done** (low priority): `pv_mapping/.ipynb_checkpoints/make_pv_mapping-checkpoint.ipynb` is still tracked from a previous commit. `.gitignore` does not untrack already-committed files. Run `git rm --cached pv_mapping/.ipynb_checkpoints/make_pv_mapping-checkpoint.ipynb` to remove.

### 2. Created `CLAUDE.md`

Project guidance for future Claude Code sessions in this repo. Sections cover what the repo is, how to run the cron-managed loop and the manual variants, the four-stage pipeline architecture, the PV-mapping CSV contract, output directories, dependencies, the SSH origin, an Environment section identifying the conda env as `lume-eblt-dev` and host as s3df, and three hard rules:

1. Do not delete any code unless explicitly asked. If modifying, make a copy and edit the copy.
2. Do not modify or delete files outside `/sdf/data/ad/ard/u/jytang/lume-belt-live/` without asking.
3. PVs are read-only — never `epics.PV.put()` / `caput` without an explicit ask.

### 3. Saved persistent memories

Stored under `/sdf/home/j/jytang/.claude/projects/-sdf-data-ad-ard-u-jytang-lume-belt-live/memory/` so the rules survive across sessions even when CLAUDE.md isn't loaded:

- `feedback_no_code_deletion.md` — never delete code without explicit ask; copy then edit
- `feedback_scope_to_repo.md` — never modify/delete outside the repo without asking
- `feedback_pvs_read_only.md` — never write to PVs without an explicit ask
- `MEMORY.md` — index pointing to all three

### 4. Iterated on the GUI plan (Part 2 below)

The plan went through three rounds of refinement:

- **v1** — four per-stage progress bars (one each for Initial run, BC1 collimator, L1 phase tune, L2 phase tune); live PV panel polled at 1 Hz; worker re-implemented `run1_lcls()` body so it could fire signals between stages.
- **v2** — replaced four bars with one overall 0..100 progress bar plus a `QPlainTextEdit` log streaming captured `print()` output; still re-implemented the body so the bar could be advanced at stage boundaries.
- **v3 (current)** — no per-stage UI at all. Worker calls `pipeline.run1_lcls()` *directly*, no rewrite. Progress bar is now busy/indeterminate (Qt marquee) plus an elapsed-time label — no fake percentages. The live PV panel is now `SnapshotPVPanel`: refreshed once per cycle (at the start, via `pipeline.get_snapshot()`) and kept static for the ~1-minute run, since that matches the values the simulation actually used.

Implementation still deferred until the user finishes algorithmic edits to `lume-belt-live-demo-lcls.py`.

---

## Part 2 — Approved plan: PyQt5 GUI for `lume-belt-live-demo-lcls.py`

### Context

`lume-belt-live-demo-lcls.py` runs a BELT simulation cycle against live LCLS EPICS PVs and currently has only a CLI entry point — operators have no visibility into the PVs used for the run, no progress feedback, and no way to start/stop the loop without `Ctrl-C`. A single `run1_lcls()` call takes ~1 minute and internally does four `evaluate_belt()` calls (initial → BC1 collimator → L1 phase tune → L2 phase tune), but the GUI does NOT need to surface that internal structure.

This plan wraps the existing pipeline in a PyQt5 desktop GUI: pick input lattice and particles from searchable dropdowns; see the snapshot of PVs captured at the start of the current run; one overall progress bar for `run1_lcls()` (busy-style, plus elapsed time) with the script's `print()` output streamed in a log panel underneath; view post-run statistics and longitudinal phase-space plots after each cycle completes; Start/Stop the loop. **`run1_lcls()` is called directly — its body is not duplicated.** The original script is left untouched (per the no-deletion rule).

### Architecture

```
┌───────────────────────────────────────────────────────────────┐
│ Main thread (Qt event loop)                                    │
│   MainWindow:                                                  │
│     - searchable dropdowns (lattice, particles)                │
│     - Start/Stop buttons                                       │
│     - SnapshotPVPanel (curated PVs from THIS run's snapshot,   │
│         refreshed once per cycle, NOT continuously)            │
│     - RunPanel: busy QProgressBar + elapsed timer +            │
│         QPlainTextEdit log                                     │
│     - StatsPanel (current, sigma_t, chirp at BC1/BC2/Final)    │
│     - PlotPanel (4 matplotlib canvases: 101/109/113/201)       │
└───────────────────────────────────────────────────────────────┘
        | Qt signals/slots (thread-safe)
        v
┌───────────────────────────────────────────────────────────────┐
│ SimulationWorker (QThread)                                     │
│   - holds threading.Event for stop                             │
│   - per cycle:                                                 │
│       1) call pipeline.get_snapshot() once -> emit pv_snapshot │
│       2) call pipeline.run1_lcls(input_beam, input_lattice)    │
│          inside contextlib.redirect_stdout(_SignalStream)      │
│       3) extract stats from dat['outputs'] -> emit stats_ready │
│       4) BELT.from_archive(dat['outputs']['archive']) ->       │
│          plot 4 phase-space figures -> emit plots_ready        │
│       5) emit cycle_finished, sleep_interruptible(10)          │
│   - emits: pv_snapshot, log_text, stats_ready, plots_ready,    │
│            cycle_finished, error                               │
└───────────────────────────────────────────────────────────────┘
        | uses imported helpers
        v
┌───────────────────────────────────────────────────────────────┐
│ Imported from lume-belt-live-demo-lcls.py (unchanged):         │
│   run1_lcls, get_snapshot, MONITOR, CSV, DF                    │
│ Imported from belt: BELT.from_archive                          │
└───────────────────────────────────────────────────────────────┘
```

The original script's hyphens block a normal `import lume-belt-live-demo-lcls`. The GUI loads it via `importlib.util.spec_from_file_location` — no rename, no edits to the original. The script's top-level only builds `MONITOR` (EPICS connections) and defines functions; importing has no other side effects (the runner is guarded by `if __name__ == '__main__'`).

### Files

**Create:**

- `/sdf/data/ad/ard/u/jytang/lume-belt-live/gui_lume_belt_live_lcls.py` — main GUI, ~400 lines
- `/sdf/data/ad/ard/u/jytang/lume-belt-live/run_gui.sh` — launcher that activates `lume-eblt-dev`, sets EPICS env (mirroring `cron_job_setup.sh`), and runs `python gui_lume_belt_live_lcls.py`

**Untouched:**

- `lume-belt-live-demo-lcls.py` — read by the GUI via `importlib`; not modified.
- `cron_job_setup.sh`, `lume-belt-live-demo.py` (the LCLS-II flavor) — not in scope.

### Module design: `gui_lume_belt_live_lcls.py`

#### Loading the pipeline module

```python
import importlib.util, pathlib
HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location(
    "belt_live_lcls", HERE / "lume-belt-live-demo-lcls.py")
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)
# Now: pipeline.run1_lcls, pipeline.get_snapshot, pipeline.MONITOR, ...
```

This triggers `MONITOR = {pv: epics.PV(pv) for pv in PVLIST}` once at GUI startup — same as the CLI. The GUI reuses that dict rather than opening a second set of connections.

#### `SimulationWorker(QThread)`

Calls `pipeline.run1_lcls()` directly — **no re-implementation of its body**. The worker just orchestrates the loop, captures stdout, and pulls results out of the returned `dat` dict.

```python
def run(self):
    while not self.stop_event.is_set():
        try:
            pvdata, itime, _ = pipeline.get_snapshot(None)
            self.pv_snapshot.emit(pvdata, itime)

            with contextlib.redirect_stdout(_SignalStream(self.log_text)):
                dat = pipeline.run1_lcls(
                    input_beam=self.input_beam,
                    input_lattice=self.input_lattice,
                )

            self.stats_ready.emit(_extract_stats(dat['outputs']))
            belt_obj = BELT.from_archive(dat['outputs']['archive'])
            self.plots_ready.emit(_build_figures(belt_obj))
            self.cycle_finished.emit()

        except Exception as e:
            self.error.emit(repr(e))

        self._sleep_interruptible(10)
```

Signals:
- `pv_snapshot(dict, str)` — pvdata + isotime captured at the start of the cycle
- `log_text(str)` — captured stdout chunks from inside the cycle (see "Stdout capture")
- `stats_ready(dict)` — current/sigma_t/chirp at BC1/BC2/Final, pulled from `dat['outputs']`
- `plots_ready(list)` — list of (file_id, matplotlib.figure.Figure) for IDs 101/109/113/201
- `cycle_finished()`, `error(str)`

Stop semantics: a `threading.Event` is checked between cycles and during the post-cycle sleep. `evaluate_belt()` has no interrupt hook, so a Stop click during a `run1_lcls()` call lets the in-flight cycle finish (~up to a minute) before the worker exits. The Stop button shows "Stopping…" so the operator knows it's pending.

#### Why this is safe (no `run1_lcls` rewrite needed)

- `run1_lcls(input_beam, input_lattice)` is already parameterized for inputs (line 376 of the original).
- It writes its own snapshot/archive/summary files via the existing `convertToDatedFormat` mechanics — the GUI doesn't need to manage those.
- The returned `dat` dict already contains everything needed for the StatsPanel and PlotPanel (`dat['outputs']` is the final merit dict from `my_belt_merit`, which includes `BC1_current`, `BC1_bunch_length`, `BC2_current`, `BC2_bunch_length`, `L1_chirp`, `L2_chirp`, plus the archive path for the BELT object reload).
- The original script's print output is captured intact via `redirect_stdout`.

#### Stdout capture

`run1_lcls()` is print-heavy (`mysettings`, "Second run, cutting beam charge…", per-iteration "Change L1 phase from … to …", BC1/BC2 R56 values, etc.). The worker wraps the call in `contextlib.redirect_stdout` to a tiny file-like object that emits `log_text(str)` on every `write()`:

```python
class _SignalStream:
    def __init__(self, sig): self.sig = sig
    def write(self, s):
        if s: self.sig.emit(s)
    def flush(self): pass
```

`evaluate_belt(..., verbose=False)` stays as in the original, so only the orchestration's own `print()` calls land in the GUI log. Impact-T subprocess output is not captured by `redirect_stdout` and is left out — that matches the user's request: "the information in the print() function".

#### `SnapshotPVPanel`

Displays the PVs **captured at the start of the current run only** — not a continuous live view. The worker emits `pv_snapshot(pvdata, itime)` once at cycle start (and again at the start of the next cycle); the panel rewrites its labels from that payload and stays static for the duration of the run (~1 minute).

Curated subset shown (resolved via `pipeline.DF` `Variable` column lookup, same as `get_settings`):

- `Charge_inj`, `Charge_BC1`
- `Initial_energy`, `BC1_energy`, `BC2_energy`, `L3B_energy`
- `HL_phase`, `L1B_phase`, `L2B_phase`, `L3B_phase`
- `BC1_current`, `BC2_current`

Layout: two-column `QLabel` grid with units. A small header above the grid shows the snapshot's `itime`. Rationale: `run1_lcls()` itself takes its own snapshot inside `get_settings()` for the simulation; showing PV values that may drift during the minute-long run would mismatch what's actually being simulated. The GUI's panel reflects the same instant the operator's run was based on.

(There are two snapshots per cycle — one by the worker for display, one inside `run1_lcls` for the simulation. They're a few seconds apart, which is fine for display.)

#### `RunPanel`

A vertical layout:

- **Header row:** "Running… elapsed: 0:42" label (updated by a 1 s QTimer in the main thread that ticks while a cycle is in flight; reset on `cycle_finished`). Idle-state text: "Idle".
- **One `QProgressBar`** in **busy/indeterminate mode** (`setRange(0, 0)`) — Qt renders an animated marquee while running. Switched back to `setRange(0, 100); setValue(100)` on `cycle_finished` and `setRange(0, 100); setValue(0)` on idle. No fake percentages.
- **One read-only `QPlainTextEdit`** underneath, sized ~12 lines, monospace, with a max block count (e.g. 5000) so it auto-trims. Connected to `log_text(str)` via `appendPlainText` — appends each chunk as it arrives. A small "Clear" button above wipes the buffer between cycles.

#### `StatsPanel`

Three columns (After BC1 / After BC2 / Final), three rows (Peak current [A], Bunch length sigma_t [fs], Chirp [1/m]). Updated **once per cycle** from the `stats_ready` payload, which is built from `dat['outputs']` after `run1_lcls()` returns. All values are pulled directly from keys already computed by `my_belt_merit` (lines 285–319 of the original): `BC1_current`, `BC1_bunch_length`, `BC1_sigma_t`, `BC2_current`, `BC2_bunch_length`, `BC2_sigma_t`, `L1_chirp`, `L2_chirp`, plus `end_higher_order_energy_spread` / final stats for the Final column.

#### `PlotPanel`

Four `FigureCanvasQTAgg` widgets in a 2x2 grid (Initial / After BC1 / After BC2 / Final), drawn from a single `BELT.from_archive(dat['outputs']['archive'])` reload at end-of-cycle in the worker. Each Figure is generated via `belt_obj.output.plot_distribution(file_id, 'z', 'energy', bins=100)` for `file_id` in `[101, 109, 113, 201]`. On `plots_ready`, each canvas swaps its `Figure` to the latest one and calls `draw_idle()`. Old figures get `plt.close(fig)` to prevent memory growth across cycles.

#### Top of window — searchable dropdown inputs

Two `QComboBox` widgets with `setEditable(True)` and a `QCompleter` for type-to-search filtering. Each combo is populated by recursively scanning the project (`pathlib.Path('/sdf/data/ad/ard/u/jytang/lume-belt-live').rglob(...)`) at GUI startup, with gitignored dirs (`archive/`, `scan/`, `plot/`, `snapshot/`, `summary/`, `__pycache__/`, `.ipynb_checkpoints/`, etc.) skipped to keep the list tractable:

- **Input lattice** combo: `*.in` files. Default selection `example_lcls/belt.in`. A "Refresh" button next to it re-scans on demand.
- **Input particles** combo: `*.h5` files. Default selection `./example_lcls/from_Litrack_250pC.h5`.

Each combo's entries are project-relative paths so the list is readable. The completer uses `Qt.MatchContains` so typing `Litrack` finds `example_lcls/from_Litrack_250pC.h5`. Items that don't resolve to existing files are flagged red. A small "Browse…" button stays available for paths outside the project (rare).

- `QPushButton` **Start** (disabled while running) — reads the two combos, resolves paths against the repo root, instantiates `SimulationWorker(input_beam=..., input_lattice=...)`, starts it.
- `QPushButton` **Stop** (disabled when idle) — sets the worker's stop event; button shows "Stopping…" until the worker emits `cycle_finished` and exits.

### Threading and EPICS interaction notes

- All `MONITOR` / `epics.PV.get()` access happens on the worker thread (inside `pipeline.get_snapshot()` for the snapshot panel and inside `run1_lcls()` for the simulation). The main thread only renders Qt widgets.
- All cross-thread updates use Qt signals (auto-connected with `Qt.QueuedConnection` when receiver is in another thread), so no manual locking is needed for UI updates.
- `plt.close('all')` between cycles, mirroring `my_merit` line 372, prevents matplotlib figure leaks.

### Reuse — nothing rewritten

Everything in `run1_lcls()` is called as-is. The GUI imports and calls:

| From `lume-belt-live-demo-lcls.py` | Used for |
|-----------------------------------|----------|
| `run1_lcls(input_beam, input_lattice)` | the whole simulation cycle |
| `get_snapshot(None)` | the SnapshotPVPanel update at cycle start |
| `MONITOR`, `DF` | resolving the curated PV subset to display |
| `BELT.from_archive` (via `belt.run`) | reloading the final BELT object for plotting |

No code is duplicated. `run1_lcls()`'s body is unchanged in the original script.

### Verification

Run from a `lume-eblt-dev` shell with EPICS network access:

```bash
cd /sdf/data/ad/ard/u/jytang/lume-belt-live
./run_gui.sh
```

Manual checks:

1. **Startup** — window opens; Snapshot PV panel is empty/Idle until the first cycle starts.
2. **Inputs** — typing in the lattice/particles dropdowns filters the project file list; defaults populate without typing.
3. **Start one cycle** — click Start. Verify: snapshot panel populates with the curated PVs and an `itime` header (and stays static for the run), busy progress bar starts animating, elapsed timer ticks (0:01, 0:02, …), log panel fills with the same lines you'd see on `stdout` if you ran the script in a terminal (`mysettings` dict, "Second run, cutting beam charge…", "Change L1 phase from …", "Fourth run, tune L2 phase…", etc.), stats columns and phase-space plots refresh after the cycle ends, and a new cycle begins after ~10 s sleep with a fresh snapshot.
4. **Stop** — during a cycle, click Stop. Button switches to "Stopping…" and the worker finishes the in-flight `run1_lcls()` (~up to a minute), emits `cycle_finished`, and the GUI returns to idle (Start enabled, Stop disabled).
5. **No-EPICS fallback** — verify a clean error message in the GUI when `get_snapshot` raises (e.g. run with `EPICS_CA_ADDR_LIST=` empty); the `error` signal should populate a `QMessageBox` rather than crash.
6. **Cron path unaffected** — `python lume-belt-live-demo-lcls.py` still runs end-to-end identically to before.
7. **No file growth** — after 3 cycles, `ls -la archive/$(date +%Y/%m/%d)/` shows new entries (the cron path's normal output) and matplotlib RSS doesn't climb (run `ps -o rss= -p <pid>` between cycles).

### Out of scope

- The LCLS-II demo (`lume-belt-live-demo.py`) — same pattern would apply but is a separate task.
- Persisting GUI state across launches.
- Replacing or extending `cron_job_setup.sh`.
- Percentage-style progress within a cycle — `run1_lcls()` exposes no progress hook, and the GUI uses an indeterminate busy bar + elapsed timer rather than fake percentages.
- Live PV monitoring — the SnapshotPVPanel intentionally only refreshes once per cycle, so values shown match what the simulation actually used.
- Capturing Impact-T subprocess output in the log panel — `evaluate_belt(..., verbose=False)` is kept; `redirect_stdout` only captures the Python-level `print()` calls in the orchestration, which is what the user asked for.

---

## Status

- [x] Repo on GitHub, clean push to `main`
- [x] CLAUDE.md with rules (no deletion, scope-to-repo, PVs read-only) and environment info
- [x] Persistent memories saved (3 feedback memories + index)
- [x] GUI plan approved (v3 — call `run1_lcls()` directly, busy progress bar, snapshot PVs)
- [x] User's algorithmic changes to `lume-belt-live-demo-lcls.py` landed (new `linac_phase_feedback_from_current` controller; `find_linac_info` helper; merit dict now also exposes `L1_lamb`/`L2_lamb`/`L1_V`/`L2_V`/`L1_phase_deg`/`L2_phase_deg` plus `BC1_energy`/`BC2_energy`)
- [x] Implement `gui_lume_belt_live_lcls.py` and `run_gui.sh`
  - Uses **PySide6** (the only Qt binding installed in `lume-eblt-dev`); plan said "PyQt5 / PySide6", same Qt API.
  - Curated PV list adjusted to match actual `pv_mapping/lcls_belt.csv` `Variable` column (`L1X_phase` not `HL_phase`, `DL1_energy` not `Initial_energy`, no `L3B_energy`; added `*_amplitude` rows).
  - Smoke-tested: `python -m py_compile` clean, module imports cleanly with `QT_QPA_PLATFORM=offscreen`, MainWindow constructs, file scanner returns 17 lattices and 34 particle files in the project.
- [ ] End-to-end verification per checklist above (requires EPICS network access)
