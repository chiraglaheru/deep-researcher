from unittest.mock import MagicMock

from research import report


def test_synthesize_report_returns_final_answer(monkeypatch):
    mock_response = MagicMock()
    mock_response.text = "FastAPI is a Python web framework."

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    monkeypatch.setattr(
        report.genai,
        "Client",
        lambda api_key: mock_client,
    )

    findings = [
        {
            "subquestion": "What is FastAPI?",
            "finding": "FastAPI is a Python web framework for building APIs.",
        }
    ]

    result = report.synthesize_report(
        "What is FastAPI?",
        findings,
    )

    assert result == "FastAPI is a Python web framework."

    mock_client.models.generate_content.assert_called_once()


def test_synthesize_report_includes_all_findings(monkeypatch):
    mock_response = MagicMock()
    mock_response.text = "Combined research report."

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    monkeypatch.setattr(
        report.genai,
        "Client",
        lambda api_key: mock_client,
    )

    findings = [
        {
            "subquestion": "What is FastAPI?",
            "finding": "FastAPI is a Python web framework.",
        },
        {
            "subquestion": "What are FastAPI features?",
            "finding": "FastAPI provides automatic API documentation.",
        },
    ]

    result = report.synthesize_report(
        "Tell me about FastAPI.",
        findings,
    )

    assert result == "Combined research report."

    prompt = mock_client.models.generate_content.call_args.kwargs["contents"]

    assert "What is FastAPI?" in prompt
    assert "FastAPI is a Python web framework." in prompt
    assert "What are FastAPI features?" in prompt
    assert "FastAPI provides automatic API documentation." in prompt


def test_synthesize_report_handles_empty_findings(monkeypatch):
    mock_response = MagicMock()
    mock_response.text = "Insufficient evidence."

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    monkeypatch.setattr(
        report.genai,
        "Client",
        lambda api_key: mock_client,
    )

    result = report.synthesize_report(
        "What is FastAPI?",
        [],
    )

    assert result == "Insufficient evidence."

    mock_client.models.generate_content.assert_called_once()