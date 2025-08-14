import os
import sys
import random
import re
from pathlib import Path
import traceback

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer, QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar,
    QGroupBox, QLineEdit, QFormLayout, QMessageBox, QComboBox, QSpinBox, QCheckBox, QSlider,
    QTabWidget, QToolButton, QAbstractItemView, QDialog, QDialogButtonBox
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

# Try to load QDarkStyle if available
_HAS_QDARK = False
try:
    import qdarkstyle
    _HAS_QDARK = True
except Exception:
    _HAS_QDARK = False

# ---------------- util ----------------
AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aiff', '.aif'}
GENRES = [
    "pop", "rock", "r&b", "house", "trap", "reggaeton",
    "afrobeat", "brasil funk", "funk", "soul", "jazz",
]

def _is_audio_path(p: Path) -> bool:
    return p.suffix.lower() in AUDIO_EXTS

def _slug(txt: str, maxlen: int = 40) -> str:
    out = re.sub(r"[^a-zA-Z0-9_-]+", "-", txt).strip("-")
    if len(out) > maxlen:
        out = out[:maxlen].rstrip("-")
    return out or "x"

# ---------------- config ----------------
DEFAULT_CFG = {
    "bpm": 100.0,
    "attack_ms": 1.0,
    "release_ms": 0.5,
    "floor_db": -40.0,
    "mode": "hilbert",             # hilbert | rms
    "combine_mode": "max",         # max | mean | geom_mean | product | sum_limited | weighted
    "weights": "",                 # comma-separated, optional
    "out_path": str(Path.cwd() / "salida.wav"),
    "auto_name": True,
    "match_lufs": False,
}

class AppConfig:
    ORG = "CopyEnvelope"
    APP = "CopyEnvelope2"

    def __init__(self):
        self.settings = QSettings(self.ORG, self.APP)

    def load(self) -> dict:
        cfg = dict(DEFAULT_CFG)
        for k, v in DEFAULT_CFG.items():
            val = self.settings.value(k, v)
            cfg[k] = val
        # Cast types
        cfg["bpm"] = float(cfg["bpm"]) 
        cfg["attack_ms"] = float(cfg["attack_ms"]) 
        cfg["release_ms"] = float(cfg["release_ms"]) 
        cfg["floor_db"] = float(cfg["floor_db"]) 
        cfg["auto_name"] = str(cfg["auto_name"]).lower() in ("1","true","yes","on")
        cfg["match_lufs"] = str(cfg["match_lufs"]).lower() in ("1","true","yes","on")
        cfg["weights"] = str(cfg.get("weights") or "")
        cfg["mode"] = str(cfg["mode"]).lower()
        cfg["combine_mode"] = str(cfg["combine_mode"]).lower()
        cfg["out_path"] = str(cfg["out_path"]) if cfg["out_path"] else str(Path.cwd()/"salida.wav")
        return cfg

    def save(self, cfg: dict):
        for k, v in cfg.items():
            self.settings.setValue(k, v)

    def reset(self):
        for k, v in DEFAULT_CFG.items():
            self.settings.setValue(k, v)

