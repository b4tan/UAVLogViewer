from fastapi import FastAPI, UploadFile, File, HTTPException
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
from .embeddings import build_snippets, create_embeddings, save_faiss_index, retrieve_relevant_snippets

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

def call_gemini_llm(message: str, parsed_data: dict, chat_history: List[dict], context: str = "") -> str:
    """
    Calls Gemini LLM to decide if the question is flight-specific and answer accordingly.
    Uses the google.generativeai library and the GEMINI_API_KEY environment variable.
    """
    import google.generativeai as genai
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return "[Error: Gemini API key not set in GOOGLE_API_KEY environment variable.]"
    genai.configure(api_key=api_key)

    # Build the context prompt
    prompt = (
        "You are FlightDataAgent. Use ONLY the following flight log data snippets to answer the user's question.\n"
        f"CONTEXT SNIPPETS:\n{context}\n\n"
        "If the user's question is about this specific flight, answer using ONLY the provided telemetry data. "
        "If you do not have enough data to answer, politely ask the user to upload a flight log or provide more information. "
        "Here is the parsed telemetry data (metadata and message types):\n"
        f"METADATA: {parsed_data.get('metadata', {})}\n"
        f"MESSAGE TYPES: {parsed_data.get('message_types', [])}\n"
        "\nUSER QUESTION: '" + message + "'\n"
        "If the answer isn't in the provided data, say 'I don't see that in the log.'\n"
        "If more clarification is needed, ask the user a follow-up.\n"
        "Keep answers concise and reference timestamps where possible.\n"
        "You may use the ArduPilot log documentation for reference: https://ardupilot.org/plane/docs/logmessages.html\n"
    )

    # Prepare chat history for Gemini (if any)
    history = []
    for turn in chat_history:
        if turn.get("role") == "user":
            history.append({"role": "user", "parts": [turn.get("content", "")]})
        elif turn.get("role") == "assistant":
            history.append({"role": "model", "parts": [turn.get("content", "")]})

    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        chat = model.start_chat(history=history)
        response = chat.send_message(prompt)
        return response.text
    except Exception as e:
        return f"[Error communicating with Gemini LLM: {e}]"

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        # Generate unique file key
        file_key = str(uuid.uuid4())
        
        # Save file
        file_path = UPLOAD_DIR / f"{file_key}.bin"
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Parse the file
        parser = MAVLinkParser(file_path)
        parsed_data = parser.parse()


        # Do NOT save parsed_data to disk as JSON (avoid serialization errors)
        # Only keep in memory and process embeddings as in open-sample

        # Store data
        file_data[file_key] = {
            "filename": file.filename,
            "content_type": file.content_type,
            "size": file_path.stat().st_size,
            "parsed_data": parsed_data
        }
        
        logger.info(f"Successfully processed file {file.filename} with key {file_key}")

        
        # After parsing, build and store vector embeddings
        snippets = build_snippets(parsed_data)
        embeddings = create_embeddings(snippets)
        save_faiss_index(file_key, embeddings, snippets)
        
        return {"fileKey": file_key}
    except Exception as e:
        logger.error(f"Error processing file: {e}")
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
            "parsed_data": parsed_data
        }
        
        logger.info(f"Successfully processed sample file with key {file_key}")

        
        # After parsing, build and store vector embeddings (open-sample)
        snippets = build_snippets(parsed_data)
        embeddings = create_embeddings(snippets)
        save_faiss_index(file_key, embeddings, snippets)
        
        return {"fileKey": file_key}
    except Exception as e:
        logger.error(f"Error processing sample file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class ChatRequest(BaseModel):
    message: str
    fileKey: str
    telemetryData: Optional[Dict[str, Any]] = None
    chatHistory: Optional[List[Dict[str, str]]] = None

    class Config:
        extra = "allow"  # Allow extra fields in the telemetryData

@app.post("/api/chat")
async def chat(request: ChatRequest):
    try:
        message = request.message
        fileKey = request.fileKey
        telemetryData = request.telemetryData
        chatHistory = request.chatHistory

        # Try to get parsed_data, but allow it to be empty
        parsed_data = {}
        if fileKey and fileKey in file_data:
            parsed_data = file_data[fileKey].get("parsed_data", {})

        # Retrieve relevant telemetry snippets using vector search
        context = ""
        if fileKey:
            try:
                relevant_snippets = retrieve_relevant_snippets(fileKey, message, top_k=10)
                context = "\n".join([s["text"] for s in relevant_snippets])
            except Exception as e:
                logger.warning(f"Could not retrieve vector search context: {e}")

        llm_response = call_gemini_llm(message, parsed_data, chatHistory or [], context)
        return {"response": llm_response}
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