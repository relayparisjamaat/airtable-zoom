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
def register_participant(token, webinar_id, email, name):
    print("Register email start function")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    print("Register email headers")
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
    print("Register email name split")
    payload = {
        "email": email,
        "first_name": firstname,
        "last_name": lastname,
    }
    
    print("Register email payload")
    r = requests.post(
        f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants",
        headers=headers,
        json=payload
    )
    print("Register result")
    print(r)
    print(r.status_code)

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
            "status_code": r.status_code + " : " + error_msg,
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

# ------------------------
# SEND EMAIL
# ------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background-color:#f4f6f8;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;padding:24px;">
          <tr>
            <td>
              <h2 style="margin:0 0 16px 0;font-weight:600;color:#222;">
                Inscription confirmée
              </h2>

              <p style="margin:0 0 16px 0;color:#333;">
                Votre inscription au webinaire suivant est confirmée :
              </p>

              <p style="margin:0 0 12px 0;font-size:16px;">
                <strong>{{WEBINAR_NAME}}</strong>
              </p>

              <p style="margin:0 0 20px 0;color:#333;">
                <strong>Date :</strong> {{DATE}}<br>
                <strong>Heure :</strong> {{TIME}} (heure de Paris)
              </p>

              <table cellpadding="0" cellspacing="0" align="center">
                <tr>
                  <td style="background:#0e72ed;border-radius:4px;">
                    <a href="{{JOIN_URL}}"
                       style="display:inline-block;padding:12px 24px;
                              color:#ffffff;text-decoration:none;
                              font-weight:600;">
                      Rejoindre le webinaire
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin:24px 0 0 0;font-size:12px;color:#777;">
                Ce message a été envoyé automatiquement. Merci de ne pas y répondre.
              </p>

            </td>
          </tr>
        </table>

        <p style="font-size:11px;color:#999;margin:12px 0;">
          © Zoom Video Communications, Inc. – Notification automatisée
        </p>
      </td>
    </tr>
  </table>
</body>
</html>
"""

def render_html(template, data):
    for k, v in data.items():
        template = template.replace(f"{{{{{k}}}}}", v)
    return template
    
def build_text_version(data):
    return f"""Inscription confirmée
    
    Webinaire : {data["WEBINAR_NAME"]}
    Date : {data["DATE"]}
    Heure : {data["TIME"]} (heure de Paris)
    Lien : {data["JOIN_URL"]}
    
    Ceci est un message automatique.
    """
    
def send_confirmation_email(to_email, subject, data):
    print("Start email function")
    html_content = render_html(HTML_TEMPLATE, data)
    print("HTML generated")
    text_content = build_text_version(data)
    print("Text content")

    payload = {
        "personalizations": [
            {
                "to": [{"email": to_email}],
                "subject": subject
            }
        ],
        "from": {
            "email": "relay.parisjamaat@gmail.com",
            "name": "Relay Paris Jamaat"
        },
        "content": [
            {"type": "text/plain", "value": text_content},
            {"type": "text/html", "value": html_content}
        ]
    }
    print("Payload done")
    
    r = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {os.getenv("SENDGRID_API_KEY")}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=10
    )
    
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
        try:
            result = register_participant(token, webinar_id, email, name)

            if result["success"]:
                success.append({
                    "email": result["email"],
                    "name": result["name"],
                    "join_url": result["join_url"]
                })
            else:
                errors.append({
                    "email": result["email"],
                    "name": result["name"],
                    "status_code": result["status_code"],
                    "error": result["error"]
                })

        except Exception as e:
            errors.append({
                "email": email,
                "name": name,
                "status_code": 500,
                "error": str(e)
            })

    return {
        "status": "ok",
        "webinar_id": webinar_id,
        "success": success,
        "errors": errors
    }
    
    '''
    mail_success = 0
    print("Registered emails : ")
    print(registered_emails)
    print(len(registered_emails), len(join_urls))
    
    for i in range(len(registered_emails)):
        email = registered_emails[i]
        join_url = join_urls[i]
        print("Trying to send email to " + email + " with link " + join_url)
        r = send_confirmation_email(to_email=email, subject="[RELAY] Inscription confirmée : " + webinar_name, data={
            "WEBINAR_NAME": webinar_name,
            "DATE": webinar_date,
            "TIME": webinar_time,
            "JOIN_URL": join_url}
        )
        print("Raw result : ")
        print(r)
        try:
            body = r.json()
        except ValueError:
            body = {"message": response.text}

        status = response.status_code
        print("Mail status : ")
        print(status)
        print(body)
        
        # Succès
        if status == 202: mail_succes += 1

    return {
        "status": status,
        "webinar_id": webinar_id,
        "registered": len(registered_emails),
        "requested": len(data["emails"]),
        "emails_sent": mail_success,
    }
    '''
    
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
def add_registrants_csv(data: dict):
    token = get_zoom_token()
    webinar_id = data["webinar_id"]
    
    csv_buffer = build_zoom_csv(data["emails"], data["names"])
    
    result = register_emails_csv(token, webinar_id, csv_buffer)
    
    return {
        "status": "ok",
        "webinar_id": webinar_id,
        "registered": result.get("total_records", 0),
        "requested": len(data["emails"])
    }
