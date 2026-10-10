"""Shared fixtures: force the light TF-IDF engine for fast deterministic tests."""
import os
import sys

os.environ["SEMANTIC_ENGINE"] = "tfidf"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
