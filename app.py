import os
import json
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from imap_tools import MailBox, A
from openai import OpenAI

# --- 1. KONFIGURATION ---
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

IMAP_SERVER = "mail.simply.com"
SMTP_SERVER = "smtp.simply.com"
SMTP_PORT = 465 
DATA_FILE = "database.json" # Her gemmer vi dataen

# Opsæt OpenAI
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        print(f"Kunne ikke initialisere OpenAI: {e}")

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

# --- 2. DATABASE & FIL-HÅNDTERING ---
database = []

def load_database():
    """ Indlæser data fra filen når serveren starter """
    global database
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                database = json.load(f)
            print(f"--- Hukommelse indlæst: {len(database)} poster fundet ---")
        except Exception as e:
            print(f"Kunne ikke læse database fil: {e}")
            database = []
    else:
        print("--- Ingen tidligere hukommelse fundet (Starter frisk) ---")
        database = []

def save_database():
    """ Gemmer data ned i filen """
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(database, f, ensure_ascii=False, indent=4)
        print("--- Data gemt i fil ---")
    except Exception as e:
        print(f"Kunne ikke gemme database: {e}")

# --- 3. AI FUNKTIONER ---
def analyze_with_gpt(subject, body, sender):
    if not client:
        return {"score": 0, "summary": "FEJL: Mangler Key", "category": "SYSTEM", "proximity": "Ingen"}

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
    except Exception:
        return {"score": 0, "summary": "AI Fejl", "category": "SYSTEM", "proximity": "Fejl"}

# --- 4. EMAIL FUNKTIONER (MODTAGE) ---
def fetch_emails():
    print("--- Tjekker for mails ---")
    if not EMAIL_PASS: return

    try:
        with MailBox(IMAP_SERVER).login(EMAIL_USER, EMAIL_PASS) as mailbox:
            # Hent mails (vi tjekker nu de seneste 10 mails, også læste, for at være sikre)
            # Men for at undgå dobbeltarbejde, tjekker vi om vi allerede har dem
            for msg in mailbox.fetch(limit=5, reverse=True): 
                
                # TJEK FOR DUBLETTER: Har vi allerede en mail med dette emne og dato?
                is_duplicate = any(x['title'] == msg.subject and x['real_email'] == msg.from_ for x in database)
                
                if is_duplicate:
                    continue # Spring over, vi har den allerede

                print(f"Ny mail fundet: {msg.subject}")
                
                # Kør AI Analyse
                analysis = analyze_with_gpt(msg.subject, msg.text or msg.html, msg.from_)
                
                final_title = msg.subject
                if analysis.get("category") == "SYSTEM": final_title = f"⚠️ {msg.subject}"

                new_entry = {
                    "id": int(datetime.now().timestamp()), # Bruger tid som unikt ID
                    "category": analysis.get("category", "Indbakke"),
                    "title": final_title,
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
                print(f"Tilføjet: {msg.subject}")
                
            # VIGTIGT: Gem ændringerne!
            save_database()
                
    except Exception as e:
        print(f"IMAP Fejl: {e}")

# --- 5. EMAIL FUNKTIONER (SENDE) ---
def send_reward_email(recipient_email, amount, idea_title):
    msg = EmailMessage()
    msg['Subject'] = "🏆 Din idé er blevet belønnet!"
    msg['From'] = EMAIL_USER
    msg['To'] = recipient_email
    
    body = f"""
Hej,

Tusind tak for dit input vedrørende: "{idea_title}".
Vi har udvalgt din idé til implementering!

Du har modtaget en strakspramie på: {amount} kr.

De bedste hilsner,
NinetySix
    """
    msg.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
            return True
    except Exception as e:
        print(f"SMTP Fejl: {e}")
        return False

# --- 6. SETUP ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_database() # 1. HENT GAMLE DATA
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_emails, 'interval', minutes=2)
    scheduler.start()
    
    yield
    # (Her kunne man også gemme data når serveren lukker)

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# --- 7. ENDPOINTS ---
class StatusUpdate(BaseModel):
    status: str

@app.get("/api/inputs")
def get_inputs():
    return [{k:v for k,v in i.items() if k!='real_email'} for i in database]

@app.put("/api/status/{item_id}")
def update_status(item_id: int, update: StatusUpdate):
    item = next((x for x in database if x["id"] == item_id), None)
    if item: 
        item["status"] = update.status
        save_database() # GEM EFTER STATUS ÆNDRING
    return {"success": True}

class RewardRequest(BaseModel):
    amount: int

@app.post("/api/reward/{item_id}")
def reward_user(item_id: int, request: RewardRequest):
    item = next((x for x in database if x["id"] == item_id), None)
    if not item: raise HTTPException(404, "Ikke fundet")
    
    success = send_reward_email(item.get('real_email'), request.amount, item.get('title'))
    
    if success:
        item["status"] = "rewarded"
        save_database() # GEM AT VI HAR BELØNNET
        return {"success": True}
    else:
        raise HTTPException(500, "Mail kunne ikke sendes")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
