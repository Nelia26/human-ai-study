# Human–AI decision-making study

The updated project includes a frozen answer key for all 15 scenario tasks and
optional Google Sheets saving alongside the existing SQLite/CSV backup.
See [GOOGLE_SHEETS_SETUP.md](GOOGLE_SHEETS_SETUP.md) for the complete credentials,
sharing, deployment, retry, and backfill instructions.

## Run

To update an existing installation, replace the project source/configuration files
with those in this package and install the updated requirements. **Keep your
existing `data/` directory and real `.streamlit/secrets.toml`.** The package omits
the original archive's virtual environment and participant data; it does not
change or delete them. SQLite adds the sync queue automatically. To upload older
local records as well, follow the setup guide's `--requeue-all` instructions.

Use Python 3.10 or newer (also tested here with Python 3.9 and Streamlit 1.50).
Run from this folder so Streamlit loads the included theme:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL printed by Streamlit. For remote participants, deploy this
folder on a **single server with persistent local storage**, expose the app over
HTTPS, and share the base URL without a `session` query parameter. Set
`STUDY_DATA_DIR` to the mounted persistent directory. The server needs write
permission there. Do not expose that directory as static web content.

A container restart or redeploy must retain this directory. Ephemeral hosting,
including an unconfigured Community Cloud filesystem, is not durable research
storage. This version is for one server process/deployment using a shared local
filesystem; multiple hosts need a shared transactional database and a revised
export design. Back up the database and both CSVs through researcher-only access.
No researcher-wide results download is exposed to participants.

## Before recruiting participants

Edit `consent.md` and `study_config.json` with the real investigator/contact,
ethics approval or exemption, recruitment/eligibility rules, compensation,
data controller, hosting/storage location, researcher access, retention period,
and withdrawal/deletion procedure. The included copy is a pilot template and
makes no claim of ethics approval. The configured eligibility is adults 18+.
Increment consent and study versions after protocol changes. Obtain the required
institutional approvals before using the protocol with participants.

## Answer key and advice accuracy

All 15 tasks now have `task_id`, `correct_answer`, and `answer_criteria`. Each key
is exactly one existing option. Scenario wording now states the constraints or
success criterion needed to identify the best listed action; the options are
unchanged. These are scenario-specific decision rubrics, not universal factual
claims about the best action in every real-world setting. Review and freeze the
rubric before collecting study data.

AI advice matches the key on **11/15 tasks (73.3%)** and differs on **4/15 (26.7%)**.
The deliberately incorrect recommendations are on task IDs **2, 4, 10, 13**.
Correctness was determined from each scenario, then four recommendations were
changed to create the requested manipulation. `recommendation_source` explicitly
identifies fixed, researcher-selected study stimuli; nothing is generated live.
Neither answer keys nor correctness feedback are shown during participation.

The ratio applies to the full task set. The existing random assignment gives each
participant 7 or 8 AI trials, so the percentage of correct advice *actually shown*
varies by participant. It is not individually constrained to 70–80%.

New enrollments use study version `answer-key-sheets-2.0` and a new task-set hash.
Previously enrolled sessions retain their original frozen tasks, advice, and keys
(including missing keys). Keep pilot and updated-protocol data separate using
`study_version` and `task_set_sha256`.

## Participant flow

1. Read the consent information and affirm adult eligibility and agreement.
   Declining creates no participant record.
2. Provide age, gender, education, AI experience; field of study and general AI
   trust are optional. Gender/education include a prefer-not-to-say response.
3. Complete all 15 tasks, each exactly once. AI trials collect an independent
   choice and confidence, save them, then reveal advice. Participants may keep,
   adopt, or choose another option. The final answer is saved and locked before
   trust and autonomy are collected on a separate screen.
4. NO_AI trials collect one answer and confidence, save and lock them, then ask
   autonomy. No advice or trust item is rendered.
5. Save ratings to complete each trial. A progress bar counts completed trials.
   A completion message appears only after all 15 rows have been saved.

All answer/rating inputs start unselected. Ratings are single items on anchored
1–10 response scales; no psychometric validation is implied. Confidence refers
to the independent judgment in both conditions. Trust and autonomy are
**post-decision** measures. They are temporally separated from choice submission
but are not antecedent causal predictors; reverse causality remains possible.
AI trials contain more stages than NO_AI trials, so timing and autonomy contrasts
also reflect procedural differences.

## Randomization and repeated measures

For each participant, choose 7 or 8 AI trials with equal probability, then shuffle
that condition list independently of shuffled task order. Shuffle answer options
once per task and keep their order fixed across stages. Freeze this schedule,
its random seed, task text, recommendation, consent copy and configuration in the
participant checkpoint. Refresh does not randomize it again.

This is a randomized within-participant design, approximately 1:1 per participant
and in expectation across participants. It does **not** guarantee exact sample-wide
or task-wide counterbalance. Record any required block/counterbalance scheme in a
revised protocol before recruitment rather than changing assignments mid-study.
Each result row includes participant_id, task_id, trial_order, condition and seed.
Use models that account for participant and task clustering, with planned order,
learning/fatigue and carryover controls as appropriate. Rows are not independent
participants. The app does not perform inferential analysis or power analysis.

