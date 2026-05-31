"""
PyQt-style GUI for lume-belt-live-demo-lcls.py.

Wraps the existing pipeline without modifying it: loads
`lume-belt-live-demo-lcls.py` via importlib, calls `run1_lcls()` directly in a
worker thread, captures its `print()` output into a log panel, and shows the
PV snapshot used for the run plus the resulting stats and four phase-space
plots after the cycle completes.

See PLAN.md (Part 2) for design rationale.

Uses PySide6 (the only Qt binding available in lume-eblt-dev).
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import pathlib
import sys
import threading
import time
import traceback

import numpy as np
import psutil

REPO_ROOT = pathlib.Path("/sdf/data/ad/ard/u/jytang/lume-belt-live")

os.chdir(REPO_ROOT)

import matplotlib

matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg,
    NavigationToolbar2QT,
)

from PySide6 import QtCore, QtGui, QtWidgets


def _load_pipeline():
    spec = importlib.util.spec_from_file_location(
        "belt_live_lcls", REPO_ROOT / "lume-belt-live-demo-lcls.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pipeline = _load_pipeline()
from belt.run import BELT


CURATED_PVS: list[tuple[str, str]] = [
    ("Charge_inj", "nC"),
    ("Charge_BC1", "pC"),
    ("DL1_energy", "GeV"),
    ("BC1_energy", "GeV"),
    ("BC2_energy", "GeV"),
    ("L1X_phase", "deg"),
    ("L1X_amplitude", "MV"),
    ("L1B_phase", "deg"),
    ("L1B_amplitude", "MV"),
    ("L2B_phase", "deg"),
    ("L2B_amplitude", "MV"),
    ("L3B_phase", "deg"),
    ("L3B_amplitude", "MV"),
    ("BC1_current", "A"),
    ("BC2_current", "A"),
]


EXCLUDE_DIRS = {
    ".git",
    "archive",
    "scan",
    "snapshot",
    "summary",
    "plot",
    "__pycache__",
    ".ipynb_checkpoints",
}


def find_project_files(suffix: str) -> list[str]:
    """Recursive scan from REPO_ROOT, skipping huge data dirs. Returns project-relative paths."""
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for f in filenames:
            if f.endswith(suffix):
                rel = pathlib.Path(dirpath, f).relative_to(REPO_ROOT)
                matches.append(str(rel))
    return sorted(matches)


class _SignalStream:
    """File-like object that emits each write() as a Qt signal."""

    def __init__(self, sig):
        self.sig = sig

    def write(self, s):
        if s:
            self.sig.emit(s)
        return len(s) if s else 0

    def flush(self):
        pass


def _resolve_pv_value(pvdata: dict, variable: str):
    """Look up the live value of a Variable using pipeline.DF as the Variable->PV map."""
    rows = pipeline.DF[pipeline.DF["Variable"] == variable]
    if rows.empty:
        return None, None
    pv_name = rows["device_pv_name"].iloc[0]
    return pvdata.get(pv_name), pv_name


def _format_value(v, unit: str) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return f"{v} {unit}".strip()
    return f"{f:.4g} {unit}".strip()


def _extract_stats(merit: dict, belt_obj: BELT) -> dict:
    """Build the StatsPanel payload from the final merit dict + a final-distribution recompute."""
    out = {
        "BC1_current": merit.get("BC1_current"),
        "BC1_sigma_t": merit.get("BC1_sigma_t"),
        "L1_chirp": merit.get("L1_chirp"),
        "BC2_current": merit.get("BC2_current"),
        "BC2_sigma_t": merit.get("BC2_sigma_t"),
        "L2_chirp": merit.get("L2_chirp"),
    }
    try:
        pg = belt_obj.output.particle_distributions[201].to_particlegroup()
        pg = pg.where(pg.status == 1)
        if len(pg) > 0:
            sigma_t = float(pg["sigma_t"])
            bunch_length = sigma_t * (12.0 ** 0.5)
            current = float(pg.charge) / bunch_length if bunch_length > 0 else None
            z = np.asarray(pg.z)
            e = np.asarray(pg.energy)
            me = float(pg["mean_energy"])
            chirp = None
            if me != 0 and len(z) >= 2:
                chirp = float(np.polyfit(z, (e - me) / me, 1)[0])
            out["Final_current"] = current
            out["Final_sigma_t"] = sigma_t
            out["Final_chirp"] = chirp
    except Exception:
        out["Final_current"] = None
        out["Final_sigma_t"] = None
        out["Final_chirp"] = None
    return out


def _build_figures(belt_obj: BELT) -> list[tuple[int, str, "matplotlib.figure.Figure"]]:
    """Generate (file_id, label, Figure) tuples for the four phase-space panels.

    File-id mapping (per lume-belt-live-demo-lcls.py:297-300):
        101 = Initial, 113 = After BC1, 117 = After BC2, 201 = Final
    """
    panels = [(101, "Initial"), (113, "After BC1"), (117, "After BC2"), (201, "Final")]
    figs = []
    for fid, label in panels:
        try:
            fig = belt_obj.output.plot_distribution(fid, "t", "energy", bins=100)
            if fig is not None:
                fig.suptitle(f"{label} (id={fid})")
                figs.append((fid, label, fig))
        except Exception:
            pass
    return figs


def _build_overview(belt_obj: BELT):
    """The wide BELT main figure (energy/length along the linac). See make_dashboard.py L116."""
    try:
        return belt_obj.plot(return_figure=True)
    except Exception:
        return None


class SimulationWorker(QtCore.QThread):
    pv_snapshot = QtCore.Signal(dict, str)
    log_text = QtCore.Signal(str)
    stats_ready = QtCore.Signal(dict)
    plots_ready = QtCore.Signal(list)
    overview_ready = QtCore.Signal(object)
    cycle_started = QtCore.Signal()
    cycle_finished = QtCore.Signal()
    error = QtCore.Signal(str)

    def __init__(
        self,
        input_beam: str,
        input_lattice: str,
        force_normal_compression: bool = True,
        Amin: float = 0.02,
        sleep_seconds: int = 10,
    ):
        super().__init__()
        self.input_beam = input_beam
        self.input_lattice = input_lattice
        self.force_normal_compression = force_normal_compression
        self.Amin = Amin
        self.sleep_seconds = sleep_seconds
        self.stop_event = threading.Event()

    def request_stop(self):
        """Set the stop flag and kill any BELT/Impact-T child processes so the
        in-flight `run1_lcls()` aborts quickly. Without this, Stop only takes
        effect at cycle boundaries and the user sees 'Stopping…' for ~minutes.
        """
        self.stop_event.set()
        try:
            me = psutil.Process(os.getpid())
            children = me.children(recursive=True)
        except psutil.NoSuchProcess:
            return

        for c in children:
            try:
                c.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        gone, alive = psutil.wait_procs(children, timeout=2.0)
        for c in alive:
            try:
                c.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def _sleep_interruptible(self, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.stop_event.is_set():
                return
            time.sleep(0.1)

    def run(self):
        stream = _SignalStream(self.log_text)
        while not self.stop_event.is_set():
            try:
                pvdata, itime, _ = pipeline.get_snapshot(None)
                self.pv_snapshot.emit(pvdata, str(itime))
                self.cycle_started.emit()

                with contextlib.redirect_stdout(stream):
                    dat = pipeline.run1_lcls(
                        input_beam=self.input_beam,
                        input_lattice=self.input_lattice,
                        force_normal_compression=self.force_normal_compression,
                        Amin=self.Amin,
                    )

                merit = dat.get("outputs", {})
                archive_path = merit.get("archive")
                belt_obj = BELT.from_archive(archive_path) if archive_path else None

                if belt_obj is not None:
                    self.stats_ready.emit(_extract_stats(merit, belt_obj))
                    overview = _build_overview(belt_obj)
                    if overview is not None:
                        self.overview_ready.emit(overview)
                    self.plots_ready.emit(_build_figures(belt_obj))
                else:
                    self.error.emit("No archive returned from run1_lcls")

            except Exception:
                self.error.emit(traceback.format_exc())

            self.cycle_finished.emit()
            self._sleep_interruptible(self.sleep_seconds)


class SnapshotPVPanel(QtWidgets.QGroupBox):
    """Two-column grid showing the curated PVs from THIS run's snapshot."""

    def __init__(self, parent=None):
        super().__init__("PV snapshot (used for current run)", parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.itime_label = QtWidgets.QLabel("Snapshot: (idle)")
        f = self.itime_label.font()
        f.setItalic(True)
        self.itime_label.setFont(f)
        layout.addWidget(self.itime_label)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        layout.addLayout(grid)

        self.value_labels: dict[str, QtWidgets.QLabel] = {}
        for i, (var, unit) in enumerate(CURATED_PVS):
            row, col = i // 2, (i % 2) * 2
            name_lbl = QtWidgets.QLabel(f"{var} [{unit}]")
            val_lbl = QtWidgets.QLabel("—")
            mono = QtGui.QFont("monospace")
            mono.setStyleHint(QtGui.QFont.TypeWriter)
            val_lbl.setFont(mono)
            grid.addWidget(name_lbl, row, col)
            grid.addWidget(val_lbl, row, col + 1)
            self.value_labels[var] = val_lbl

        layout.addStretch(1)

    @QtCore.Slot(dict, str)
    def update_snapshot(self, pvdata: dict, itime: str):
        self.itime_label.setText(f"Snapshot: {itime}")
        for var, unit in CURATED_PVS:
            v, _ = _resolve_pv_value(pvdata, var)
            self.value_labels[var].setText(_format_value(v, unit))


class RunPanel(QtWidgets.QGroupBox):
    """Header status, busy progress bar, and live log of captured stdout."""

    clear_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__("Run", parent)
        layout = QtWidgets.QVBoxLayout(self)

        header_row = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("Idle")
        header_row.addWidget(self.status_label, 1)
        self.clear_btn = QtWidgets.QPushButton("Clear log")
        self.clear_btn.clicked.connect(self._on_clear_clicked)
        header_row.addWidget(self.clear_btn)
        layout.addLayout(header_row)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        mono = QtGui.QFont("monospace")
        mono.setStyleHint(QtGui.QFont.TypeWriter)
        self.log.setFont(mono)
        self.log.setMinimumHeight(120)
        layout.addWidget(self.log)

        self._cycle_start: float | None = None
        self._tick = QtCore.QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)

    def _on_clear_clicked(self):
        self.log.clear()
        self.clear_requested.emit()

    def _on_tick(self):
        if self._cycle_start is None:
            return
        secs = int(time.monotonic() - self._cycle_start)
        m, s = divmod(secs, 60)
        self.status_label.setText(f"Running… elapsed: {m}:{s:02d}")

    def set_busy(self):
        self.bar.setRange(0, 0)

    def set_done(self):
        self.bar.setRange(0, 100)
        self.bar.setValue(100)

    def set_idle(self):
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.status_label.setText("Idle")
        self._cycle_start = None
        self._tick.stop()

    @QtCore.Slot()
    def on_cycle_started(self):
        self._cycle_start = time.monotonic()
        self.status_label.setText("Running… elapsed: 0:00")
        self.set_busy()
        self._tick.start()

    @QtCore.Slot()
    def on_cycle_finished(self):
        self.set_done()
        self._tick.stop()
        self._cycle_start = None
        self.status_label.setText("Cycle complete")

    @QtCore.Slot(str)
    def on_log(self, chunk: str):
        cursor = self.log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(chunk)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()


