"""Private backup downloads and a two-step restore workflow."""
import json
import math
import tempfile

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from core.backups import BackupError, BackupService, MAX_ARCHIVE_BYTES, get_backup_service
from core.backup_crypto import MAX_CIPHERTEXT_BYTES, generate_recovery_key
from core.state_gate import install_state_gate

bp = Blueprint("backups", __name__, url_prefix="/api/backups")


@bp.record_once
def setup(state):
    app = state.app
    install_state_gate(app)
    base = app.request_class
    class BackupRequest(base):
        @property
        def max_content_length(self):
            if self.endpoint == "backups.preview_restore" and self.mimetype == 'multipart/form-data':
                return max(MAX_ARCHIVE_BYTES, MAX_CIPHERTEXT_BYTES) + 64 * 1024
            if self.endpoint == 'companion.create_shared_photo':
                from core.shared_reference import MAX_BYTES
                return ((MAX_BYTES + 2) // 3) * 4 + 8192
            if self.endpoint == 'companions.import_character' and self.mimetype == 'multipart/form-data':
                from core.charcard import MAX_CHARX_BYTES
                return MAX_CHARX_BYTES + 64 * 1024
            return super().max_content_length

        def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
            # Werkzeug can spool uploads before the route runs. Keep that disk
            # material in the same private instance as voice and backup staging.
            from core.private_paths import private_temp_dir
            return tempfile.SpooledTemporaryFile(max_size=512 * 1024, mode='w+b', dir=private_temp_dir())
    app.request_class = BackupRequest


@bp.after_request
def private(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def body(allowed):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field.')
            result[key] = value
        return result

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Nonfinite JSON number.')
        return number

    def nonfinite(_value):
        raise ValueError('Nonfinite JSON number.')

    try:
        if request.is_json:
            raw = request.get_data(cache=True)
            if ((request.environ.get('CONTENT_LENGTH') in (None, '') or request.environ.get('wsgi.input_terminated'))
                    and request.max_content_length is not None
                    and len(raw) >= request.max_content_length):
                raise RequestEntityTooLarge()
            if (request.environ.get('CONTENT_LENGTH') not in (None, '')
                    and request.content_length != len(raw)):
                raise ValueError('JSON request length does not match its body.')
            data = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite, parse_float=finite_float)
        else:
            data = None
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
        data = None
    if not isinstance(data, dict) or not set(data) <= set(allowed):
        raise BackupError('Expected a JSON object with supported backup fields.')
    return data


def credentials(data):
    result = {key: data[key] for key in ('password', 'recovery_key') if data.get(key) is not None}
    if len(result) > 1:
        raise BackupError('Use one backup password or one recovery key.')
    for value in result.values():
        try:
            valid = isinstance(value, str) and len(value) <= 1024 and len(value.encode('utf-8')) <= 1024
        except UnicodeError:
            valid = False
        if not valid:
            raise BackupError('Provide a bounded password or recovery key.')
    return result


@bp.errorhandler(BackupError)
def invalid_backup(error):
    return jsonify(error=str(error), code="backup_error"), 400, {"Cache-Control": "no-store"}


@bp.get("")
def list_backups():
    service = get_backup_service()
    recovery_id = None
    if service.journal.exists():
        try:
            recovery_id = service._recovery_record()["recovery_backup"]
        except BackupError:
            pass
    return jsonify(backups=service.list(), recovery_required=bool(service.gate.fault), recovery_backup=recovery_id,
                   backup_api_version=2), 200, {"Cache-Control": "no-store"}


@bp.post("")
@bp.post("/v2")
def create_backup():
    data = body({'format', 'password', 'include_recovery_key', 'browser_state'})
    kind = data.get('format', 'encrypted')
    include_key = data.get('include_recovery_key', False)
    if not isinstance(kind, str) or kind not in {'encrypted', 'zip'} or type(include_key) is not bool:
        raise BackupError('Choose encrypted or zip format and a boolean recovery-key option.')
    password = data.get('password')
    if kind == 'zip' and (password is not None or include_key):
        raise BackupError('A plaintext ZIP cannot protect a password or archive recovery key.')
    if kind == 'encrypted':
        BackupService._validate_password(password)
    service = get_backup_service()
    recovery_key = generate_recovery_key() if include_key else None
    result = service.create(data.get('browser_state'), password=password, recovery_key=recovery_key)
    if recovery_key is not None:
        result['recovery_key'] = recovery_key  # One response only; never stored in a receipt.
    return jsonify(result), 201


@bp.get("/<backup_id>/download")
def download_backup(backup_id):
    path = get_backup_service().archive_path(backup_id)
    response = send_file(path, as_attachment=True, download_name=path.name,
                         mimetype='application/octet-stream' if path.suffix in ('.alterabak', '.herbak') else 'application/zip', max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/restore/preview")
@bp.post("/v2/restore/preview")
def preview_restore():
    if request.is_json:
        data = body({'backup_id', 'restore_connections', 'password', 'recovery_key'})
        if type(data.get('restore_connections', False)) is not bool:
            raise BackupError('restore_connections must be a boolean.')
        connections = data.get("restore_connections") is True
        unlock = credentials(data)
        service = get_backup_service()
        source = service.archive_path(data.get("backup_id"))
    else:
        if (not set(request.form) <= {'restore_connections', 'password', 'recovery_key'} or
                set(request.files) != {'file'} or any(len(request.form.getlist(key)) != 1 for key in request.form) or
                len(request.files.getlist('file')) != 1):
            raise BackupError('Choose one backup file and supported restore fields.')
        source = request.files.get("file")
        if source is None:
            raise BackupError("Choose an encrypted backup or ZIP file first.")
        if request.form.get('restore_connections', 'false') not in {'true', 'false'}:
            raise BackupError('restore_connections must be true or false.')
        connections = request.form.get("restore_connections") == "true"
        unlock = credentials(request.form)
        service = get_backup_service()
    return jsonify(service.preview(source, restore_connections=connections, **unlock))








@bp.post("/restore/apply")
@bp.post("/v2/restore/apply")
def apply_restore():
    data = body({'token', 'confirm', 'browser_state', 'password', 'recovery_key', 'recovery_format', 'recovery_password'})
    if data.get("confirm") != "restore":
        raise BackupError("Preview the backup and confirm replacement before restoring.")
    service = get_backup_service()
    recovery_kind = data.get('recovery_format', 'encrypted')
    if not isinstance(recovery_kind, str) or recovery_kind not in {'encrypted', 'zip'}:
        raise BackupError('Choose encrypted or zip format for the recovery backup.')
    recovery_password = None
    if not service.gate.fault:
        if recovery_kind == 'encrypted':
            recovery_password = data.get('recovery_password')
            service._validate_password(recovery_password)
        elif data.get('recovery_password') is not None:
            raise BackupError('Remove the password or choose an encrypted recovery backup.')
    result = service.apply(data.get("token"), current_browser_state=data.get("browser_state"),
                           recovery_password=recovery_password, **credentials(data))
    current_app.logger.info("Restored Altera data; recovery archive %s", result["recovery_backup"]["id"])
    return jsonify(result)


@bp.get("/recovery")
def recovery_page():
    return render_template("recovery.html")
