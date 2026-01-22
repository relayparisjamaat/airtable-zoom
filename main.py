from fastapi import FastAPI, Request, HTTPException
import requests
import logging
import os
import json
from datetime import datetime, timezone
import socket
from pydantic import BaseModel

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
# Zoom Credentials
# --------------------------------------------------

ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")

# ------------------------
# MODELS
# ------------------------
class WebinarUpdateRequest(BaseModel):
    webinar_name: str
    webinar_date: str  # YYYY-MM-DD
    emails: list[str]

# ------------------------
# AUTH ZOOM
# ------------------------
def get_zoom_token():
    url = "https://zoom.us/oauth/token"
    payload = {
        "grant_type": "account_credentials",
        "account_id": ZOOM_ACCOUNT_ID
    }
    r = requests.post(
        url,
        params=payload,
        auth=(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET)
    )
    r.raise_for_status()
    return r.json()["access_token"]

# ------------------------
# GET WEBINARS
# ------------------------
def find_webinar(token, name, date_str):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(
        "https://api.zoom.us/v2/users/me/webinars",
        headers=headers,
        params={"page_size": 100}
    )
    r.raise_for_status()

    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()

    for w in r.json().get("webinars", []):
        start = datetime.fromisoformat(w["start_time"].replace("Z", "+00:00"))
        if w["topic"] == name and start.date() == target_date:
            return w["id"]

    return None

# ------------------------
# REGISTER EMAIL
# ------------------------
def register_email(token, webinar_id, email):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": email,
        "first_name": "",
        "last_name": ""
    }
    r = requests.post(
        f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants",
        headers=headers,
        json=payload
    )
    return r.status_code == 201

# ------------------------
# ROUTES
# ------------------------

# --------------------------------------------------
# Healthcheck endpoint (obligatoire pour Render)
# --------------------------------------------------
@app.post("/")
def wakeup():
    return {"status": "ok"}

# --------------------------------------------------
# Mise à jour du webinaire
# --------------------------------------------------
@app.post("/update-webinar")
def update_webinar(data: WebinarUpdateRequest):
    token = get_zoom_token()
    webinar_id = find_webinar(token, data.webinar_name, data.webinar_date)

    if not webinar_id:
        return {
            "status": "webinar_not_found",
            "registered": 0
        }

    success = 0
    for email in set(data.emails):
        try:
            if register_email(token, webinar_id, email):
                success += 1
        except:
            continue

    return {
        "status": "ok",
        "webinar_id": webinar_id,
        "registered": success,
        "requested": len(data.emails)
    }

# --------------------------------------------------
# Création du webinaire
# --------------------------------------------------
@app.post("/create-webinar")
def create_webinar(data: dict):
    token = get_zoom_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "topic": data["name"],
        "type": 5,  # Scheduled webinar
        "start_time": data["start_time"],
        "duration": data["duration"],
        "timezone": "Europe/Paris",
        "settings": {
            "approval_type": 0,
            "registration_type": 1,
        },
    }

    r = requests.post(
        "https://api.zoom.us/v2/users/me/webinars",
        headers=headers,
        json=payload,
        timeout=20,
    )

    if r.status_code != 201:
        raise HTTPException(status_code=400, detail=r.text)

    webinar = r.json()

    return {"status": "ok", "webinar_id": webinar["id"],}
