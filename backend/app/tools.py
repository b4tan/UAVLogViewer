from typing import List, Dict, Any
import numpy as np
from pathlib import Path
import json
from .embeddings import model
import faiss

def retrieve_snippets(fileKey: str, question: str, k: int = 10) -> List[Dict]:
    """Retrieve relevant telemetry snippets using vector search."""
    try:
        outdir = Path("uploads/faiss_indexes")
        index = faiss.read_index(str(outdir / f"{fileKey}.index"))
        with open(outdir / f"{fileKey}_snippets.json") as f:
            snippets = json.load(f)
        
        # Encode question and search
        q_emb = model.encode([question]).astype("float32")
        D, I = index.search(q_emb, k)
        
        # Format results
        results = []
        for i in I[0]:
            snippet = snippets[i]
            results.append({
                "timestamp": snippet.get("time", ""),
                "msg_type": snippet.get("msg_type", ""),
                "text": snippet.get("text", "")
            })
        return results
    except Exception as e:
        return [{"error": f"Failed to retrieve snippets: {str(e)}"}]

def detect_anomalies(fileKey: str) -> List[Dict]:
    """Detect anomalies by scanning snippets for anomaly keywords, but filter out false positives."""
    try:
        outdir = Path("uploads/faiss_indexes")
        with open(outdir / f"{fileKey}_snippets.json") as f:
            snippets = json.load(f)

        keywords = [
            "battery low", "gps lost", "gps signal lost", "failsafe", "critical", "ekf", "inconsistent", "rc lost", "voltage spike", "altitude jump", "gps position jump"
        ]
        anomalies = []
        for snippet in snippets:
            text = snippet.get("text", "").lower()
            # Only match keywords, not just "error"
            if any(kw in text for kw in keywords):
                anomalies.append({
                    "timestamp": snippet.get("time", ""),
                    "type": "anomaly",
                    "description": snippet.get("text", "")
                })
            # Special handling for SYS_STATUS: only if error counts are nonzero
            elif "sys_status" in text and "error" in text:
                import ast
                try:
                    msg_dict = ast.literal_eval(text.split("]")[-1].strip())
                    if any(msg_dict.get(f"errors_count{i}", 0) > 0 for i in range(1, 5)) or msg_dict.get("errors_comm", 0) > 0:
                        anomalies.append({
                            "timestamp": snippet.get("time", ""),
                            "type": "sys_status_error",
                            "description": snippet.get("text", "")
                        })
                except Exception:
                    continue  # If parsing fails, skip
        return anomalies
    except Exception as e:
        return [{"error": f"Failed to detect anomalies: {str(e)}"}] 