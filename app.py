import os
import random
import json
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from imap_tools import MailBox, A
from openai import OpenAI

# --- 1. KONFIGURATION ---
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
IMAP_SERVER = "mail.simply.com"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") # Hentes fra Render

# Opsæt OpenAI klienten
client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

# --- AI KONTEKST ---
ORG_CONTEXT = """
Du er en forretnings-AI for virksomheden "NinetySix".
Din opgave er at vurdere mails fra medarbejdere ud fra forretningsværdi.

Scoringsregler (0-100):
- < 30: Småting, personlige klager.
- 30-60: Irritationsmomenter, mindre tidsspilde.
- 60-85: Gode forslag til effektivisering.
- > 85: Kritiske problemer eller enorme muligheder.

Vurder også "Proximity" (Nærhed): Er afsenderen tæt på problemet?
"""

# --- 2. DATABASE ---
database = [
    {
        "id": 1,
        "category": "System",
        "title": "System Startet",
        "content": "Venter på emails...",
        "aiScore": 100,
        "proximity": "System",
        "status": "inbox",
        "date": datetime.now().strftime("%d. %b"),
        "groupCount": 0,
        "analysis": "Systemet kører.",
        "real_email": "admin@ninetysix.dk"
    }
]

# --- 3. AI FUNKTIONER ---
def analyze_with_gpt(subject, body, sender):
    if not client:
        print("Ingen API nøgle fundet - Bruger simulation")
        return {"score": random.randint(40,80), "summary": "Simuleret analyse", "category": "Indbakke", "proximity": "Ukendt"}

    prompt = f"""
    Afsender: {sender}
    Emne: {subject}
    Indhold: {body}
    
    Analyser dette ud fra konteksten. Returner JSON:
    {{
        "score": (int 0-100),
        "summary": (kort tekst på dansk, max 10 ord),
        "proximity": (tekst: "Høj", "Mellem" eller "Lav"),
        "category": (tekst: "IT", "Salg", "Drift", "HR" eller "Ledelse")
    }}
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Du er en JSON maskine. Svar kun valid JSON. " + ORG_CONTEXT},
                {"role": "user", "content": prompt}
            ],
            response_format={ "type": "json_object" }
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"GPT Fejl: {e}")
        return {"score": 50, "summary": "Fejl i analyse", "category": "Fejl", "proximity": "Fejl"}

# --- 4. EMAIL FUNKTIONER ---
def fetch_emails():
    print("--- Tjekker for mails ---")
    if not EMAIL_PASS: return

    try:
        with MailBox(IMAP_SERVER).login(EMAIL_USER, EMAIL_PASS) as mailbox:
            # Hent alle ulæste mails
            for msg in mailbox.fetch(A(seen=False)):
                print(f"Behandler: {msg.subject}")
                
                # Kør AI Analyse
                analysis = analyze_with_gpt(msg.subject, msg.text or msg.html, msg.from_)
                
                new_entry = {
                    "id": len(database) + 1,
                    "category": analysis.get("category", "Indbakke"),
                    "title": msg.subject,
                    "content": (msg.text or msg.html)[:400] + "...",
                    "aiScore": analysis.get("score", 0),
                    "proximity": analysis.get("proximity", "Ukendt"),
                    "status": "inbox",
                    "date": datetime.now().strftime("%d. %b"),
                    "groupCount": 0,
                    "analysis": analysis.get("summary", "Ingen analyse"),
                    "real_email": msg.from_
                }
                
                database.insert(0, new_entry)
                print(f"Tilføjet: {msg.subject} (Score: {new_entry['aiScore']})")
                
    except Exception as e:
        print(f"Mail fejl: {e}")

# --- 5. SETUP ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_emails, 'interval', minutes=2)
    scheduler.start()
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 6. ENDPOINTS ---
class StatusUpdate(BaseModel):
    status: str

@app.get("/api/inputs")
def get_inputs():
    return [{k:v for k,v in i.items() if k!='real_email'} for i in database]

@app.put("/api/status/{item_id}")
def update_status(item_id: int, update: StatusUpdate):
    item = next((x for x in database if x["id"] == item_id), None)
    if item: item["status"] = update.status
    return {"success": True}

class RewardRequest(BaseModel):
    amount: int

@app.post("/api/reward/{item_id}")
def reward_user(item_id: int, request: RewardRequest):
    item = next((x for x in database if x["id"] == item_id), None)
    if item:
        print(f"$$$ BELØNNING SENDT TIL {item.get('real_email')} PÅ {request.amount} KR $$$")
        item["status"] = "rewarded"
        return {"success": True}
    raise HTTPException(404, "Ikke fundet")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
