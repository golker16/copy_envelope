import os
import sys
import random
import re
from pathlib import Path
import traceback

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar,
    QGroupBox, QLineEdit, QFormLayout, QMessageBox, QComboBox, QSpinBox, QCheckBox, QSlider,
    QTabWidget, QToolButton, QAbstractItemView
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

# Import processing engine
try:
    from engine import apply_envelopes
except Exception as e:
    def apply_envelopes(dest_path, mold_paths, out_path, cfg, progress_cb, log_cb):
        log_cb("[WARN] engine.apply_envelopes no encontrado, se copia el destino como salida dummy.")
        import shutil, time
        total = max(1, len(mold_paths))
        for i, p in enumerate(mold_paths, start=1):
            time.sleep(0.1)
            progress_cb(int(i * 80 / total))
            log_cb(f"Molde dummy: {Path(p).name}")
        shutil.copy2(dest_path, out_path)
        progress_cb(100)
        log_cb(f"Listo. (Salida: {out_path})")

AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aiff', '.aif'}
GENRES = [
    "pop", "rock", "r&b", "house", "trap", "reggaeton",
    "afrobeat", "brasil funk", "funk", "soul", "jazz",
]

# ---------------- utilidades de ruta ----------------
def _base_dir_for_data() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent

def app_genres_dir() -> Path:
    return _base_dir_for_data() / "genres"

def ensure_genre_dirs() -> None:
    gdir = app_genres_dir()
    gdir.mkdir(parents=True, exist_ok=True)
    for g in GENRES:
        (gdir / g).mkdir(parents=True, exist_ok=True)

def _slug(s: str, max_len: int = 30) -> str:
    s = s.lower().strip()
    s = ''.join(ch if (ch.isalnum() or ch in ['_', '-', '+']) else '-' for ch in s)
    s = re.sub('-{2,}', '-', s)
    return s[:max_len].strip('-_')

def _is_audio_file(p: Path) -> bool:
    try:
        return p.is_file() and p.suffix.lower() in AUDIO_EXTS
    except Exception:
        return False

def _collect_audios_from_dir(folder: Path, recursive: bool = True):
    files = []
    try:
        if recursive:
            for ext in AUDIO_EXTS:
                files.extend(sorted(folder.rglob(f"*{ext}")))
        else:
            for child in sorted(folder.iterdir()):
                if _is_audio_file(child):
                    files.append(child)
    except Exception:
        pass
    seen = set(); uniq = []
    for f in files:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq

# ---------------- Widgets ----------------
class ReadOnlyList(QListWidget):
    """Solo muestra rutas (moldes por género)."""
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(False)
        self.setMinimumHeight(120)
        self.setAlternatingRowColors(True)

    def set_paths(self, paths):
        self.clear()
        for p in paths:
            self.addItem(QListWidgetItem(str(p)))

    def paths(self):
        return [self.item(i).text() for i in range(self.count())]

class BasicMoldList(QListWidget):
    """Lista para moldes ad-hoc: acepta archivos y carpetas (recursivo)."""
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDropIndicatorShown(True)

    def _urls_to_paths(self, urls):
        paths = []
        for url in urls:
            p = Path(url.toLocalFile())
            if not p.exists():
                continue
            if p.is_dir():
                paths.extend(_collect_audios_from_dir(p, recursive=True))
            elif _is_audio_file(p):
                paths.append(p)
        return paths

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self._urls_to_paths(event.mimeData().urls()):
            event.setDropAction(Qt.CopyAction); event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            new_paths = self._urls_to_paths(event.mimeData().urls())
            if new_paths:
                self.add_files(new_paths)
                event.setDropAction(Qt.CopyAction); event.accept(); return
        event.ignore()

    def add_files(self, paths):
        existing = set(self.paths()); added = 0
        for p in paths:
            sp = str(p)
            if sp not in existing:
                self.addItem(QListWidgetItem(sp))
                existing.add(sp); added += 1
        return added

    def paths(self):
        return [self.item(i).text() for i in range(self.count())]

class DestDropList(QListWidget):
    """Lista que acepta arrastrar y soltar archivo destino."""
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(60)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDropIndicatorShown(True)

    def _urls_have_valid_audio(self, urls):
        for url in urls:
            p = Path(url.toLocalFile())
            if _is_audio_file(p):
                return True
        return False

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self._urls_have_valid_audio(event.mimeData().urls()):
            event.setDropAction(Qt.CopyAction); event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = Path(url.toLocalFile())
                if _is_audio_file(p):
                    self.clear()
                    self.addItem(QListWidgetItem(str(p)))
                    event.setDropAction(Qt.CopyAction); event.accept(); return
        event.ignore()

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
        except Exception as e:
            tb = traceback.format_exc()
            self.failed.emit(tb)

