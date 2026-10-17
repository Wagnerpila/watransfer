"""Interface gráfica (PySide6)."""

from __future__ import annotations

import os
import re
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import __version__, android, export, ios, store, workflows
from .model import Archive

HELP_ANDROID = (
    "<b>Antes de começar, no celular Android:</b><ol>"
    "<li>WhatsApp Business → Configurações → Conversas → Backup de conversas → "
    "<b>Backup criptografado de ponta a ponta</b> → ativar e escolher "
    "<b>usar chave de criptografia de 64 dígitos</b>. Anote a chave.</li>"
    "<li>Toque em <b>Fazer backup</b> e aguarde terminar.</li>"
    "<li>Ative as Opções do desenvolvedor e a <b>Depuração USB</b>, conecte o cabo e aceite o aviso.</li></ol>"
)
_PERCENT = re.compile(r"Progresso do backup: (\d+)%")
_COUNT = re.compile(r"^\s*(\d+)/(\d+)\s*$")


def _fmt_secs(secs: float) -> str:
    secs = int(secs)
    h, rest = divmod(secs, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60:02d}:{rest % 60:02d}"


HELP_IOS = (
    "<b>Antes de começar, com o iPhone:</b><ol>"
    "<li>Instale o app <b>Apple Devices</b> (ou iTunes) no Windows para ter o driver USB.</li>"
    "<li>Conecte o iPhone, desbloqueie e toque em <b>Confiar</b>.</li>"
    "<li>Use um backup existente da lista ou crie um novo. Backups criptografados pedem a senha.</li></ol>"
)