class StatsPanel(QtWidgets.QGroupBox):
    """3 columns × 3 rows: After BC1 / After BC2 / Final."""

    COLUMNS = [
        ("After BC1", ("BC1_current", "BC1_sigma_t", "L1_chirp")),
        ("After BC2", ("BC2_current", "BC2_sigma_t", "L2_chirp")),
        ("Final", ("Final_current", "Final_sigma_t", "Final_chirp")),
    ]
    ROW_LABELS = [
        ("Peak current [A]", "current"),
        ("σ_t [fs]", "sigma_t"),
        ("Chirp [1/m]", "chirp"),
    ]

    def __init__(self, parent=None):
        super().__init__("Beam stats", parent)
        grid = QtWidgets.QGridLayout(self)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(4)

        # column headers
        for ci, (col, _) in enumerate(self.COLUMNS):
            lbl = QtWidgets.QLabel(col)
            f = lbl.font()
            f.setBold(True)
            lbl.setFont(f)
            grid.addWidget(lbl, 0, ci + 1, alignment=QtCore.Qt.AlignCenter)

        self.cells: dict[tuple[int, int], QtWidgets.QLabel] = {}
        mono = QtGui.QFont("monospace")
        mono.setStyleHint(QtGui.QFont.TypeWriter)
        for ri, (row_label, _) in enumerate(self.ROW_LABELS):
            grid.addWidget(QtWidgets.QLabel(row_label), ri + 1, 0)
            for ci in range(len(self.COLUMNS)):
                lbl = QtWidgets.QLabel("—")
                lbl.setFont(mono)
                grid.addWidget(lbl, ri + 1, ci + 1, alignment=QtCore.Qt.AlignCenter)
                self.cells[(ri, ci)] = lbl

    @QtCore.Slot(dict)
    def update_stats(self, payload: dict):
        for ci, (_, keys) in enumerate(self.COLUMNS):
            current_key, sigma_t_key, chirp_key = keys
            current = payload.get(current_key)
            sigma_t = payload.get(sigma_t_key)
            chirp = payload.get(chirp_key)

            self.cells[(0, ci)].setText(self._fmt(current, "{:.2f}"))
            self.cells[(1, ci)].setText(
                self._fmt(sigma_t * 1e15 if sigma_t is not None else None, "{:.1f}")
            )
            self.cells[(2, ci)].setText(self._fmt(chirp, "{:.3g}"))

    @staticmethod
    def _fmt(v, fmt: str) -> str:
        if v is None:
            return "—"
        try:
            return fmt.format(float(v))
        except (TypeError, ValueError):
            return str(v)


