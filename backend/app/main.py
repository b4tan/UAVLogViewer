from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import os
import uuid
import shutil
from typing import Optional, Dict, Any, List
import json
from pathlib import Path
import logging
from .mavlink_parser import MAVLinkParser
from pydantic import BaseModel
from dotenv import load_dotenv
from .embeddings import build_snippets, create_embeddings, save_faiss_index, retrieve_relevant_snippets, classify_query_type
from .tools import retrieve_snippets, detect_anomalies
from .agents import FlightLogAgentOrchestrator
import numpy as np

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080"],  # Vue.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create upload directory if it doesn't exist
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Store for file metadata and parsed data
file_data = {}

# Store active WebSocket connections
active_connections: List[WebSocket] = []

# Initialize orchestrator
orchestrator = FlightLogAgentOrchestrator(api_key=os.getenv("GOOGLE_API_KEY"))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except:
        active_connections.remove(websocket)

async def notify_embedding_status(message: str):
    for connection in active_connections:
        try:
            await connection.send_text(json.dumps({"type": "embedding_status", "message": message}))
        except:
            continue

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        # Generate unique file key
        file_key = str(uuid.uuid4())
        
        # Preserve original file extension
        original_extension = Path(file.filename).suffix
        file_path = UPLOAD_DIR / f"{file_key}{original_extension}"
        
        # Save file
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Parse the file
        parser = MAVLinkParser(file_path)
        parsed_data = parser.parse()

        # Get vehicle type from parsed data
        vehicle_type = parsed_data.get("vehicle_type", "UNKNOWN")
        if vehicle_type == "UNKNOWN" and "HEARTBEAT" in parsed_data.get("messages", {}):
            heartbeat = parsed_data["messages"]["HEARTBEAT"][0]
            vehicle_type = heartbeat.get("type", "UNKNOWN")

        # Store data
        file_data[file_key] = {
            "filename": file.filename,
            "content_type": file.content_type,
            "size": file_path.stat().st_size,
            "parsed_data": parsed_data,
            "file_extension": original_extension,
            "vehicle_type": vehicle_type  # Add vehicle type to stored data
        }
        
        logger.info(f"Successfully processed file {file.filename} with key {file_key}")

        # Notify about embedding creation
        await notify_embedding_status("Creating embeddings for flight log analysis...")
        
        # After parsing, build and store vector embeddings
        snippets = build_snippets(parsed_data)
        embeddings = create_embeddings(snippets)
        save_faiss_index(file_key, embeddings, snippets)
        
        # Notify completion
        await notify_embedding_status("Embeddings created successfully!")
        
        return {"fileKey": file_key}
    except Exception as e:
        logger.error(f"Error processing file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/open-sample")
