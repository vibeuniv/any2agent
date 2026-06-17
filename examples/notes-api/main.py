"""A tiny FastAPI app used as a demo target for `any2agent connect`.

It has a few routes (read / write / delete) and JWT bearer auth, so the scanner
detects: framework=fastapi, auth=jwt-bearer (Authorization header passthrough),
and classifies POST as write and DELETE as danger.

Run it:  uvicorn main:app --reload     # http://localhost:8000
"""
from fastapi import FastAPI, Depends, Header, HTTPException
import jwt  # PyJWT

app = FastAPI(title="Notes API")
SECRET = "demo-secret"


def current_user(authorization: str = Header(default="")):
    """Auth: expects `Authorization: Bearer <jwt>` and verifies it."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return jwt.decode(authorization.split(" ", 1)[1], SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")


@app.get("/notes")
def list_notes(user=Depends(current_user)):
    """List the current user's notes."""
    return []


@app.get("/notes/{note_id}")
def get_note(note_id: int, user=Depends(current_user)):
    """Get a single note by id."""
    return {"id": note_id}


@app.post("/notes")
def create_note(user=Depends(current_user)):
    """Create a new note."""
    return {"ok": True}


@app.delete("/notes/{note_id}")
def delete_note(note_id: int, user=Depends(current_user)):
    """Delete a note by id."""
    return {"deleted": note_id}


@app.get("/health")
def health():
    """Public health check."""
    return {"status": "ok"}
