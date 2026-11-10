"""Create Streamlit secrets from an original Google service-account JSON key."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from google.oauth2.service_account import Credentials

from sheets_sync import normalize_spreadsheet_id

DEFAULT_SHEET = '1KRLiqUCVmwjY28YzVv5BhdYm956_kLcOKMKV6Xz2h5s'


def render_secrets(info, spreadsheet):
    # Validate locally; never make an HTTP request or print the private key.
    Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    sheet_id = normalize_spreadsheet_id(spreadsheet)
    lines = ['[google_sheets]', 'enabled = true',
             f'spreadsheet_id = {json.dumps(sheet_id)}',
             'participants_tab = "Participants"', 'trials_tab = "Trials"', '',
             '[google_sheets.service_account]']
    for key in ('type', 'project_id', 'private_key_id', 'private_key', 'client_email',
                'client_id', 'auth_uri', 'token_uri', 'auth_provider_x509_cert_url',
                'client_x509_cert_url', 'universe_domain'):
        if key in info:
            lines.append(f'{key} = {json.dumps(info[key])}')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', type=Path, required=True,
                        help='Path to the original Google service-account JSON download.')
    parser.add_argument('--sheet', default=DEFAULT_SHEET, help='Spreadsheet URL or ID.')
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).resolve().parent / '.streamlit/secrets.toml')
    parser.add_argument('--overwrite', action='store_true', help='Replace an existing secrets file.')
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        raise SystemExit('Secrets file already exists. Use --overwrite only if you intend to replace it.')
    try:
        info = json.loads(args.key_file.read_text(encoding='utf-8'))
        contents = render_secrets(info, args.sheet)
    except Exception as exc:
        raise SystemExit(f'Invalid service-account JSON/key ({type(exc).__name__}). '
                         'Use the original unedited Google JSON download; no secrets were written.') from None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=args.output.parent,
                                         delete=False) as handle:
            name = handle.name
            os.chmod(name, 0o600)
            handle.write(contents)
        os.replace(name, args.output)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
    print(f'Validated the key locally and wrote {args.output}.')
    print('Share the spreadsheet as Editor with: ' + info['client_email'])
    print('Then run: python sync_sheets.py --check')


if __name__ == '__main__':
    main()
