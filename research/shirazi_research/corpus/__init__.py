"""Minimal package marker — intentionally empty.

The full corpus/__init__ in the shirazi-research checkout imports the
whole corpus layer (schemas, bok_reader, indexer, ...). This bundled
copy exists only so search_all.py can import
shirazi_research.corpus.arabic_norm (query normalization) without
pulling that chain. Nothing else in this bundle imports from here.
"""
