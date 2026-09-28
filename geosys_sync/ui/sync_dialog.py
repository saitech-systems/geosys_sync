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
from qgis.gui import QgsProjectionSelectionDialog
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from geosys_sync.core import api_client
from geosys_sync.core.api_client import GeosysClient
from geosys_sync.core.errors import (
    ApiError, ApprovalRequiredError, AuthRequiredError, SyncConflictError,
)
from geosys_sync.core import sync_plan
from geosys_sync.core import upload_session
from geosys_sync.core.i18n import tr
from geosys_sync.qgis_adapter import cog_export
from geosys_sync.qgis_adapter import layer_export, layer_load, layer_props
from geosys_sync.qgis_adapter import style_extract
from geosys_sync.core.models import MfaChallenge
from geosys_sync.qgis_adapter.settings_store import SettingsStore
from geosys_sync.ui.login_dialog import LoginDialog, MfaDialog

log = logging.getLogger(__name__)

_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled


# ---------------------------------------------------------------------------
# QgsTask workers - background thread, core objects ONLY (no qgis layers)
# ---------------------------------------------------------------------------

def _pull_worker(task, client, actions):
    results = []
    n = len(actions)
    for i, action in enumerate(actions):
        def file_progress(done, total, _base=100.0 * i / n):
            # Byte-level progress inside the current file; without it a single
            # large raster sits at 0% for the whole transfer.
            if total > 0:
                task.setProgress(min(99.0, _base + 100.0 * done / total / n))
        try:
            if action.entry.kind == 'vector':
                etag = client.download_vector(action.entry.id, action.target_path,
                                              progress=file_progress)
            else:
                cog = client.get_cog_url(action.entry.id)
                client.download_file(cog.url, action.target_path,
                                     progress=file_progress)
                etag = cog.sync_etag
            results.append({'action': action, 'etag': etag, 'error': None})
        except AuthRequiredError:
            raise  # abort the whole task -> finished(exception) -> re-login
        except ApiError as e:
            results.append({'action': action, 'etag': None, 'error': e})
        task.setProgress(100.0 * (i + 1) / len(actions))
    return results


# Share of one job's progress span spent converting before any byte moves.
_CONVERT_SHARE = 0.4
_UPLOAD_SHARE = 0.55


def _push_raster_cog(task, client, job, base, span):
    """Convert locally, upload the artifacts to object storage, register.

    Runs on the QgsTask worker thread: it touches GDAL and the filesystem,
    never QGIS layer objects.
    """
    action = job['action']
    # Coerce once, up front: build_raster_artifacts and plan_raster_upload
    # both need an int, and plan_raster_upload's copy also goes straight into
    # the register payload - a bad epsg should fail here, before the upload,
    # not after it at register time.
    epsg = int(job['epsg'])
    work_dir = tempfile.mkdtemp(prefix='geosys_cog_')
    try:
        artifacts = cog_export.build_raster_artifacts(
            job['file_path'], epsg, work_dir,
            progress=lambda pct: task.setProgress(
                base + span * _CONVERT_SHARE * pct / 100.0))
        files, register_payload, dataset_id, if_match = sync_plan.plan_raster_upload(
            action.mode, action.dataset_id, action.if_match, epsg,
            artifacts)

        def on_bytes(done, total):
            if total:
                task.setProgress(base + span * (
                    _CONVERT_SHARE + _UPLOAD_SHARE * done / float(total)))

        return upload_session.push_raster_session(
            client,
            project_id=job['project_id'],
            dataset_name=action.name,
            files=files,
            register_payload=register_payload,
            dataset_id=dataset_id,
            if_match=if_match,
            style=job['style'],
            crs_confirmed=job.get('crs_confirmed', False),
            progress=on_bytes)
    finally:
        # Only the work dir: artifacts.original_path may be the user's own
        # file, which lives outside it and must not be touched.
        shutil.rmtree(work_dir, ignore_errors=True)