class ConfigDialog(QDialog):
    def __init__(self, parent: QWidget, cfg: dict):
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.setModal(True)
        self.result_cfg = dict(cfg)

        v = QVBoxLayout(self)

        g = QGroupBox("Configuración global")
        form = QFormLayout(g)

        self.ed_bpm = QLineEdit(str(cfg.get("bpm", DEFAULT_CFG["bpm"])))
        self.ed_attack = QLineEdit(str(cfg.get("attack_ms", DEFAULT_CFG["attack_ms"])))
        self.ed_release = QLineEdit(str(cfg.get("release_ms", DEFAULT_CFG["release_ms"])))
        self.ed_floor_db = QLineEdit(str(cfg.get("floor_db", DEFAULT_CFG["floor_db"])))

        self.cb_mode = QComboBox(); self.cb_mode.addItems(["hilbert","rms"])
        self.cb_mode.setCurrentText(cfg.get("mode", DEFAULT_CFG["mode"]))

        self.cb_combine = QComboBox(); self.cb_combine.addItems(["max","mean","geom_mean","product","sum_limited","weighted"])
        self.cb_combine.setCurrentText(cfg.get("combine_mode", DEFAULT_CFG["combine_mode"]))

        self.ed_weights = QLineEdit(cfg.get("weights", DEFAULT_CFG["weights"]))

        self.ed_out = QLineEdit(cfg.get("out_path", DEFAULT_CFG["out_path"]))
        self.btn_browse = QToolButton(); self.btn_browse.setText("…")
        self.btn_open_dir = QPushButton("Abrir carpeta")
        out_row = QHBoxLayout(); out_row.addWidget(self.ed_out, 1); out_row.addWidget(self.btn_browse); out_row.addWidget(self.btn_open_dir)

        self.chk_auto = QCheckBox("Auto-nombrar salida (destino + moldes)")
        self.chk_auto.setChecked(bool(cfg.get("auto_name", True)))

        self.chk_lufs = QCheckBox("Match LUFS (si disponible)")
        self.chk_lufs.setChecked(bool(cfg.get("match_lufs", False)))

        form.addRow("BPM:", self.ed_bpm)
        form.addRow("Attack ms:", self.ed_attack)
        form.addRow("Release ms:", self.ed_release)
        form.addRow("Floor dB:", self.ed_floor_db)
        form.addRow("Envelope mode:", self.cb_mode)
        form.addRow("Combine mode:", self.cb_combine)
        form.addRow("Weights (coma):", self.ed_weights)
        form.addRow("Archivo de salida:", out_row)
        form.addRow("", self.chk_auto)
        form.addRow("", self.chk_lufs)

        v.addWidget(g)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.btn_reset = QPushButton("Restablecer a default")
        h = QHBoxLayout(); h.addWidget(self.btn_reset); h.addStretch(1); h.addWidget(btns)
        v.addLayout(h)

        # Connections
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_browse.clicked.connect(self._browse)
        self.btn_open_dir.clicked.connect(self._open_dir)

    def _on_reset(self):
        # Reset UI to defaults (not writing to disk yet)
        self.ed_bpm.setText(str(DEFAULT_CFG["bpm"]))
        self.ed_attack.setText(str(DEFAULT_CFG["attack_ms"]))
        self.ed_release.setText(str(DEFAULT_CFG["release_ms"]))
        self.ed_floor_db.setText(str(DEFAULT_CFG["floor_db"]))
        self.cb_mode.setCurrentText(DEFAULT_CFG["mode"]) 
        self.cb_combine.setCurrentText(DEFAULT_CFG["combine_mode"]) 
        self.ed_weights.setText(DEFAULT_CFG["weights"]) 
        self.ed_out.setText(DEFAULT_CFG["out_path"]) 
        self.chk_auto.setChecked(DEFAULT_CFG["auto_name"]) 
        self.chk_lufs.setChecked(DEFAULT_CFG["match_lufs"]) 

    def _browse(self):
        start = self.ed_out.text().strip() or str(Path.cwd())
        start_dir = str(Path(start).parent if start else Path.cwd())
        fname, _ = QFileDialog.getSaveFileName(self, "Elegir archivo de salida", start_dir,
            "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif);;Todos (*.*)")
        if fname:
            self.ed_out.setText(fname)

    def _open_dir(self):
        p = Path(self.ed_out.text().strip() or Path.cwd() / "salida.wav")
        folder = p if p.is_dir() else p.parent
        try: folder.mkdir(parents=True, exist_ok=True)
        except Exception: pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _on_accept(self):
        # Validate
        try:
            bpm = float(self.ed_bpm.text() or DEFAULT_CFG["bpm"]) 
            attack = float(self.ed_attack.text() or DEFAULT_CFG["attack_ms"]) 
            release = float(self.ed_release.text() or DEFAULT_CFG["release_ms"]) 
            floor_db = float(self.ed_floor_db.text() or DEFAULT_CFG["floor_db"]) 
        except Exception:
            QMessageBox.warning(self, "Valores inválidos", "Revisa BPM / Attack / Release / Floor dB.")
            return
        mode = self.cb_mode.currentText().strip().lower() or DEFAULT_CFG["mode"]
        combine = self.cb_combine.currentText().strip().lower() or DEFAULT_CFG["combine_mode"]
        wtxt = self.ed_weights.text().strip()
        # We keep weights as raw string; they will be parse-checked in run
        out_path = self.ed_out.text().strip()
        auto = self.chk_auto.isChecked()
        if not auto and not out_path:
            QMessageBox.warning(self, "Salida inválida", "Especifica el archivo de salida (ej: salida.wav).")
            return
        self.result_cfg = {
            "bpm": bpm,
            "attack_ms": attack,
            "release_ms": release,
            "floor_db": floor_db,
            "mode": mode,
            "combine_mode": combine,
            "weights": wtxt,
            "out_path": out_path or DEFAULT_CFG["out_path"],
            "auto_name": auto,
            "match_lufs": self.chk_lufs.isChecked(),
        }
        self.accept()

