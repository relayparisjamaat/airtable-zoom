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
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

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
def register_participant(token, webinar_id, email, name):
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

    # ❌ erreur Zoom
    if r.status_code != 201:
        try:
            error_msg = r.json().get("message", r.text)
        except Exception:
            error_msg = r.text

        return {
            "success": False,
            "email": email,
            "name": name,
            "status_code": str(r.status_code),
            "error": error_msg
        }

    # ✅ succès
    data = r.json()
    return {
        "success": True,
        "email": email,
        "name": name,
        "join_url": data.get("join_url")
    }
    
    return r

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

    registered_emails = []
    join_urls = []
    
    if not webinar_id:
        return {
            "status": "webinar_not_found",
            "webinar_id": webinar_id,
            "registered": 0,
            "requested": len(data["emails"])
        }
        
    success = []
    errors = []
    
    for email, name in zip(data["emails"], data["names"]):
        print(email, " ", name)
        try:
            result = register_participant(token, webinar_id, email, name)

            if result["success"]:
                success.append({
                    "email": result["email"],
                    "name": result["name"],
                    "join_url": result["join_url"]
                })
                print("success : ", result["join_url"])
            else:
                errors.append({
                    "email": result["email"],
                    "name": result["name"],
                    "status_code": result["status_code"],
                    "error": result["error"]
                })
                print("fail : ", result["error"])

        except Exception as e:
            errors.append({
                "email": email,
                "name": name,
                "status_code": 500,
                "error": str(e)
            })
            print("exception : ", str(e))

    return {
        "status": "ok",
        "webinar_id": webinar_id,
        "success": success,
        "errors": errors
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
# Obtention des liens de connexion pour participants déjà enregistrés
# --------------------------------------------------
@app.post("/get-join-urls")
def get_join_urls(data: dict):

    token = get_zoom_access_token()
    headers = {
        "Authorization": f"Bearer {token}"
    }

    webinar_id = data["webinar_id"]

    success = []
    errors = []

    for email in data["emails"]:
        try:
            url = f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants",
            
            params = {
                "email": email
            }

            r = requests.get(url, headers=headers, params=params)

            # ===============================
            # GESTION ERREURS HTTP
            # ===============================
            if r.status_code == 200:
                response_data = r.json()

                if response_data.get("total_records", 0) > 0:
                    registrant = response_data["registrants"][0]

                    success.append({
                        "email": email,
                        "join_url": registrant.get("join_url"),
                        "registrant_id": registrant.get("id")
                    })
                else:
                    errors.append({
                        "email": email,
                        "status_code": 404,
                        "error": "Registrant not found"
                    })

            elif r.status_code == 401:
                errors.append({
                    "email": email,
                    "status_code": 401,
                    "error": "Unauthorized (invalid or expired token)"
                })

            elif r.status_code == 404:
                errors.append({
                    "email": email,
                    "status_code": 404,
                    "error": "Webinar not found"
                })

            elif r.status_code == 429:
                errors.append({
                    "email": email,
                    "status_code": 429,
                    "error": "Rate limit exceeded"
                })

            else:
                errors.append({
                    "email": email,
                    "status_code": r.status_code,
                    "error": r.text
                })

        except Exception as e:
            errors.append({
                "email": email,
                "status_code": 500,
                "error": str(e)
            })

    return {
        "success": success,
        "errors": errors
    }
