from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
KB_DIR = REPO / "data" / "knowledge_base"
FIXTURES_DIR = REPO / "data" / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _offline_settings(tmp_path_factory):
    """Tests never hit the network: hashing embeddings, temp index/drafts dirs."""
    tmp = tmp_path_factory.mktemp("ops")
    os.environ["OPS_COPILOT_EMBEDDING_PROVIDER"] = "hashing"
    os.environ["OPS_COPILOT_INDEX_DIR"] = str(tmp / "index")
    os.environ["OPS_COPILOT_DRAFTS_DIR"] = str(tmp / "drafts")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    from ops_copilot.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def retriever():
    from ops_copilot.rag import HashingEmbeddings, build_in_memory_retriever

    return build_in_memory_retriever(KB_DIR, HashingEmbeddings())


@pytest.fixture(scope="session")
def local_tools(retriever):
    from ops_copilot.mcp_client import load_local_tools
    from ops_copilot.mcp_servers import knowledge_server, ops_server

    knowledge_server.set_retriever(retriever)
    ops_server.set_fixtures_dir(FIXTURES_DIR)
    return load_local_tools()