# ---------------- Minimal audio engine (placeholder hooks) ----------------
# NOTE: In tu repo original, la lógica DSP está en este archivo o en engine.py.
# Aquí asumimos que existe una función apply_envelopes(dest_path, mold_paths, out_path, cfg, progress_cb, log_cb)
# Reemplaza este bloque por tu import real si ya lo tienes separado.

import numpy as np
import soundfile as sf
import time, shutil

def ensure_genre_dirs():
    base = Path.cwd() / "genres"
    base.mkdir(exist_ok=True)
    for g in GENRES:
        (base / g).mkdir(parents=True, exist_ok=True)

# Dummy DSP example; sustituye por tu engine real

def apply_envelopes(dest_path, mold_paths, out_path, cfg, progress_cb, log_cb):
    # Simula trabajo para demo
    total = max(1, len(mold_paths))
    for i, p in enumerate(mold_paths, start=1):
        time.sleep(0.1)
        progress_cb(int(i * 80 / total))
        log_cb(f"Molde: {Path(p).name}")
    # Copia destino a salida como demo
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dest_path, out)
    progress_cb(100)
    log_cb(f"Listo. (Salida: {out})")

# ---------------- Widgets auxiliares ----------------
class ReadOnlyList(QListWidget):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.setAlternatingRowColors(True)

    def paths(self):
        return [self.item(i).text() for i in range(self.count())]

class BasicMoldList(ReadOnlyList):
    # Acepta drops de archivos o carpetas
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                p = Path(u.toLocalFile())
                if p.is_dir() or _is_audio_path(p):
                    event.acceptProposedAction(); return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                p = Path(u.toLocalFile())
                if p.is_dir():
                    for q in p.rglob("*"):
                        if q.is_file() and _is_audio_path(q):
                            self.addItem(str(q))
                elif _is_audio_path(p):
                    self.addItem(str(p))
            event.acceptProposedAction(); return
        super().dropEvent(event)

# ---------------- Worker ----------------
class Worker(QThread):
    progressed = Signal(int)
    logged = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, dest_path, mold_paths, out_path, cfg):
        super().__init__()
        self.dest_path = dest_path
        self.mold_paths = mold_paths
        self.out_path = out_path
        self.cfg = cfg

    def run(self):
        try:
            def _p(v): self.progressed.emit(int(v))
            def _l(msg): self.logged.emit(str(msg))
            _l("Iniciando procesamiento…")
            apply_envelopes(self.dest_path, self.mold_paths, self.out_path, self.cfg, _p, _l)
            self.finished_ok.emit(self.out_path)
        except Exception:
            tb = traceback.format_exc()
            self.failed.emit(tb)

