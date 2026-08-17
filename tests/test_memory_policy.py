import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from rag_assistant import embeddings
from rag_assistant import parsers


class DesktopMemoryPolicyTests(unittest.TestCase):
    def tearDown(self):
        embeddings.release_embedding_models()
        parsers.release_parser_models()

    def test_embedding_cache_is_released_in_low_memory_mode(self):
        model = Mock()
        model.encode.return_value = np.asarray([[1.0, 0.0]], dtype="float32")
        with patch.object(embeddings, "embedding_model", return_value=model), patch.object(
            embeddings, "release_embedding_models"
        ) as release, patch.dict(os.environ, {"ATLAS_LOW_MEMORY": "1"}):
            result = embeddings.embed_query("Atlas", "local-model")

        self.assertEqual((2,), result.shape)
        release.assert_called_once_with()

    def test_parser_models_are_released_after_desktop_media_parse(self):
        with patch.object(parsers, "parse_image", return_value=[]), patch.object(
            parsers, "release_parser_models"
        ) as release, patch.dict(os.environ, {"ATLAS_LOW_MEMORY": "1"}):
            parsers.parse_file(parsers.Path("probe.png"))

        release.assert_called_once_with()

    def test_parser_models_remain_cached_in_docker_mode(self):
        with patch.object(parsers, "parse_image", return_value=[]), patch.object(
            parsers, "release_parser_models"
        ) as release, patch.dict(os.environ, {"ATLAS_LOW_MEMORY": "0"}):
            parsers.parse_file(parsers.Path("probe.png"))

        release.assert_not_called()

    def test_desktop_ocr_cannot_download_missing_weights(self):
        reader = Mock(return_value=object())
        fake_module = SimpleNamespace(Reader=reader)
        parsers._ocr_reader.cache_clear()
        with patch.dict(sys.modules, {"easyocr": fake_module}), patch.dict(
            os.environ, {"EASYOCR_DOWNLOAD_ENABLED": "0"}
        ):
            parsers._ocr_reader()

        self.assertFalse(reader.call_args.kwargs["download_enabled"])

    def test_desktop_whisper_uses_local_model_path(self):
        model = Mock(return_value=object())
        fake_module = SimpleNamespace(WhisperModel=model)
        parsers._whisper_model.cache_clear()
        with patch.dict(sys.modules, {"faster_whisper": fake_module}), patch.dict(
            os.environ,
            {"WHISPER_MODEL": "D:/Atlas/models/whisper", "HF_HUB_OFFLINE": "1"},
        ):
            parsers._whisper_model()

        model.assert_called_once_with(
            "D:/Atlas/models/whisper",
            device="cpu",
            compute_type="int8",
            local_files_only=True,
        )


if __name__ == "__main__":
    unittest.main()
