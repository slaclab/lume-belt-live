#!/bin/bash
# Launcher for the lume-belt-live LCLS GUI.
# Mirrors the EPICS env from cron_job_setup.sh and uses lume-eblt-dev.
# Note: the GUI uses PySide6 (the only Qt binding installed in lume-eblt-dev),
# not PyQt5 — same Qt API.

set -e

export EPICS_PVA_SERVER_PORT=5075
export EPICS_PVA_BROADCAST_PORT=5076
export EPICS_PVA_AUTO_ADDR_LIST=FALSE
export EPICS_PVA_ADDR_LIST="lcls-prod01:5068"
export EPICS_PVA_ADDR_LIST="${EPICS_PVA_ADDR_LIST} lcls-prod01:5063"
export EPICS_PVA_ADDR_LIST="${EPICS_PVA_ADDR_LIST} mcc-dmz mccas0.slac.stanford.edu"
export EPICS_CA_AUTO_ADDR_LIST=NO
export EPICS_CA_ADDR_LIST="lcls-prod01:5068 lcls-prod01:5063 mcc-dmz"
export EPICS_CA_REPEATER_PORT="5069"
export EPICS_CA_SERVER_PORT="5068"
export EPICS_TS_NTP_INET="134.79.48.11"
export EPICS_IOC_LOG_INET="134.79.151.21"

export LCLS_LATTICE=/sdf/group/ad/beamphysics/lcls-lattice

REPO_ROOT="/sdf/data/ad/ard/u/jytang/lume-belt-live"
cd "$REPO_ROOT"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate lume-eblt-dev

exec python "$REPO_ROOT/gui_lume_belt_live_lcls.py" "$@"