## Files and persistence

Optional online backup writes current participant records to **Participants** and
completed trial records to **Trials**. A durable `sheets_outbox` table queues
uploads in the same SQLite transaction as the local save. Failures do not block
participation, and retries upsert by stable keys. See the setup guide for limits
and recovery commands. Private resume tokens are never uploaded.

The app writes to `data/` by default:

- `study.sqlite3`: authoritative participant checkpoints and completed trial rows.
  Includes saved initial choices and locked final choices for incomplete trials.
- `results.csv`: one row per **completed participant × task**, updated after each
  completed trial. Blank cells represent unavailable or inapplicable values.
- `participants.csv`: one row per consented participant, with status, consent,
  demographics, completed trial count and assignment schedule. Use this for
  attrition accounting. A consented participant who abandons their tab remains
  `in_progress`; timeout/dropout classification must be specified in analysis.
- `study.lock`: serializes writes and CSV snapshot generation on this server.

SQLite commits the checkpoint and completed row together. CSVs are generated via
atomic replacement under the same lock. If an export fails after a database
commit, the next successful load regenerates the CSV snapshots. The two CSVs
are individually atomic; copy them after activity has stopped for a consistent
researcher export. Never edit live CSVs: regeneration replaces edits.

A cryptographically random private resume token is placed in the URL; only its
hash is stored as the lookup key. Participant IDs do not grant resume access.
Bookmarking that URL allows refresh/reconnection and resumption, including
between initial and final choices. Anyone with the private URL can access that
session. Avoid public/shared devices and leaking query strings in server logs.
A new visit to the base URL is a new enrollment; this does not enforce one person
per enrollment. The optional End participation control records a withdrawal and
prevents resumption. It does not delete previously collected data.

## Data dictionary

| Fields | Meaning |
| --- | --- |
| participant_id, task_id | Stable clustering keys; task_id defaults to original tasks.json position, independent of randomized order |
| condition | AI or NO_AI |
| trial_order | Display sequence, 1–15 |
| ai_recommendation | Fixed advice shown; blank in NO_AI |
| ai_correct | 1/0 only if AI shown and a verified correct_answer exists; otherwise blank |
| initial_choice | Actual pre-advice answer in AI; the single answer in NO_AI |
| final_choice | Locked final answer |
| switched | 1 if AI final differs from initial; 0 otherwise, including NO_AI |
| accepted_ai | Requested legacy name: 1 if final matches advice, 0 if not; blank in NO_AI. This measures agreement, not necessarily adoption |
| initial_agreement | 1/0 pre-advice match; blank in NO_AI |
| advice_uptake | Among initially disagreeing AI trials, 1 if participant switches to advice, otherwise 0; blank when already agreeing or NO_AI |
| confidence | Independent-decision confidence, 1–10, measured before advice in AI |
| trust | Post-decision trust in that specific recommendation, 1–10; blank in NO_AI |
| autonomy | Post-decision perceived control, 1–10, both conditions |
| general_trust | Optional dispositional trust in AI, separate from trial trust |
| age, gender, education, field_of_study, ai_experience | Baseline demographics repeated on trial rows |
| correct_answer, initial_correct, final_correct | Scenario key and binary human correctness; blank only for historical/unkeyed tasks |
| response_time | Seconds from task-stage initialization to final answer submission; excludes subsequent trust/autonomy ratings |
| initial_response_time | Seconds from initialization to initial AI answer or single NO_AI decision, including confidence entry |
| final_response_time | Seconds from advice disclosure-stage initialization to final AI submission; blank in NO_AI |
| ratings_response_time | Seconds from final submission to ratings submission |
| trial_duration | Seconds from initialization to ratings submission |
| *_at | ISO 8601 UTC stage timestamps |
| option_order | JSON list of displayed answers |
| recommendation_source | Provenance if supplied; otherwise explicitly unverified |
| study_version, consent_version, consented_at | Protocol and consent tracking |
| task_set_sha256 | Hash of tasks.json bytes at enrollment |
| schedule_seed, ai_trial_count | Reproducibility and allocation metadata |

Timings use server wall-clock timestamps and include network latency, reloads,
and pauses. They are not browser render or active-attention measurements. A
stage timestamp precedes client rendering by a small server/network delay.
Do not compare a two-stage AI response_time to NO_AI as pure deliberation time.

Use agreement among initially agreeing trials separately from advice uptake
among initially disagreeing trials. `switched=1, accepted_ai=0` identifies a
change to a different nonrecommended option. Use the frozen scenario answer key to distinguish beneficial and harmful changes;
interpret that correctness within the stated scenario constraints.

## Verify

```sh
python -m unittest -v test_app.py
```

Tests use Streamlit AppTest and temporary storage; they exercise consent,
required fields, every trial, advice disclosure, keep/adopt/other choices,
post-decision ratings, NO_AI missing values, refresh, deduplication, withdrawal,
randomization and answer-key correctness. Google Sheets tests use fake worksheets and never
contact Google or upload study data. Run the complete suite with:

```sh
python -m unittest -v
```
