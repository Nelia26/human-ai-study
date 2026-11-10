"""Human–AI study. Run with: streamlit run app.py (Python 3.10+)."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import secrets
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

from filelock import FileLock, Timeout
import streamlit as st
import sheets_sync

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('STUDY_DATA_DIR', str(BASE / 'data'))).resolve()
TASK_FIELDS = [
    'participant_id', 'task_id', 'condition', 'ai_recommendation', 'ai_correct',
    'initial_choice', 'final_choice', 'switched', 'accepted_ai', 'confidence',
    'trust', 'autonomy', 'response_time', 'age', 'gender', 'education',
    'field_of_study', 'ai_experience', 'general_trust', 'trial_order',
    'initial_agreement', 'advice_uptake', 'initial_correct', 'final_correct',
    'correct_answer', 'initial_response_time', 'final_response_time',
    'ratings_response_time', 'trial_duration', 'task_started_at',
    'initial_submitted_at', 'advice_shown_at', 'final_submitted_at',
    'ratings_submitted_at', 'option_order', 'recommendation_source',
    'study_version', 'task_set_sha256', 'consent_version', 'consented_at',
    'schedule_seed', 'ai_trial_count',
]
PARTICIPANT_FIELDS = [
    'participant_id', 'status', 'created_at', 'consented_at', 'completed_at',
    'ended_at', 'consent_version', 'study_version', 'task_set_sha256',
    'age', 'gender', 'education', 'field_of_study', 'ai_experience',
    'general_trust', 'completed_trials', 'schedule_seed', 'ai_trial_count',
    'schedule',
]


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_materials():
    raw = (BASE / 'tasks.json').read_bytes()
    tasks = json.loads(raw)
    if not isinstance(tasks, list) or len(tasks) != 15:
        raise ValueError('tasks.json must contain exactly 15 tasks.')
    ids = set()
    for i, task in enumerate(tasks, 1):
        task.setdefault('task_id', i)
        tid = str(task['task_id'])
        if not tid or tid in ids:
            raise ValueError('Each task must have a unique task_id.')
        ids.add(tid)
        options = task.get('options')
        if (not isinstance(task.get('task'), str) or not task['task'].strip()
                or not isinstance(options, list) or len(options) < 2
                or any(not isinstance(x, str) or not x.strip() for x in options)
                or len(set(options)) != len(options)):
            raise ValueError(f'Invalid question/options for task {tid}.')
        if task.get('ai') not in options:
            raise ValueError(f'Task {tid} needs a fixed ai recommendation in options.')
        if task.get('correct_answer') is not None:
            if task['correct_answer'] not in options or not task.get('answer_criteria'):
                raise ValueError(f'Task {tid}: provide a valid correct_answer and answer_criteria.')
    config = json.loads((BASE / 'study_config.json').read_text(encoding='utf-8'))
    consent = (BASE / 'consent.md').read_text(encoding='utf-8')
    return tasks, hashlib.sha256(raw).hexdigest(), config, consent


@contextmanager
def connect():
    db = sqlite3.connect(DATA / 'study.sqlite3', timeout=30)
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
    finally:
        db.close()


def atomic_csv(path: Path, fields, rows):
    """Replace the whole CSV atomically; never expose a partially written file."""
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='',
                                         dir=DATA, delete=False) as f:
            name = f.name
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def export_csv(db):
    trials = [json.loads(r[0]) for r in db.execute(
        'SELECT payload FROM trials ORDER BY participant_id, trial_order')]
    people = []
    for (raw,) in db.execute('SELECT state FROM participants ORDER BY participant_id'):
        s = json.loads(raw)
        people.append(participant_row(s))
    atomic_csv(DATA / 'results.csv', TASK_FIELDS, trials)
    atomic_csv(DATA / 'participants.csv', PARTICIPANT_FIELDS, people)


def participant_row(state):
    row = {**state, **state.get('demographics', {}),
           'completed_trials': state['index'],
           'schedule': json.dumps(state['schedule'], ensure_ascii=False)}
    # Never send private resume tokens, in-flight decisions, or internal state.
    return {key: row.get(key) for key in PARTICIPANT_FIELDS}



def google_sheets_settings():
    try:
        settings = dict(st.secrets.get("google_sheets", {}))
        if settings:
            return settings
    except Exception:
        pass

    try:
        import toml
        with open("/etc/secrets/secrets.toml", "r", encoding="utf-8") as f:
            data = toml.load(f)
        return dict(data.get("google_sheets", {}))
    except FileNotFoundError:
        return {}



def sync_google_sheets(state=None):
    """Best-effort online backup; local persistence never depends on Google."""
    marker = (state['participant_id'], state['index'], state['status'],
              bool(state.get('demographics'))) if state else None
    new_trial = marker != st.session_state.get('_sheets_marker')
    # Network requests belong between trials, before the next trial timer starts.
    can_sync = state is None or state['stage'] in {
        'demographics', 'start_trial', 'completed', 'withdrawn'}
    if can_sync and (new_trial or time.time() >= st.session_state.get('_sheets_retry_at', 0)):
        try:
            settings = google_sheets_settings()
            sheets_sync.sync_pending(connect, DATA, settings, PARTICIPANT_FIELDS, TASK_FIELDS)
            st.session_state['_sheets_failed'] = False
        except ModuleNotFoundError as exc:
            import logging
            logging.warning('Google Sheets missing module: %s', exc.name)
            st.session_state['_sheets_failed'] = True
        except Exception as exc:
            import logging
            import traceback

            if isinstance(exc, ValueError):
                frame = traceback.extract_tb(exc.__traceback__)[-1]
                logging.warning(
                    'Google Sheets ValueError at %s:%s in %s',
                    frame.filename, frame.lineno, frame.name
                )
            else:
                logging.warning(
                    'Google Sheets backup pending (%s)',
                    type(exc).__name__
                )

            st.session_state['_sheets_failed'] = True
            st.session_state['_sheets_failed'] = True
        st.session_state['_sheets_retry_at'] = time.time() + 30
        st.session_state['_sheets_marker'] = marker
    if st.session_state.get('_sheets_failed'):
        st.warning('Online backup is temporarily unavailable. Responses continue to save locally.')
        if can_sync:
            st.button('Retry online backup', on_click=on_submit, args=('retry_sheets',))


def initialize_storage():
    DATA.mkdir(parents=True, exist_ok=True)
    with FileLock(str(DATA / 'study.lock'), timeout=30), connect() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS participants (
                token_hash TEXT PRIMARY KEY, participant_id TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trials (
                participant_id TEXT NOT NULL REFERENCES participants(participant_id),
                task_id TEXT NOT NULL, trial_order INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (participant_id, task_id));
        ''')
        sheets_sync.initialize(db)
        export_csv(db)  # Also repairs CSV snapshots after a prior export interruption.


def restore(token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        row = db.execute('SELECT state FROM participants WHERE token_hash=?',
                         (digest,)).fetchone()
    return json.loads(row[0]) if row else None


def persist(token, state, row=None, *, new=False):
    """Commit stage and trial together. Stale tabs cannot overwrite later stages."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    updated = copy.deepcopy(state)
    updated['revision'] += 1
    with FileLock(str(DATA / 'study.lock'), timeout=30), connect() as db:
        with db:
            if new:
                db.execute('INSERT INTO participants VALUES (?, ?, ?)',
                           (digest, updated['participant_id'], json.dumps(updated)))
            else:
                current = db.execute('SELECT state FROM participants WHERE token_hash=?',
                                     (digest,)).fetchone()
                if not current or json.loads(current[0])['revision'] != state['revision']:
                    raise ValueError('This session changed in another tab. Reload to continue.')
                db.execute('UPDATE participants SET state=? WHERE token_hash=?',
                           (json.dumps(updated), digest))
            if row:
                db.execute('INSERT INTO trials VALUES (?, ?, ?, ?)',
                           (row['participant_id'], str(row['task_id']),
                            row['trial_order'], json.dumps(row)))
                sheets_sync.enqueue(db, 'trial',
                    json.dumps([row['participant_id'], str(row['task_id'])]), json.dumps(row))
            if new or row or updated['stage'] == 'start_trial' or updated['status'] == 'withdrawn':
                sheets_sync.enqueue(db, 'participant', updated['participant_id'],
                                    json.dumps(participant_row(updated)))
        export_csv(db)
    return updated


