# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

- **Conda environment:** `lume-eblt-dev` — activate before running any script or notebook in this repo.
- **Host:** s3df (SLAC); EPICS access requires the env vars set in `cron_job_setup.sh`.
- **Repo path:** `/sdf/data/ad/ard/u/jytang/lume-belt-live/`

## Rules

1. **Do not delete any code unless the user explicitly asks for it.** If you need to modify code, copy the original block (or file) first and edit the copy — preserve the original. This applies to `.py`, `.ipynb`, `.csv`, `belt.in`, and config files alike.
2. **Do not work outside this folder** (`/sdf/data/ad/ard/u/jytang/lume-belt-live/`). Reading is fine when needed (e.g. `$LCLS_LATTICE`, input beam HDF5s); deleting or modifying *any* file outside this folder is strictly prohibited. Always ask first.
3. **PVs are read-only.** Never write to EPICS PVs unless the user explicitly asks. Use only `epics.PV.get()`, `caget`, `MONITOR[k].get()`, etc. Never call `epics.PV.put()`, `caput`, `MONITOR[k].put()`, or any equivalent. This applies to all scripts, notebooks, GUI code, and ad-hoc Bash commands — writing to a live accelerator PV can affect beam operations.

## What this repo is

A live-monitoring service that runs the LUME-BELT longitudinal-dynamics simulation against real-time EPICS data from the LCLS / LCLS-II accelerator. It fetches PV values, translates them to BELT inputs, runs the simulation, archives results, and emits a composite dashboard PNG. Two parallel pipelines exist for the two machines.

## Running the service

The service is launched via a cron-managed wrapper that pins EPICS network env vars and activates the `lume-eblt-dev` conda environment:

```bash
./cron_job_setup.sh    # PID-locked; runs lume-belt-live-demo.py in a loop via ipython
```

`cron_job_setup.sh` exports `EPICS_PVA_*` / `EPICS_CA_*` and `LCLS_LATTICE=/sdf/group/ad/beamphysics/lcls-lattice`. EPICS connectivity is required — without it `get_snapshot()` raises immediately.

Manual run inside the conda env:

```bash
conda activate lume-eblt-dev
ipython lume-belt-live-demo.py        # LCLS-II pipeline, MODEL='LCLSII'
ipython lume-belt-live-demo-lcls.py   # LCLS Cu-linac pipeline
```

Both scripts loop forever (`while True: run1(); sleep(10)`); only `Exception` (not subclasses) breaks the loop.

The notebooks (`lume-belt-live-demo*.ipynb`) are the source-of-truth — the `.py` files are exported from them via `jupyter nbconvert` and are what cron actually executes.

## Pipeline architecture

Each iteration of `run1()`:

1. **Acquire** — `get_snapshot()` calls `epics.PV.get()` on every row of the active PV-mapping CSV. Saves raw `pvdata` + isotime to `snapshot/YYYY/MM/DD/<MODEL>-snapshot-<isotime>.h5`.
2. **Translate** — `get_settings()` reads the CSV, maps PV values to BELT simulation knobs (`BC1:angle`, `L1:gradient`, `L1:phase_deg`, `EBC1:energy_increment`, …). The energy/gradient math is hard-coded against linac section lengths (e.g. `L1_gradient = L1_amplitude / 16.603888`); changing the lattice means changing these constants.
3. **Simulate** — `belt.belt_impact.evaluate_belt(CONFIG0, settings, …)` runs BELT (Impact-T-backed). Template is `example/belt.in` (LCLS-II demo) — note `templete/belt.in` is a separate seed kept under version control.
4. **Score & plot** — `default_belt_merit` collects stats; `make_dashboard.make_dashboard` composes a PNG of the BELT main figure plus phase-space screens at file IDs 101 (Initial), 109 (After BC1), 113 (After BC2), 201 (Final).
5. **Archive** — outputs go under dated subdirs of `archive/`, `summary/`, `plot/` (created on demand by `convertToDatedFormat`).

`SETTINGS0["Impact_particles"]` points at the input beam HDF5 (e.g. an STCAV-derived `STCAV_data/particle-YYYY-MM-DD.h5`); the simulation starts from this distribution rather than running the injector. The two top-level constants `phase_shift` and `initial_energy` at the top of `lume-belt-live-demo.py` are physics tuning knobs and matter to results.

## PV mapping

CSVs in `pv_mapping/` are the contract between EPICS and BELT:

- `lclsii_belt.csv` — LCLS-II SC linac (used by `lume-belt-live-demo.py`)
- `lcls_belt.csv` — LCLS Cu linac (used by `lume-belt-live-demo-lcls.py`)
- `lclsii_belt_old.csv` — historical, kept for reference

Schema: `Variable, device_pv_name, pv_unit`. The translator looks up rows by the `Variable` column (e.g. `df.loc[df["Variable"] == "L1B_phase"]`), so renames must be coordinated with the mapping code in `get_settings()`. Edit via `make_pv_mapping*.ipynb` rather than the CSV directly.

## Output directories (all gitignored)

`archive/`, `scan/`, `snapshot/`, `summary/`, `plot/`, `test/`, `test_50pC/`, `XTCAV_data/`, `STCAV_data/`, `example*/`, `benchmark_with_Litrack/`, `impact_particles/` — the working tree contains ~70 GB of accumulated run data. Never `git add -A` blindly. `*.h5` is also gitignored.

A previously-tracked `pv_mapping/.ipynb_checkpoints/make_pv_mapping-checkpoint.ipynb` is still in history; `.gitignore` does not untrack it. Use `git rm --cached` if cleanup is wanted.

## Analysis notebooks

- `read_archive*.ipynb` — load archived BELT outputs for post-hoc analysis
- `compare_XTCAV_data.ipynb`, `get_initial_LPS_from_data.ipynb` — XTCAV-vs-sim comparison and initial LPS reconstruction
- `for_Nick_forward_and_backtracking_10102025.ipynb`, `read_archive_backtraking.ipynb` — backtracking studies
- `test_wakes.ipynb` — wakefield validation against the `belt.in` template

## Dependencies that must exist on the host

- `belt` (LUME-BELT) — `belt.run.BELT`, `belt.evaluate.default_belt_merit`, `belt.belt_impact.{run_belt,evaluate_belt}`, `belt.tools.NpEncoder`
- `pyepics` (`epics`), `pmd_beamphysics`, `h5py`, `toml`, Pillow
- Impact-T binary on PATH (BELT shells out to it)
- Conda env `lume-eblt-dev`; `$LCLS_LATTICE` and `$SCRATCH` (workdir for BELT runs)

## Git remote

`origin` is `git@github.com:slaclab/lume-belt-live.git` (SSH). HTTPS auth is not configured on s3df; do not `git remote set-url` back to HTTPS.
