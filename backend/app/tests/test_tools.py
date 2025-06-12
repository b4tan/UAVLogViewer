import pytest
from backend.app.tools import retrieve_snippets, detect_anomalies
from unittest.mock import patch, mock_open

@patch("faiss.read_index")
@patch("backend.app.tools.model.encode")
def test_retrieve_snippets_success(mock_encode, mock_faiss):
    class DummyIndex:
        def search(self, q_emb, k):
            return None, [[0, 1]]
    mock_faiss.return_value = DummyIndex()
    mock_encode.return_value = [[0.1]*384]
    snippets = [
        {"time": "123", "msg_type": "GPS", "text": "GPS fix"},
        {"time": "456", "msg_type": "BATTERY", "text": "Battery low"}
    ]
    with patch("builtins.open", mock_open(read_data=str(snippets))):
        result = retrieve_snippets("fakekey", "battery", 2)
        assert isinstance(result, list)
        assert any("Battery" in s["text"] for s in result)

@patch("builtins.open", new_callable=mock_open, read_data='[{"time": "1", "msg_type": "SYS_STATUS", "text": "[SYS_STATUS at 1] {\'errors_count1\': 0, \'errors_comm\': 0}"}, {"time": "2", "msg_type": "SYS_STATUS", "text": "[SYS_STATUS at 2] {\'errors_count1\': 2, \'errors_comm\': 0}"}, {"time": "3", "msg_type": "BATTERY", "text": "[BATTERY at 3] Battery low detected"}]')
def test_detect_anomalies_filters(mock_file):
    result = detect_anomalies("fakekey")
    assert any(a["type"] == "sys_status_error" for a in result)
    assert any("battery low" in a["description"].lower() for a in result) 