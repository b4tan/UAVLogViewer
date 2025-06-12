from backend.app.embeddings import classify_query_type

def test_classify_query_type_retrieval():
    assert classify_query_type("What was the highest altitude?") == "retrieval"
    assert classify_query_type("When did the GPS drop?") == "retrieval"

def test_classify_query_type_anomaly():
    assert classify_query_type("Were there any anomalies?") == "anomaly_tool"
    assert classify_query_type("Any errors or warnings?") == "anomaly_tool"

def test_classify_query_type_unknown():
    assert classify_query_type("Tell me a joke") == "unknown" 