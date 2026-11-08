# Google Sheets setup

The app uses a **Google service account** through `gspread`. Participants do not
need Google accounts. No Apps Script deployment or public webhook is needed.
Local SQLite/CSV saving stays enabled, including when Sheets is disabled or fails.

## 1. Get the service-account JSON key

1. Open [Google Cloud Console](https://console.cloud.google.com/), create a project
   or select an existing project.
2. Under **APIs & Services → Library**, enable **Google Sheets API** in that project.
   This implementation opens an existing spreadsheet by ID with the Sheets scope;
   it does not require Google Drive API or a Drive-wide OAuth scope.
3. Open **IAM & Admin → Service Accounts → Create service account**. Give it a
   name such as `human-ai-study`. No project-level IAM role is needed just to
   access a sheet shared with this account.
4. Open that account → **Keys → Add key → Create new key → JSON**. Download the key.
   If your institution disables service-account key creation, ask its administrator
   for an approved service account/key rather than using an API key.
5. Keep this JSON private. Its `client_email` identifies the service account;
   its `private_key` is the secret that authorizes access.

## 2. Create and share the spreadsheet

1. Create a new Google spreadsheet under your research account.
2. Click **Share**. Add the exact `client_email` from the JSON file as an **Editor**.
   Keep **General access: Restricted**. Participants do not need access to the sheet.
3. Copy the spreadsheet ID from its URL:

   `https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`

   Use only `SPREADSHEET_ID`, not the full URL and not the numeric `gid` of a tab.
4. You can leave the default Sheet1 tab alone. The app creates **Participants** and
   **Trials**, including headers, on its first sync. Alternatively create empty tabs
   with those exact names. Do not insert custom headers or edit their structure.

## 3. Configure Streamlit secrets

Install the updated dependencies from the project folder:

```sh
pip install -r requirements.txt
```

For local use, copy `.streamlit/secrets.toml.example` to
`.streamlit/secrets.toml`. Fill in `spreadsheet_id` and the service-account values
from the downloaded JSON. The example lists all necessary fields. Its placeholder
values are not working credentials. Leave `enabled = true` to activate syncing.

The JSON key contains escaped newlines (`\n`) in `private_key`. In the example's
triple-single-quoted TOML string, paste the **decoded key with actual line breaks**,
including the BEGIN/END lines. Do not paste the entire raw JSON object as TOML.
You can alternatively use a TOML double-quoted string containing the JSON key's
`\n` escapes. Both forms must decode to a real multiline PEM key.

For **Streamlit Community Cloud**, open your app's **Settings → Secrets** and paste
the complete filled TOML there instead of uploading the secrets file to GitHub.
Restart the app after adding or changing secrets. Run local commands from this
project folder so Streamlit finds `.streamlit/secrets.toml`.

Neither the original JSON key nor `secrets.toml` belongs in version control.
The real secrets file is ignored by `.gitignore`; the example is safe to commit.

To disable online saving, omit the secrets file or set:

```toml
[google_sheets]
enabled = false
```

## 4. Verify with a test participant

```sh
streamlit run app.py
```

1. Enroll a test participant and submit demographics. Check that a **Participants**
   row appears with age, gender, education, field of study, AI experience and general
   trust. A consent-only row may appear earlier with demographics still blank.
2. Finish one task **including the trust/autonomy ratings**. Check **Trials** for
   its row. All fields requested for the study are included, plus the existing
   timing, assignment, and provenance metadata. Times are in seconds.
3. Finish another task and refresh. There should be one participant row and one
   row per completed task. Participant status/count update as the study progresses.
4. In an AI trial, `ai_correct` is 1 or 0 by exact comparison to `correct_answer`.
   In NO_AI trials it is blank, because advice was not shown. Human correctness
   remains recorded in both conditions. Missing optional values are blank cells.

## What happens if Google is unavailable?

The app commits each completed trial and its pending sync entry to SQLite in the
same transaction, then updates the local CSVs. The next Streamlit run immediately
attempts the upload. Demographics, completion, and withdrawal also queue a current
participant snapshot. Initial and final choices of an incomplete trial remain in
the local checkpoint; the full trial row uploads after ratings are submitted.

A failed upload keeps the local record queued. The page shows a generic backup
warning; a retry button is available on untimed screens such as the completion
page. Automatic uploads run only before a task timer starts or on other untimed
screens. Eligible page activity retries after 30 seconds; new demographics or a
newly completed trial also triggers a retry. Network calls do not run inside a
timed task. There is no background worker when the app is idle. To flush pending
records from the server, run:

```sh
python sync_sheets.py
```

To import existing local records, repair a deleted remote row, or fill a **new**
spreadsheet after changing `spreadsheet_id`, run:

```sh
python sync_sheets.py --requeue-all
```

The utility uses the same secrets and `STUDY_DATA_DIR` as the app. Previously
synced rows are not automatically resent after a Sheet ID change; use
`--requeue-all`. Historical responses keep their original frozen answer keys;
this update does not retroactively label older sessions.

Participant rows are matched by `participant_id`; trials by the pair
`participant_id` + `task_id`. Retry after an upload timeout updates those keys
rather than appending duplicate records. A local file lock serializes syncing
across sessions on this server. Use **one deployment per spreadsheet**; separate
hosts do not share that lock. Avoid manually sorting/editing raw tabs while the
study is running; use a separate analysis tab or a copy.

Keep the local `data/` directory on persistent storage. Sheets backs up submitted
rows, but it is not a replacement for the SQLite resume database. Community Cloud
local disks can be reset on redeploy; lost checkpoints cannot be restored from
these Sheets rows. Pending uploads also require the local database to survive.

## Troubleshooting

- **SpreadsheetNotFound / PermissionError:** verify Sheet ID and Editor sharing
  with the JSON file's exact `client_email`.
- **APIError:** check Sheets API is enabled in the key's project, that the key
  remains active, and that quota/access restrictions permit writing. Queued data
  can be retried later.
- **Malformed key / RefreshError / credentials error:** check the copied fields,
  private-key newlines, and whether the key was revoked; restart after fixing.
- **Header mismatch / ValueError:** use empty tabs or restore the exact original
  header. The app refuses to write into an incompatible schema.
- **Pending stays nonzero:** finish/retry after other active sessions, or run the
  utility again after fixing the underlying configuration. Server logs record
  the exception class without printing credentials or response contents.

Before recruitment, reflect the chosen Google storage location and researcher
access/retention policy in the study's existing consent and configuration text.

References: [gspread service-account authentication](https://docs.gspread.org/en/latest/oauth2.html),
[Streamlit secrets management](https://docs.streamlit.io/develop/concepts/connections/secrets-management),
[Community Cloud secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).