def new_state(tasks, task_hash, config, consent):
    seed = secrets.randbits(63)
    rng = random.Random(seed)
    count = rng.choice([7, 8])
    conditions = ['AI'] * count + ['NO_AI'] * (15 - count)
    rng.shuffle(conditions)
    order = copy.deepcopy(tasks)
    rng.shuffle(order)
    schedule = []
    for task, condition in zip(order, conditions):
        rng.shuffle(task['options'])
        schedule.append({'task': task, 'condition': condition})
    return dict(participant_id='P' + secrets.token_hex(12).upper(), revision=0,
                status='in_progress', stage='demographics', index=0,
                created_at=utc(), consented_at=utc(), consent_version=config['consent_version'],
                study_version=config['study_version'], task_set_sha256=task_hash,
                config=config, consent_text=consent, schedule_seed=seed,
                ai_trial_count=count, schedule=schedule, demographics={}, trial={})


class InputError(ValueError):
    """A safe message that can be displayed to the participant."""


def widget_key(s, field):
    """Separate every participant, trial, stage, and input."""
    return f'{s["participant_id"]}:{s["index"]}:{s["stage"]}:{field}'


def submitted_value(s, field):
    return st.session_state.get(widget_key(s, field))


def require_rating(value):
    if type(value) is not int or not 1 <= value <= 10:
        raise InputError('Please answer each required rating question.')
    return value