class Worker(QObject):
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs

    def run(self):
        try:
            result = self.fn(*self.args, log=self.log.emit, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 — mostrar qualquer erro ao usuário
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc))
        else:
            self.done.emit(result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"WATransfer {__version__} — WhatsApp Business")
        self.resize(1150, 760)
        self._threads: list[QThread] = []
        self.archive: Archive | None = None

        self.tabs = QTabWidget()
        self.tabs.addTab(self._android_tab(), "Android")
        self.tabs.addTab(self._ios_tab(), "iPhone")
        self.tabs.addTab(self._viewer_tab(), "Backups salvos")

        self.log_box = QPlainTextEdit(readOnly=True)
        self.log_box.setMaximumBlockCount(5000)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.log_box)
        splitter.setSizes([600, 160])
        self.setCentralWidget(splitter)
        self._status_bar()

        self.refresh_android()
        self.refresh_ios()
        self.refresh_stored()

    # ------------------------------------------------------------ barra de status
    def _status_bar(self):
        bar = self.statusBar()
        self.status_text = QLabel("Pronto.")
        self.status_progress = QProgressBar(maximumWidth=320, minimumWidth=220, textVisible=True)
        self.status_progress.hide()
        self.status_time = QLabel()
        bar.addWidget(self.status_text, 1)
        bar.addPermanentWidget(self.status_progress)
        bar.addPermanentWidget(self.status_time)
        self._task_start: float | None = None
        self._task_percent: float | None = None
        self._clock = QTimer(self, interval=1000)
        self._clock.timeout.connect(self._tick)

    def _task_started(self):
        self._task_start, self._task_percent = time.monotonic(), None
        self.status_text.setText("Iniciando…")
        self.status_progress.setRange(0, 0)  # animação "ocupado" até haver porcentagem
        self.status_progress.show()
        self._clock.start()
        self._tick()

    def _task_finished(self, ok: bool):
        self._clock.stop()
        elapsed = _fmt_secs(time.monotonic() - self._task_start) if self._task_start else ""
        self._task_start = None
        self.status_progress.hide()
        self.status_text.setText(("✔ Concluído" if ok else "✖ Falhou — veja o log") + f" em {elapsed}.")
        self.status_time.clear()

    def _set_progress(self, value: float, maximum: float = 100):
        self._task_percent = 100 * value / maximum if maximum else None
        self.status_progress.setRange(0, int(maximum))
        self.status_progress.setValue(int(value))
        self._tick()

    def _tick(self):
        if self._task_start is None:
            return
        elapsed = time.monotonic() - self._task_start
        text = f"Decorrido {_fmt_secs(elapsed)}"
        p = self._task_percent
        if p and 1 <= p < 100:
            text += f" · restante ≈ {_fmt_secs(elapsed * (100 - p) / p)}"
        self.status_time.setText(text)

    def _update_status(self, text: str):
        if self._task_start is None or "\n" in text or not text.strip():
            return
        if m := _PERCENT.search(text):
            self._set_progress(float(m.group(1)))
            self.status_text.setText(f"Backup do iPhone: {m.group(1)}%")
        elif m := _COUNT.match(text):
            self._set_progress(float(m.group(1)), float(m.group(2)))
            self.status_text.setText(f"Extraindo arquivos: {m.group(1)} de {m.group(2)}")
        else:
            # remove prefixo de data/host/módulo das mensagens de log do pymobiledevice3
            clean = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \S+ \S+\[\d+\] \w+ ", "", text.strip())
            self.status_text.setText(clean[:150])

    # ------------------------------------------------------------ helpers
    def log(self, text: str):
        self.log_box.appendPlainText(text)
        self._update_status(text)

    def run_task(self, buttons: list[QPushButton], fn, *args, on_done=None, **kwargs):
        for b in buttons:
            b.setEnabled(False)
        thread, worker = QThread(self), Worker(fn, *args, **kwargs)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.log)
        self._task_started()

        def finish(success: bool):
            for b in buttons:
                b.setEnabled(True)
            thread.quit()
            self._task_finished(success)

        def ok(result):
            finish(True)
            self.refresh_stored()
            if on_done:
                on_done(result)

        def fail(message):
            finish(False)
            QMessageBox.critical(self, "Erro", message)

        worker.done.connect(ok)
        worker.failed.connect(fail)
        thread.finished.connect(lambda: self._threads.remove(thread))
        thread._worker = worker  # mantém referência viva
        self._threads.append(thread)
        thread.start()

    # ------------------------------------------------------------ Android
    def _android_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(HELP_ANDROID, wordWrap=True))

        box = QGroupBox("Backup direto do celular")
        form = QFormLayout(box)
        row = QHBoxLayout()
        self.android_combo = QComboBox()
        btn_refresh = QPushButton("Atualizar")
        btn_refresh.clicked.connect(self.refresh_android)
        row.addWidget(self.android_combo, 1)
        row.addWidget(btn_refresh)
        form.addRow("Aparelho:", row)
        self.android_key = QLineEdit(echoMode=QLineEdit.Password, placeholderText="64 dígitos (0-9, a-f)")
        show = QCheckBox("mostrar")
        show.toggled.connect(
            lambda on: self.android_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        keyrow = QHBoxLayout()
        keyrow.addWidget(self.android_key, 1)
        keyrow.addWidget(show)
        form.addRow("Chave do backup:", keyrow)
        self.android_media = QCheckBox("Incluir mídias (fotos, vídeos, áudios, documentos)", checked=True)
        form.addRow("", self.android_media)
        self.btn_android = QPushButton("Fazer backup do Android")
        self.btn_android.clicked.connect(self.do_android_backup)
        form.addRow("", self.btn_android)
        lay.addWidget(box)

        box2 = QGroupBox("Ou importar um arquivo já copiado")
        h = QHBoxLayout(box2)
        self.btn_android_import = QPushButton("Importar msgstore.db.crypt15…")
        self.btn_android_import.clicked.connect(self.do_android_import)
        h.addWidget(QLabel("Use a mesma chave informada acima."))
        h.addStretch()
        h.addWidget(self.btn_android_import)
        lay.addWidget(box2)
        lay.addStretch()
        return w

    def refresh_android(self):
        self.android_combo.clear()
        try:
            devices = android.Adb().devices()
        except Exception as exc:  # noqa: BLE001
            self.android_combo.addItem(f"adb indisponível: {exc}", None)
            return
        for d in devices:
            self.android_combo.addItem(d.label, d.serial)
        if not devices:
            self.android_combo.addItem("Nenhum aparelho conectado", None)

    def do_android_backup(self):
        serial = self.android_combo.currentData()
        if not serial:
            QMessageBox.warning(self, "Android", "Conecte e autorize um aparelho Android.")
            return
        self.run_task(
            [self.btn_android, self.btn_android_import], workflows.android_backup,
            serial, self.android_key.text(), self.android_media.isChecked(),
            on_done=self.open_stored,
        )

    def do_android_import(self):
        file, _ = QFileDialog.getOpenFileName(self, "Arquivo do backup", "", "Backup WhatsApp (*.crypt15);;Todos (*)")
        if not file:
            return
        media = QFileDialog.getExistingDirectory(self, "Pasta Media (opcional — cancele para pular)")
        self.run_task(
            [self.btn_android, self.btn_android_import], workflows.android_import_file,
            Path(file), self.android_key.text(), Path(media) if media else None,
            on_done=self.open_stored,
        )

    # ------------------------------------------------------------ iPhone
    def _ios_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(HELP_IOS, wordWrap=True))
        box = QGroupBox("Backups de iPhone neste computador")
        v = QVBoxLayout(box)
        self.ios_list = QListWidget()
        v.addWidget(self.ios_list)
        row = QHBoxLayout()
        b_refresh = QPushButton("Atualizar lista")
        b_refresh.clicked.connect(self.refresh_ios)
        b_folder = QPushButton("Abrir outra pasta de backup…")
        b_folder.clicked.connect(self.add_ios_folder)
        row.addWidget(b_refresh)
        row.addWidget(b_folder)
        row.addStretch()
        v.addLayout(row)
        lay.addWidget(box, 1)

        form = QFormLayout()
        self.ios_password = QLineEdit(echoMode=QLineEdit.Password, placeholderText="só para backups criptografados")
        form.addRow("Senha do backup:", self.ios_password)
        self.ios_media = QCheckBox("Incluir mídias", checked=True)
        form.addRow("", self.ios_media)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.btn_ios_extract = QPushButton("Extrair WhatsApp Business do backup selecionado")
        self.btn_ios_extract.clicked.connect(self.do_ios_extract)
        self.btn_ios_new = QPushButton("Criar novo backup do iPhone conectado")
        self.btn_ios_new.clicked.connect(self.do_ios_new)
        row.addWidget(self.btn_ios_extract)
        row.addWidget(self.btn_ios_new)
        lay.addLayout(row)
        self._extra_ios_roots: list[Path] = []
        return w

    def refresh_ios(self):
        self.ios_list.clear()
        for b in ios.list_backups(self._extra_ios_roots):
            item = QListWidgetItem(b.label)
            item.setData(Qt.UserRole, str(b.path))
            self.ios_list.addItem(item)
        if self.ios_list.count() == 0:
            self.ios_list.addItem("Nenhum backup encontrado nas pastas padrão.")

    def add_ios_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Pasta do backup (contém Manifest.plist)")
        if not folder:
            return
        path = Path(folder)
        self._extra_ios_roots.append(path if not (path / "Manifest.plist").is_file() else path.parent)
        self.refresh_ios()

    def do_ios_extract(self):
        item = self.ios_list.currentItem()
        path = item.data(Qt.UserRole) if item else None
        if not path:
            QMessageBox.warning(self, "iPhone", "Selecione um backup na lista.")
            return
        self.run_task(
            [self.btn_ios_extract, self.btn_ios_new], workflows.ios_extract,
            Path(path), self.ios_password.text() or None, self.ios_media.isChecked(),
            on_done=self.open_stored,
        )

    def do_ios_new(self):
        self.run_task(
            [self.btn_ios_extract, self.btn_ios_new], workflows.ios_backup_and_extract,
            self.ios_password.text() or None, self.ios_media.isChecked(),
            on_done=self.open_stored,
        )

    # ------------------------------------------------------------ Visualizador
    def _viewer_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.stored_combo = QComboBox()
        self.stored_combo.currentIndexChanged.connect(self.load_selected)
        b_folder = QPushButton("Abrir pasta")
        b_folder.clicked.connect(self.open_folder)
        self.btn_html = QPushButton("Exportar HTML…")
        self.btn_html.clicked.connect(lambda: self.do_export("html"))
        self.btn_csv = QPushButton("Exportar CSV…")
        self.btn_csv.clicked.connect(lambda: self.do_export("csv"))
        top.addWidget(QLabel("Backup:"))
        top.addWidget(self.stored_combo, 1)
        for b in (b_folder, self.btn_html, self.btn_csv):
            top.addWidget(b)
        lay.addLayout(top)

        self.summary = QLabel()
        lay.addWidget(self.summary)
        split = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.chat_filter = QLineEdit(placeholderText="Buscar conversa…")
        self.chat_filter.textChanged.connect(self.fill_chats)
        self.chat_list = QListWidget()
        self.chat_list.currentRowChanged.connect(self.show_chat)
        lv.addWidget(self.chat_filter)
        lv.addWidget(self.chat_list)
        self.chat_view = QTextBrowser(openExternalLinks=True)
        split.addWidget(left)
        split.addWidget(self.chat_view)
        split.setSizes([330, 800])
        lay.addWidget(split, 1)
        return w

    def refresh_stored(self, select: Path | None = None):
        self.stored_combo.blockSignals(True)
        self.stored_combo.clear()
        for b in store.list_stored():
            self.stored_combo.addItem(b.label, str(b.path))
        self.stored_combo.blockSignals(False)
        if select:
            idx = self.stored_combo.findData(str(select))
            if idx >= 0:
                self.stored_combo.setCurrentIndex(idx)
        self.load_selected()

    def open_stored(self, backup: store.StoredBackup):
        self.refresh_stored(backup.path)
        self.tabs.setCurrentIndex(2)

    def load_selected(self):
        path = self.stored_combo.currentData()
        self.archive = None
        self.chat_list.clear()
        self.chat_view.clear()
        if not path:
            self.summary.setText("Nenhum backup salvo ainda.")
            return
        try:
            self.archive = store.open_backup(Path(path)).load()
        except Exception as exc:  # noqa: BLE001
            self.summary.setText(f"Não foi possível abrir: {exc}")
            return
        a = self.archive
        self.summary.setText(
            f"{len(a.chats)} conversas · {a.message_count} mensagens · formato {a.schema}"
            + ("" if a.media_root else " · sem mídias")
        )
        self.fill_chats()

    def _visible_chats(self):
        if not self.archive:
            return []
        q = self.chat_filter.text().lower()
        return [c for c in self.archive.chats if q in c.title.lower() or q in c.jid]

    def fill_chats(self):
        self.chat_list.clear()
        for chat in self._visible_chats():
            self.chat_list.addItem(f"{'👥 ' if chat.is_group else ''}{chat.title}  ({len(chat.messages)})")

    def show_chat(self, row: int):
        chats = self._visible_chats()
        if not self.archive or not 0 <= row < len(chats):
            return
        chat = chats[row]
        messages = chat.messages[-2000:]
        note = f"<p><i>Mostrando as últimas {len(messages)} de {len(chat.messages)} mensagens.</i></p>" \
            if len(messages) < len(chat.messages) else ""
        body = "".join(
            "<table width='100%'><tr><td align='{}'>{}</td></tr></table>".format(
                "right" if m.from_me else "left",
                export.render_message_html(self.archive, m, show_sender=chat.is_group),
            )
            for m in messages
        )
        self.chat_view.setHtml(f"<style>{export.CSS}</style><h3>{chat.title}</h3>{note}{body}")
        self.chat_view.verticalScrollBar().setValue(self.chat_view.verticalScrollBar().maximum())

    def open_folder(self):
        path = self.stored_combo.currentData()
        os.startfile(path or str(store.backups_root()))  # noqa: S606

    def do_export(self, fmt: str):
        if not self.archive:
            return
        if fmt == "csv":
            out, _ = QFileDialog.getSaveFileName(self, "Salvar CSV", "conversas.csv", "CSV (*.csv)")
            if out:
                export.export_csv(self.archive, Path(out))
                self.log(f"CSV exportado: {out}")
        else:
            out = QFileDialog.getExistingDirectory(self, "Pasta de destino do HTML")
            if out:
                index = export.export_html(self.archive, Path(out))
                self.log(f"HTML exportado: {index}")
                os.startfile(index)  # noqa: S606


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
