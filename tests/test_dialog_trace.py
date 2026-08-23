"""Dialog trace helper."""

from __future__ import annotations

import logging
import unittest
from unittest import mock


class DialogTraceTests(unittest.TestCase):
    def test_logs_without_window(self) -> None:
        from checkmate.dialog_trace import dlg_trace

        with mock.patch("checkmate.dialog_trace._log") as log:
            dlg_trace("unit", extra=1)
        log.info.assert_called_once()
        msg = log.info.call_args[0][0]
        self.assertIn("[dlg-trace] unit", msg)
        self.assertIn("extra=1", msg)