# ---------------- Ventana principal ----------------
class MainWin(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Copy Envelope 2")
        self.setAcceptDrops(True)  # solo actuará en a_Género (ver dragEnterEvent)

        ensure_genre_dirs()

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.9)
        self._duration = 0
        self._autoplay_pending = False

        icon_path = Path("assets/app.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QVBoxLayout(self)

        # (Descripción quitada)
        copyright = QLabel("© 2025 Gabriel Golker")
        root.addWidget(copyright)

        # ---------- Tabs ----------
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # --- Pestaña a_Género ---
        self.tab_genre = QWidget()
        tab_genre_layout = QVBoxLayout(self.tab_genre)

        g_gen = QGroupBox("Fuente de moldes por género")
        lg = QVBoxLayout(g_gen)

        row = QHBoxLayout()
        row.addWidget(QLabel("Género:"))
        self.cb_genre = QComboBox()
        self.cb_genre.addItems(GENRES)
        row.addWidget(self.cb_genre, 1)

        row.addWidget(QLabel("Cantidad:"))
        self.spn_count = QSpinBox()
        self.spn_count.setRange(1, 50)
        self.spn_count.setValue(3)
        row.addWidget(self.spn_count)

        self.btn_open_folder = QPushButton("Abrir carpeta…")
        self.btn_pick_random = QPushButton("Elegir N al azar")
        self.btn_refresh = QPushButton("Refrescar")
        row.addWidget(self.btn_open_folder)
        row.addWidget(self.btn_pick_random)
        row.addWidget(self.btn_refresh)

        lg.addLayout(row)

        self.mold_list = ReadOnlyList()
        lg.addWidget(self.mold_list)

        # Pre-escucha compartida
        g_player1 = QGroupBox("Pre-escucha")
        lp1 = QVBoxLayout(g_player1)
        ctl1 = QHBoxLayout()
        self.btn_play = QPushButton("▶︎")
        self.btn_pause = QPushButton("⏸")
        self.btn_stop = QPushButton("⏹")
        ctl1.addWidget(self.btn_play); ctl1.addWidget(self.btn_pause); ctl1.addWidget(self.btn_stop)
        self.chk_autoplay = QCheckBox("Auto reproducir al seleccionar"); self.chk_autoplay.setChecked(True)
        ctl1.addWidget(self.chk_autoplay, 1)
        lp1.addLayout(ctl1)
        self.sld_pos = QSlider(Qt.Horizontal); self.sld_pos.setRange(0, 0); lp1.addWidget(self.sld_pos)
        self.lbl_time = QLabel("00:00 / 00:00"); lp1.addWidget(self.lbl_time)
        lg.addWidget(g_player1)
        tab_genre_layout.addWidget(g_gen)

        # --- Pestaña b_Básico ---
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

        self.basic_list = BasicMoldList()
        lb.addWidget(self.basic_list)

        # Nota de reproducción (usa controles compartidos)
        g_player2 = QGroupBox("Pre-escucha")
        lp2 = QVBoxLayout(g_player2)
        note = QLabel("Usa los controles de reproducción compartidos (arriba).")
        lp2.addWidget(note)
        lb.addWidget(g_player2)

        tab_basic_layout.addWidget(g_basic)

        # --- Tab Configuración (global) ---
        self.tab_cfg = QWidget()
        tab_cfg_layout = QVBoxLayout(self.tab_cfg)

        g_cfg = QGroupBox("Configuración rápida")
        lf = QFormLayout(g_cfg)
        self.ed_bpm = QLineEdit("100")
        self.ed_attack = QLineEdit("1.0")
        self.ed_release = QLineEdit("0.5")
        self.ed_floor_db = QLineEdit("-40.0")
        self.ed_mode = QLineEdit("hilbert")
        self.ed_combine = QLineEdit("max")
        self.ed_weights = QLineEdit("")

        # Campo salida + "…" + Abrir carpeta
        self.ed_out = QLineEdit(str(Path.cwd() / "salida.wav"))
        self.btn_browse_out = QToolButton(); self.btn_browse_out.setText("…")
        self.btn_open_out_dir = QPushButton("Abrir carpeta")
        out_row = QHBoxLayout()
        out_row.addWidget(self.ed_out, 1)
        out_row.addWidget(self.btn_browse_out)
        out_row.addWidget(self.btn_open_out_dir)

        lf.addRow("BPM:", self.ed_bpm)
        lf.addRow("Attack ms:", self.ed_attack)
        lf.addRow("Release ms:", self.ed_release)
        lf.addRow("Floor dB:", self.ed_floor_db)
        lf.addRow("Envelope mode:", self.ed_mode)
        lf.addRow("Combine mode:", self.ed_combine)
        lf.addRow("Weights (coma):", self.ed_weights)
        lf.addRow("Archivo de salida:", out_row)

        self.chk_auto_name = QCheckBox("Auto-nombrar salida (destino + moldes)")
        self.chk_auto_name.setChecked(True)
        lf.addRow(self.chk_auto_name)

        tab_cfg_layout.addWidget(g_cfg)

        # Añadir pestañas
        self.tabs.addTab(self.tab_genre, "a_Género")
        self.tabs.addTab(self.tab_basic, "b_Básico")
        self.tabs.addTab(self.tab_cfg, "Configuración")

        # --- Destino (compartido) ---
        g_dest = QGroupBox("Destino (arrastra o elige un archivo)")
        ld = QVBoxLayout(g_dest)
        self.dest_list = DestDropList()
        ld.addWidget(self.dest_list)
        btn_dest = QPushButton("Elegir destino…")
        btn_clear_d = QPushButton("Limpiar")
        bdh = QHBoxLayout()
        bdh.addWidget(btn_dest); bdh.addWidget(btn_clear_d)
        ld.addLayout(bdh)
        root.addWidget(g_dest)

        # Progreso & Logs
        self.progress = QProgressBar(); self.progress.setRange(0, 100); root.addWidget(self.progress)
        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumBlockCount(5000); root.addWidget(self.logs)

        # Botón procesar
        hb = QHBoxLayout()
        self.btn_run = QPushButton("Procesar"); hb.addWidget(self.btn_run)
        root.addLayout(hb)

        # Señales (Género)
        self.cb_genre.currentIndexChanged.connect(self.on_genre_changed)
        self.btn_open_folder.clicked.connect(self.on_open_folder)
        self.btn_pick_random.clicked.connect(self.pick_random_n)
        self.btn_refresh.clicked.connect(self.refresh_current_folder)

        # Señales (Básico)
        self.btn_basic_add_files.clicked.connect(self.basic_add_files)
        self.btn_basic_add_dir.clicked.connect(self.basic_add_dir)
        self.btn_basic_clear.clicked.connect(self.basic_list.clear)

        # Señales (Destino / general)
        btn_dest.clicked.connect(self.pick_dest_file)
        btn_clear_d.clicked.connect(self.dest_list.clear)
        self.btn_run.clicked.connect(self.on_run)

        # Reproductor (compartido)
        self.btn_play.clicked.connect(self.on_play)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_stop.clicked.connect(self.on_stop)
        self._connect_player_signals()

        # Selección para pre-escucha
        self.mold_list.currentItemChanged.connect(lambda curr, prev: self.on_any_mold_item_changed(self.mold_list, curr, prev))
        self.mold_list.itemDoubleClicked.connect(lambda _: self.on_play())
        self.basic_list.currentItemChanged.connect(lambda curr, prev: self.on_any_mold_item_changed(self.basic_list, curr, prev))
        self.basic_list.itemDoubleClicked.connect(lambda _: self.on_play())

        # Salida
        self.btn_browse_out.clicked.connect(self.browse_out_file)
        self.btn_open_out_dir.clicked.connect(self.open_out_folder)

        # Inicializar
        self.on_genre_changed()
        self.worker = None

    # --- Drag & drop a nivel ventana ---
    # NOTA: Solo activo para a_Género (en b_Básico se usa drop "por zona")
    def _urls_have_valid_audio(self, urls):
        for url in urls:
            p = Path(url.toLocalFile())
            if _is_audio_file(p):
                return True
        return False

    def dragEnterEvent(self, event):
        if self.tabs.currentWidget() is self.tab_genre and event.mimeData().hasUrls() and self._urls_have_valid_audio(event.mimeData().urls()):
            event.setDropAction(Qt.CopyAction); event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if self.tabs.currentWidget() is self.tab_genre and event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = Path(url.toLocalFile())
                if _is_audio_file(p):
                    self.dest_list.clear()
                    self.dest_list.addItem(QListWidgetItem(str(p)))
                    event.setDropAction(Qt.CopyAction); event.accept(); return
        event.ignore()

    # -------- utilidades de UI --------
    def append_log(self, text):
        self.logs.appendPlainText(text)

    def _current_genre_dir(self) -> Path:
        return app_genres_dir() / self.cb_genre.currentText()

    def _list_audio_files(self, folder: Path):
        files = []
        if folder.exists():
            for child in sorted(folder.iterdir()):
                if _is_audio_file(child):
                    files.append(child)
        return files

    def on_genre_changed(self):
        self.refresh_current_folder(pick_random=True)

    def on_open_folder(self):
        folder = self._current_genre_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def refresh_current_folder(self, pick_random=False):
        folder = self._current_genre_dir()
        folder.mkdir(parents=True, exist_ok=True)
        files = self._list_audio_files(folder)
        if pick_random:
            self._set_random_n(files, self.spn_count.value())
        else:
            self.mold_list.set_paths(files)

    def _set_random_n(self, files, n):
        if not files:
            self.mold_list.clear()
            QMessageBox.warning(self, "Sin samples", "No hay archivos de audio en la carpeta del género seleccionado.")
            return
        n = max(1, int(n))
        chosen = files if len(files) <= n else random.sample(files, n)
        self.mold_list.set_paths(chosen)

    # -------- Básico: añadir archivos/carpeta --------
    def basic_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Añadir moldes", str(Path.cwd()),
                    "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif)")
        if files:
            self.basic_list.add_files([Path(f) for f in files])

    def basic_add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Elegir carpeta de moldes", str(Path.cwd()))
        if d:
            paths = _collect_audios_from_dir(Path(d), recursive=True)
            self.basic_list.add_files(paths)

    # -------- Pre-escucha compartida --------
    def _connect_player_signals(self):
        try:
            self.player.positionChanged.connect(self.on_pos_changed)
            self.player.durationChanged.connect(self.on_dur_changed)
            self.player.mediaStatusChanged.connect(self.on_media_status)
            try: self.player.errorOccurred.connect(self.on_media_error)
            except Exception: pass
            try: self.sld_pos.sliderMoved.disconnect()
            except Exception: pass
            self.sld_pos.sliderMoved.connect(self.player.setPosition)
        except Exception as e:
            self.append_log(f"[Audio] conectar señales: {e}")

    def _recreate_player(self):
        try: vol = self.audio.volume() if hasattr(self, 'audio') and self.audio else 0.9
        except Exception: vol = 0.9
        try: self.player.stop()
        except Exception: pass
        try: self.player.deleteLater()
        except Exception: pass
        try: self.audio.deleteLater()
        except Exception: pass
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(vol)
        self.player.setAudioOutput(self.audio)
        self._duration = 0
        self._autoplay_pending = False
        self._connect_player_signals()

    def _fmt_ms(self, ms: int) -> str:
        ms = int(ms or 0); s = ms // 1000; m = s // 60; s = s % 60
        return f"{m:02}:{s:02}"

    def _load_player_source(self, path: Path):
        try:
            self._recreate_player()
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.sld_pos.setRange(0, 0)
            self.lbl_time.setText("00:00 / 00:00")
            if self._autoplay_pending:
                QTimer.singleShot(200, self.on_play)
        except Exception as e:
            self.append_log(f"[Audio] No se pudo cargar: {path} -> {e}")

    def on_any_mold_item_changed(self, which_list: QListWidget, curr, prev):
        if curr is None:
            return
        if which_list is self.mold_list or which_list is self.basic_list:
            p = Path(curr.text())
            self._autoplay_pending = bool(self.chk_autoplay.isChecked())
            self._load_player_source(p)

    def on_play(self):
        try:
            self.player.play()
            self._autoplay_pending = False
        except Exception as e:
            self.append_log(f"[Audio] play() error: {e}")

    def on_pause(self):
        try:
            self.player.pause()
        except Exception as e:
            self.append_log(f"[Audio] pause() error: {e}")

    def on_stop(self):
        try:
            self.player.stop()
        except Exception as e:
            self.append_log(f"[Audio] stop() error: {e}")

    def on_pos_changed(self, pos):
        try:
            self.sld_pos.blockSignals(True)
            self.sld_pos.setValue(int(pos))
            self.sld_pos.blockSignals(False)
            dur = int(self.player.duration())
            self.lbl_time.setText(f"{self._fmt_ms(pos)} / {self._fmt_ms(dur)}")
        except Exception:
            pass

    def on_dur_changed(self, dur):
        try:
            self._duration = int(dur)
            self.sld_pos.setRange(0, self._duration)
            self.lbl_time.setText(f"{self._fmt_ms(self.player.position())} / {self._fmt_ms(dur)}")
        except Exception:
            pass

    def on_media_status(self, status):
        try:
            if int(status) in (int(QMediaPlayer.LoadedMedia), int(QMediaPlayer.BufferedMedia)):
                if self._autoplay_pending:
                    self.on_play()
        except Exception:
            pass

    def on_media_error(self, *args):
        try:
            err_text = self.player.errorString() if hasattr(self.player, 'errorString') else None
            self.append_log(f"[Audio] error: {args} {('-> ' + err_text) if err_text else ''}")
        except Exception:
            pass

    # -------- destino --------
    def pick_dest_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Elegir destino", str(Path.cwd()),
                                          "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif)")
        if f:
            self.dest_list.clear()
            self.dest_list.addItem(QListWidgetItem(f))

    # -------- ejecutar --------
    def _active_mold_paths(self):
        current = self.tabs.currentWidget()
        if current is self.tab_basic:
            return self.basic_list.paths()
        return self.mold_list.paths()

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
        out = self.ed_out.text().strip()
        ext = Path(out).suffix if out else ".wav"
        if self.chk_auto_name.isChecked():
            out_dir = Path(out).parent if out else Path(dest).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            dest_base = _slug(Path(dest).stem, 20)
            mold_names = [(_slug(Path(p).stem, 12)[:4] or 'xxxx') for p in molds]
            mold_part = "+".join(mold_names)
            if len(mold_part) > 40: mold_part = mold_part[:40]
            out = str(out_dir / f"{dest_base}__{mold_part}{ext}")
        elif not out:
            QMessageBox.warning(self, "Falta salida", "Especifica el archivo de salida (ej: salida.wav).")
            return

        weights = None
        wtxt = self.ed_weights.text().strip()
        if wtxt:
            try:
                weights = [float(x) for x in wtxt.split(",")]
            except Exception:
                QMessageBox.warning(self, "Weights inválidos", "Usa números separados por coma, ej: 1,0.8,1.2")
                return

        cfg = {
            "bpm": float(self.ed_bpm.text() or 100),
            "attack_ms": float(self.ed_attack.text() or 1.0),
            "release_ms": float(self.ed_release.text() or 0.5),
            "floor_db": float(self.ed_floor_db.text() or -40.0),
            "mode": (self.ed_mode.text() or "hilbert").strip().lower(),
            "combine_mode": (self.ed_combine.text() or "max").strip().lower(),
            "weights": weights,
            "match_lufs": False,
        }

        self.progress.setValue(0); self.logs.clear()

        self.worker = Worker(dest, molds, out, cfg)
        self.worker.progressed.connect(self.progress.setValue)
        self.worker.logged.connect(self.append_log)
        self.worker.finished_ok.connect(self.on_done)
        self.worker.failed.connect(self.on_fail)
        self.worker.start()

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

    # -------- abrir/cambiar carpeta/archivo de salida --------
    def open_out_folder(self):
        path_txt = self.ed_out.text().strip()
        if not path_txt:
            QMessageBox.warning(self, "Ruta vacía", "Primero especifica el archivo de salida."); return
        p = Path(path_txt)
        folder = p if p.is_dir() else p.parent
        try: folder.mkdir(parents=True, exist_ok=True)
        except Exception: pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def browse_out_file(self):
        start_path = self.ed_out.text().strip()
        start_dir = str(Path(start_path).parent) if start_path else str(Path.cwd())
        fname, _ = QFileDialog.getSaveFileName(
            self, "Elegir archivo de salida", start_dir,
            "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif);;Todos los archivos (*.*)"
        )
        if fname:
            self.ed_out.setText(fname)

# ---------------- main ----------------
def main():
    app = QApplication(sys.argv)
    if _HAS_QDARK:
        app.setStyleSheet(qdarkstyle.load_stylesheet_pyside6())
    win = MainWin()
    win.resize(980, 820)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()