async def open_sample():
    try:
        # Generate unique file key for sample
        file_key = str(uuid.uuid4())
        
        # Copy sample file to uploads directory
        sample_path = Path("../src/assets/vtol.tlog")
        if not sample_path.exists():
            raise HTTPException(status_code=404, detail="Sample file not found")
            
        dest_path = UPLOAD_DIR / f"{file_key}.tlog"
        shutil.copy2(sample_path, dest_path)
        
        # Parse the sample file
        parser = MAVLinkParser(dest_path)
        parsed_data = parser.parse()
        
        # Store data
        file_data[file_key] = {
            "filename": "vtol.tlog",
            "content_type": "application/octet-stream",
            "size": dest_path.stat().st_size,
            "parsed_data": parsed_data,
            "file_extension": ".tlog"
        }
        
        logger.info(f"Successfully processed sample file with key {file_key}")

        # Notify about embedding creation
        await notify_embedding_status("Creating embeddings for flight log analysis...")
        
        # After parsing, build and store vector embeddings
        snippets = build_snippets(parsed_data)
        embeddings = create_embeddings(snippets)
        save_faiss_index(file_key, embeddings, snippets)
        
        # Notify completion
        await notify_embedding_status("Embeddings created successfully!")
        
        return {"fileKey": file_key}
    except Exception as e:
        logger.error(f"Error processing sample file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class ChatRequest(BaseModel):
    message: str
    fileKey: Optional[str] = None
    telemetryData: Optional[Dict[str, Any]] = None
    chatHistory: Optional[List[Dict[str, str]]] = None

    class Config:
        extra = "allow"  # Allow extra fields in the telemetryData

@app.post("/api/chat")
async def chat(request: ChatRequest):
    try:
        message = request.message
        fileKey = request.fileKey
        chatHistory = request.chatHistory or []

        # Handle chat with or without flight log
        if fileKey and fileKey not in file_data:
            # Check if this is a sample file
            sample_path = Path("../src/assets/vtol.tlog")
            if sample_path.exists():
                dest_path = UPLOAD_DIR / f"{fileKey}.tlog"
                shutil.copy2(sample_path, dest_path)
                parser = MAVLinkParser(dest_path)
                parsed_data = parser.parse()
                file_data[fileKey] = {
                    "filename": "vtol.tlog",
                    "content_type": "application/octet-stream",
                    "size": dest_path.stat().st_size,
                    "parsed_data": parsed_data,
                    "vehicle_type": parsed_data.get("vehicle_type", "UNKNOWN")
                }
                logger.info(f"Successfully processed sample file with key {fileKey}")
                snippets = build_snippets(parsed_data)
                embeddings = create_embeddings(snippets)
                save_faiss_index(fileKey, embeddings, snippets)
            else:
                raise HTTPException(status_code=404, detail="File not found or embeddings not created")

        # Add file context to chat history if it's not already there
        if fileKey and not any("Flight log loaded successfully" in msg.get("content", "") for msg in chatHistory):
            vehicle_type = file_data[fileKey].get("vehicle_type", "UNKNOWN")
            chatHistory.insert(0, {
                "role": "system",
                "content": f"Flight log loaded successfully. This is a {vehicle_type} flight log. You can now ask questions about the flight data. FileKey: {fileKey}"
            })

        # --- Classify the query type ---
        query_type = classify_query_type(message)
        if query_type == "unknown":
            logger.info("General chat detected, answering without embedding/LLM/tool.")
            response = await orchestrator.answer_question(message, fileKey, chatHistory)
            return {"response": response}

        # --- Embedding short-circuit: Try to answer using FAISS before LLM ---
        if fileKey and query_type in ("retrieval", "anomaly_tool"):
            try:
                # Retrieve top 1 relevant snippet and its similarity
                top_k = 1
                snippets = retrieve_relevant_snippets(fileKey, message, top_k=top_k)
                if snippets:
                    # Compute cosine similarity between question and snippet
                    from .embeddings import model
                    q_emb = model.encode([message]).astype("float32")[0]
                    outdir = Path("uploads/faiss_indexes")
                    import faiss
                    index = faiss.read_index(str(outdir / f"{fileKey}.index"))
                    D, I = index.search(np.expand_dims(q_emb, 0), top_k)
                    # FAISS returns L2 distance, convert to cosine similarity
                    # If vectors are normalized, cosine_sim = 1 - 0.5 * L2^2
                    l2 = D[0][0]
                    cosine_sim = 1 - 0.5 * l2
                    logger.info(f"Embedding similarity for '{message}': {cosine_sim:.3f}")
                    if cosine_sim > 0.3:
                        logger.info("High confidence embedding match found, refining response...")
                        # Instead of returning raw snippet, use the orchestrator to refine it
                        response = await orchestrator.answer_question(
                            message=message,
                            fileKey=fileKey,
                            chatHistory=chatHistory,
                            embedding_snippet=snippets[0]["text"]  # Pass the snippet to the orchestrator
                        )
                        return {"response": response}
            except Exception as e:
                logger.warning(f"Embedding search failed: {e}")
                # Fallback to LLM if embedding search fails
                pass

        # --- If no good embedding match, call orchestrator/LLM ---
        logger.info("Calling LLM orchestrator for response.")
        response = await orchestrator.answer_question(message, fileKey, chatHistory)
        return {"response": response}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clear-history")
async def clear_history():
    try:
        # Clear all uploaded files and data
        for file_key in list(file_data.keys()):
            file_path = UPLOAD_DIR / f"{file_key}.bin"
            if file_path.exists():
                file_path.unlink()
            del file_data[file_key]
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error clearing history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 