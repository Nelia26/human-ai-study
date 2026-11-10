# Follow-up review — 9 November 2026

The experiment flow and local saving pass the checks below. No remaining
final-decision lock or task-numbering failure was found in the tested sessions.
Live Google Sheets saving still requires valid credentials and an actual test
upload; it is not claimed to be connected or verified end to end.

## What was checked

| Check | Result |
| --- | --- |
| Expanded regression suite | 22 passed |
| Complete continuous-client sessions | 14 passed; 210 completed trials |
| Different randomized schedules | Seeds 0–11 |
| Six consecutive AI trials | Seed 8; every final decision remained editable |
| Sheets unavailable throughout the experiment | Seeds 1 and 8; both completed all 15 trials locally |
| Two tabs submitting stale initial/final forms | Stale events rejected; saved choices preserved |
| Refresh after final choice, before ratings | Final choice preserved and locked; ratings remain available |
| Progress and task number | Checked after every submitted screen in full-session tests |
| Keep answer / adopt advice / select third option | Choice, switching, agreement, and uptake fields match |
| NO_AI | No advice/trust UI; advice/trust fields blank; one decision only |
| SQLite/CSV saving | One completed row per participant/task; refresh does not duplicate it |
| Google upload timeout after remote commit | Offline worksheet simulation updates the existing key on retry |
| Newer participant update during sync | Remains queued after the older upload is acknowledged |
| Invalid headers and bad keys | Rejected without deleting queued records or writing invalid secrets |
| Original task answer key | 11 correct and 4 incorrect recommendations; keys remain valid options |

The earlier real Chrome check completed 15 trials (8 AI, 7 NO_AI) without a
reload. Every AI answer was revised, and the browser console recorded no errors.
The follow-up tests above use Streamlit AppTest, not a claim of 14 additional
Chrome browser sessions. The exact previously reported `removeChild` exception
has not been reproduced or conclusively attributed to one cause.

## One additional bug found and fixed

The enrollment database save and CSV snapshot export are separate steps. If the
database commit succeeded but CSV export failed, `persist_new_session()` raised
before binding the new private resume URL. The participant checkpoint existed,
but the browser could not resume it.

Version `answer-key-sheets-2.2` checks whether that enrollment was committed and
retains its private resume link before reporting the failure. A later reload
repairs the CSV snapshots from SQLite and resumes the same participant. A failure
before the database save does not bind an invalid resume link. Both outcomes have
regression tests.

Existing schedules and answer keys are preserved. The earlier source/ZIP is
backed up when applying this review update. No real participant data or private
credentials were used by these checks or added to the ZIP.

## Reproduce

From the project directory, after installing requirements:

```sh
python -m unittest -v
python review_stress.py
```

The first command runs the 22 regression tests. The second runs the 14 seeded
full-session checks. The stress script disables Streamlit secret-file loading;
its two failure runs simulate an unavailable sync service. Test data use temporary
directories, and the tests do not contact Google.
The AppTest fixtures also replace Streamlit secrets with an empty test mapping,
so the regression command cannot use the owner's real Sheets credentials to
upload synthetic responses after the host is configured.

Verified locally with Streamlit 1.50.0 and gspread 6.2.1. The main project recommends
Python 3.10 or newer; the available test interpreter was Python 3.9.6. Other
Streamlit versions allowed by the requirement range were not all individually
tested.

## Remaining setup and limits

- The original owner/host must configure a valid service-account key, enable
  Sheets API, and share the supplied spreadsheet with that account as Editor.
  The source package contains the Sheet ID and a template, not a usable key.
- Run `python sync_sheets.py --check`, then complete a test participant/trial and
  confirm rows in Participants and Trials. The check command alone does not
  prove that response writes have succeeded.
- Sync failures retain data in the local queue. There is no idle background
  worker: later eligible app activity or `python sync_sheets.py` retries it.
- Keep the SQLite data directory on persistent storage and use one deployment
  per spreadsheet, as already described in the setup guide.

See [GOOGLE_SHEETS_SETUP.md](GOOGLE_SHEETS_SETUP.md) for the exact owner handoff.
