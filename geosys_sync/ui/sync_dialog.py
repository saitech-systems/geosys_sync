"""Main sync dialog: connection header, project picker, Pull / Push tabs.

Threading contract: GeosysClient calls run inside QgsTask.fromFunction
workers (background thread, core objects only). QGIS layers are only
touched in the finished-callbacks, which Qt runs on the main thread.
"""
import logging
import os
import shutil
import tempfile

from qgis.core import QgsApplication, QgsProject, QgsTask
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from geosys_sync.core.api_client import GeosysClient
from geosys_sync.core.errors import (
    ApiError, ApprovalRequiredError, AuthRequiredError,
)
from geosys_sync.core import sync_plan
from geosys_sync.qgis_adapter import layer_export, layer_load, layer_props
from geosys_sync.qgis_adapter import style_extract
from geosys_sync.qgis_adapter.settings_store import SettingsStore
from geosys_sync.ui.login_dialog import LoginDialog

log = logging.getLogger(__name__)

_CHECKABLE = Qt.ItemIsUserCheckable | Qt.ItemIsEnabled


# ---------------------------------------------------------------------------
# QgsTask workers - background thread, core objects ONLY (no qgis layers)
# ---------------------------------------------------------------------------

def _pull_worker(task, client, actions):
    results = []
    for i, action in enumerate(actions):
        try:
            if action.entry.kind == 'vector':
                etag = client.download_vector(action.entry.id, action.target_path)
            else:
                cog = client.get_cog_url(action.entry.id)
                client.download_file(cog.url, action.target_path)
                etag = cog.sync_etag
            results.append({'action': action, 'etag': etag, 'error': None})
        except AuthRequiredError:
            raise  # abort the whole task -> finished(exception) -> re-login
        except ApiError as e:
            results.append({'action': action, 'etag': None, 'error': e})
        task.setProgress(100.0 * (i + 1) / len(actions))
    return results


def _push_worker(task, client, jobs):
    """jobs: prepared on the main thread - each is a dict:
    {action, project_id, file_path, style, epsg}"""
    results = []
    for i, job in enumerate(jobs):
        action = job['action']
        try:
            if action.mode == 'overwrite':
                entry = client.overwrite_dataset(
                    action.dataset_id, job['file_path'], action.kind,
                    epsg=job['epsg'], style=job['style'],
                    if_match=action.if_match)
            else:
                entry = client.create_dataset(
                    job['project_id'], action.name, action.kind,
                    job['file_path'], epsg=job['epsg'], style=job['style'])
            results.append({'action': action, 'entry': entry, 'error': None})
        except AuthRequiredError:
            raise  # abort the whole task -> finished(exception) -> re-login
        except ApiError as e:
            results.append({'action': action, 'entry': None, 'error': e})
        task.setProgress(100.0 * (i + 1) / len(jobs))
    return results


