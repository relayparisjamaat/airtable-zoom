from fastapi import FastAPI, Request, HTTPException
import requests
import logging
import os
import json
from datetime import datetime, timezone
import socket

# --------------------------------------------------
# Configuration logging (logs visibles dans Render)
# --------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------
# FastAPI app
# --------------------------------------------------
app = FastAPI(title="Jotform Data Service")

# --------------------------------------------------
# Healthcheck endpoint (obligatoire pour Render)
# --------------------------------------------------
@app.get("/")
def healthcheck():
    return {"status": "ok"}
    
# --------------------------------------------------
# Endpoint test : lecture des soumissions
# --------------------------------------------------
@app.get("/update-webinar")
def update_webinar():
    return