def _push_worker(task, client, jobs, cog_flow=False):
    """jobs: prepared on the main thread - each is a dict:
    {action, project_id, file_path, style, epsg}"""
    results = []
    for i, job in enumerate(jobs):
        action = job['action']
        base = 100.0 * i / len(jobs)
        span = 100.0 / len(jobs)
        try:
            if cog_flow and action.kind == 'raster':
                entry = _push_raster_cog(task, client, job, base, span)
            elif action.mode == 'overwrite':
                entry = client.overwrite_dataset(
                    action.dataset_id, job['file_path'], action.kind,
                    epsg=job['epsg'], style=job['style'],
                    if_match=action.if_match)
            else:
                entry = client.create_dataset(
                    job['project_id'], action.name, action.kind,
                    job['file_path'], epsg=job['epsg'], style=job['style'],
                    crs_confirmed=job.get('crs_confirmed', False))
            results.append({'action': action, 'entry': entry, 'error': None})
        except AuthRequiredError:
            raise  # abort the whole task -> finished(exception) -> re-login
        except ApiError as e:
            results.append({'action': action, 'entry': None, 'error': e})
        except Exception as e:
            # Local conversion/planning failure: report it per layer like an
            # API error rather than killing the rest of the push, so layers
            # already uploaded still get their sync state written. Covers
            # more than RuntimeError - e.g. ValueError from plan_parts or
            # bounds_from_points, TypeError from a bad epsg, KeyError from a
            # malformed register response.
            results.append({'action': action, 'entry': None,
                            'error': ApiError('CONVERSION_FAILED', str(e))})
        task.setProgress(100.0 * (i + 1) / len(jobs))
    return results