def require_choice(value, task):
    if value not in task['options']:
        raise InputError('Please select an answer.')
    return value


def validate_stage(s):
    """Never render an AI stage for NO_AI, or ratings before a locked decision."""
    total, index, stage = len(s['schedule']), s['index'], s['stage']
    if type(index) is not int or not 0 <= index <= total:
        raise ValueError('Invalid completed-trial count.')
    if s['status'] == 'completed':
        if index != total or stage != 'completed':
            raise ValueError('Inconsistent completion checkpoint.')
        return
    if s['status'] == 'withdrawn':
        return
    if stage == 'demographics':
        if index != 0:
            raise ValueError('Demographics cannot interrupt a trial.')
        return
    if index == total:
        raise ValueError('No active trial exists.')
    item = s['schedule'][index]
    condition = item['condition']
    allowed = {'AI': {'start_trial', 'initial', 'show_advice', 'final', 'ai_ratings'},
               'NO_AI': {'start_trial', 'no_ai', 'no_ai_ratings'}}
    if condition not in allowed or stage not in allowed[condition]:
        raise ValueError('Stage does not match the assigned condition.')
    if stage in {'show_advice', 'final', 'ai_ratings', 'no_ai_ratings'}:
        if s['trial'].get('initial_choice') not in item['task']['options']:
            raise ValueError('Initial choice is missing from the checkpoint.')
        require_rating(s['trial'].get('confidence'))
    if stage in {'final', 'ai_ratings'} and 'advice_epoch' not in s['trial']:
        raise ValueError('AI advice has not been disclosed.')
    if stage in {'ai_ratings', 'no_ai_ratings'}:
        if (s['trial'].get('final_choice') not in item['task']['options']
                or 'final_submitted_at' not in s['trial']):
            raise ValueError('Ratings require a submitted final decision.')


