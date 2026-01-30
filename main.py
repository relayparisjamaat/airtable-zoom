from fastapi import FastAPI, Request, HTTPException
import requests
import logging
import os
import json
from datetime import datetime, timezone
import socket
from pydantic import BaseModel
import csv
import io
    
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
# GENERATE CSV
# ------------------------
def build_zoom_csv(emails, names):
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(["email", "first_name", "last_name"])

    for email, name in zip(emails, names):
        parts = name.split()
        first_name = parts[0] if parts else "Prénom"
        last_name = " ".join(parts[1:]) if len(parts) > 1 else "Nom"

        writer.writerow([email, first_name, last_name])

    buffer.seek(0)
    return buffer

def register_emails_csv(token, webinar_id, csv_buffer):
    headers = {
        "Authorization": f"Bearer {token}",
    }

    files = {
        "file": ("registrants.csv", csv_buffer.getvalue(), "text/csv")
    }

    r = requests.post(
        f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants/import",
        headers=headers,
        files=files,
        timeout=30
    )

    r.raise_for_status()
    return r.json()
    
# ------------------------
# REGISTER EMAIL
# ------------------------
def register_email(token, webinar_id, email, name):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Vérification de différents cas sur le nom prénom sinon le processus va planter. name est censé contenir : "Prénom Nom"
    if(len(name.split()) == 1) :
        firstname = name
        lastname = "Nom de famille"
    elif(len(name.split()) == 2) :
        firstname = name.split()[0]
        lastname = name.split()[1]
    elif(len(name.split()) > 2): # exemple nom de famille composé en 2 mots
        firstname = name.split()[0]
        lastname = name.split()[1] + name.split()[2]
    else: # sinon, name est vide on fait un remplissage par défaut
        firstname = "Prénom"
        lastname = "Nom"

    payload = {
        "email": email,
        "first_name": firstname,
        "last_name": lastname,
    }
    
    r = requests.post(
        f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants",
        headers=headers,
        json=payload
    )
    
    return {"status_code": r.status_code, "status_body": r.text}

# ------------------------------------------------
# ------------------------------------------------
#             MAIN CODE : WEB ROUTES
# ------------------------------------------------
# ------------------------------------------------

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
            if r["status_code"] == 201 : 
                success += 1
            else : 
                status = r["status_body"]

            time.sleep(10)  # CRUCIAL pour s'assurer que les e-mails s'envoient bien
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
        "type": 5,  # Scheduled webinar (not a periodic one)
        "start_time": data["start_time"],
        "duration": int(data["duration"]),
        "timezone": "Europe/Paris",
        "settings": {
            "approval_type": 0,
            "registration_type": 1 if data["diffusion"] == "Sur inscription" else 3,
            "registrants_confirmation_email": True,
            "registrants_email_notification": True,
            "send_1080p_video_to_attendees": True,
            "auto_recording": "cloud" if data["recording"] == "Oui" else "none",
            "attendees_and_panelists_reminder_email_notification": {
                "enable": True,
                "type": 0
            },
            "request_permission_to_unmute_participants": True,
            "allow_host_control_participant_mute_state": True,
            "email_in_attendee_report": True,
            "add_watermark": True,
            "email_language": "fr-FR",
            
            "question_and_answer": {
                "allow_submit_questions": True,
                "allow_anonymous_questions": True,
                "answer_questions": "all",
                "attendees_can_comment": True,
                "attendees_can_upvote": True,
                "allow_auto_reply": True,
                "enable": True
            },
        },
    }

    print("Payload for meeting creation : ")
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

# --------------------------------------------------
# Ajout des participants en csv
# --------------------------------------------------
@app.api_route("/add-registrants-csv", methods=["POST", "GET"])
def add_registrants(data: dict):
    csv_buffer = build_zoom_csv(data["emails"], data["names"])
    result = register_emails_csv(token, webinar_id, csv_buffer)
    
    return {
        "status": "ok",
        "webinar_id": webinar_id,
        "registered": result.get("total_records", 0),
        "requested": len(data["emails"])
    }
