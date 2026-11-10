"""Run: python -m unittest -v test_app.py. All data goes to temporary folders."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

BASE = Path(__file__).resolve().parent


class ExperimentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Tests must never upload synthetic responses using the host's real key.
        secret_patch = patch.object(st, 'secrets', {})
        secret_patch.start()
        self.addCleanup(secret_patch.stop)
        self.env = patch.dict(os.environ, {'STUDY_DATA_DIR': self.tmp.name})
        self.env.start()
        self.app = AppTest.from_file(str(BASE / 'app.py'), default_timeout=15).run()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def click(self, label):
        matches = [b for b in self.app.button if b.label == label]
        self.assertEqual(len(matches), 1, label)
        matches[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.error)
        # Keep this SAME client for the entire experiment. Recreating it here
        # would hide the stale forms/headers that this regression must catch.
        if self.app.query_params.get('session'):
            s = self.state()
            if s['status'] == 'in_progress' and s['stage'] != 'demographics':
                self.assertEqual([h.value for h in self.app.header],
                                 [f'Task {s["index"] + 1} of 15'])
                progress, = self.app.get('progress')
                self.assertEqual(progress.proto.text, f'{s["index"]} of 15 tasks complete')

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

    def _full_experiment(self, refresh):
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
                self.assertEqual(len(self.app.radio), 1)
                self.assertEqual(self.app.radio[0].label, 'Your final answer')
                if refresh and ai_seen == 2:
                    token = self.app.query_params['session']
                    old_trial = self.state()['trial'].copy()
                    self.app = AppTest.from_file(str(BASE / 'app.py'), default_timeout=15)
                    self.app.query_params['session'] = token
                    self.app.run()
                    self.assertEqual(self.state()['trial'], old_trial)
                self.assertIsNone(self.app.radio[0].value)
                self.click('Submit Final Decision')
                self.assertTrue(self.app.warning)
                if ai_seen % 3 == 0:
                    final = next(x for x in task['options'] if x not in [ai, initial])
                elif ai_seen % 3 == 1:
                    final = initial
                else:
                    final = ai
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
            elif ai_seen % 3 == 2:
                self.assertEqual((row['accepted_ai'], row['switched'], row['advice_uptake']), ('1', '1', '1'))
            elif ai_seen % 3 == 0:
                self.assertEqual((row['accepted_ai'], row['switched'], row['advice_uptake']), ('0', '1', '0'))
            else:
                self.assertEqual((row['accepted_ai'], row['switched'], row['advice_uptake']), ('0', '0', '0'))
            self.app.run()  # Repeated reruns cannot duplicate a submitted trial.
            self.assertEqual(len(self.rows('results.csv')), trial)
        self.assertIn(ai_seen, [7, 8])
        self.assertEqual(self.state()['status'], 'completed')
        self.assertEqual(self.rows('participants.csv')[0]['completed_trials'], '15')
        self.assertEqual(len({r['task_id'] for r in self.rows('results.csv')}), 15)

    def test_full_experiment_continuous_client(self):
        self._full_experiment(refresh=False)

    def test_full_experiment_and_resume(self):
        self._full_experiment(refresh=True)

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

    def test_two_tabs_cannot_skip_final_or_rewrite_a_locked_decision(self):
        with patch('secrets.randbits', return_value=1):
            self.start()
        first = self.state()['schedule'][0]
        self.assertEqual(first['condition'], 'AI')
        task = first['task']
        token = self.app.query_params['session']
        other = AppTest.from_file(str(BASE / 'app.py'), default_timeout=15)
        other.query_params['session'] = token
        other.run()
        self.app.radio[0].set_value(task['options'][0])
        self.app.selectbox[0].select(6)
        self.click('Continue')
        self.assertEqual(self.state()['stage'], 'final')
        # The other tab still has the initial form from the previous revision.
        other.radio[0].set_value(task['options'][1])
        other.selectbox[0].select(8)
        next(b for b in other.button if b.label == 'Continue').click().run()
        self.assertFalse(other.exception)
        self.assertTrue(other.warning)
        self.assertEqual(self.state()['trial']['initial_choice'], task['options'][0])
        self.assertEqual(other.radio[0].label, 'Your final answer')
        other.radio[0].set_value(task['options'][2])
        next(b for b in other.button if b.label == 'Submit Final Decision').click().run()
        self.assertEqual(self.state()['stage'], 'ai_ratings')
        # The first tab's now-stale final form cannot change the locked answer.
        self.app.radio[0].set_value(task['options'][1])
        self.click('Submit Final Decision')
        self.assertTrue(self.app.warning)
        self.assertFalse(self.app.radio)
        self.assertEqual(self.state()['trial']['final_choice'], task['options'][2])
        refreshed = AppTest.from_file(str(BASE / 'app.py'), default_timeout=15)
        refreshed.query_params['session'] = token
        self.app = refreshed.run()
        self.assertFalse(self.app.radio)
        self.assertEqual(len(self.app.selectbox), 2)
        self.app.selectbox[0].select(7)
        self.app.selectbox[1].select(8)
        self.click('Save ratings and continue')
        row, = self.rows('results.csv')
        self.assertEqual(row['initial_choice'], task['options'][0])
        self.assertEqual(row['final_choice'], task['options'][2])
        self.assertEqual(row['switched'], '1')


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

    def test_stale_submissions_cannot_skip_or_rewrite_final_choice(self):
        m = self.module
        tasks, digest, cfg, consent = m.load_materials()
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'DATA', Path(d)), \
                patch.object(m.st, 'session_state', {}) as inputs:
            m.initialize_storage()
            state = m.new_state(tasks, digest, cfg, consent)
            ai_index = next(i for i, item in enumerate(state['schedule']) if item['condition'] == 'AI')
            state['schedule'][0], state['schedule'][ai_index] = state['schedule'][ai_index], state['schedule'][0]
            state.update(stage='start_trial', demographics={'age': 25})
            saved = m.persist('test-token', state, new=True)
            initial = m.begin_trial('test-token', saved)
            task = initial['schedule'][0]['task']
            inputs[m.widget_key(initial, 'choice')] = task['options'][0]
            inputs[m.widget_key(initial, 'confidence')] = 6
            m.on_submit('initial', 'test-token', initial)
            final = m.show_advice('test-token', m.restore('test-token'))
            self.assertEqual(final['stage'], 'final')
            self.assertNotIn('final_choice', final['trial'])
            m.on_submit('initial', 'test-token', initial)  # Duplicate old button event.
            self.assertEqual(m.restore('test-token'), final)
            inputs[m.widget_key(final, 'choice')] = task['options'][2]
            m.on_submit('final', 'test-token', final)
            locked = m.restore('test-token')
            self.assertEqual(locked['stage'], 'ai_ratings')
            self.assertEqual(locked['trial']['initial_choice'], task['options'][0])
            self.assertEqual(locked['trial']['final_choice'], task['options'][2])
            inputs[m.widget_key(final, 'choice')] = task['options'][1]
            m.on_submit('final', 'test-token', final)
            self.assertEqual(m.restore('test-token'), locked)

    def test_enrollment_keeps_resume_link_after_csv_export_failure(self):
        m = self.module
        tasks, digest, cfg, consent = m.load_materials()
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'DATA', Path(d)), \
                patch.object(m.st, 'query_params', {}) as params:
            m.initialize_storage()
            state = m.new_state(tasks, digest, cfg, consent)
            with patch.object(m, 'atomic_csv', side_effect=OSError('CSV temporarily unavailable')):
                with self.assertRaises(OSError):
                    m.persist_new_session('test-token', state)
            self.assertEqual(params.get('session'), 'test-token')
            self.assertEqual(m.restore('test-token')['participant_id'], state['participant_id'])
            m.initialize_storage()
            with open(Path(d) / 'participants.csv', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['participant_id'], state['participant_id'])

    def test_failed_enrollment_does_not_bind_an_invalid_resume_link(self):
        m = self.module
        tasks, digest, cfg, consent = m.load_materials()
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'DATA', Path(d)), \
                patch.object(m.st, 'query_params', {}) as params:
            m.initialize_storage()
            state = m.new_state(tasks, digest, cfg, consent)
            with patch.object(m, 'persist', side_effect=OSError('Database save failed')):
                with self.assertRaises(OSError):
                    m.persist_new_session('test-token', state)
            self.assertIsNone(m.restore('test-token'))
            self.assertNotIn('session', params)

    def test_wrong_condition_or_premature_ratings_are_rejected(self):
        m = self.module
        tasks, digest, cfg, consent = m.load_materials()
        state = m.new_state(tasks, digest, cfg, consent)
        state['schedule'][0]['condition'] = 'NO_AI'
        state['stage'] = 'final'
        with self.assertRaisesRegex(ValueError, 'assigned condition'):
            m.validate_stage(state)
        state['schedule'][0]['condition'] = 'AI'
        state['stage'] = 'ai_ratings'
        task = state['schedule'][0]['task']
        state['trial'] = dict(initial_choice=task['options'][0], confidence=6, advice_epoch=0)
        with self.assertRaisesRegex(ValueError, 'submitted final decision'):
            m.validate_stage(state)


if __name__ == '__main__':
    unittest.main()
