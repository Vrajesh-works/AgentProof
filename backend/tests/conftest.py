from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUITES = ROOT / "suites"
AGENTS = ROOT / "agents"
FIXTURES = Path(__file__).parent / "fixtures"
FAKE_MCP = FIXTURES / "fake_customer_mcp.py"


@pytest.fixture
def fake_mcp_command() -> list[str]:
    return [sys.executable, str(FAKE_MCP)]
