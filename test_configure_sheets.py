"""Validate credential formatting locally with a generated, disposable test key."""
import unittest

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import toml

import configure_sheets


class CredentialFormatTest(unittest.TestCase):
    def test_generated_toml_preserves_real_pem_newlines(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        info = dict(type='service_account', project_id='offline-test',
                    private_key_id='offline-test-key', private_key=pem,
                    client_email='offline-test@offline-test.iam.gserviceaccount.com',
                    client_id='123', token_uri='https://oauth2.googleapis.com/token')
        output = configure_sheets.render_secrets(info, configure_sheets.DEFAULT_SHEET + '/edit?gid=0')
        parsed = toml.loads(output)['google_sheets']
        self.assertEqual(parsed['service_account'], info)
        self.assertEqual(parsed['spreadsheet_id'], configure_sheets.DEFAULT_SHEET)
        self.assertTrue(parsed['enabled'])

    def test_bad_key_is_rejected_before_rendering_secrets(self):
        info = dict(client_email='offline@example.com', token_uri='https://oauth2.googleapis.com/token',
                    private_key='not a PEM private key')
        with self.assertRaises(ValueError):
            configure_sheets.render_secrets(info, configure_sheets.DEFAULT_SHEET)


if __name__ == '__main__':
    unittest.main()
