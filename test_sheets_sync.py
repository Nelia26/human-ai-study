"""Offline Sheets integration tests. No credentials or network requests required."""
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import gspread

import app
import sheets_sync


class FakeWorksheet:
    def __init__(self, cols):
        self.col_count = cols
        self.values = []
        self.fail_after_append = False

    def get_all_values(self):
        return [[str(value) for value in row] for row in copy.deepcopy(self.values)]

    def resize(self, *, cols):
        self.col_count = cols

    def update(self, *, range_name, values, value_input_option):
        assert range_name == 'A1' and value_input_option == 'RAW'
        self.values = copy.deepcopy(values)

    def batch_update(self, data, *, value_input_option):
        assert value_input_option == 'RAW'
        for item in data:
            number = int(re.match(r'A(\d+):', item['range']).group(1))
            self.values[number - 1] = copy.deepcopy(item['values'][0])

    def append_rows(self, rows, *, value_input_option):
        assert value_input_option == 'RAW'
        self.values.extend(copy.deepcopy(rows))
        if self.fail_after_append:
            self.fail_after_append = False
            raise TimeoutError('Server committed but response was lost')


class FakeBook:
    def __init__(self):
        self.tabs = {}

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, *, title, rows, cols):
        self.tabs[title] = FakeWorksheet(cols)
        return self.tabs[title]


class SheetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch = patch.object(app, 'DATA', Path(self.tmp.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        app.initialize_storage()
        self.book = FakeBook()
        self.settings = {'enabled': True}

    def queue(self, kind, key, record):
        with app.connect() as db:
            with db:
                sheets_sync.enqueue(db, kind, key, json.dumps(record))

    def pending(self):
        with app.connect() as db:
            return db.execute('SELECT kind, record_key, payload FROM sheets_outbox').fetchall()

    def sync(self):
        with patch.object(sheets_sync, 'open_spreadsheet', return_value=self.book):
            return sheets_sync.sync_pending(app.connect, app.DATA, self.settings,
                                            app.PARTICIPANT_FIELDS, app.TASK_FIELDS)

    def records(self, title):
        values = self.book.tabs[title].get_all_values()
        return [dict(zip(values[0], row)) for row in values[1:]]

    def test_real_saved_participant_and_trial_payloads(self):
        tasks, digest, config, consent = app.load_materials()
        with patch.object(app.st, 'rerun'):
            app.persist('private-test-token', app.new_state(tasks, digest, config, consent), new=True)
            state = app.restore('private-test-token')
            state['demographics'] = dict(age=25, gender='Woman', education='Bachelor',
                field_of_study='=literal text', ai_experience='High', general_trust=7)
            state['stage'] = 'start_trial'
            app.persist('private-test-token', state)
            state = app.restore('private-test-token')
            task = next(t for t in tasks if t['ai'] != t['correct_answer'])
            state['trial'] = dict(initial_choice=task['correct_answer'], final_choice=task['ai'],
                                 confidence=6, start_epoch=0, ratings_epoch=0)
            row = app.make_row(state, task, 'AI', 7, 8)
            state['index'] = 1
            app.persist('private-test-token', state, row)
        self.assertEqual(len(self.pending()), 2)  # Latest participant + completed trial.
        self.assertEqual(self.sync(), 2)
        person, = self.records('Participants')
        trial, = self.records('Trials')
        self.assertEqual(person['age'], '25')
        self.assertEqual(person['completed_trials'], '1')
        self.assertEqual(person['field_of_study'], '=literal text')
        self.assertEqual(person['schedule_seed'], str(state['schedule_seed']))
        self.assertEqual((trial['ai_correct'], trial['initial_correct'], trial['final_correct']),
                         ('0', '1', '0'))
        self.assertEqual(set(trial), set(app.TASK_FIELDS))
        self.assertNotIn('private-test-token', json.dumps(self.book.tabs['Participants'].values))
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.sync(), 0)
        # Completion/withdrawal snapshots update the same participant row.
        state.update(status='withdrawn', ended_at=app.utc())
        self.queue('participant', state['participant_id'], app.participant_row(state))
        self.sync()
        person, = self.records('Participants')
        self.assertEqual(person['status'], 'withdrawn')
        self.assertEqual(len(self.records('Trials')), 1)

    def test_network_failure_retains_queue_then_retry_deduplicates(self):
        trial = {'participant_id': 'P1', 'task_id': 2, 'schedule_seed': 123, 'final_choice': 'B'}
        self.queue('trial', 'P1/2', trial)
        sheet = self.book.add_worksheet(title='Trials', rows=100, cols=len(app.TASK_FIELDS))
        sheet.fail_after_append = True
        with self.assertRaises(TimeoutError):
            self.sync()
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(len(self.records('Trials')), 1)  # Google already wrote it.
        self.assertEqual(self.sync(), 1)
        self.assertEqual(len(self.records('Trials')), 1)  # Retry updated it.
        self.assertEqual(self.pending(), [])

    def test_newer_participant_snapshot_survives_acknowledgement(self):
        old = {'participant_id': 'P1', 'status': 'in_progress', 'schedule_seed': 123}
        new = {**old, 'status': 'completed'}
        self.queue('participant', 'P1', old)
        real_upsert = sheets_sync.upsert_records

        def concurrent_update(*args):
            real_upsert(*args)
            self.queue('participant', 'P1', new)

        with patch.object(sheets_sync, 'upsert_records', side_effect=concurrent_update):
            self.sync()
        self.assertEqual(len(self.pending()), 1)
        self.sync()
        person, = self.records('Participants')
        self.assertEqual(person['status'], 'completed')
        self.assertEqual(self.pending(), [])

    def test_disabled_never_connects_and_bad_header_keeps_records(self):
        self.queue('trial', 'P1/2', {'participant_id': 'P1', 'task_id': 2, 'schedule_seed': 123})
        with patch.object(sheets_sync, 'open_spreadsheet') as connect:
            sheets_sync.sync_pending(app.connect, app.DATA, {}, [], [])
            connect.assert_not_called()
        sheet = self.book.add_worksheet(title='Trials', rows=100, cols=5)
        sheet.values = [['wrong header']]
        with self.assertRaisesRegex(ValueError, 'header'):
            self.sync()
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(sheet.values, [['wrong header']])

    def test_remote_failure_does_not_block_local_app(self):
        self.queue('trial', 'P1/2', {'participant_id': 'P1', 'task_id': 2})
        with patch.object(app, 'google_sheets_settings', return_value=self.settings), \
                patch.object(sheets_sync, 'open_spreadsheet', side_effect=ConnectionError), \
                patch.object(app.st, 'session_state', {}), \
                patch.object(app.st, 'warning') as warning, patch.object(app.st, 'button', return_value=False):
            app.sync_google_sheets()  # No exception escapes to the participant flow.
            warning.assert_called_once()
        self.assertEqual(len(self.pending()), 1)

    def test_sync_does_not_run_during_a_timed_trial(self):
        state = dict(participant_id='P1', index=1, status='in_progress',
                     stage='initial', demographics={'age': 25})
        with patch.object(app.st, 'session_state', {}), \
                patch.object(sheets_sync, 'sync_pending') as sync:
            app.sync_google_sheets(state)
            sync.assert_not_called()

    def test_demographics_upload_is_not_delayed_by_previous_sync(self):
        state = dict(participant_id='P1', index=0, status='in_progress',
                     stage='demographics', demographics={})
        with patch.object(app.st, 'session_state', {}), \
                patch.object(app.time, 'time', return_value=100), \
                patch.object(app, 'google_sheets_settings', return_value=self.settings), \
                patch.object(sheets_sync, 'sync_pending') as sync:
            app.sync_google_sheets(state)
            state.update(stage='start_trial', demographics={'age': 25})
            app.sync_google_sheets(state)
            self.assertEqual(sync.call_count, 2)


if __name__ == '__main__':
    unittest.main()