def on_submit(action, token=None, rendered=None):
    """Run once, before Streamlit renders the next page. Never call st.rerun here."""
    try:
        if action == 'decline':
            st.session_state['declined'] = True
            return
        if action == 'reset':
            st.query_params.clear()
            return
        if action == 'retry_sheets':
            st.session_state['_sheets_retry_at'] = 0
            return
        if action == 'enroll':
            if not st.session_state.get('consent_agree'):
                raise InputError('Consent is required to begin. You may close this page without participating.')
            tasks, task_hash, config, consent = load_materials()
            token = secrets.token_urlsafe(32)
            persist_new_session(token, new_state(tasks, task_hash, config, consent))
            return
        s = restore(token)
        if not s or any(s[key] != rendered[key] for key in ('revision', 'index', 'stage')):
            raise InputError('This session changed in another tab. The latest saved step is shown below.')
        validate_stage(s)
        if s['status'] != 'in_progress':
            raise InputError('This participation has already ended.')
        if action == 'withdraw':
            if not st.session_state.get(widget_key(s, 'end')):
                raise InputError('Select the confirmation before ending participation.')
            s.update(status='withdrawn', stage='withdrawn', ended_at=utc())
        elif action == 'demographics' and s['stage'] == 'demographics':
            age = submitted_value(s, 'age')
            values = {key: submitted_value(s, key) for key in
                      ('gender', 'education', 'ai_experience', 'general_trust')}
            if age is None or not 18 <= age <= 120 or any(values[key] is None for key in
                                                        ('gender', 'education', 'ai_experience')):
                raise InputError('Enter your age and select gender, education, and AI experience.')
            values.update(age=int(age), field_of_study=(submitted_value(s, 'field_of_study') or '').strip() or None)
            s.update(demographics=values, stage='start_trial')
        else:
            item = s['schedule'][s['index']]
            task, condition, t = item['task'], item['condition'], s['trial']
            stage = s['stage']
            now, stamp = time.time(), utc()
            if action == 'initial' and stage == 'initial' and condition == 'AI':
                choice = require_choice(submitted_value(s, 'choice'), task)
                confidence = require_rating(submitted_value(s, 'confidence'))
                t.update(initial_choice=choice, confidence=confidence,
                         initial_submitted_at=stamp, initial_response_time=now - t['start_epoch'])
                s['stage'] = 'show_advice'
            elif action == 'final' and stage == 'final' and condition == 'AI':
                choice = require_choice(submitted_value(s, 'choice'), task)
                t.update(final_choice=choice, final_submitted_at=stamp,
                         final_response_time=now - t['advice_epoch'],
                         response_time=now - t['start_epoch'], ratings_epoch=now)
                s['stage'] = 'ai_ratings'
            elif action == 'no_ai' and stage == 'no_ai' and condition == 'NO_AI':
                choice = require_choice(submitted_value(s, 'choice'), task)
                confidence = require_rating(submitted_value(s, 'confidence'))
                elapsed = now - t['start_epoch']
                t.update(initial_choice=choice, final_choice=choice, confidence=confidence,
                         initial_submitted_at=stamp, final_submitted_at=stamp,
                         initial_response_time=elapsed, response_time=elapsed, ratings_epoch=now)
                s['stage'] = 'no_ai_ratings'
            elif action == 'ratings' and stage in {'ai_ratings', 'no_ai_ratings'}:
                autonomy = require_rating(submitted_value(s, 'autonomy'))
                trust = require_rating(submitted_value(s, 'trust')) if condition == 'AI' else None
                row = make_row(s, task, condition, trust, autonomy)
                s.update(index=s['index'] + 1, trial={}, stage='start_trial')
                if s['index'] == len(s['schedule']):
                    s.update(status='completed', stage='completed', completed_at=stamp)
                persist(token, s, row)
                return
            else:
                raise InputError('That action is no longer available. Use the current step below.')
        persist(token, s)
    except InputError as exc:
        st.session_state['_flow_warning'] = str(exc)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, Timeout):
        import logging
        logging.exception('Could not save study submission')
        st.session_state['_flow_error'] = ('This step could not be saved. Your last saved progress is preserved. '
                                           'Reload to retry or contact the study team.')


def submit_button(label, action, token, s, *, primary=True):
    st.form_submit_button(label, type='primary' if primary else 'secondary',
                          on_click=on_submit, args=(action, token, s))


def rating(label, key, low, high, *, optional=False):
    st.caption(f'1 = {low} · 10 = {high}' + (' · Optional' if optional else ''))
    return st.selectbox(label, list(range(1, 11)), index=None, key=key,
                        placeholder='Select a rating')


def demographics(token, s):
    st.header('About you')
    st.write('Please complete these questions before starting the tasks. Do not enter your name.')
    with st.form(widget_key(s, 'form')):
        st.number_input('Age (years)', min_value=18, max_value=120, value=None, step=1,
                        key=widget_key(s, 'age'))
        st.selectbox('Gender', ['Woman', 'Man', 'Nonbinary', 'Another gender', 'Prefer not to say'],
                     index=None, key=widget_key(s, 'gender'))
        st.selectbox('Highest education level completed', [
            'Primary or lower secondary', 'Upper secondary', 'Vocational / technical',
            'Bachelor’s degree or equivalent', 'Master’s degree or equivalent',
            'Doctoral degree or equivalent', 'Other', 'Prefer not to say'],
            index=None, key=widget_key(s, 'education'))
        st.text_input('Field of study / discipline (optional)', max_chars=100,
                      key=widget_key(s, 'field_of_study'))
        st.selectbox('Previous experience using AI tools',
                     ['Low — rarely or never', 'Medium — occasionally', 'High — frequently'],
                     index=None, key=widget_key(s, 'ai_experience'))
        if s['config'].get('collect_general_trust', True):
            rating('In general, how much do you trust AI systems?', widget_key(s, 'general_trust'),
                   'Not at all', 'Completely', optional=True)
        submit_button('Start tasks', 'demographics', token, s)


