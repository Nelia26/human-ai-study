"""Targeted repeatability audit. Does not contact Google or use real study data."""
import logging
import unittest
from unittest.mock import patch
import streamlit.config as config
import sheets_sync
import test_app

config.set_option('secrets.files', [])
logging.getLogger('streamlit.runtime.scriptrunner_utils.script_run_context').setLevel(logging.ERROR)

class SeededFlow(test_app.ExperimentTest):
    seed = 0
    fail_sheets = False

    def setUp(self):
        self.seed_patch = patch('secrets.randbits', return_value=self.seed)
        self.seed_patch.start()
        self.addCleanup(self.seed_patch.stop)
        if self.fail_sheets:
            self.failure_patch = patch.object(sheets_sync, 'sync_pending', side_effect=ConnectionError('offline test'))
            self.failure_patch.start()
            self.addCleanup(self.failure_patch.stop)
        super().setUp()

    def runTest(self):
        self._full_experiment(refresh=False)

    def shortDescription(self):
        return f'15 continuous trials; seed={self.seed}; Sheets failure={self.fail_sheets}'

suite = unittest.TestSuite()
for seed in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]:
    test = SeededFlow()
    test.seed = seed
    suite.addTest(test)
for seed in [1, 8]:
    test = SeededFlow()
    test.seed = seed
    test.fail_sheets = True
    suite.addTest(test)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