# ---------------- Ventana principal ----------------
class MainWin(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Copy Envelope 2")
        self.setAcceptDrops(True)  # solo actuará en a_Género

        ensure_genre_dirs()
        self.cfg_mgr = AppConfig()
        self.cfg = self.cfg_mgr.load()

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.9)
        self._duration = 0
        self._autoplay_pending = False

        root = QVBoxLayout(self)

        # ---------- Tabs (Género / Básico) ----------
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # --- Pestaña Género ---
        self.tab_genre = QWidget()
        tab_genre_layout = QVBoxLayout(self.tab_genre)

        g_gen = QGroupBox("Fuente de moldes por género")
        lg = QVBoxLayout(g_gen)

        row = QHBoxLayout()
        row.addWidget(QLabel("Género:"))
        self.cb_genre = QComboBox(); self.cb_genre.addItems(GENRES)
        row.addWidget(self.cb_genre, 1)
        row.addWidget(QLabel("Cantidad:"))
        self.spn_count = QSpinBox(); self.spn_count.setRange(1, 999); self.spn_count.setValue(4)
        row.addWidget(self.spn_count)
        self.btn_refresh_genre = QPushButton("Refrescar lista")
        row.addWidget(self.btn_refresh_genre)
        lg.addLayout(row)

        self.mold_list = ReadOnlyList(); lg.addWidget(self.mold_list)
        tab_genre_layout.addWidget(g_gen)

        # --- Pestaña Básico ---
        self.tab_basic = QWidget()
        tab_basic_layout = QVBoxLayout(self.tab_basic)

        g_basic = QGroupBox("Moldes (arrastra archivos o suelta una carpeta)")
        lb = QVBoxLayout(g_basic)
        bar = QHBoxLayout()
        self.btn_basic_add_files = QPushButton("Añadir archivos…")
        self.btn_basic_add_dir = QPushButton("Añadir carpeta…")
        self.btn_basic_clear = QPushButton("Limpiar")
        bar.addWidget(self.btn_basic_add_files)
        bar.addWidget(self.btn_basic_add_dir)
        bar.addWidget(self.btn_basic_clear)
        lb.addLayout(bar)
        self.basic_list = BasicMoldList(); lb.addWidget(self.basic_list)
        tab_basic_layout.addWidget(g_basic)

        # Añadir pestañas (¡sin Configuración!)
        self.tabs.addTab(self.tab_genre, "Género")
        self.tabs.addTab(self.tab_basic, "Básico")

        # --- Pre-escucha (compartida) ---
        g_player = QGroupBox("Pre-escucha")
        lp = QVBoxLayout(g_player)
        ctl = QHBoxLayout()
        self.btn_play = QPushButton("▶︎"); ctl.addWidget(self.btn_play)
        self.btn_pause = QPushButton("⏸"); ctl.addWidget(self.btn_pause)
        self.btn_stop = QPushButton("⏹"); ctl.addWidget(self.btn_stop)
        self.chk_autoplay = QCheckBox("Auto reproducir al seleccionar"); self.chk_autoplay.setChecked(True)
        ctl.addWidget(self.chk_autoplay, 1)
        lp.addLayout(ctl)
        self.sld_pos = QSlider(Qt.Horizontal); self.sld_pos.setRange(0, 0); lp.addWidget(self.sld_pos)
        self.lbl_time = QLabel("00:00 / 00:00"); lp.addWidget(self.lbl_time)
        root.addWidget(g_player)

        # --- Destino (compartido) ---
        g_dest = QGroupBox("Destino")
        ld = QVBoxLayout(g_dest)
        rowd = QHBoxLayout()
        self.dest_list = ReadOnlyList(); rowd.addWidget(self.dest_list, 1)
        col = QVBoxLayout();
        btn_dest = QPushButton("Elegir destino…"); col.addWidget(btn_dest)
        btn_clear_d = QPushButton("Limpiar"); col.addWidget(btn_clear_d)
        rowd.addLayout(col)
        ld.addLayout(rowd)
        root.addWidget(g_dest)

        # --- Logs y progreso ---
        self.progress = QProgressBar(); root.addWidget(self.progress)
        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumBlockCount(5000); root.addWidget(self.logs)

        # --- Fila de acciones (Configuración + Procesar) ---
        hb = QHBoxLayout()
        self.btn_config = QPushButton("⚙ Configuración…")
        self.btn_run = QPushButton("Procesar")
        hb.addWidget(self.btn_config)
        hb.addStretch(1)
        hb.addWidget(self.btn_run)
        root.addLayout(hb)

        # Copyright
        self.copyright = QLabel("© 2025 Gabriel Golker")
        self.copyright.setAlignment(Qt.AlignCenter)
        root.addWidget(self.copyright, alignment=Qt.AlignCenter)

        # Señales (Género)
        self.cb_genre.currentIndexChanged.connect(self.on_genre_changed)
        self.spn_count.valueChanged.connect(self.on_genre_changed)
        self.btn_refresh_genre.clicked.connect(self.on_genre_changed)

        # Señales (Básico)
        self.btn_basic_add_files.clicked.connect(self.add_basic_files)
        self.btn_basic_add_dir.clicked.connect(self.add_basic_dir)
        self.btn_basic_clear.clicked.connect(self.basic_list.clear)

        # Señales (Destino / general)
        btn_dest.clicked.connect(self.pick_dest_file)
        btn_clear_d.clicked.connect(self.dest_list.clear)
        self.btn_run.clicked.connect(self.on_run)
        self.btn_config.clicked.connect(self.open_config_dialog)

        # Reproductor
        self.btn_play.clicked.connect(self.on_play)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_stop.clicked.connect(self.on_stop)
        self._connect_player_signals()

        # Selección para pre-escucha
        self.mold_list.currentItemChanged.connect(lambda curr, prev: self.on_any_mold_item_changed(curr))
        self.basic_list.currentItemChanged.connect(lambda curr, prev: self.on_any_mold_item_changed(curr))
        self.on_genre_changed()
        self.worker = None

    # --- Drag & drop a nivel ventana (solo a_Género) ---
    def dragEnterEvent(self, event):
        if self.tabs.currentWidget() is self.tab_genre and event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = Path(url.toLocalFile())
                if p.is_dir() or _is_audio_path(p):
                    event.acceptProposedAction(); return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        if self.tabs.currentWidget() is self.tab_genre and event.mimeData().hasUrls():
            any_added = False
            for url in event.mimeData().urls():
                p = Path(url.toLocalFile())
                if p.is_dir():
                    candidates = [q for q in (Path.cwd()/"genres"/self.cb_genre.currentText()).rglob("*") if q.is_file() and _is_audio_path(q)]
                    self.mold_list.clear()
                    for q in sorted(candidates)[: self.spn_count.value()]:
                        self.mold_list.addItem(str(q))
                        any_added = True
                elif _is_audio_path(p):
                    self.mold_list.addItem(str(p)); any_added = True
            if any_added:
                event.acceptProposedAction(); return
        super().dropEvent(event)

    # --- Género helpers ---
    def on_genre_changed(self):
        genre = self.cb_genre.currentText()
        base = Path.cwd() / "genres" / genre
        candidates = [q for q in base.rglob("*") if q.is_file() and _is_audio_path(q)]
        random.shuffle(candidates)
        take = max(1, min(self.spn_count.value(), len(candidates)))
        self.mold_list.clear()
        for q in sorted(candidates)[: take]:
            self.mold_list.addItem(str(q))

    # --- Básico helpers ---
    def add_basic_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Añadir archivos", str(Path.cwd()),
            "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif)")
        for f in files:
            self.basic_list.addItem(f)

    def add_basic_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Añadir carpeta", str(Path.cwd()))
        if d:
            for q in Path(d).rglob("*"):
                if q.is_file() and _is_audio_path(q):
                    self.basic_list.addItem(str(q))

    # --- Destino ---
    def pick_dest_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Elegir destino", str(Path.cwd()),
                                          "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif)")
        if f:
            self.dest_list.clear(); self.dest_list.addItem(QListWidgetItem(f))

    # --- Player ---
    def _connect_player_signals(self):
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)

    def _on_pos(self, pos):
        self.sld_pos.blockSignals(True)
        self.sld_pos.setValue(pos)
        self.sld_pos.blockSignals(False)
        self._update_time_label(pos, self._duration)

    def _on_dur(self, dur):
        self._duration = dur
        self.sld_pos.setRange(0, dur if dur is not None else 0)
        self._update_time_label(self.player.position(), dur)

    def _update_time_label(self, pos, dur):
        def fmt(ms):
            s = max(0, int(ms/1000.0)); m = s//60; s = s%60; return f"{m:02d}:{s:02d}"
        self.lbl_time.setText(f"{fmt(pos)} / {fmt(dur or 0)}")

    def on_play(self):
        sel = self._current_selected_item()
        if not sel: return
        f = sel.text()
        self.player.setSource(QUrl.fromLocalFile(f))
        self.player.play()

    def on_pause(self):
        self.player.pause()

    def on_stop(self):
        self.player.stop()

    def on_any_mold_item_changed(self, curr):
        if not curr: return
        if self.chk_autoplay.isChecked():
            self._autoplay_pending = True
            QTimer.singleShot(50, lambda: self.on_play())

    def _current_selected_item(self):
        w = self.tabs.currentWidget()
        if w is self.tab_basic:
            return self.basic_list.currentItem()
        else:
            return self.mold_list.currentItem()

    # --- Run ---
    def _active_mold_paths(self):
        current = self.tabs.currentWidget()
        if current is self.tab_basic:
            return self.basic_list.paths()
        return self.mold_list.paths()

    def open_config_dialog(self):
        dlg = ConfigDialog(self, self.cfg)
        if dlg.exec() == QDialog.Accepted:
            self.cfg = dlg.result_cfg
            self.cfg_mgr.save(self.cfg)
            QMessageBox.information(self, "Configuración", "Guardada.")

    def on_run(self):
        molds = self._active_mold_paths()
        if not molds:
            QMessageBox.warning(self, "Faltan moldes", "No hay moldes seleccionados.")
            return
        dests = [self.dest_list.item(i).text() for i in range(self.dest_list.count())]
        if not dests:
            QMessageBox.warning(self, "Falta destino", "Elige o arrastra el archivo destino.")
            return
        dest = dests[0]

        # --- salida ---
        out = str(self.cfg.get("out_path") or DEFAULT_CFG["out_path"]).strip()
        ext = Path(out).suffix if out else ".wav"
        if self.cfg.get("auto_name", True):
            out_dir = Path(out).parent if out else Path(dest).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            dest_base = _slug(Path(dest).stem, 20)
            molds_base = _slug("+".join(Path(m).stem for m in molds[:3]), 30)
            if len(molds) > 3:
                molds_base += f"+{len(molds)-3}"
            out = str(out_dir / f"{dest_base}__{molds_base}{ext}")
        else:
            if not out:
                QMessageBox.warning(self, "Salida inválida", "Especifica el archivo de salida (ej: salida.wav).")
                return

        # --- cfg ---
        weights = None
        wtxt = (self.cfg.get("weights") or "").strip()
        if wtxt:
            try:
                weights = [float(x) for x in wtxt.split(",")]
            except Exception:
                QMessageBox.warning(self, "Weights inválidos", "Usa números separados por coma, ej: 1,0.8,1.2")
                return
            if weights and len(weights) not in (1, len(molds)):
                QMessageBox.warning(self, "Weights inválidos", "Debe haber 1 peso o uno por cada molde.")
                return

        cfg = {
            "bpm": float(self.cfg.get("bpm", DEFAULT_CFG["bpm"])),
            "attack_ms": float(self.cfg.get("attack_ms", DEFAULT_CFG["attack_ms"])),
            "release_ms": float(self.cfg.get("release_ms", DEFAULT_CFG["release_ms"])),
            "floor_db": float(self.cfg.get("floor_db", DEFAULT_CFG["floor_db"])),
            "mode": str(self.cfg.get("mode", DEFAULT_CFG["mode"])) .strip().lower(),
            "combine_mode": str(self.cfg.get("combine_mode", DEFAULT_CFG["combine_mode"])) .strip().lower(),
            "weights": weights,
            "match_lufs": bool(self.cfg.get("match_lufs", False)),
        }

        # lanzar worker
        self.progress.setValue(0); self.logs.clear()
        self.worker = Worker(dest, molds, out, cfg)
        self.worker.progressed.connect(self.progress.setValue)
        self.worker.logged.connect(self.append_log)
        self.worker.finished_ok.connect(self.on_done)
        self.worker.failed.connect(self.on_fail)
        self.worker.start()

    def append_log(self, text):
        self.logs.appendPlainText(text)

    def on_done(self, out_path):
        self.append_log(f"OK: {out_path}")
        QMessageBox.information(self, "Listo", f"Se generó: {out_path}")
        try:
            folder = Path(out_path).parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))
        except Exception:
            pass

    def on_fail(self, tb):
        self.append_log(tb)
        QMessageBox.critical(self, "Error", "Ocurrió un error. Revisa los logs.")

# ---------------- main ----------------
def main():
    app = QApplication(sys.argv)
    if _HAS_QDARK:
        app.setStyleSheet(qdarkstyle.load_stylesheet_pyside6())
    win = MainWin()
    win.resize(980, 840)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()



