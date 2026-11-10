# Stage-flow fix and owner handoff

## Diagnosis

The supplied `app.py` has no branch that intentionally stops revision after task
2. It does, however, call `st.rerun()` inside every `persist()`, including saves
made while a form is being rendered. `start_trial` and `show_advice` each save and
rerun again. Trial headers are emitted before these automatic transitions. This
can interrupt multiple partial renders during a single submission, making stale
forms and headings a plausible explanation for the reported mixed screens.
Several rating inputs also reused the same key across different trials.

Confirmed reproduction: removing the old test helper's forced client recreation
makes the original app's continuous-session test fail with stale widget state.
The old helper recreated `AppTest` after every saved transition, hiding that
failure. The fix passes the same interaction sequence without that workaround.

The archive's database contains only a demographics checkpoint and zero completed
trials, so it does not preserve the specific reported task-2 incident. The exact
`removeChild` browser exception was not reproduced; its exact cause is therefore
unconfirmed. No such console error occurred during the fixed app's full Chrome
test. This is a confirmed correction to the rerun/state handling, not a claim
that every possible browser DOM exception has been diagnosed.

## Changes

- Form submissions use `on_click` callbacks. The callback commits state before
  Streamlit's normal top-to-bottom render. `persist()` returns the saved state;
  it never interrupts rendering with `st.rerun()`.
- Automatic `start_trial` and `show_advice` stages resolve before the title,
  progress, task header, or task form is drawn. Advice disclosure is recorded once.
- Every input/form key includes participant ID, trial index, stage, and field.
  An initial-answer widget cannot become a later trial's final-answer widget.
- Every submission checks its saved revision/index/stage. A late click from an
  old form cannot skip the final screen or rewrite an already locked decision.
- Stage validation rejects AI stages for NO_AI and rejects ratings before a
  final decision was explicitly submitted.
- `completed = index` drives progress; `completed + 1` drives the task header.
- All requested choice/reliance fields and local SQLite/CSV backup remain present.
  New enrollments record study version `answer-key-sheets-2.2`; existing saved
  schedules and submitted responses are preserved.

The paths are:

```text
AI:    start_trial → initial → show_advice → final → ai_ratings → next trial
NO_AI: start_trial → no_ai → no_ai_ratings → next trial
```

The initial AI answer and confidence are committed before advice. A final answer
is committed only by Submit Final Decision. Ratings cannot be used to change a
locked final answer. The next task starts only after ratings are saved.

This follows Streamlit's documented callback ordering: callbacks run before the
page rerenders. See [forms](https://docs.streamlit.io/develop/concepts/architecture/forms)
and [widget behavior](https://docs.streamlit.io/develop/concepts/architecture/widget-behavior).

## Follow-up review

Version 2.2 adds recovery of a committed enrollment's resume link after a CSV
export failure. The expanded checks pass 22 regression tests plus 14 complete
seeded sessions (210 trials), including six consecutive AI trials and persistent
Sheets failures. See [REVIEW_REPORT.md](REVIEW_REPORT.md) for the tested scope and
remaining live-Google dependency.

## Verification

On 9 November 2026, all **19 tests passed** with Streamlit 1.50.0 and gspread 6.2.1.

Run from the project folder:

```sh
python -m unittest -v
```

The suite covers a continuous 15-task client, a separate resume test, keeping an
answer, adopting advice, choosing a third option, all task headings/progress,
NO_AI missing values, stale submissions, premature ratings, local recovery,
Sheets retry/deduplication, concurrent participant updates, and credential format.
Test folders and generated test credentials are temporary. No Google calls are
made by these tests.

A separate real Chrome run completed all 15 trials without a reload: 8 AI and
7 NO_AI. Every AI final choice differed from its initial choice; all 15 trial
keys were unique. NO_AI advice, trust, accepted_ai, and advice_uptake were null.
The browser console reported no errors during that run. These test responses
are isolated outside the project and are not included in the release package.

## Google Sheets handoff for the original owner

The app can be configured on any machine. The researcher/host can do the setup
directly; the person helping with code does not need to hold the private key.

Your spreadsheet ID and account details are already in
`.streamlit/secrets.toml.example` and `GOOGLE_SHEETS_SETUP.md`. Use these output
tabs: **Participants** and **Trials**. SQLite/CSV stays active as backup.

The uploaded `.streamlit/secrets.toml.save` is not a working Streamlit secrets
file: it contains malformed JSON-like data and its private key fails local
validation. The 40-character value provided separately is a key identifier,
not an authentication credential. No valid private key was available to test
live Google access.

Ask the original owner to do one of the following on the actual host:

1. If they have an existing working service-account JSON, run the included
   helper below. If working Streamlit secrets already exist in deployment,
   retain them and ensure they use the documented nested section format.
2. If the JSON was lost, an authorized Google Cloud administrator can create a
   new JSON key for the same service account, subject to the organization's
   policy. Google does not allow re-downloading an old private key.
   [Google key instructions](https://docs.cloud.google.com/iam/docs/keys-create-delete)

Then, from the project folder on their machine:

```sh
pip install -r requirements.txt
python configure_sheets.py --key-file "/path/to/original-service-account.json"
python sync_sheets.py --check
streamlit run app.py
```

Share the spreadsheet as Editor with the JSON file's `client_email`. The uploaded
file identifies `human-ai-study@human-ai-study.iam.gserviceaccount.com`. The
spreadsheet ID is `1KRLiqUCVmwjY28YzVv5BhdYm956_kLcOKMKV6Xz2h5s`.

For Streamlit Community Cloud, paste the helper-generated TOML into the app's
Settings → Secrets instead of committing the key. A working key can stay with
the original host/owner; it need not be sent through chat.

## Short manual checklist

1. Start a fresh test participant. On **every AI trial**, submit an initial
   answer/confidence, then verify an advice panel and editable final radio list.
   Try keep, adopt advice, and third-option decisions, including after the second
   AI trial. Trust/autonomy must appear only after final submission.
2. On **NO_AI**, verify one answer/confidence form followed by autonomy only.
   There must be no advice panel, final-choice revision form, or trust question.
3. At `n` completed trials, verify progress says `n of 15` and the header says
   `Task n+1 of 15`. Finish without refreshing between trials.
4. With Google credentials configured, run `python sync_sheets.py --check`.
   Submit demographics and a complete test trial including ratings. Verify one
   Participants row and one Trials row. Refresh; verify no duplicate trial row.
5. Inspect local `data/study.sqlite3`, `participants.csv`, and `results.csv`.
   Local saving must continue if Google is unavailable; retry later with
   `python sync_sheets.py`. Use `--requeue-all` only to backfill existing records.

Live Google saving remains **unverified until the owner supplies/configures a
valid credential and checks an actual test row in the spreadsheet**.
