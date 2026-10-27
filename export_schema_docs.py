#!/usr/bin/env python
"""Regenerate docs/DATA_DICTIONARY.md from the schema module (single source of truth)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from segmentation_gen import schema as S  # noqa: E402

path = os.path.join(os.path.dirname(__file__), "..", "docs", "DATA_DICTIONARY.md")
with open(path, "w", encoding="utf-8") as fh:
    fh.write(S.dictionary_markdown())
print("wrote", os.path.abspath(path))
