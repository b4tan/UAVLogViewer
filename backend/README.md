# UAV Log Chatbot Backend

This is the backend service for the UAV Log Chatbot application. It provides APIs for file upload, sample file handling, and chat functionality.

## Setup

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the server:
```bash
uvicorn app.main:app --reload
```

The server will start at http://localhost:8000

## API Endpoints

- `POST /api/upload`: Upload a flight log file
- `POST /api/open-sample`: Load the sample flight log file
- `POST /api/chat`: Send a chat message and get a response
- `POST /api/clear-history`: Clear all uploaded files and chat history

## Development

The backend is built with FastAPI and provides the following features:
- File upload handling
- Sample file processing
- Chat functionality (to be implemented)
- CORS support for local development
- File metadata tracking

## Next Steps

1. Implement MAVLink log parsing
2. Add chat functionality with LLM integration
3. Add telemetry data analysis
4. Implement error handling and validation
5. Add file cleanup and management 