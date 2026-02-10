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
# Henter hemmeligheder fra Render Environment
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Server indstillinger for Simply.dk
IMAP_SERVER = "mail.simply.com"   # Til at modtage
SMTP_SERVER = "smtp.simply.com"   # Til at sende
SMTP_PORT = 587                   # Vi bruger 587 med STARTTLS (Bedst til Cloud)
DATA_FILE = "database.json"       # Fil til at gemme hukommelse

# Opsæt OpenAI klient
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        print(f"Kunne ikke initialisere OpenAI: {e}")

# --- AI KONTEKST (Hjernen) ---
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

    except Exception as e:
        print(f"AI Fejl: {e}")
        return {"score": 0, "summary": "AI Analyse Fejlede", "category": "SYSTEM", "proximity": "Fejl"}

# --- 4. EMAIL FUNKTIONER (MODTAGE - IMAP) ---
def fetch_emails():
    print("--- Tjekker for mails ---")
    if not EMAIL_PASS: return

    try:
        with MailBox(IMAP_SERVER).login(EMAIL_USER, EMAIL_PASS) as mailbox:
            # Hent de nyeste 5 mails (også læste, for at være sikker)
            for msg in mailbox.fetch(limit=5, reverse=True):
                
                # DUBLET-TJEK: Har vi allerede denne mail?
                # Vi tjekker på Emne + Afsender for at undgå at oprette den samme igen
                is_duplicate = any(x['title'] == msg.subject and x['real_email'] == msg.from_ for x in database)
                if is_duplicate:
                    continue 

                print(f"Ny mail fundet: {msg.subject}")
                
                # Kør AI Analyse
                analysis = analyze_with_gpt(msg.subject, msg.text or msg.html, msg.from_)
                
                final_title = msg.subject
                if analysis.get("category") == "SYSTEM": final_title = f"⚠️ {msg.subject}"

                new_entry = {
                    "id": int(datetime.now().timestamp()), # Unikt ID baseret på tid
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
                
            save_database() # Gem til fil
                
    except Exception as e:
        print(f"IMAP Fejl: {e}")

# --- 5. EMAIL FUNKTIONER (SENDE - SMTP) ---
def send_reward_email(recipient_email, amount, idea_title):
    """ Sender email via Port 587 med STARTTLS """
    print(f"Forsøger at sende mail til {recipient_email}...")
    
    msg = EmailMessage()
    msg['Subject'] = "🏆 Din idé er blevet belønnet!"
    msg['From'] = EMAIL_USER
    msg['To'] = recipient_email
    
    body = f"""
Hej,

Tusind tak for dit input vedrørende: "{idea_title}".
Ledelsen har vurderet din idé, og den er udvalgt til implementering!

Du har modtaget en strakspramie på: {amount} kr.

De bedste hilsner,
NinetySix
    """
    msg.set_content(body)

    try:
        # Vi bruger her 'SMTP' (ikke SMTP_SSL) til port 587
        # Timeout på 10 sekunder forhindrer at serveren hænger
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
            # server.set_debuglevel(1) # Fjern kommentar hvis du vil se rå netværksdata i loggen
            server.ehlo()            # Sig hej til serveren
            server.starttls()        # Bed om kryptering
            server.ehlo()            # Sig hej igen (krypteret)
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
            print("Email sendt succesfuldt!")
            return True
    except Exception as e:
        print(f"SMTP FEJL: {e}")
        return False

# --- 6. SETUP & LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_database() # Hent gamle data
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_emails, 'interval', minutes=2)
    scheduler.start()
    
    yield
    # Her lukker serveren ned

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 7. ENDPOINTS ---

@app.get("/")
def read_root():
    return {"status": "Online", "message": "NinetySix Backend kører. Brug /api/inputs"}

@app.get("/api/inputs")
def get_inputs():
    # Returner data minus emails (for sikkerhed på frontend)
    return [{k:v for k,v in i.items() if k!='real_email'} for i in database]

class StatusUpdate(BaseModel):
    status: str

@app.put("/api/status/{item_id}")
def update_status(item_id: int, update: StatusUpdate):
    item = next((x for x in database if x["id"] == item_id), None)
    if item: 
        item["status"] = update.status
        save_database()
    return {"success": True}

class RewardRequest(BaseModel):
    amount: int

@app.post("/api/reward/{item_id}")
def reward_user(item_id: int, request: RewardRequest):
    item = next((x for x in database if x["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Ikke fundet")
    
    # Send mailen
    success = send_reward_email(item.get('real_email'), request.amount, item.get('title'))
    
    if success:
        item["status"] = "rewarded"
        save_database()
        return {"success": True}
    else:
        # Hvis SMTP fejler, giver vi besked til dashboardet
        raise HTTPException(status_code=500, detail="Kunne ikke sende mail (SMTP Timeout/Fejl)")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
