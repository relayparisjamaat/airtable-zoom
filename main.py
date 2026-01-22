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
# REGISTER EMAIL
# ------------------------
def register_email(token, webinar_id, email, name):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": email,
        "first_name": name.split()[0],
        "last_name": name.split()[1],
    }
    r = requests.post(
        f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants",
        headers=headers,
        json=payload
    )
    
    return {"status code": r.status_code, "status body": r.text}

# ------------------------
# ROUTES
# ------------------------
# --------------------------------------------------
# Healthcheck endpoint (obligatoire pour Render)
# --------------------------------------------------
@app.api_route("/", methods=["POST", "GET"])
def wakeup():
    return {"status": "ok"}

# --------------------------------------------------
# Mise à jour du webinaire
# --------------------------------------------------
@app.api_route("/update-webinar", methods=["POST", "GET"])
def update_webinar(data: dict):
    token = get_zoom_token()
    webinar_id = data["webinar_id"]
    
    if not webinar_id:
        return {
            "status": "webinar_not_found",
            "webinar_id": webinar_id,
            "registered": 0,
            "requested": len(data["emails"])
        }

    success = 0
    status = "ok"
    for i in range(len(data["emails"])):
        email = data["emails"][i]
        name = data["names"][i]
        try:
            r = register_email(token, webinar_id, email, name)
            if r["status code"] == 201 : 
                success += 1
            else : 
                status = r["status body"]
        except:
            continue

    return {
        "status": status,
        "webinar_id": webinar_id,
        "registered": success,
        "requested": len(data["emails"])
    }

# --------------------------------------------------
# Création du webinaire
# --------------------------------------------------
@app.api_route("/create-webinar", methods=["POST", "GET"])
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
        "duration": int(data["duration"]),
        "timezone": "Europe/Paris",
        "settings": {
            "approval_type": 0,
            "registration_type": 1,
        },
    }

    print("Payload")
    print(payload)

    r = requests.post(
        "https://api.zoom.us/v2/users/me/webinars",
        headers=headers,
        json=payload,
        timeout=20,
    )

    if r.status_code != 201:
        print({
            "status": "error",
            "zoom_status": r.status_code,
            "zoom_response": r.text
        })
        raise HTTPException(status_code=400, detail=r.text)

    webinar = r.json()

    return {"status": "ok", "webinar_id": webinar["id"],}
