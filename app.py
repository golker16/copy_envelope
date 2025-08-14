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
    QGroupBox, QLineEdit, QFormLayout, QMessageBox, QComboBox, QSpinBox, QCheckBox, QSlider, QTabWidget
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
    """Dónde crear/leer las carpetas de géneros.
    - En PyInstaller onedir: junto al .exe
    - En dev: junto a este archivo
    """
    if getattr(sys, 'frozen', False):
        # Ejecutándose empaquetado
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

# Utilidad: slug para nombres de archivo
def _slug(s: str, max_len: int = 30) -> str:
    s = s.lower().strip()
    # permite letras/números/_ - + ; reemplaza el resto por '-'
    s = ''.join(ch if (ch.isalnum() or ch in ['_', '-', '+']) else '-' for ch in s)
    s = re.sub('-{2,}', '-', s)
    return s[:max_len].strip('-_')

# ---------------- Widgets ----------------
class ReadOnlyList(LISTWIDGET := QListWidget):
    """Solo muestra rutas; sin drag&drop del usuario."""
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

        # Crear carpetas de géneros si no existen
        ensure_genre_dirs()

        # Inicializar reproductor multimedia
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.9)
        self._duration = 0
        self._autoplay_pending = False

        # Optional window icon si existe (assets/app.png)
        icon_path = Path("assets/app.png")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QVBoxLayout(self)

        # (Descripción quitada a pedido)

        copyright = QLabel("© 2025 Gabriel Golker")
        root.addWidget(copyright)

        # ---------- Tabs ----------
        tabs = QTabWidget()
        root.addWidget(tabs)

        # --- Pestaña Principal: Géneros + Pre-escucha + Destino ---
        tab_main = QWidget()
        tab_main_layout = QVBoxLayout(tab_main)

        # --- Fuente de moldes: Género + control de carpeta ---
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

        # --- Reproductor (QtMultimedia) ---
        g_player = QGroupBox("Pre-escucha")
        lp = QVBoxLayout(g_player)
        ctl = QHBoxLayout()
        self.btn_play = QPushButton("▶︎")
        self.btn_pause = QPushButton("⏸")
        self.btn_stop = QPushButton("⏹")
        ctl.addWidget(self.btn_play)
        ctl.addWidget(self.btn_pause)
        ctl.addWidget(self.btn_stop)
        self.chk_autoplay = QCheckBox("Auto reproducir al seleccionar")
        self.chk_autoplay.setChecked(True)
        ctl.addWidget(self.chk_autoplay, 1)
        lp.addLayout(ctl)
        self.sld_pos = QSlider(Qt.Horizontal)
        self.sld_pos.setRange(0, 0)
        lp.addWidget(self.sld_pos)
        self.lbl_time = QLabel("00:00 / 00:00")
        lp.addWidget(self.lbl_time)
        lg.addWidget(g_player)

        tab_main_layout.addWidget(g_gen)

        # --- Destino ---
        g_dest = QGroupBox("Destino (arrastra o elige un archivo)")
        ld = QVBoxLayout(g_dest)
        self.dest_list = QListWidget()
        self.dest_list.setAcceptDrops(True)
        self.dest_list.setMinimumHeight(60)
        ld.addWidget(self.dest_list)
        btn_dest = QPushButton("Elegir destino…")
        btn_clear_d = QPushButton("Limpiar")
        bdh = QHBoxLayout()
        bdh.addWidget(btn_dest)
        bdh.addWidget(btn_clear_d)
        ld.addLayout(bdh)
        tab_main_layout.addWidget(g_dest)

        tabs.addTab(tab_main, "Principal")

        # --- Pestaña Configuración (lo que antes estaba en el root) ---
        tab_cfg = QWidget()
        tab_cfg_layout = QVBoxLayout(tab_cfg)

        g_cfg = QGroupBox("Configuración rápida")
        lf = QFormLayout(g_cfg)
        self.ed_bpm = QLineEdit("100")
        self.ed_attack = QLineEdit("1.0")
        self.ed_release = QLineEdit("0.5")
        self.ed_floor_db = QLineEdit("-40.0")
        self.ed_mode = QLineEdit("hilbert")  # 'hilbert' o 'rms'
        self.ed_combine = QLineEdit("max")   # max/mean/geom_mean/product/sum_limited/weighted
        self.ed_weights = QLineEdit("")      # pesos opcionales separados por coma
        self.ed_out = QLineEdit(str(Path.cwd() / "salida.wav"))
        lf.addRow("BPM:", self.ed_bpm)
        lf.addRow("Attack ms:", self.ed_attack)
        lf.addRow("Release ms:", self.ed_release)
        lf.addRow("Floor dB:", self.ed_floor_db)
        lf.addRow("Envelope mode:", self.ed_mode)
        lf.addRow("Combine mode:", self.ed_combine)
        lf.addRow("Weights (coma):", self.ed_weights)
        lf.addRow("Archivo de salida:", self.ed_out)
        self.chk_auto_name = QCheckBox("Auto-nombrar salida (destino + moldes)")
        self.chk_auto_name.setChecked(True)
        lf.addRow(self.chk_auto_name)

        tab_cfg_layout.addWidget(g_cfg)
        tabs.addTab(tab_cfg, "Configuración")

        # Progreso & Logs (siempre visibles)
        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        root.addWidget(self.progress)

        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumBlockCount(5000)
        root.addWidget(self.logs)

        # Botón procesar
        hb = QHBoxLayout()
        self.btn_run = QPushButton("Procesar")
        hb.addWidget(self.btn_run)
        root.addLayout(hb)

        # Señales
        self.cb_genre.currentIndexChanged.connect(self.on_genre_changed)
        self.btn_open_folder.clicked.connect(self.on_open_folder)
        self.btn_pick_random.clicked.connect(self.pick_random_n)
        self.btn_refresh.clicked.connect(self.refresh_current_folder)
        btn_dest.clicked.connect(self.pick_dest_file)
        btn_clear_d.clicked.connect(self.dest_list.clear)
        self.btn_run.clicked.connect(self.on_run)
        # Reproductor
        self.btn_play.clicked.connect(self.on_play)
        self.btn_pause.clicked.connect(self.on_pause)
        self.btn_stop.clicked.connect(self.on_stop)
        self._connect_player_signals()
        self.mold_list.currentItemChanged.connect(self.on_mold_item_changed)
        self.mold_list.itemDoubleClicked.connect(lambda _: self.on_play())

        # Inicializar con el primer género
        self.on_genre_changed()

        self.worker = None

    # -------- utilidades de UI --------
    def append_log(self, text):
        self.logs.appendPlainText(text)

    def _current_genre_dir(self) -> Path:
        return app_genres_dir() / self.cb_genre.currentText()

    def _list_audio_files(self, folder: Path):
        files = []
        if folder.exists():
            for child in sorted(folder.iterdir()):
                if child.is_file() and child.suffix.lower() in AUDIO_EXTS:
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
        if len(files) <= n:
            chosen = files
        else:
            chosen = random.sample(files, n)
        self.mold_list.set_paths(chosen)

    # -------- Reproductor --------
    def _connect_player_signals(self):
        try:
            self.player.positionChanged.connect(self.on_pos_changed)
            self.player.durationChanged.connect(self.on_dur_changed)
            self.player.mediaStatusChanged.connect(self.on_media_status)
            try:
                self.player.errorOccurred.connect(self.on_media_error)
            except Exception:
                pass
            try:
                self.sld_pos.sliderMoved.disconnect()
            except Exception:
                pass
            self.sld_pos.sliderMoved.connect(self.player.setPosition)
        except Exception as e:
            self.append_log(f"[Audio] conectar señales: {e}")

    def _recreate_player(self):
        try:
            vol = self.audio.volume() if hasattr(self, 'audio') and self.audio else 0.9
        except Exception:
            vol = 0.9
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            self.player.deleteLater()
        except Exception:
            pass
        try:
            self.audio.deleteLater()
        except Exception:
            pass
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(vol)
        self.player.setAudioOutput(self.audio)
        self._duration = 0
        self._autoplay_pending = False
        self._connect_player_signals()

    def _fmt_ms(self, ms: int) -> str:
        ms = int(ms or 0)
        s = ms // 1000
        m = s // 60
        s = s % 60
        return f"{m:02}:{s:02}"

    def _load_player_source(self, path: Path):
        try:
            # recrear completamente el reproductor para evitar estados colgados del backend
            self._recreate_player()
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.sld_pos.setRange(0, 0)
            self.lbl_time.setText("00:00 / 00:00")
            # fallback: si hay autoplay marcado, intentar play tras breve retardo por si el backend tarda en cargar
            if self._autoplay_pending:
                QTimer.singleShot(200, self.on_play)
        except Exception as e:
            self.append_log(f"[Audio] No se pudo cargar: {path} -> {e}")

    def on_mold_item_changed(self, curr, prev):
        if curr:
            p = Path(curr.text())
            # marcar autoplay diferido hasta que el medio esté cargado
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
        # Solo auto-reproducir cuando el medio ya está cargado/bufferizado
        try:
            if int(status) in (int(QMediaPlayer.LoadedMedia), int(QMediaPlayer.BufferedMedia)):
                if self._autoplay_pending:
                    self.on_play()
        except Exception:
            pass

    def on_media_error(self, *args):
        try:
            # PySide6 cambia la firma entre versiones; mostramos lo que tengamos
            err_text = None
            if hasattr(self.player, 'errorString'):
                err_text = self.player.errorString()
            self.append_log(f"[Audio] error: {args} {('-> ' + err_text) if err_text else ''}")
        except Exception:
            pass

    def pick_random_n(self):
        try:
            self.player.stop()
        except Exception:
            pass
        self.refresh_current_folder(pick_random=True)

    def pick_dest_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Elegir destino", str(Path.cwd()),
                                          "Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff *.aif)")
        if f:
            self.dest_list.clear()
            self.dest_list.addItem(QListWidgetItem(f))

    # -------- ejecutar --------
    def on_run(self):
        molds = self.mold_list.paths()
        if not molds:
            QMessageBox.warning(self, "Faltan moldes", "No hay moldes seleccionados (revisa la carpeta del género).")
            return
        dests = [self.dest_list.item(i).text() for i in range(self.dest_list.count())]
        if not dests:
            QMessageBox.warning(self, "Falta destino", "Elige el archivo destino.")
            return
        dest = dests[0]
        out = self.ed_out.text().strip()
        # Determinar extensión (por defecto .wav si no se especifica)
        ext = Path(out).suffix if out else ".wav"
        if self.chk_auto_name.isChecked():
            # Carpeta de salida: si el campo tiene ruta, usar su carpeta; si no, junto al destino
            out_dir = Path(out).parent if out else Path(dest).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            dest_base = _slug(Path(dest).stem, 20)
            mold_names = [(_slug(Path(p).stem, 12)[:4] or 'xxxx') for p in molds]
            mold_part = "+".join(mold_names)
            if len(mold_part) > 40:
                mold_part = mold_part[:40]
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

        self.progress.setValue(0)
        self.logs.clear()

        self.worker = Worker(dest, molds, out, cfg)
        self.worker.progressed.connect(self.progress.setValue)
        self.worker.logged.connect(self.append_log)
        self.worker.finished_ok.connect(self.on_done)
        self.worker.failed.connect(self.on_fail)
        self.worker.start()

    def on_done(self, out_path):
        self.append_log(f"OK: {out_path}")
        QMessageBox.information(self, "Listo", f"Se generó: {out_path}")

    def on_fail(self, tb):
        self.append_log(tb)
        QMessageBox.critical(self, "Error", "Ocurrió un error. Revisa los logs.")

# ---------------- main ----------------
def main():
    app = QApplication(sys.argv)
    if _HAS_QDARK:
        app.setStyleSheet(qdarkstyle.load_stylesheet_pyside6())
    win = MainWin()
    win.resize(900, 780)
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()


