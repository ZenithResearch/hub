from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from services.stt_http import main


class SttHttpServiceTests(unittest.TestCase):
    def test_request_model_must_be_allowlisted(self) -> None:
        with patch.dict(os.environ, {"STT_ALLOWED_WHISPER_MODELS": "tiny,base"}, clear=False):
            with self.assertRaises(HTTPException) as raised:
                main._model_name("large")

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("not allowed", str(raised.exception.detail))

    def test_request_model_uses_allowlisted_env_default(self) -> None:
        with patch.dict(os.environ, {"STT_ALLOWED_WHISPER_MODELS": "tiny,base", "STT_WHISPER_MODEL": "base"}, clear=False):
            self.assertEqual(main._model_name(None), "base")


if __name__ == "__main__":
    unittest.main()
