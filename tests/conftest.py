"""Make the project root importable and share the fixture loader.

The signal/scoring/capital modules under test are PURE (no database, no
network, no Streamlit), so these tests run anywhere with just pytest:

    python -m pytest tests
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture
def load_fixture():
    def _load(name):
        with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    return _load
