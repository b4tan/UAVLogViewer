from sentence_transformers import SentenceTransformer
import faiss
import numpy as np
import json
from pathlib import Path

model = SentenceTransformer('all-MiniLM-L6-v2')

def build_snippets(parsed_data):
    snippets = []
    for msg_type, msgs in parsed_data.get("messages", {}).items():
        for msg in msgs:
            time = msg.get("time_boot_ms") or msg.get("timestamp") or ""
            
            # Special handling for GPS-related messages
            if msg_type == "GPS_RAW_INT":
                fix_type = msg.get("fix_type", 0)
                satellites_visible = msg.get("satellites_visible", 0)
                gps_status = "GPS signal lost" if fix_type == 0 else f"GPS fix type {fix_type} with {satellites_visible} satellites"
                snippet = f"[{msg_type} at {time}] {gps_status}"
            elif msg_type == "GLOBAL_POSITION_INT":
                lat = msg.get("lat", 0)
                lon = msg.get("lon", 0)
                alt = msg.get("relative_alt", 0)
                gps_status = "GPS position lost" if lat == 0 and lon == 0 else f"GPS position: lat={lat}, lon={lon}, alt={alt}"
                snippet = f"[{msg_type} at {time}] {gps_status}"
            else:
                # Default handling for other message types
                snippet = f"[{msg_type} at {time}] {msg}"
            
            snippets.append({"text": snippet, "msg_type": msg_type, "time": time})
    return snippets

def create_embeddings(snippets):
    texts = [s["text"] for s in snippets]
    embeddings = model.encode(texts, show_progress_bar=True)
    return np.array(embeddings).astype("float32")

def save_faiss_index(fileKey, embeddings, snippets):
    index = faiss.IndexFlatL2(embeddings.shape[1])
    index.add(embeddings)
    outdir = Path("uploads/faiss_indexes")
    outdir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(outdir / f"{fileKey}.index"))
    with open(outdir / f"{fileKey}_snippets.json", "w") as f:
        json.dump(snippets, f)

def retrieve_relevant_snippets(fileKey, question, top_k=10):
    outdir = Path("uploads/faiss_indexes")
    index = faiss.read_index(str(outdir / f"{fileKey}.index"))
    with open(outdir / f"{fileKey}_snippets.json") as f:
        snippets = json.load(f)
    q_emb = model.encode([question]).astype("float32")
    D, I = index.search(q_emb, top_k)
    return [snippets[i] for i in I[0]]

def classify_query_type(question: str) -> str:
    """Classify the user question as 'retrieval', 'anomaly_tool', or 'unknown'."""
    q = question.lower()
    # Retrieval: factual, telemetry, or data lookup
    retrieval_keywords = [
        "highest", "altitude", "duration", "when did", "maximum", "min", "max", "how long", "flight time", "temperature", "list all", "first instance", "rc signal", "gps", "error", "critical", "mid-flight", "battery", "speed", "distance", "takeoff", "land", "mode", "arm", "disarm"
    ]
    anomaly_keywords = [
        "anomaly", "anomalies", "error", "warning", "problem", "issue", "fail", "failsafe", "inconsistent", "lost", "spike", "jump"
    ]
    if any(word in q for word in retrieval_keywords):
        return "retrieval"
    if any(word in q for word in anomaly_keywords):
        return "anomaly_tool"
    return "unknown"