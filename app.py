import os
import random
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from imap_tools import MailBox, A

# --- 1. KONFIGURATION ---
# Vi henter dine hemmelige koder fra Render's "Environment Variables"
EMAIL_USER = os.getenv("EMAIL_USER")  # Forventer: ninetysix@madsjansen.dk
EMAIL_PASS = os.getenv("EMAIL_PASS")  # Forventer: Din mail-adgangskode
IMAP_SERVER = "mail.simply.com"       # Simply.dk's server

# --- 2. DATABASE (Hukommelse) ---
# Vi starter med en test-besked, så du kan se, systemet virker
database = [
    {
        "id": 1,
        "category": "System",
        "title": "Systemet er live",
        "content": "Jeg overvåger nu ninetysix@madsjansen.dk hvert 2. minut for nye idéer.",
        "aiScore": 100,
        "proximity": "System",
        "status": "inbox",
        "date": datetime.now().strftime("%d. %b"),
        "groupCount": 0,
        "analysis": "Forbindelse til Simply.dk oprettet succesfuldt.",
        "real_email": "admin@ninetysix.dk"
    }
]

# --- 3. EMAIL FUNKTIONER ---
def fetch_emails():
    """ Denne funktion kører automatisk hvert 2. minut """
    print(f"--- Forbinder til {IMAP_SERVER} som {EMAIL_USER} ---")

    if not EMAIL_PASS:
        print("FEJL: Du mangler at indtaste EMAIL_PASS i Render Environment!")
        return

    try:
        # Log ind på Simply.dk
        with MailBox(IMAP_SERVER).login(EMAIL_USER, EMAIL_PASS) as mailbox:
            
            # Hent kun mails der IKKE er læst endnu (seen=False)
            for msg in mailbox.fetch(A(seen=False)):
                print(f"Ny mail modtaget fra: {msg.from_}")
                
                # Her simulerer vi AI-analysen (giver et tal mellem 30-99)
                # Senere kan vi indsætte rigtig OpenAI her
                simulated_score = random.randint(30, 99)
                
                # Opret det nye kort til dashboardet
                new_entry = {
                    "id": len(database) + 1,
                    "category": "Indbakke",  # Vi sætter den standard til Indbakke
                    "title": msg.subject,    # Emnefeltet bliver overskriften
                    "content": msg.text or msg.html or "(Intet tekstindhold)",
                    "aiScore": simulated_score,
                    "proximity": "Ukendt",   # AI skal senere vurdere dette
                    "status": "inbox",
                    "date": datetime.now().strftime("%d. %b"),
                    "groupCount": 0,
                    "analysis": f"Automatisk import fra email: {msg.subject}",
                    "real_email": msg.from_  # Gemmes skjult til belønning
                }
                
                # Læg den nye mail øverst i bunken
                database.insert(0, new_entry)
                print(f"Succes: '{msg.subject}' er lagt på dashboardet.")

    except Exception as e:
        print(f"Fejl ved email-tjek: {e}")

# --- 4. OPSTART & BAGGRUNDSJOBS ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start uret der tjekker mails
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_emails, 'interval', minutes=2)
    scheduler.start()
    print("Email-robotten er startet...")
    yield

app = FastAPI(lifespan=lifespan)

# Tillad at din hjemmeside snakker med denne server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 5. API ENDPOINTS (Indgangen) ---

@app.get("/api/inputs")
def get_inputs():
    """ Sender listen af idéer til din hjemmeside """
    # Vi laver en kopi og fjerner email-adressen før vi sender data ud (Sikkerhed)
    public_data = []
    for item in database:
        safe_item = item.copy()
        if "real_email" in safe_item:
            del safe_item["real_email"]
        public_data.append(safe_item)
    return public_data

# --- NYT: Model til statusopdatering ---
class StatusUpdate(BaseModel):
    status: str

@app.put("/api/status/{item_id}")
def update_status_endpoint(item_id: int, update: StatusUpdate):
    # Find elementet
    item = next((x for x in database if x["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Ikke fundet")
    
    # Opdater status i hukommelsen
    item["status"] = update.status
    print(f"Opdateret status for ID {item_id} -> {update.status}")
    return {"success": True}

class RewardRequest(BaseModel):
    amount: int

@app.post("/api/reward/{item_id}")
def reward_user(item_id: int, request: RewardRequest):
    """ Håndterer belønning """
    # Find idéen i databasen
    item = next((x for x in database if x["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Idé ikke fundet")
    
    email_modtager = item.get("real_email")
    
    # Her ville koden til at sende svar-mailen ligge
    # Vi printer det i loggen indtil SMTP er sat op
    print(f"------------------------------------------------")
    print(f"SENDER MAIL TIL: {email_modtager}")
    print(f"BESKED: Tak! Du har modtaget en belønning på {request.amount} kr.")
    print(f"------------------------------------------------")
    
    # Opdater status så knappen bliver grøn på dashboardet
    item["status"] = "rewarded"
    return {"success": True, "message": "Belønning registreret"}

if __name__ == "__main__":
    import uvicorn
    # Render kræver port 10000
    uvicorn.run(app, host="0.0.0.0", port=10000)
