"""Deterministic tool implementations the agent calls via native tool-use.

Each module is a plain, testable function over the typed domain model. The agent decides
*when* to call them (dynamic control flow); the determinism lives here, under unit tests.
"""
