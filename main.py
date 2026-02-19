from fastapi import FastAPI, Request, HTTPException
import requests
import logging
import os
import json
from datetime import datetime, timezone
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import pytz

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
    payload = {"grant_type": "account_credentials", "account_id": ZOOM_ACCOUNT_ID}
    r = requests.post(url, params=payload, auth=(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET))
    r.raise_for_status()
    return r.json()["access_token"]
    
# ------------------------
# REGISTER EMAIL
# ------------------------
def register_participant(token, webinar_id, email, name):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

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

    payload = {"email": email, "first_name": firstname, "last_name": lastname,}

    try :
        r = requests.post(f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants", headers=headers,json=payload)
    except Exception as e:
        return {"success": False, "email": email, "name": name, "status_code": 500, "error": str(e)}    

    # erreur Zoom
    if r.status_code != 201:
        try: error_msg = r.json().get("message", r.text)
        except Exception: error_msg = r.text
        return {"success": False, "email": email, "name": name, "status_code": str(r.status_code), "error": error_msg}

    # succès
    data = r.json()
    return { "success": True, "email": email, "name": name, "join_url": data.get("join_url")}

# ------------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------------
#                                     MAIN CODE : WEB ROUTES
# ------------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------------

# --------------------------------------------------
# Healthcheck endpoint (obligatoire pour Render)
# --------------------------------------------------
@app.api_route("/", methods=["POST", "GET"])
def wakeup():
    return {"status": "ok"}

# --------------------------------------------------
# Fetch upcoming webinars
# --------------------------------------------------
@app.post("/fetch-upcoming-webinars")
def fetch_upcoming_webinars():

    token = get_zoom_token()

    headers = {"Authorization": f"Bearer {token}"}

    paris_tz = pytz.timezone("Europe/Paris")
    now_paris = datetime.now(paris_tz)

    webinars_list = []
    next_page_token = ""

    try:
        # PAGINATION ZOOM
        while True:

            url = "https://api.zoom.us/v2/users/me/webinars"

            params = {"type": "upcoming", "page_size": 300, "next_page_token": next_page_token}

            r = requests.get(url, headers=headers, params=params, timeout=15)

            if r.status_code != 200:
                return {"status": "error","zoom_status": r.status_code, "zoom_response": r.text}

            data = r.json()
            webinars = data.get("webinars", [])

            for webinar in webinars:
                start_time_utc = datetime.strptime(
                    webinar["start_time"],
                    "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=pytz.utc)

                start_time_paris = start_time_utc.astimezone(paris_tz)

                # Filtrer ≥ aujourd'hui
                if start_time_paris >= now_paris:
                    formatted_date = start_time_paris.strftime("%d/%m/%Y %H:%M")
                    webinars_list.append({
                        "Name": webinar.get("topic"),
                        "Webinar ID": webinar.get("id"),
                        "Date": formatted_date,
                        "Duration": webinar.get("duration"),
                        "Recording": "Oui" if webinar.get("settings", {}).get("auto_recording") == "cloud" else "Non",
                        "Diffusion": "Sur inscription" if webinar.get("settings", {}).get("registration_type") == 1 else "Public"
                    })

            next_page_token = data.get("next_page_token", "")
            if not next_page_token:
                break

        return {"status": "ok", "count": len(webinars_list), "webinars": webinars_list}

    except Exception as e: return {"status": "error", "message": str(e)}
        
# --------------------------------------------------
# Mise à jour du webinaire
# --------------------------------------------------
@app.post("/update-webinar")
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

    success = []
    errors = []

    participants = list(zip(data["emails"], data["names"]))

    MAX_WORKERS = 8  # 5 à 10 recommandé pour éviter 429

    def worker(email, name):
        retry = 0

        while retry < 3:
            result = register_participant(token, webinar_id, email, name)

            if result["success"]:
                return ("success", result)

            if result.get("status_code") == "429":
                retry += 1
                time.sleep(1.5)
                continue

            return ("error", result)

        return ("error", result)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        futures = [
            executor.submit(worker, email, name)
            for email, name in participants
        ]

        for future in as_completed(futures):
            status, result = future.result()

            if status == "success":
                success.append({
                    "email": result["email"],
                    "name": result["name"],
                    "join_url": result["join_url"]
                })
            else:
                errors.append({
                    "email": result["email"],
                    "name": result["name"],
                    "status_code": result.get("status_code"),
                    "error": result.get("error")
                })

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
    
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
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

    return {"status": "ok", "webinar_id": webinar["id"]}

# --------------------------------------------------
# Obtention des liens de connexion pour participants déjà enregistrés
# --------------------------------------------------
'''
@app.post("/get-join-urls")
def get_join_urls(data: dict):

    token = get_zoom_token()
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
'''

@app.post("/get-join-urls")
def get_join_urls(data: dict):

    token = get_zoom_token()
    headers = {
        "Authorization": f"Bearer {token}"
    }

    webinar_id = data["webinar_id"]
    requested_emails = [email.lower() for email in data["emails"]]

    success = []
    errors = []

    try:
        all_registrants = []
        next_page_token = ""

        # ===============================
        # PAGINATION ZOOM (300 max/page)
        # ===============================
        while True:
            url = f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants"

            params = {
                "page_size": 300,
                "next_page_token": next_page_token
            }

            r = requests.get(url, headers=headers, params=params)

            if r.status_code != 200:
                return {
                    "success": [],
                    "errors": [{
                        "status_code": r.status_code,
                        "error": r.text
                    }]
                }

            response_data = r.json()

            registrants = response_data.get("registrants", [])
            all_registrants.extend(registrants)

            next_page_token = response_data.get("next_page_token", "")
            if not next_page_token:
                break

        # ===============================
        # CREATION DICTIONNAIRE EMAIL
        # ===============================
        registrant_map = {
            r["email"].lower(): r
            for r in all_registrants
        }

        # ===============================
        # MATCH DEMANDE
        # ===============================
        for email in requested_emails:

            if email in registrant_map:
                registrant = registrant_map[email]

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

        return {
            "success": success,
            "errors": errors
        }

    except Exception as e:
        return {
            "success": [],
            "errors": [{
                "status_code": 500,
                "error": str(e)
            }]
        }