def _style_worker(task, client, jobs):
    """jobs: [{action, style}] - a style push moves no files.

    Same per-layer containment as _push_worker: one layer's failure must not
    abort the task, because _push_finished is what writes each successful
    layer's refreshed sync_etag back, and the server folds the style into that
    etag. Losing the write-back would leave the layer reporting "changed on
    server" and 409 its next data overwrite on a stale If-Match.
    """
    results = []
    for i, job in enumerate(jobs):
        action = job['action']
        try:
            entry = client.update_style(action.dataset_id, job['style'])
            results.append({'action': action, 'entry': entry, 'error': None})
        except AuthRequiredError:
            raise  # abort the whole task -> finished(exception) -> re-login
        except ApiError as e:
            results.append({'action': action, 'entry': None, 'error': e})
        except Exception as e:  # malformed response, unexpected local error
            results.append({'action': action, 'entry': None,
                            'error': ApiError('STYLE_FAILED', str(e))})
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
        # Populated per table refresh. Empty here so a failed first manifest
        # load leaves the buttons harmless instead of raising AttributeError:
        # the tables are empty too, so nothing can be selected anyway.
        self._pull_actions = []
        self._push_actions = []
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
        self.conn_label = QLabel(tr('Not connected'))
        # Both labels echo server-supplied text (usernames, error messages);
        # never render it as rich text.
        self.conn_label.setTextFormat(Qt.TextFormat.PlainText)
        self.login_btn = QPushButton(tr('Log in...'))
        self.login_btn.clicked.connect(self._login_flow)
        header.addWidget(self.conn_label, 1)
        header.addWidget(self.login_btn)
        root.addLayout(header)

        proj_row = QHBoxLayout()
        proj_row.addWidget(QLabel(tr('Project:')))
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._project_changed)
        self.refresh_btn = QPushButton(tr('Refresh'))
        self.refresh_btn.clicked.connect(self._load_manifest)
        proj_row.addWidget(self.project_combo, 1)
        proj_row.addWidget(self.refresh_btn)
        root.addLayout(proj_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_pull_tab(), tr('Download from server'))
        self.tabs.addTab(self._build_push_tab(), tr('Upload to server'))
        self.tabs.currentChanged.connect(lambda _i: self._refresh_tables())
        root.addWidget(self.tabs, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)
        self.status_label = QLabel('')
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status_label)

    def _build_pull_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel(tr('Save to:')))
        self.dest_edit = QLineEdit(self._default_dest_dir())
        browse = QPushButton('...')
        browse.clicked.connect(self._pick_dest_dir)
        dest_row.addWidget(self.dest_edit, 1)
        dest_row.addWidget(browse)
        v.addLayout(dest_row)
        self.pull_table = self._make_table(
            ['', tr('Dataset'), tr('Kind'), tr('Status')])
        v.addWidget(self.pull_table, 1)
        self.pull_btn = QPushButton(tr('Download selected'))
        self.pull_btn.clicked.connect(self._run_pull)
        v.addWidget(self.pull_btn)
        return w

    def _build_push_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.push_table = self._make_table(
            ['', tr('QGIS layer'), tr('Kind'), tr('Action')])
        v.addWidget(self.push_table, 1)
        buttons = QHBoxLayout()
        self.push_btn = QPushButton(tr('Upload selected'))
        self.push_btn.clicked.connect(self._run_push)
        # Symbology alone needs no data transfer, and re-sending a multi-GB
        # raster to change three colours is not a reasonable way to ask.
        self.style_btn = QPushButton(tr('Upload style only'))
        self.style_btn.setToolTip(tr(
            'Send the selected layers\' symbology and labels to the server without re-uploading their data.'))
        self.style_btn.clicked.connect(self._run_style_push)
        buttons.addWidget(self.push_btn, 1)
        buttons.addWidget(self.style_btn)
        v.addLayout(buttons)
        return w

    @staticmethod
    def _make_table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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
            self, tr('Choose download folder'), self.dest_edit.text())
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
            if not self.settings.save_refresh_token(bundle.refresh_token):
                # The authentication database refused (disabled, or the user
                # dismissed the master-password prompt). The token is stored
                # nowhere, so stop promising a remembered session rather than
                # retrying - and prompting - on every rotation.
                self._remember = False
                self.status_label.setText(tr(
                    'Could not save the session in the QGIS authentication '
                    'database, so you will need to log in again next time.'))

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
            self.conn_label.setText(tr('Not connected'))
            self.login_btn.setText(tr('Log in...'))
            self._clear_tables()
            return
        saved = self.settings.load()
        dlg = LoginDialog(self, server_base=saved.get('server_base') or '',
                          username=saved.get('username') or '')
        while dlg.exec():
            server, identifier, password, remember = dlg.values()
            problem = api_client.server_url_problem(server)
            if problem:
                dlg.show_error(problem)
                continue
            client = self._make_client(server)
            self._remember = remember  # before login(): its token emission
            try:                       # already persists via _persist_tokens
                info = client.login(identifier, password)
            except ApiError as e:
                if e.status == 404:
                    dlg.show_error(tr(
                        'This server does not expose the QGIS Sync API. Check the '
                        'URL, and that the server runs a build with '
                        'QGIS_SYNC_API_ENABLED=true.'))
                else:
                    dlg.show_error(e.message)
                continue
            if isinstance(info, MfaChallenge):
                info = self._mfa_flow(client, info)
                if info is None:
                    dlg.show_error(tr('Verification cancelled or expired - try again.'))
                    continue
            self.settings.save_connection(server, identifier)
            if not remember:  # drop any token remembered by a prior login
                self.settings.save_refresh_token(None)
            self.client = client
            self._connected(info)
            return

    def _mfa_flow(self, client, challenge):
        """Prompt for the second-factor code until it verifies, the user
        cancels, or the pending token dies. Returns SessionInfo or None."""
        mfa = MfaDialog(self, methods=challenge.methods)
        while mfa.exec():
            if not mfa.code():
                mfa.show_error(tr('Enter the verification code.'))
                continue
            try:
                return client.verify_mfa(challenge.pending_token, mfa.code())
            except ApiError as e:
                if e.code in ('MFA_TOKEN_EXPIRED', 'MFA_TOKEN_INVALID'):
                    return None  # challenge window is dead; back to login
                mfa.show_error(e.message)
        return None

    def _connected(self, info):
        self.session_info = info
        self.conn_label.setText(tr('Connected to {} as {}').format(
            self.client.base_url, info.user.get('username', '?')))
        self.login_btn.setText(tr('Log out'))
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

    def _current_project(self):
        pid = self._current_project_id()
        for p in self.projects:
            if p.id == pid:
                return p
        return None

    def _load_manifest(self, clear_status=True):
        """clear_status=False when the caller has just written a result the
        user still needs to read: a push reloads the manifest to refresh the
        tables, and blanking the label here would wipe its own summary."""
        pid = self._current_project_id()
        if not (self.client and pid):
            return
        try:
            self.manifest = self.client.get_manifest(pid)
        except ApiError as e:
            self._show_error(e)
            return
        if clear_status:
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
            check.setCheckState(Qt.CheckState.Checked if action.changed else Qt.CheckState.Unchecked)
            self.pull_table.setItem(row, 0, check)
            self.pull_table.setItem(row, 1, QTableWidgetItem(action.entry.name))
            self.pull_table.setItem(row, 2, QTableWidgetItem(tr(action.entry.kind)))
            status = (tr('up to date') if not action.changed
                      else tr('changed on server') if action.mode == 'replace'
                      else tr('new'))
            if action.entry.kind == 'raster' and action.entry.cog_status not in ('ready', 'active'):
                status = tr('converting (not downloadable yet)')
                check.setCheckState(Qt.CheckState.Unchecked)
                check.setFlags(Qt.ItemFlag.NoItemFlags)
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
                check.setFlags(Qt.ItemFlag.NoItemFlags)
                label = action.blocked_reason
            else:
                check.setFlags(_CHECKABLE)
                check.setCheckState(Qt.CheckState.Unchecked)
                label = (tr('overwrite dataset #{}').format(action.dataset_id)
                         if action.mode == 'overwrite' else tr('create new dataset'))
            self.push_table.setItem(row, 0, check)
            self.push_table.setItem(row, 1, QTableWidgetItem(action.name))
            self.push_table.setItem(row, 2, QTableWidgetItem(tr(action.kind)))
            self.push_table.setItem(row, 3, QTableWidgetItem(label))

    def _checked_rows(self, table):
        rows = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                rows.append(row)
        return rows

    # -- pull ----------------------------------------------------------------

    def _run_pull(self):
        if not self._require_connection():
            return
        actions = [self._pull_actions[r]
                   for r in self._checked_rows(self.pull_table)]
        if not actions:
            self.status_label.setText(tr('Nothing selected.'))
            return
        os.makedirs(self.dest_edit.text() or self._default_dest_dir(),
                    exist_ok=True)
        pid = self._current_project_id()
        client = self.client  # pin: a mid-task logout must not swap clients
        server = client.base_url
        self._start_task(
            tr('GeosysAI download'),
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
        self._finish_status(tr('Downloaded {} layer(s).').format(ok), failed)
        self._refresh_tables()

    # -- push ----------------------------------------------------------------

    def _project_awaiting_crs_confirmation(self):
        """The current Project when its next raster upload still needs a CRS
        acknowledgement, else None.

        Re-read from the server rather than trusting the project list cached
        at login: a stale "already locked" would skip the prompt and turn the
        upload into a 409 the user cannot resolve from here. A failed re-read
        falls back to the cached row."""
        project = self._current_project()
        pid = self._current_project_id()
        if pid:
            try:
                project = self.client.get_project(pid)
            except ApiError:
                log.debug('project re-read failed; using the cached lock state')
        if project is not None and project.crs_confirmation_required:
            return project
        return None

    def _confirm_project_crs(self, project):
        """Ask before the FIRST raster goes into a project: completing the
        upload freezes its map CRS for good. Returns True to proceed."""
        crs = ('EPSG:{}'.format(project.effective_epsg_code)
               if project.effective_epsg_code else tr('its current coordinate system'))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr('Confirm project coordinate system'))
        # project.name is server-supplied text - never render it as rich text.
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(tr('This is the first raster in project "{}".').format(project.name))
        box.setInformativeText(tr(
            'Raster data is converted into the project coordinate system ({}) '
            'when it is uploaded. Completing this upload permanently freezes '
            'the project map CRS at that value - it cannot be changed '
            'afterwards.\n\nContinue with the upload?').format(crs))
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Ok

    def _ask_layer_crs(self, layer, reason):
        """Ask for a coordinate system for one layer. Returns the chosen
        QgsCoordinateReferenceSystem, or None if the user cancelled.

        Its own method so the surrounding push logic stays testable without
        opening a modal.
        """
        dlg = QgsProjectionSelectionDialog(self)
        dlg.setWindowTitle(tr('Coordinate system for "{}"').format(layer.name()))
        dlg.setMessage(tr(
            'This layer cannot be uploaded as it is: {}.\n'
            'Choose a coordinate system with an EPSG code to continue.\n'
            'This re-labels the layer; it does not reproject it, so choose '
            'the coordinate system the data is already in.'
        ).format(reason))
        # Deliberately left with nothing selected. Pre-seeding a plausible CRS
        # would make one impatient OK re-label the layer to an answer nobody
        # chose, and nothing downstream can catch mislabeled geometry.
        accepted = dlg.exec() == QDialog.DialogCode.Accepted
        crs = dlg.crs() if accepted else None
        dlg.deleteLater()   # parented to the session-lived dialog otherwise
        return crs

    def _resolve_push_crs(self, actions):
        """Make sure every layer about to be pushed carries an EPSG code,
        asking the user to pick one where it does not.

        Returns (resolved, failed): resolved is a list of (action, layer)
        pairs cleared for upload, failed is report strings in the same
        '<name>: <reason>' shape as the rest of prep_failed. A layer the user
        declines to fix is dropped rather than aborting the whole push, which
        matches how every other per-layer failure behaves here.
        """
        project = QgsProject.instance()
        resolved, failed = [], []
        for action in actions:
            layer = project.mapLayer(action.layer_id)
            if layer is None:
                failed.append('{}: {}'.format(
                    action.name, tr('layer is no longer in the project')))
                continue
            problem = layer_export.layer_crs_problem(layer)
            if problem is not None:
                crs = self._ask_layer_crs(layer, problem)
                if crs is None:
                    failed.append('{}: {}'.format(action.name, problem))
                    continue
                # The picker can hand back another authority-less CRS. Check
                # before applying, so a rejected pick never mutates the layer.
                picked_problem = layer_export.crs_problem(crs)
                if picked_problem is not None:
                    failed.append('{}: {}'.format(action.name, picked_problem))
                    continue
                layer.setCrs(crs)
            resolved.append((action, layer))
        return resolved, failed

    def _run_push(self):
        if not self._require_connection():
            return
        actions = [self._push_actions[r]
                   for r in self._checked_rows(self.push_table)]
        actions = [a for a in actions if not a.blocked_reason]
        if not actions:
            self.status_label.setText(tr('Nothing selected.'))
            return
        # Before anything else: the upload carries an integer epsg, and a
        # layer without one otherwise only fails deep inside the worker as a
        # generic conversion error, after the user committed to the push.
        resolved, prep_failed = self._resolve_push_crs(actions)
        if not resolved:
            self._finish_status(tr('Nothing uploaded.'), prep_failed)
            return
        actions = [action for action, _ in resolved]
        # Only a NEW raster can be a project's first one; an overwrite targets
        # a dataset that already exists, so the server never re-prompts there.
        crs_confirmed = False
        if any(a.kind == 'raster' and a.mode != 'overwrite' for a in actions):
            crs_project = self._project_awaiting_crs_confirmation()
            if crs_project is not None:
                if not self._confirm_project_crs(crs_project):
                    self._finish_status(tr('Upload cancelled.'), prep_failed)
                    return
                crs_confirmed = True
        pid = self._current_project_id()
        jobs = []
        prep_notes = []   # style downgrades: the layer uploads, styled worse
        export_dir = None  # created lazily: raster-only pushes need no exports
        for action, layer in resolved:  # MAIN THREAD: exports + style extraction
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
            except Exception as e:
                # Wider than the RuntimeError layer_export raises: style
                # extraction reads renderer internals and can raise on an
                # exotic one, and that would otherwise escape into the Qt slot
                # and kill the whole push before a single byte moved.
                prep_failed.append('{}: {}'.format(action.name, e))
                continue
            for w in warnings:
                prep_notes.append('{}: {}'.format(action.name, w))
            jobs.append({'action': action, 'project_id': pid,
                         'file_path': file_path, 'style': style,
                         'epsg': layer_export.layer_epsg(layer),
                         'crs_confirmed': crs_confirmed})
        if not jobs:
            if export_dir:
                shutil.rmtree(export_dir, ignore_errors=True)
            self._finish_status(tr('Nothing uploaded.'), prep_failed, prep_notes)
            return
        caps = (self.session_info.capabilities if self.session_info else {})
        cog_flow = any(sync_plan.uses_cog_flow(j['action'].kind, caps)
                       for j in jobs)
        client = self.client  # pin: a mid-task logout must not swap clients
        server = client.base_url
        self._start_task(
            tr('GeosysAI upload'),
            lambda task, c=client, f=cog_flow: _push_worker(task, c, jobs, f),
            lambda results: self._push_finished(results, server, pid,
                                                export_dir, prep_failed,
                                                prep_notes))

    def _push_finished(self, results, server, project_id, export_dir=None,
                       prep_failed=None, prep_notes=None,
                       ok_text=None):
        """Shared by the data push and the style-only push: both write each
        successful layer's refreshed sync_etag back and report the same way.
        `ok_text` takes the count."""
        if ok_text is None:  # not a default arg: the language is set at load, after import
            ok_text = tr('Uploaded {} layer(s).')
        if export_dir:  # uploads are done; drop the temp GPKG exports
            shutil.rmtree(export_dir, ignore_errors=True)
        project = QgsProject.instance()
        ok, failed = 0, list(prep_failed or [])
        notes = list(prep_notes or [])
        for r in results:
            action = r['action']
            if r['error'] is not None:
                hint = (' ' + tr('Download it first, then upload again.')
                        if isinstance(r['error'], SyncConflictError) else '')
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
            # What the server itself had to downgrade while storing the style.
            # Discarding these would hide, for instance, a symbology_type it
            # does not support having been rewritten to 'single'.
            for w in entry.style_warnings:
                notes.append('{}: {}'.format(action.name, w))
            note = (' (server is converting the raster)'
                    if entry.cog_status == 'processing' else '')
            ok += 1
            log.info('pushed %s -> dataset %s%s', action.name, entry.id, note)
        self._finish_status(ok_text.format(ok), failed, notes)
        self._load_manifest(clear_status=False)

    # -- style-only push -----------------------------------------------------

    def _run_style_push(self):
        """Send symbology and labels for the selected layers, no data.

        Only layers already synced to this project qualify; the server's style
        endpoint needs a dataset to write to.
        """
        if not self._require_connection():
            return
        chosen = [self._push_actions[r]
                  for r in self._checked_rows(self.push_table)]
        if not chosen:
            self.status_label.setText(tr('Nothing selected.'))
            return
        actions, skipped = sync_plan.plan_style_push(chosen)
        failed = ['{}: {}'.format(name, reason) for name, reason in skipped]
        project = QgsProject.instance()
        jobs, notes = [], []
        for action in actions:  # MAIN THREAD: style extraction touches layers
            layer = project.mapLayer(action.layer_id)
            if layer is None:
                failed.append('{}: {}'.format(
                    action.name, tr('layer is no longer in the project')))
                continue
            try:
                style, warnings = style_extract.extract_wire(layer)
            except Exception as e:
                # Extraction reads renderer internals and can raise on an
                # exotic one. Report the layer and carry on: uncontained, this
                # escapes into the Qt slot and the user sees nothing at all.
                failed.append('{}: {}'.format(
                    action.name, tr('could not read the style ({})').format(e)))
                continue
            for w in warnings:
                notes.append('{}: {}'.format(action.name, w))
            jobs.append({'action': action, 'style': style})
        if not jobs:
            self._finish_status(tr('No styles uploaded.'), failed, notes)
            return
        pid = self._current_project_id()
        client = self.client  # pin: a mid-task logout must not swap clients
        server = client.base_url
        self._start_task(
            tr('GeosysAI style upload'),
            lambda task, c=client: _style_worker(task, c, jobs),
            lambda results: self._push_finished(
                results, server, pid, None, failed, notes,
                ok_text=tr('Updated the style on {} layer(s).')))

    # -- task + status plumbing ----------------------------------------------

    def _require_connection(self):
        if self.client and self.client.tokens:
            return True
        self.status_label.setText(tr('Log in first.'))
        return False

    def _busy_widgets(self):
        """Everything that must grey out while a sync task runs. One list, so
        a new action cannot be added to the dialog and left live by accident -
        _start_task refuses a second concurrent task anyway."""
        return (self.pull_btn, self.push_btn, self.style_btn, self.login_btn,
                self.refresh_btn, self.project_combo)

    def _start_task(self, name, worker, on_done):
        if self._task is not None:
            self.status_label.setText(tr('A sync operation is already running.'))
            return
        self.progress.setVisible(True)
        self.progress.setValue(0)
        for widget in self._busy_widgets():
            widget.setEnabled(False)

        def finished(exception, result=None):
            self._task = None
            self.progress.setVisible(False)
            for widget in self._busy_widgets():
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

    def _finish_status(self, ok_text, failed, notes=None):
        """notes are things that succeeded but not as asked - a downgraded
        renderer, symbology the platform cannot store. Kept apart from failed
        so a warning never reads as a layer that did not upload."""
        parts = [ok_text]
        if failed:
            parts.append(tr('Failed: {}').format(' | '.join(failed)))
        if notes:
            parts.append(tr('Note: {}').format(' | '.join(notes)))
        self.status_label.setText(' '.join(parts))

    def _show_error(self, err):
        if isinstance(err, AuthRequiredError):
            self.status_label.setText(tr('Session expired - please log in again.'))
            self.login_btn.setText(tr('Log in...'))
            self.client = None
        elif isinstance(err, ApprovalRequiredError):
            QMessageBox.information(
                self, tr('Approval required'),
                tr('Project creation requires approval. Submit a request from '
                   'the GeosysAI web dashboard, or ask your administrator.'))
        elif isinstance(err, ApiError):
            self.status_label.setText(err.message)
        else:
            self.status_label.setText(str(err))
            log.exception('sync error', exc_info=err)