class SyncDialog(QDialog):

    # Token rotations can fire on the QgsTask background thread (the client
    # transparently refreshes mid-transfer); emitting this signal queues the
    # QgsSettings write onto the main thread.
    _tokensRotated = pyqtSignal(object)

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.settings = SettingsStore()
        self.client = None
        self.session_info = None
        self.projects = []
        self.manifest = []
        self._task = None
        self._remember = False  # explicit stay-logged-in opt-in
        self.setWindowTitle('GeosysAI Sync')
        self.resize(720, 520)
        self._tokensRotated.connect(self._persist_tokens)
        self._build_ui()
        self._try_resume()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        header = QHBoxLayout()
        self.conn_label = QLabel('Not connected')
        self.login_btn = QPushButton('Log in...')
        self.login_btn.clicked.connect(self._login_flow)
        header.addWidget(self.conn_label, 1)
        header.addWidget(self.login_btn)
        root.addLayout(header)

        proj_row = QHBoxLayout()
        proj_row.addWidget(QLabel('Project:'))
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._project_changed)
        self.refresh_btn = QPushButton('Refresh')
        self.refresh_btn.clicked.connect(self._load_manifest)
        proj_row.addWidget(self.project_combo, 1)
        proj_row.addWidget(self.refresh_btn)
        root.addLayout(proj_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_pull_tab(), 'Download from server')
        self.tabs.addTab(self._build_push_tab(), 'Upload to server')
        self.tabs.currentChanged.connect(lambda _i: self._refresh_tables())
        root.addWidget(self.tabs, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)
        self.status_label = QLabel('')
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    def _build_pull_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel('Save to:'))
        self.dest_edit = QLineEdit(self._default_dest_dir())
        browse = QPushButton('...')
        browse.clicked.connect(self._pick_dest_dir)
        dest_row.addWidget(self.dest_edit, 1)
        dest_row.addWidget(browse)
        v.addLayout(dest_row)
        self.pull_table = self._make_table(
            ['', 'Dataset', 'Kind', 'Status'])
        v.addWidget(self.pull_table, 1)
        self.pull_btn = QPushButton('Download selected')
        self.pull_btn.clicked.connect(self._run_pull)
        v.addWidget(self.pull_btn)
        return w

    def _build_push_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.push_table = self._make_table(
            ['', 'QGIS layer', 'Kind', 'Action'])
        v.addWidget(self.push_table, 1)
        self.push_btn = QPushButton('Upload selected')
        self.push_btn.clicked.connect(self._run_push)
        v.addWidget(self.push_btn)
        return w

    @staticmethod
    def _make_table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 28)
        table.setColumnWidth(1, 280)
        return table

    @staticmethod
    def _default_dest_dir():
        return os.path.join(
            QgsApplication.qgisSettingsDirPath(), 'geosys_sync')

    def _pick_dest_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, 'Choose download folder', self.dest_edit.text())
        if chosen:
            self.dest_edit.setText(chosen)

    # -- connection ---------------------------------------------------------

    def _make_client(self, server_base):
        return GeosysClient(server_base,
                            device_id=self.settings.device_id(),
                            device_name='QGIS Desktop',
                            on_tokens_changed=self._tokens_changed)

    def _tokens_changed(self, bundle):
        # May run on a QgsTask worker thread - do NOT touch QgsSettings here.
        self._tokensRotated.emit(bundle)

    def _persist_tokens(self, bundle):
        """Main-thread slot: persist rotated tokens per the user's opt-in."""
        if bundle is None:
            self.settings.save_refresh_token(None)
        elif self._remember:  # only persist when the user opted in at login
            self.settings.save_refresh_token(bundle.refresh_token)

    def _try_resume(self):
        saved = self.settings.load()
        if not (saved.get('server_base') and saved.get('refresh_token')):
            return
        client = self._make_client(saved['server_base'])
        self._remember = True  # a saved token means the user opted in before
        try:
            info = client.resume(saved['refresh_token'])
        except ApiError:
            self._remember = False
            self.settings.save_refresh_token(None)
            return
        self.client = client
        self._connected(info)

    def _login_flow(self):
        if self.client and self.client.tokens:
            self._remember = False
            self.client.logout()  # emits None -> _persist_tokens clears
            self.client = None
            self.session_info = None
            self.conn_label.setText('Not connected')
            self.login_btn.setText('Log in...')
            self._clear_tables()
            return
        saved = self.settings.load()
        dlg = LoginDialog(self, server_base=saved.get('server_base') or '',
                          username=saved.get('username') or '')
        while dlg.exec_():
            server, identifier, password, remember = dlg.values()
            if not (server.startswith('http://') or server.startswith('https://')):
                dlg.show_error('Server URL must start with http(s)://')
                continue
            client = self._make_client(server)
            self._remember = remember  # before login(): its token emission
            try:                       # already persists via _persist_tokens
                info = client.login(identifier, password)
            except ApiError as e:
                if e.status == 404:
                    dlg.show_error('This server does not expose the QGIS Sync API. '
                                   'Check the URL, and that the server runs a build '
                                   'with QGIS_SYNC_API_ENABLED=true.')
                else:
                    dlg.show_error(e.message)
                continue
            self.settings.save_connection(server, identifier)
            if not remember:  # drop any token remembered by a prior login
                self.settings.save_refresh_token(None)
            self.client = client
            self._connected(info)
            return

    def _connected(self, info):
        self.session_info = info
        self.conn_label.setText('Connected to {} as {}'.format(
            self.client.base_url, info.user.get('username', '?')))
        self.login_btn.setText('Log out')
        self._load_projects()

    # -- projects + manifest --------------------------------------------------

    def _load_projects(self):
        try:
            self.projects = self.client.list_projects()
        except ApiError as e:
            self._show_error(e)
            return
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for p in self.projects:
            self.project_combo.addItem(p.name, p.id)
        self.project_combo.blockSignals(False)
        if self.projects:
            self._load_manifest()

    def _project_changed(self, _index):
        if self.client:
            self._load_manifest()

    def _current_project_id(self):
        return self.project_combo.currentData()

    def _load_manifest(self):
        pid = self._current_project_id()
        if not (self.client and pid):
            return
        try:
            self.manifest = self.client.get_manifest(pid)
        except ApiError as e:
            self._show_error(e)
            return
        self.status_label.setText('')
        self._refresh_tables()

    def _local_facts(self):
        return [layer_export.collect_layer_facts(layer)
                for layer in QgsProject.instance().mapLayers().values()]

    def _refresh_tables(self):
        self._populate_pull_table()
        self._populate_push_table()

    def _clear_tables(self):
        self.pull_table.setRowCount(0)
        self.push_table.setRowCount(0)

    def _populate_pull_table(self):
        actions = sync_plan.plan_pull(self.manifest, self._local_facts(),
                                      self.dest_edit.text() or self._default_dest_dir())
        self._pull_actions = actions
        self.pull_table.setRowCount(len(actions))
        for row, action in enumerate(actions):
            check = QTableWidgetItem()
            check.setFlags(_CHECKABLE)
            check.setCheckState(Qt.Checked if action.changed else Qt.Unchecked)
            self.pull_table.setItem(row, 0, check)
            self.pull_table.setItem(row, 1, QTableWidgetItem(action.entry.name))
            self.pull_table.setItem(row, 2, QTableWidgetItem(action.entry.kind))
            status = ('up to date' if not action.changed
                      else 'changed on server' if action.mode == 'replace'
                      else 'new')
            if action.entry.kind == 'raster' and action.entry.cog_status not in ('ready', 'active'):
                status = 'converting (not downloadable yet)'
                check.setCheckState(Qt.Unchecked)
                check.setFlags(Qt.NoItemFlags)
            self.pull_table.setItem(row, 3, QTableWidgetItem(status))

    def _populate_push_table(self):
        pid = self._current_project_id()
        caps = (self.session_info.capabilities if self.session_info else {})
        actions = sync_plan.plan_push(
            self._local_facts(), {e.id: e for e in self.manifest}, caps,
            self.client.base_url if self.client else '', pid)
        self._push_actions = actions
        self.push_table.setRowCount(len(actions))
        for row, action in enumerate(actions):
            check = QTableWidgetItem()
            if action.blocked_reason:
                check.setFlags(Qt.NoItemFlags)
                label = action.blocked_reason
            else:
                check.setFlags(_CHECKABLE)
                check.setCheckState(Qt.Unchecked)
                label = ('overwrite dataset #{}'.format(action.dataset_id)
                         if action.mode == 'overwrite' else 'create new dataset')
            self.push_table.setItem(row, 0, check)
            self.push_table.setItem(row, 1, QTableWidgetItem(action.name))
            self.push_table.setItem(row, 2, QTableWidgetItem(action.kind))
            self.push_table.setItem(row, 3, QTableWidgetItem(label))

    def _checked_rows(self, table):
        rows = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                rows.append(row)
        return rows

    # -- pull ----------------------------------------------------------------

    def _run_pull(self):
        if not self._require_connection():
            return
        actions = [self._pull_actions[r]
                   for r in self._checked_rows(self.pull_table)]
        if not actions:
            self.status_label.setText('Nothing selected.')
            return
        os.makedirs(self.dest_edit.text() or self._default_dest_dir(),
                    exist_ok=True)
        pid = self._current_project_id()
        client = self.client  # pin: a mid-task logout must not swap clients
        server = client.base_url
        self._start_task(
            'GeosysAI pull',
            lambda task, c=client: _pull_worker(task, c, actions),
            lambda results: self._pull_finished(results, server, pid))

    def _pull_finished(self, results, server, project_id):
        ok, failed = 0, []
        for r in results:
            if r['error'] is not None:
                failed.append('{}: {}'.format(r['action'].entry.name,
                                              r['error'].message))
                continue
            entry = r['action'].entry
            # trust the etag observed at download time
            entry.sync_etag = r['etag'] or entry.sync_etag
            try:
                layer_load.load_pulled_dataset(entry, r['action'].target_path,
                                               server, project_id)
                ok += 1
            except RuntimeError as e:
                failed.append('{}: {}'.format(entry.name, e))
        self._finish_status('Downloaded {} layer(s).'.format(ok), failed)
        self._refresh_tables()

    # -- push ----------------------------------------------------------------

    def _run_push(self):
        if not self._require_connection():
            return
        actions = [self._push_actions[r]
                   for r in self._checked_rows(self.push_table)]
        actions = [a for a in actions if not a.blocked_reason]
        if not actions:
            self.status_label.setText('Nothing selected.')
            return
        pid = self._current_project_id()
        project = QgsProject.instance()
        jobs = []
        prep_failed = []  # export/extract failures + style-downgrade warnings
        export_dir = None  # created lazily: raster-only pushes need no exports
        for action in actions:  # MAIN THREAD: exports + style extraction
            layer = project.mapLayer(action.layer_id)
            if layer is None:
                prep_failed.append('{}: layer is no longer in the project'
                                   .format(action.name))
                continue
            try:
                style, warnings = style_extract.extract_wire(layer)
                if action.kind == 'vector':
                    if export_dir is None:
                        export_dir = tempfile.mkdtemp(prefix='geosys_push_')
                    file_path = layer_export.export_vector_gpkg(
                        layer, os.path.join(export_dir,
                                            sync_plan.sanitize_filename(action.name) + '.gpkg'))
                else:
                    file_path = layer_export.raster_source_path(layer)
            except RuntimeError as e:
                prep_failed.append('{}: {}'.format(action.name, e))
                continue
            for w in warnings:
                prep_failed.append('{}: {}'.format(action.name, w))
            jobs.append({'action': action, 'project_id': pid,
                         'file_path': file_path, 'style': style,
                         'epsg': layer_export.layer_epsg(layer)})
        if not jobs:
            if export_dir:
                shutil.rmtree(export_dir, ignore_errors=True)
            self._finish_status('Nothing uploaded.', prep_failed)
            return
        client = self.client  # pin: a mid-task logout must not swap clients
        server = client.base_url
        self._start_task(
            'GeosysAI push',
            lambda task, c=client: _push_worker(task, c, jobs),
            lambda results: self._push_finished(results, server, pid,
                                                export_dir, prep_failed))

    def _push_finished(self, results, server, project_id, export_dir=None,
                       prep_failed=None):
        if export_dir:  # uploads are done; drop the temp GPKG exports
            shutil.rmtree(export_dir, ignore_errors=True)
        project = QgsProject.instance()
        ok, failed = 0, list(prep_failed or [])
        for r in results:
            action = r['action']
            if r['error'] is not None:
                hint = (' Pull first, then push again.'
                        if r['error'].code == 'SYNC_CONFLICT' else '')
                failed.append('{}: {}{}'.format(action.name,
                                                r['error'].message, hint))
                continue
            entry = r['entry']
            layer = project.mapLayer(action.layer_id)
            if layer is not None:
                layer_props.write_sync_state(
                    layer, server_base=server, project_id=project_id,
                    dataset_id=entry.id, kind=entry.kind,
                    sync_etag=entry.sync_etag)
            note = (' (server is converting the raster)'
                    if entry.cog_status == 'processing' else '')
            ok += 1
            log.info('pushed %s -> dataset %s%s', action.name, entry.id, note)
        self._finish_status('Uploaded {} layer(s).'.format(ok), failed)
        self._load_manifest()

    # -- task + status plumbing ----------------------------------------------

    def _require_connection(self):
        if self.client and self.client.tokens:
            return True
        self.status_label.setText('Log in first.')
        return False

    def _start_task(self, name, worker, on_done):
        if self._task is not None:
            self.status_label.setText('A sync operation is already running.')
            return
        self.progress.setVisible(True)
        self.progress.setValue(0)
        for widget in (self.pull_btn, self.push_btn, self.login_btn,
                       self.refresh_btn, self.project_combo):
            widget.setEnabled(False)

        def finished(exception, result=None):
            self._task = None
            self.progress.setVisible(False)
            for widget in (self.pull_btn, self.push_btn, self.login_btn,
                           self.refresh_btn, self.project_combo):
                widget.setEnabled(True)
            if exception is not None:
                self._show_error(exception)
                return
            on_done(result or [])

        task = QgsTask.fromFunction(name, worker, on_finished=finished)
        task.progressChanged.connect(
            lambda p: self.progress.setValue(int(p)))
        self._task = task
        QgsApplication.taskManager().addTask(task)

    def _finish_status(self, ok_text, failed):
        if failed:
            self.status_label.setText(
                '{} Failed: {}'.format(ok_text, ' | '.join(failed)))
        else:
            self.status_label.setText(ok_text)

    def _show_error(self, err):
        if isinstance(err, AuthRequiredError):
            self.status_label.setText('Session expired - please log in again.')
            self.login_btn.setText('Log in...')
            self.client = None
        elif isinstance(err, ApprovalRequiredError):
            QMessageBox.information(
                self, 'Approval required',
                'Project creation requires approval. Submit a request from '
                'the GeosysAI web dashboard, or ask your administrator.')
        elif isinstance(err, ApiError):
            self.status_label.setText(err.message)
        else:
            self.status_label.setText(str(err))
            log.exception('sync error', exc_info=err)
