"""Run: python -m unittest -v test_app.py. All data goes to temporary folders."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

BASE = Path(__file__).resolve().parent


class ExperimentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'STUDY_DATA_DIR': self.tmp.name})
        self.env.start()
        self.app = AppTest.from_file(str(BASE / 'app.py'), default_timeout=15).run()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def click(self, label):
        before = Path(self.tmp.name, 'study.sqlite3').read_bytes()
        matches = [b for b in self.app.button if b.label == label]
        self.assertEqual(len(matches), 1, label)
        matches[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.error)
        after = Path(self.tmp.name, 'study.sqlite3').read_bytes()
        if before != after or label == 'I do not agree':
            # AppTest 1.50 retains stale widget nodes on explicit multi-stage reruns.
            # Recreate the client after a committed transition; also stress durable resume.
            params = dict(self.app.query_params)
            declined = label == 'I do not agree'
            self.app = AppTest.from_file(str(BASE / 'app.py'), default_timeout=15)
            self.app.query_params.update(params)
            if declined:
                self.app.session_state['declined'] = True
            self.app.run()
            self.assertFalse(self.app.exception)
            self.assertFalse(self.app.error)

    def start(self):
        self.click('Agree and continue')
        self.assertTrue(self.app.warning)
        self.assertEqual(self.rows('participants.csv'), [])
        self.app.checkbox[0].check()
        self.click('Agree and continue')
        self.click('Start tasks')
        self.assertTrue(self.app.warning)
        self.app.number_input[0].set_value(24)
        for widget, value in zip(self.app.selectbox, [
            'Woman', 'Bachelor’s degree or equivalent', 'Medium — occasionally']):
            widget.select(value)
        self.click('Start tasks')

    def state(self):
        import sqlite3
        with sqlite3.connect(Path(self.tmp.name) / 'study.sqlite3') as db:
            return json.loads(db.execute('SELECT state FROM participants').fetchone()[0])

    def rows(self, filename):
        with open(Path(self.tmp.name) / filename, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))

    def test_full_experiment_and_resume(self):
        self.start()
        schedule = self.state()['schedule']
        ai_seen = 0
        for trial, item in enumerate(schedule, 1):
            s = self.state()
            task, condition = item['task'], item['condition']
            self.assertEqual(s['index'], trial - 1)
            self.assertFalse(self.app.info)  # No advice before the initial submission.
            self.assertTrue(all(w.value is None for w in self.app.selectbox))
            ai = task['ai']
            other = next(x for x in task['options'] if x != ai)
            # First AI: already agrees; second: adopts; third: changes to another non-AI option.
            initial = ai if ai_seen == 0 and condition == 'AI' else other
            self.app.radio[0].set_value(initial)
            self.app.selectbox[0].select(6)
            if condition == 'AI':
                self.assertFalse(any(ai == x.value for x in self.app.markdown))
                self.click('Continue')
                ai_seen += 1
                self.assertEqual(self.state()['trial']['initial_choice'], initial)
                self.assertEqual(self.state()['stage'], 'final')
                token = self.app.query_params['session']
                # Refresh preserves the initial choice, schedule and advice-disclosure time.
                old_trial = self.state()['trial'].copy()
                self.app = AppTest.from_file(str(BASE / 'app.py'), default_timeout=15)
                self.app.query_params['session'] = token
                self.app.run()
                self.assertEqual(self.state()['trial'], old_trial)
                self.assertIsNone(self.app.radio[0].value)
                self.click('Submit Final Decision')
                self.assertTrue(self.app.warning)
                final = next(x for x in task['options'] if x not in [ai, initial]) if ai_seen == 3 else ai
                self.app.radio[0].set_value(final)
                self.click('Submit Final Decision')
                self.assertFalse(self.app.radio)  # Answer locked before trust/autonomy.
                self.app.selectbox[0].select(7)
                self.app.selectbox[1].select(8)
            else:
                self.assertEqual(len(self.app.selectbox), 1)
                self.click('Submit Decision')
                self.assertEqual(len(self.app.selectbox), 1)  # Autonomy only; no trust.
                self.app.selectbox[0].select(8)
            self.click('Save ratings and continue')
            rows = self.rows('results.csv')
            self.assertEqual(len(rows), trial)
            row = rows[-1]
            self.assertEqual(row['trial_order'], str(trial))
            self.assertEqual(row['correct_answer'], task['correct_answer'])
            self.assertEqual(row['initial_correct'], str(int(initial == task['correct_answer'])))
            self.assertEqual(row['final_correct'],
                             str(int(row['final_choice'] == task['correct_answer'])))
            expected_ai = str(int(ai == task['correct_answer'])) if condition == 'AI' else ''
            self.assertEqual(row['ai_correct'], expected_ai)
            self.assertEqual(row['confidence'], '6')
            self.assertGreaterEqual(float(row['response_time']), 0)
            self.assertEqual(row['age'], '24')
            if condition == 'NO_AI':
                for k in ['ai_recommendation', 'trust', 'accepted_ai', 'advice_uptake', 'advice_shown_at']:
                    self.assertEqual(row[k], '', k)
                self.assertEqual(row['initial_choice'], row['final_choice'])
                self.assertEqual(row['switched'], '0')
            elif ai_seen == 1:
                self.assertEqual((row['accepted_ai'], row['switched'], row['advice_uptake']), ('1', '0', ''))
            elif ai_seen == 2:
                self.assertEqual((row['accepted_ai'], row['switched'], row['advice_uptake']), ('1', '1', '1'))
            elif ai_seen == 3:
                self.assertEqual((row['accepted_ai'], row['switched'], row['advice_uptake']), ('0', '1', '0'))
            self.app.run()  # Repeated reruns cannot duplicate a submitted trial.
            self.assertEqual(len(self.rows('results.csv')), trial)
        self.assertIn(ai_seen, [7, 8])
        self.assertEqual(self.state()['status'], 'completed')
        self.assertEqual(self.rows('participants.csv')[0]['completed_trials'], '15')
        self.assertEqual(len({r['task_id'] for r in self.rows('results.csv')}), 15)

    def test_decline_and_invalid_resume(self):
        self.click('I do not agree')
        self.assertEqual(self.rows('participants.csv'), [])
        self.app.query_params['session'] = 'invalid-token'
        self.app.run()
        self.assertTrue(self.app.error)
        self.assertFalse(self.app.radio)

    def test_withdrawal_preserves_partial_initial(self):
        self.start()
        self.app.checkbox[0].check()
        self.click('End participation')
        self.assertEqual(self.state()['status'], 'withdrawn')
        self.assertEqual(self.rows('results.csv'), [])
        self.assertEqual(self.rows('participants.csv')[0]['status'], 'withdrawn')


class RowLogicTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('study_app', BASE / 'app.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_objective_answer_key_and_ai_error(self):
        tasks, digest, cfg, consent = self.module.load_materials()
        state = self.module.new_state(tasks, digest, cfg, consent)
        state['trial'] = dict(initial_choice='A', final_choice='B', confidence=5,
                              start_epoch=0, ratings_epoch=0)
        task = dict(task_id=1, options=['A', 'B', 'C'], ai='B', correct_answer='A')
        row = self.module.make_row(state, task, 'AI', 6, 7)
        self.assertEqual((row['ai_correct'], row['initial_correct'], row['final_correct']), (0, 1, 0))
        task['correct_answer'] = 'B'
        self.assertEqual(self.module.make_row(state, task, 'AI', 6, 7)['ai_correct'], 1)

    def test_randomization_and_answer_key(self):
        tasks, digest, cfg, consent = self.module.load_materials()
        self.assertEqual(sum(t['ai'] == t['correct_answer'] for t in tasks), 11)
        for task in tasks:
            self.assertIn(task['correct_answer'], task['options'])
            self.assertTrue(task['answer_criteria'].strip())
        for _ in range(30):
            s = self.module.new_state(tasks, digest, cfg, consent)
            self.assertIn(s['ai_trial_count'], [7, 8])
            self.assertEqual(len({x['task']['task_id'] for x in s['schedule']}), 15)
            self.assertEqual(sum(x['condition'] == 'AI' for x in s['schedule']), s['ai_trial_count'])

    def test_stale_update_rejected_and_failed_csv_export_recovers(self):
        m = self.module
        tasks, digest, cfg, consent = m.load_materials()
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'DATA', Path(d)), patch.object(m.st, 'rerun'):
            m.initialize_storage()
            original = m.new_state(tasks, digest, cfg, consent)
            m.persist('test-token', original, new=True)
            saved = m.restore('test-token')
            self.assertEqual(saved['revision'], 1)
            with self.assertRaises(ValueError):
                m.persist('test-token', original)  # Another tab has already advanced.
            saved.update(status='withdrawn', stage='withdrawn')
            with patch.object(m, 'atomic_csv', side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):
                    m.persist('test-token', saved)
            self.assertEqual(m.restore('test-token')['status'], 'withdrawn')
            m.initialize_storage()  # Repairs CSV from committed database state.
            with open(Path(d) / 'participants.csv', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['status'], 'withdrawn')


if __name__ == '__main__':
    unittest.main()
