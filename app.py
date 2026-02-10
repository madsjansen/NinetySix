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

# Server indstillinger for Simply.dk
IMAP_SERVER = "mail.simply.com" # Til at modtage (Læse)
SMTP_SERVER = "smtp.simply.com" # Til at sende (Skrive)
SMTP_PORT = 587                 # Standard port for sikker afsendelse

# Opsæt OpenAI klient
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
# Vi starter tomt - nye mails kommer automatisk ind.
database = []

# --- 3. AI FUNKTIONER ---
def analyze_with_gpt(subject, body, sender):
    if not client:
        return {
            "score": 0, 
            "summary": "FEJL: Mangler OpenAI Key", 
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
        return json.loads(response.choices[0].message.content)

    except Exception as e:
        print(f"OpenAI API Fejl: {e}")
        return {
            "score": 0, 
            "summary": "AI Analyse Fejlede", 
            "category": "SYSTEM FEJL", 
            "proximity": "Fejl"
        }

# --- 4. EMAIL FUNKTIONER (MODTAGE) ---
def fetch_emails():
    print("--- Tjekker for mails ---")
    if not EMAIL_PASS: return

    try:
        with MailBox(IMAP_SERVER).login(EMAIL_USER, EMAIL_PASS) as mailbox:
            for msg in mailbox.fetch(A(seen=False)):
                print(f"Behandler: {msg.subject}")
                
                # Kør AI Analyse
                analysis = analyze_with_gpt(msg.subject, msg.text or msg.html, msg.from_)
                
                # Håndter system fejl i titlen
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
        print(f"IMAP Fejl: {e}")

# --- 5. EMAIL FUNKTIONER (SENDE) ---
def send_reward_email(recipient_email, amount, idea_title):
    """ Sender en rigtig email via Simply.dk SMTP """
    print(f"Forsøger at sende mail til {recipient_email}...")
    
    msg = EmailMessage()
    msg['Subject'] = "🏆 Din idé er blevet belønnet!"
    msg['From'] = EMAIL_USER
    msg['To'] = recipient_email
    
    # Selve beskeden
    body = f"""
Hej,

Tusind tak for dit input vedrørende: "{idea_title}".

Ledelsen har vurderet din idé, og vi er glade for at kunne fortælle, at den er blevet udvalgt til implementering!

Som tak for dit bidrag til NinetySix, har du modtaget en strakspramie på:
{amount} kr.

Beløbet udbetales sammen med din næste løn.

Fortsæt det gode arbejde!

De bedste hilsner,
Innovations Dashboardet
NinetySix
    """
    msg.set_content(body)

    try:
        # Opret sikker forbindelse til Simply.dk
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=context) # Krypter forbindelsen
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
            print("Email sendt succesfuldt!")
            return True
    except Exception as e:
        print(f"FEJL VED AFSENDELSE AF MAIL: {e}")
        return False

# --- 6. SETUP ---
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

# --- 7. ENDPOINTS ---
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
    # 1. Find idéen
    item = next((x for x in database if x["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Ikke fundet")
    
    # 2. Send mailen
    email_success = send_reward_email(
        recipient_email=item.get('real_email'),
        amount=request.amount,
        idea_title=item.get('title')
    )
    
    # 3. Opdater status (kun hvis mailen blev sendt, eller vi beslutter at ignorere fejl)
    if email_success:
        item["status"] = "rewarded"
        return {"success": True, "message": "Mail sendt og status opdateret"}
    else:
        # Vi returnerer en fejl til dashboardet, så du ved mailen fejlede
        raise HTTPException(status_code=500, detail="Kunne ikke sende mail via SMTP")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
