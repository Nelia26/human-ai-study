"""Optional service-account sync. SQLite remains the source of truth.

Only use one app deployment per spreadsheet. The shared file lock serializes
remote upserts; stable row keys make retries safe after an ambiguous timeout.
"""
from __future__ import annotations

from filelock import FileLock, Timeout


def normalize_spreadsheet_id(value):
    import re
    text = str(value).strip()
    if '/spreadsheets/d/' in text:
        text = text.split('/spreadsheets/d/', 1)[1]
    text = re.split(r'[/\?#]', text, maxsplit=1)[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]+', text):
        raise ValueError('Provide a Google spreadsheet ID or spreadsheet URL.')
    return text


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS sheets_outbox (
        kind TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY (kind, record_key))''')


def enqueue(db, kind, record_key, payload):
    """Call in the same SQLite transaction as the local response save."""
    db.execute('''INSERT INTO sheets_outbox VALUES (?, ?, ?)
        ON CONFLICT(kind, record_key) DO UPDATE SET payload=excluded.payload''',
        (kind, record_key, payload))


def open_spreadsheet(settings):
    # Lazy import means local-only mode works without Google dependencies.
    import gspread

    client = gspread.service_account_from_dict(
        dict(settings['service_account']),
        scopes=['https://www.googleapis.com/auth/spreadsheets'])
    client.set_timeout((5, 15))
    return client.open_by_key(normalize_spreadsheet_id(settings['spreadsheet_id']))


def upsert_records(book, title, fields, key_fields, records):
    import gspread
    from gspread.utils import rowcol_to_a1

    try:
        sheet = book.worksheet(title)
    except gspread.WorksheetNotFound:
        sheet = book.add_worksheet(title=title, rows=1000, cols=len(fields))
    values = sheet.get_all_values()
if not values or not any(str(cell).strip() for cell in values[0]):
        if sheet.col_count < len(fields):
            sheet.resize(cols=len(fields))
        sheet.update(range_name='A1', values=[list(fields)], value_input_option='RAW')
    elif list(values[0]) != list(fields):
        raise ValueError(
            f'{title}: header differs from the expected schema. '
            'Use a new empty tab or restore the original header.'
        )

    positions = [fields.index(key) for key in key_fields]
    existing = {}
    for number, row in enumerate(values[1:], 2):
        key = tuple(str(row[i]) if i < len(row) else '' for i in positions)
        if not any(key):
            continue
        if key in existing:
            raise ValueError(f'{title}: duplicate record keys; repair the sheet before syncing.')
        existing[key] = number

    updates, additions = [], []
    for record in records:
        key = tuple(str(record[field]) for field in key_fields)
        # RAW keeps free text literal (including text beginning with '=').
        row = [record.get(field) if record.get(field) is not None else '' for field in fields]
        if 'schedule_seed' in fields and record.get('schedule_seed') is not None:
            # Sheets numbers cannot represent every 63-bit seed exactly.
            row[fields.index('schedule_seed')] = str(record['schedule_seed'])
        if key in existing:
            number = existing[key]
            updates.append({'range': f'A{number}:{rowcol_to_a1(number, len(fields))}',
                            'values': [row]})
        else:
            additions.append(row)
    if updates:
        sheet.batch_update(updates, value_input_option='RAW')
    if additions:
        sheet.append_rows(additions, value_input_option='RAW')


def sync_pending(connect, data_dir, settings, participant_fields, task_fields):
    """Flush durable pending records. Exceptions leave unsynced records queued.

    Participant records are sent first. A compare-and-delete acknowledgement
    preserves any newer local participant update made during the HTTP request.
    """
    import json

    if not settings.get('enabled', False):
        return 0
    if settings.get('participants_tab', 'Participants') == settings.get('trials_tab', 'Trials'):
        raise ValueError('Participant and trial tabs must have different names.')
    try:
        with FileLock(str(data_dir / 'sheets.lock'), timeout=0):
            with connect() as db:
                pending = db.execute(
                    'SELECT kind, record_key, payload FROM sheets_outbox').fetchall()
            if not pending:
                return 0
            book = open_spreadsheet(settings)
            total = 0
            for kind, title, fields, keys in [
                ('participant', settings.get('participants_tab', 'Participants'),
                 participant_fields, ['participant_id']),
                ('trial', settings.get('trials_tab', 'Trials'),
                 task_fields, ['participant_id', 'task_id']),
            ]:
                batch = [item for item in pending if item[0] == kind]
                if not batch:
                    continue
                upsert_records(book, title, fields, keys,
                               [json.loads(item[2]) for item in batch])
                with FileLock(str(data_dir / 'study.lock'), timeout=30), connect() as db:
                    with db:
                        db.executemany('''DELETE FROM sheets_outbox
                            WHERE kind=? AND record_key=? AND payload=?''', batch)
                total += len(batch)
            return total
    except Timeout:
        # Another session owns the sync lock; that session is already flushing.
        return 0
