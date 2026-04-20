import os
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from tools.query_data import QueryDataTool
from tools.search_docs import SearchDocsTool
from tools.web_search import WebSearchTool

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary SQLite database with dummy data for QueryDataTool tests."""
    db_file = tmp_path / "f1_results.db"
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE race_results (
        season INT, round INT, grand_prix TEXT, driver TEXT, finish_position INT
    )''')
    cursor.execute("INSERT INTO race_results VALUES (2023, 1, 'Bahrain Grand Prix', 'Max Verstappen', 1)")
    conn.commit()
    conn.close()
    yield str(db_file)
    os.remove(db_file)

def test_query_data_tool_success(mocker, temp_db):
    """Test standard SQL generation and local data execution without API calls."""
    # 1. Mock DB Path reference in the module
    mocker.patch('tools.query_data.DB_PATH', temp_db)
    
    # 2. Mock Gemini's SQL generation response
    mock_model = mocker.patch('google.generativeai.GenerativeModel')
    mock_response = MagicMock()
    mock_response.text = "SELECT driver FROM race_results WHERE finish_position = 1"
    mock_model.return_value.generate_content.return_value = mock_response

    tool = QueryDataTool()
    result = tool.run("Who won the 2023 Bahrain Grand Prix?")

    assert "Max Verstappen" in result
    assert "SELECT driver" in result
    assert "Results (1 rows)" in result

def test_query_data_tool_no_db():
    """Test graceful failure if database is missing."""
    tool = QueryDataTool()
    # Force the module variable to a nonexistent path
    with patch('tools.query_data.DB_PATH', 'nonexistent_database.db'):
        result = tool.run("Test query")
        assert "ERROR: Database not found" in result

def test_search_docs_tool_success(mocker, tmp_path):
    """Test ChromaDB document retrieval with mocked Gemini Embeddings."""
    # 1. Provide a dummy path representing Chroma DB existence
    mocker.patch('tools.search_docs.VECTOR_STORE_PATH', str(tmp_path))
    
    # 2. Mock Gemini Embeddings
    mocker.patch('google.generativeai.embed_content', return_value={'embedding': [[0.1, 0.2, 0.3]]})

    # 3. Mock ChromaDB Client
    mock_client = mocker.patch('chromadb.PersistentClient')
    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        'documents': [["Norris won his first race in Miami 2024."]],
        'metadatas': [[{"source": "test_source.txt", "chunk_id": 1}]]
    }
    mock_client.return_value.get_collection.return_value = mock_collection

    tool = SearchDocsTool()
    result = tool.run("When did Lando Norris win?")

    assert "Norris won his first race in Miami" in result
    assert "[Source: test_source.txt | Chunk: 1]" in result

def test_web_search_tool_success(mocker):
    """Test Tavily web search with mocked API call."""
    mocker.patch.dict(os.environ, {"TAVILY_API_KEY": "fake_test_key"})
    
    # Mock TavilyClient
    mock_client_class = mocker.patch('tavily.TavilyClient')
    mock_instance = mock_client_class.return_value
    mock_instance.search.return_value = {
        "results": [
            {"title": "Lando Norris wins in Miami", "content": "Lando secured his debut win.", "url": "http://test.com/miami"}
        ]
    }

    tool = WebSearchTool()
    result = tool.run("Lando Norris latest win")

    assert "Lando Norris wins in Miami" in result
    assert "debut win" in result
    assert "http://test.com/miami" in result

def test_web_search_tool_no_key(mocker):
    """Test failure when Tavily API key is missing."""
    mocker.patch.dict(os.environ, clear=True)
    if "TAVILY_API_KEY" in os.environ:
        del os.environ["TAVILY_API_KEY"]
    
    tool = WebSearchTool()
    result = tool.run("Test query")
    
    assert "ERROR: TAVILY_API_KEY not set" in result