def begin_trial(token, s):
    s['trial'] = dict(start_epoch=time.time(), task_started_at=utc())
    s['stage'] = 'initial' if s['schedule'][s['index']]['condition'] == 'AI' else 'no_ai'
    return persist(token, s)


def initial_decision(token, s, task):
    st.subheader('Your independent decision')
    st.write('Choose an answer before seeing any recommendation.')
    with st.form(widget_key(s, 'form')):
        st.radio('Your initial answer', task['options'], index=None, key=widget_key(s, 'choice'))
        rating('How confident are you in this initial answer?', widget_key(s, 'confidence'),
               'Not at all confident', 'Completely confident')
        submit_button('Continue', 'initial', token, s)


def show_advice(token, s):
    # Initial choice was already committed by the callback. Mark disclosure once.
    s['trial'].update(advice_shown_at=utc(), advice_epoch=time.time())
    s['stage'] = 'final'
    return persist(token, s)


def final_decision(token, s, task):
    st.subheader('Review the recommendation')
    st.write(f'Your initial answer: **{s["trial"]["initial_choice"]}**')
    with st.container(border=True):
        st.write('**AI recommendation**')
        st.write(task['ai'])
    st.write('You may keep your initial answer, choose the recommendation, or choose another option.')
    with st.form(widget_key(s, 'form')):
        st.radio('Your final answer', task['options'], index=None, key=widget_key(s, 'choice'))
        submit_button('Submit Final Decision', 'final', token, s)


def no_ai_decision(token, s, task):
    st.subheader('Your decision')
    with st.form(widget_key(s, 'form')):
        st.radio('Your answer', task['options'], index=None, key=widget_key(s, 'choice'))
        rating('How confident are you in this answer?', widget_key(s, 'confidence'),
               'Not at all confident', 'Completely confident')
        submit_button('Submit Decision', 'no_ai', token, s)


def make_row(s, task, condition, trust, autonomy):
    t = s['trial']
    ai = task['ai'] if condition == 'AI' else None
    correct = task.get('correct_answer')
    initial_agreement = int(t['initial_choice'] == ai) if ai else None
    row = {key: s.get(key) for key in TASK_FIELDS if key in s}
    row.update({key: t[key] for key in TASK_FIELDS if key in t})
    row.update(s['demographics'])
    row.update(task_id=task['task_id'], condition=condition, trial_order=s['index'] + 1,
        ai_recommendation=ai, ai_correct=int(ai == correct) if ai and correct else None,
        correct_answer=correct,
        initial_correct=int(t['initial_choice'] == correct) if correct else None,
        final_correct=int(t['final_choice'] == correct) if correct else None,
        switched=int(t['initial_choice'] != t['final_choice']) if ai else 0,
        accepted_ai=int(t['final_choice'] == ai) if ai else None,
        initial_agreement=initial_agreement,
        advice_uptake=int(t['final_choice'] == ai) if ai and not initial_agreement else None,
        trust=trust if ai else None, autonomy=autonomy,
        ratings_response_time=time.time() - t['ratings_epoch'],
        trial_duration=time.time() - t['start_epoch'], ratings_submitted_at=utc(),
        option_order=json.dumps(task['options'], ensure_ascii=False),
        recommendation_source=task.get('recommendation_source',
            'Fixed recommendation supplied in tasks.json; generation provenance unverified') if ai else None)
    return row


def post_decision_ratings(token, s, task, condition):
    st.subheader('Reflect on this decision')
    st.write(f'Your submitted answer: **{s["trial"]["final_choice"]}**')
    st.caption('Your decision has been saved and cannot be changed.')
    with st.form(widget_key(s, 'form')):
        if condition == 'AI':
            st.write(f'Recommendation: **{task["ai"]}**')
            rating('How much did you trust this specific recommendation?', widget_key(s, 'trust'),
                   'Not at all', 'Completely')
        rating('How much control did you feel you had over your final decision?', widget_key(s, 'autonomy'),
               'No control at all', 'Complete control')
        submit_button('Save ratings and continue', 'ratings', token, s)


