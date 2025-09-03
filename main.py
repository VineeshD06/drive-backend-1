from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import user, cdn
from app.database import engine
from app.routes import ai  
from app import models
import os

# Create DB tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI()
origins = ["*"]
# Allow frontend to talk to backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # adjust when deploying
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all route modules
app.include_router(user.router, tags=["Auth"])
app.include_router(cdn.router, prefix="/files", tags=["files"])
app.include_router(ai.router, prefix="/ai")
