import sqlite3
import unittest

import faiss
import numpy as np

from desktop.validate_backend import validate_faiss, validate_fts5


class DesktopBackendValidationTests(unittest.TestCase):
    def test_sqlite_has_working_fts5(self):
        validate_fts5()
        self.assertTrue(sqlite3.sqlite_version)

    def test_faiss_has_working_native_index(self):
        validate_faiss(faiss, np)


if __name__ == "__main__":
    unittest.main()