def consent_screen(tasks, task_hash, config, consent):
    st.header('Before you begin')
    st.markdown(consent)
    st.write('**Study contact:** ' + config['researcher_contact'])
    st.write('**Ethics information:** ' + config['ethics_information'])
    st.write('**Data and participation policy:** ' + config['data_policy'])
    with st.form('consent'):
        st.checkbox('I am at least 18, have read the information, and voluntarily agree to participate.',
                    key='consent_agree')
        st.form_submit_button('Agree and continue', type='primary', on_click=on_submit, args=('enroll',))
    st.button('I do not agree', on_click=on_submit, args=('decline',))


def persist_new_session(token, s):
    try:
        saved = persist(token, s, new=True)
    except (OSError, sqlite3.Error, Timeout):
        # SQLite can commit before the derived CSV snapshot fails. Preserve the
        # private link to that checkpoint so a reload repairs CSVs and resumes it.
        try:
            committed = restore(token)
        except (OSError, sqlite3.Error):
            committed = None
        if committed:
            st.query_params['session'] = token
        raise
    st.query_params['session'] = token
    return saved


def run():
    st.set_page_config(page_title='Decision-making study', layout='centered')
    tasks, task_hash, config, consent = load_materials()
    initialize_storage()
    token = st.query_params.get('session')
    s = restore(token) if token else None
    # Resolve automatic stages before rendering ANY study content. No interrupted
    # page or nested rerun can leave an old header/form alongside the new state.
    if s:
        validate_stage(s)
    sync_google_sheets(s)
    if s and s['status'] == 'in_progress':
        if s['stage'] == 'start_trial':
            s = begin_trial(token, s)
        if s['stage'] == 'show_advice':
            s = show_advice(token, s)
        validate_stage(s)
    st.title('Decision-making study')
    if st.session_state.get('_flow_warning'):
        st.warning(st.session_state.pop('_flow_warning'))
    if st.session_state.get('_flow_error'):
        st.error(st.session_state.pop('_flow_error'))
    if token and s is None:
        st.error('This study link is invalid or its session is unavailable. Contact the study team.')
        st.button('Return to the study start', on_click=on_submit, args=('reset',))
        return
    if s is None:
        if st.session_state.get('declined'):
            st.write('You have not enrolled. You may close this page.')
            return
        consent_screen(tasks, task_hash, config, consent)
        return
    st.caption('Participant code: ' + s['participant_id'])
    completed, total = s['index'], len(s['schedule'])
    if s['status'] == 'completed':
        st.progress(1.0, text=f'{completed} of {total} tasks complete')
        st.success('Your responses have been saved. Thank you for participating.')
        st.write('You may close this page.')
        return
    if s['status'] == 'withdrawn':
        st.info('Your participation has ended. Previously submitted data remain saved.')
        st.write(s['config']['researcher_contact'])
        return
    with st.expander('Pause or end participation'):
        st.write('Bookmark this private URL to resume on this server. Do not share it. '
                 'Time spent away is included in response times.')
        end = st.checkbox('I want to end my participation permanently.', key=widget_key(s, 'end'))
        st.button('End participation', disabled=not end, on_click=on_submit, args=('withdraw', token, s))
    if s['stage'] == 'demographics':
        demographics(token, s)
        return
    st.progress(completed / total, text=f'{completed} of {total} tasks complete')
    st.header(f'Task {completed + 1} of {total}')
    item = s['schedule'][completed]
    task, condition = item['task'], item['condition']
    with st.container(key='trial_screen'):
        st.write(task['task'])
        stage = s['stage']
        if stage == 'initial':
            initial_decision(token, s, task)
        elif stage == 'final':
            final_decision(token, s, task)
        elif stage == 'no_ai':
            no_ai_decision(token, s, task)
        elif stage in ('ai_ratings', 'no_ai_ratings'):
            post_decision_ratings(token, s, task, condition)
        else:
            raise ValueError('Unknown experiment stage. Contact the study team.')


if __name__ == '__main__':
    try:
        run()
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, TimeoutError, Timeout):
        import logging
        logging.exception('Study could not continue')
        st.error('The study could not save or load this step. Reload this page to retry. '
                 'If the problem persists, contact the study team. Your private URL resumes saved progress.')
