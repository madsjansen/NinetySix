import os
import json
import time
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
IMAP_SERVER = "mail.simply.com"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") 

# Opsæt OpenAI klienten (hvis nøglen findes)
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

# --- 2. DATABASE ---
database = []

# --- 3. AI FUNKTIONER ---
def analyze_with_gpt(subject, body, sender):
    """
    Denne funktion nægter nu at gætte.
    Hvis der er fejl, returnerer den en fejl-objekt.
    """
    
    # 1. TJEK: Har vi overhovedet en klient?
    if not client:
        print("KRITISK: Ingen API nøgle fundet!")
        return {
            "score": 0, 
            "summary": "FEJL 404: Mangler OpenAI API Key i Render Environment.", 
            "category": "SYSTEM FEJL", 
            "proximity": "Ingen"
        }

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
        # Parse svaret
        content = json.loads(response.choices[0].message.content)
        return content

    except Exception as e:
        # 2. TJEK: Fejlede kaldet til OpenAI? (F.eks. forkert kode eller tom konto)
        error_msg = str(e)
        print(f"OpenAI API Fejl: {error_msg}")
        
        return {
            "score": 0, 
            "summary": f"API FEJL: {error_msg[:30]}...", # Viser de første 30 tegn af fejlen
            "category": "SYSTEM FEJL", 
            "proximity": "Fejl"
        }

# --- 4. EMAIL FUNKTIONER ---
def fetch_emails():
    print("--- Tjekker for mails ---")
    if not EMAIL_PASS: 
        print("Mangler EMAIL_PASS - skipper tjek")
        return

    try:
        with MailBox(IMAP_SERVER).login(EMAIL_USER, EMAIL_PASS) as mailbox:
            # Hent alle ulæste mails
            for msg in mailbox.fetch(A(seen=False)):
                print(f"Behandler: {msg.subject}")
                
                # Kør AI Analyse (Nu uden tilfældigheder)
                analysis = analyze_with_gpt(msg.subject, msg.text or msg.html, msg.from_)
                
                # Hvis kategorien er "SYSTEM FEJL", sætter vi titlen til at reflektere dette
                final_title = msg.subject
                if analysis.get("category") == "SYSTEM FEJL":
                    final_title = f"⚠️ FEJL: {msg.subject}"

                new_entry = {
                    "id": len(database) + 1,
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
                print(f"Tilføjet: {msg.subject} (Score: {new_entry['aiScore']})")
                
    except Exception as e:
        print(f"IMAP/Mail fejl: {e}")

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
    # Returner data minus emails
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
