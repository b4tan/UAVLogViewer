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
            # Example: customize for your needs
            time = msg.get("time_boot_ms") or msg.get("timestamp") or ""
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