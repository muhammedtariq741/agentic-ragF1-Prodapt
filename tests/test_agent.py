import json
import pytest
from unittest.mock import MagicMock

from main import F1Agent, parse_llm_json

def test_parse_llm_json_standard():
    """Test parsing a perfectly formatted JSON string."""
    text = '{"action": "tool_call", "tool": "query_data", "input": "test"}'
    result = parse_llm_json(text)
    assert result["action"] == "tool_call"
    assert result["tool"] == "query_data"

def test_parse_llm_json_markdown():
    """Test parsing JSON wrapped in markdown fences."""
    text = '''```json
    {"action": "final_answer", "answer": "The answer is 42."}
    ```'''
    result = parse_llm_json(text)
    assert result["action"] == "final_answer"
    assert "42" in result["answer"]

def test_parse_llm_json_common_errors():
    """Test that the parser catches and fixes trailing commas and newlines."""
    # This JSON is technically invalid because of the trailing comma and unescaped newline.
    text = '{"action": "final_answer", "answer": "Multiline\ntext",}'
    result = parse_llm_json(text)
    assert result["action"] == "final_answer"
    assert "Multiline text" in result["answer"]

def test_f1agent_trivial_question(mocker):
    """Test that the F1Agent correctly answers trivial questions directly without tool routing."""
    agent = F1Agent()

    # Mock the LLM to return a direct final_answer decision
    mock_model = mocker.patch('google.generativeai.GenerativeModel')
    mock_response = MagicMock()
    mock_response.text = '{"action": "final_answer", "answer": "Direct answer", "citations": "None"}'
    mock_model.return_value.generate_content.return_value = mock_response

    # Inject mock into agent
    agent.model = mock_model.return_value

    result = agent.run("What is 2+2?")
    
    assert result["steps_used"] == 1
    assert result["answer"] == "Direct answer"
    assert len(result["trace"]) == 0  # No tool calls made

def test_f1agent_tool_routing(mocker):
    """Test that the F1Agent correctly parses a tool_call and injects it into trace."""
    agent = F1Agent()

    # We need to simulate TWO responses from the LLM.
    # Response 1: I need to use a tool.
    # Response 2: Now I have the result, I will output the final answer.
    mock_response_1 = MagicMock()
    mock_response_1.text = '{"action": "tool_call", "tool": "query_data", "input": "test tool input"}'
    
    mock_response_2 = MagicMock()
    mock_response_2.text = '{"action": "final_answer", "answer": "MCL38", "citations": "query_data"}'

    mock_model = mocker.patch('google.generativeai.GenerativeModel')
    # Use side_effect to return different responses on consecutive calls
    mock_model.return_value.generate_content.side_effect = [mock_response_1, mock_response_2]
    agent.model = mock_model.return_value

    # We also need to mock the tool itself so it doesn't try to query the real DB
    mocker.patch('tools.query_data.QueryDataTool.run', return_value="Simulated tool output")

    result = agent.run("Who won the race?")

    assert result["steps_used"] == 2
    assert result["answer"] == "MCL38"
    assert len(result["trace"]) == 1
    assert result["trace"][0]["tool"] == "query_data"
    assert result["trace"][0]["result"] == "Simulated tool output"

def test_f1agent_hard_cap(mocker):
    """Test that the F1Agent enforces its infinite loop cap (8) and refuses."""
    agent = F1Agent()

    # Force the LLM to return `tool_call` every single time, creating an infinite loop
    mock_model = mocker.patch('google.generativeai.GenerativeModel')
    mock_response = MagicMock()
    mock_response.text = '{"action": "tool_call", "tool": "query_data", "input": "looping"}'
    mock_model.return_value.generate_content.return_value = mock_response
    agent.model = mock_model.return_value

    # Mock tool to return useless data
    mocker.patch('tools.query_data.QueryDataTool.run', return_value="Still looping")

    result = agent.run("This question is a trap to cause an infinite loop.")
    
    # Assert it halted precisely at MAX_STEPS
    assert result["steps_used"] == agent.MAX_STEPS
    # Assert the refusal message was generated
    assert "REFUSAL" in result["answer"]
    assert "Maximum of 8" in result["answer"]
