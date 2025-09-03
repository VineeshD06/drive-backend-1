# backend/app/routes/ai.py
from fastapi import APIRouter, HTTPException, Body
from app.utils.ai import generate_ai_response

router = APIRouter()

@router.post("/answer")
async def ai_route(
    body: dict = Body(...)
):
    """
    API endpoint to get an AI-generated response using RAG with conversation history.
    Body example:
    {
      "history": [
        {"role": "user", "text": "What is quantum computing?"},
        {"role": "assistant", "text": "Quantum computing is ..."}
      ],
      "query": "Explain it like I am 10 years old"
    }
    """
    try:
        history = body.get("history", [])
        query = body.get("query", "")

        response = generate_ai_response(query, history)
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
