from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime

app = FastAPI()

# Tillad at din HTML fil snakker med serveren
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DATAMODEL ---
# Dette er din "database". I virkeligheden vil AI'en indsætte data her.
# Vi gemmer 'real_email', men sender den ikke til dashboardet.
database = [
    { 
        "id": 1, 
        "category": "IT / Systemer", 
        "title": "Dobbeltarbejde i ESDH systemet", 
        "content": "Vi bruger ca. 30 min dagligt pr. medarbejder...", 
        "aiScore": 94, 
        "proximity": "Høj", 
        "status": "inbox", 
        "date": "10. Feb", 
        "groupCount": 4, 
        "analysis": "Score 94: Direkte spild af årsværk.",
        "real_email": "jens@firma.dk" # Skjult for frontend
    },
    { 
        "id": 6, 
        "category": "HR / Rekruttering", 
        "title": "Vi mister kandidater", 
        "content": "Det tager 6 uger fra 1. samtale til kontrakt...", 
        "aiScore": 91, 
        "proximity": "Høj", 
        "status": "inbox", 
        "date": "10. Feb", 
        "groupCount": 0, 
        "analysis": "Score 91: Kritisk flaskehals for vækst.",
        "real_email": "anne@firma.dk"
    }
]

# --- ENDPOINTS ---

@app.get("/api/inputs")
def get_inputs():
    """Sender data til dit dashboard"""
    # Vi returnerer alt undtagen 'real_email' (sikkerhed)
    public_data = []
    for item in database:
        safe_item = item.copy()
        if "real_email" in safe_item:
            del safe_item["real_email"]
        public_data.append(safe_item)
    return public_data

class RewardRequest(BaseModel):
    amount: int

@app.post("/api/reward/{item_id}")
def reward_user(item_id: int, request: RewardRequest):
    """Håndterer belønning og sender email"""
    # Find inputtet
    item = next((x for x in database if x["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Ikke fundet")
    
    # 1. Opdater status
    item["status"] = "rewarded"
    
    # 2. Hent den hemmelige email
    email_to = item.get("real_email", "ukendt@firma.dk")
    
    # 3. Her ville vi sende den rigtige email via SMTP
    print(f"--- SENDER MAIL ---")
    print(f"Til: {email_to}")
    print(f"Emne: Tak for dit input!")
    print(f"Besked: Du har modtaget en belønning på {request.amount} kr.")
    print(f"-------------------")
    
    return {"success": True, "new_status": "rewarded"}

# Start serveren med: uvicorn app:app --reload