class FigureSlot(QtWidgets.QWidget):
    """A reusable widget that owns a (toolbar, canvas) pair and replaces them
    wholesale when a new Figure arrives. Replacing is more reliable than
    `canvas.figure = new_fig` for keeping the navigation toolbar's zoom/pan
    callbacks bound to the live axes.
    """

    def __init__(
        self,
        min_size: tuple[int, int] = (280, 280),
        max_size: tuple[int, int] | None = None,
        size_policy: tuple = (QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred),
        center: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._min_size = min_size
        self._max_size = max_size
        self._size_policy = size_policy
        self._center = center

        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

        from matplotlib.figure import Figure

        self.canvas: FigureCanvasQTAgg | None = None
        self.toolbar: NavigationToolbar2QT | None = None
        # Empty placeholder figure so the slot has something to draw at startup.
        self._install(Figure(figsize=(4, 4), tight_layout=True))

    def _install(self, fig):
        if self.canvas is not None:
            self._layout.removeWidget(self.canvas)
            self._layout.removeWidget(self.toolbar)
            self.canvas.deleteLater()
            self.toolbar.deleteLater()

        self.canvas = FigureCanvasQTAgg(fig)
        self.canvas.setMinimumSize(*self._min_size)
        if self._max_size is not None:
            self.canvas.setMaximumSize(*self._max_size)
        self.canvas.setSizePolicy(*self._size_policy)

        self.toolbar = NavigationToolbar2QT(self.canvas, self)

        self._layout.addWidget(self.toolbar)
        align = QtCore.Qt.AlignCenter if self._center else QtCore.Qt.Alignment()
        self._layout.addWidget(self.canvas, 0, align)
        self.canvas.draw_idle()

    def set_figure(self, new_fig):
        if new_fig is None:
            return
        old = self.canvas.figure if self.canvas is not None else None
        self._install(new_fig)
        try:
            plt.close(old)
        except Exception:
            pass


class BELTOverviewPanel(QtWidgets.QGroupBox):
    """Wide single-figure panel for `belt_object.plot()` (linac overview)."""

    def __init__(self, parent=None):
        super().__init__("BELT overview", parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.slot = FigureSlot(
            min_size=(600, 220),
            max_size=(1000, 320),
            size_policy=(
                QtWidgets.QSizePolicy.Preferred,
                QtWidgets.QSizePolicy.Preferred,
            ),
            center=True,
        )
        layout.addWidget(self.slot)

    @QtCore.Slot(object)
    def update_overview(self, new_fig):
        self.slot.set_figure(new_fig)


class PlotPanel(QtWidgets.QGroupBox):
    """2×2 grid of phase-space canvases (near-square)."""

    def __init__(self, parent=None):
        super().__init__("Longitudinal phase space (energy vs t)", parent)
        grid = QtWidgets.QGridLayout(self)
        self.slots: list[FigureSlot] = []
        self.labels: list[QtWidgets.QLabel] = []
        positions = [(0, 0), (0, 1), (1, 0), (1, 1)]
        titles = ["Initial (101)", "After BC1 (113)", "After BC2 (117)", "Final (201)"]
        for (r, c), title in zip(positions, titles):
            container = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(container)
            v.setContentsMargins(0, 0, 0, 0)
            label = QtWidgets.QLabel(title)
            label.setAlignment(QtCore.Qt.AlignCenter)
            v.addWidget(label)
            slot = FigureSlot(
                min_size=(280, 280),
                max_size=(480, 480),
                center=True,
            )
            v.addWidget(slot)
            grid.addWidget(container, r, c, alignment=QtCore.Qt.AlignCenter)
            self.slots.append(slot)
            self.labels.append(label)

    @QtCore.Slot(list)
    def update_plots(self, figs: list):
        # figs is a list of (file_id, label, Figure); align to our slots by index.
        for idx, slot in enumerate(self.slots):
            if idx >= len(figs):
                continue
            fid, label, new_fig = figs[idx]
            slot.set_figure(new_fig)
            self.labels[idx].setText(f"{label} (id={fid})")


class InputBar(QtWidgets.QGroupBox):
    """Searchable dropdowns for lattice/particles, feedback controls, Start/Stop."""

    start_clicked = QtCore.Signal(str, str, bool, float)
    stop_clicked = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__("Inputs", parent)
        layout = QtWidgets.QGridLayout(self)
        layout.setHorizontalSpacing(8)

        self.lattice_combo = self._make_searchable_combo()
        self.particles_combo = self._make_searchable_combo()
        self.refresh_btn = QtWidgets.QPushButton("Refresh files")
        self.start_btn = QtWidgets.QPushButton("Start")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)

        # run1_lcls() feedback controls
        self.force_normal_chk = QtWidgets.QCheckBox("force_normal_compression")
        self.force_normal_chk.setChecked(True)
        self.force_normal_chk.setToolTip(
            "If |A_sim| < Amin, push the beam toward the normal-compression branch "
            "(A_safe > 0) instead of trusting the current-error feedback."
        )
        self.amin_spin = QtWidgets.QDoubleSpinBox()
        self.amin_spin.setDecimals(3)
        self.amin_spin.setRange(0.001, 1.0)
        self.amin_spin.setSingleStep(0.005)
        self.amin_spin.setValue(0.02)
        self.amin_spin.setToolTip(
            "Threshold for near-full-compression detection: |A_sim| < Amin "
            "triggers the force_normal_compression branch."
        )

        layout.addWidget(QtWidgets.QLabel("Input lattice (*.in):"), 0, 0)
        layout.addWidget(self.lattice_combo, 0, 1, 1, 3)
        layout.addWidget(QtWidgets.QLabel("Input particles (*.h5):"), 1, 0)
        layout.addWidget(self.particles_combo, 1, 1, 1, 3)
        layout.addWidget(self.force_normal_chk, 2, 0, 1, 2)
        layout.addWidget(QtWidgets.QLabel("Amin:"), 2, 2)
        layout.addWidget(self.amin_spin, 2, 3)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.refresh_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        layout.addLayout(btn_row, 3, 0, 1, 4)

        self.refresh_btn.clicked.connect(self.refresh_files)
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self.stop_clicked)

        self.refresh_files()

    @staticmethod
    def _make_searchable_combo() -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        completer = QtWidgets.QCompleter(combo.model(), combo)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchContains)
        combo.setCompleter(completer)
        return combo

    def refresh_files(self):
        lattice = find_project_files(".in")
        particles = find_project_files(".h5")
        self._populate(self.lattice_combo, lattice, "example_lcls/belt.in")
        self._populate(
            self.particles_combo, particles, "example_lcls/from_Litrack_250pC.h5"
        )

    @staticmethod
    def _populate(combo: QtWidgets.QComboBox, items: list[str], default: str):
        prev = combo.currentText() or default
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        combo.blockSignals(False)
        if prev in items:
            combo.setCurrentText(prev)
        elif default in items:
            combo.setCurrentText(default)

    def _on_start(self):
        lattice = self.lattice_combo.currentText().strip()
        particles = self.particles_combo.currentText().strip()
        force_normal = self.force_normal_chk.isChecked()
        amin = self.amin_spin.value()
        self.start_clicked.emit(lattice, particles, force_normal, amin)

    def set_running(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.lattice_combo.setEnabled(not running)
        self.particles_combo.setEnabled(not running)
        self.refresh_btn.setEnabled(not running)
        self.force_normal_chk.setEnabled(not running)
        self.amin_spin.setEnabled(not running)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LUME-BELT live (LCLS)")

        # Scrollable content so small displays (NoMachine, projector, etc.)
        # can still reach every panel.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setCentralWidget(scroll)

        central = QtWidgets.QWidget()
        scroll.setWidget(central)
        outer = QtWidgets.QVBoxLayout(central)

        self.input_bar = InputBar()
        outer.addWidget(self.input_bar)

        upper_split = QtWidgets.QHBoxLayout()
        self.pv_panel = SnapshotPVPanel()
        self.run_panel = RunPanel()
        upper_split.addWidget(self.pv_panel, 1)
        upper_split.addWidget(self.run_panel, 1)
        outer.addLayout(upper_split, 0)

        self.stats_panel = StatsPanel()
        outer.addWidget(self.stats_panel)

        self.overview_panel = BELTOverviewPanel()
        outer.addWidget(self.overview_panel)

        self.plot_panel = PlotPanel()
        outer.addWidget(self.plot_panel, 1)

        self.input_bar.start_clicked.connect(self._start)
        self.input_bar.stop_clicked.connect(self._stop)

        self.worker: SimulationWorker | None = None

        # Start at a reasonable size but allow shrinking; available screen
        # geometry caps it so the window itself never spawns off-screen.
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(1080, screen.width() - 80), min(900, screen.height() - 80))

    def _start(self, lattice: str, particles: str, force_normal: bool, amin: float):
        if not lattice or not particles:
            QtWidgets.QMessageBox.warning(
                self, "Missing input", "Both lattice and particles paths are required."
            )
            return
        for label, p in [("lattice", lattice), ("particles", particles)]:
            full = (REPO_ROOT / p).resolve()
            if not full.exists():
                QtWidgets.QMessageBox.warning(
                    self,
                    "File not found",
                    f"{label} path does not exist:\n{full}",
                )
                return

        self.run_panel.set_busy()
        self.input_bar.set_running(True)

        self.worker = SimulationWorker(
            input_beam=particles,
            input_lattice=lattice,
            force_normal_compression=force_normal,
            Amin=amin,
        )
        self.worker.pv_snapshot.connect(self.pv_panel.update_snapshot)
        self.worker.log_text.connect(self.run_panel.on_log)
        self.worker.cycle_started.connect(self.run_panel.on_cycle_started)
        self.worker.cycle_finished.connect(self.run_panel.on_cycle_finished)
        self.worker.stats_ready.connect(self.stats_panel.update_stats)
        self.worker.overview_ready.connect(self.overview_panel.update_overview)
        self.worker.plots_ready.connect(self.plot_panel.update_plots)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._on_thread_finished)
        self.worker.start()

    def _stop(self):
        if self.worker is None:
            return
        self.worker.request_stop()
        self.input_bar.stop_btn.setText("Stopping…")
        self.input_bar.stop_btn.setEnabled(False)

    @QtCore.Slot(str)
    def _on_error(self, msg: str):
        # Don't block the worker — show non-modal warning so the loop continues.
        self.run_panel.on_log(f"\n[ERROR]\n{msg}\n")

    @QtCore.Slot()
    def _on_thread_finished(self):
        self.input_bar.set_running(False)
        self.input_bar.stop_btn.setText("Stop")
        self.run_panel.set_idle()
        self.worker = None

    def closeEvent(self, event: QtGui.QCloseEvent):
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(2000)
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
