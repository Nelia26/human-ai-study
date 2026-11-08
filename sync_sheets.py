"""Researcher utility: python sync_sheets.py [--requeue-all]. Run from this folder."""
import argparse
import json

from filelock import FileLock

import app
import sheets_sync


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requeue-all', action='store_true',
                        help='Also queue all existing local records for backfill or a new sheet.')
    args = parser.parse_args()
    app.initialize_storage()
    settings = app.google_sheets_settings()
    if not settings.get('enabled', False):
        raise SystemExit('Enable google_sheets in .streamlit/secrets.toml first.')
    if args.requeue_all:
        with FileLock(str(app.DATA / 'study.lock'), timeout=30), app.connect() as db:
            with db:
                for (raw,) in db.execute('SELECT state FROM participants').fetchall():
                    row = app.participant_row(json.loads(raw))
                    sheets_sync.enqueue(db, 'participant', row['participant_id'], json.dumps(row))
                for (raw,) in db.execute('SELECT payload FROM trials').fetchall():
                    row = json.loads(raw)
                    key = json.dumps([row['participant_id'], str(row['task_id'])])
                    sheets_sync.enqueue(db, 'trial', key, raw)
    try:
        count = sheets_sync.sync_pending(app.connect, app.DATA, settings,
                                        app.PARTICIPANT_FIELDS, app.TASK_FIELDS)
    except Exception as exc:
        raise SystemExit(f'Sync failed ({type(exc).__name__}). Local records remain queued. '
                         'Check credentials, Sheet ID, sharing, API access, and tab headers.') from None
    with app.connect() as db:
        remaining = db.execute('SELECT COUNT(*) FROM sheets_outbox').fetchone()[0]
    print(f'Synced {count} records. Pending: {remaining}.')


if __name__ == '__main__':
    main()
