import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import psycopg2
import psycopg2.extras
import hashlib, random, string
from datetime import datetime
from fastapi.responses import FileResponse
import uvicorn

# ──────────────────────────────────────────────────────────────────────────────
# Supabase PostgreSQL connection config
# Replace with your actual Supabase DB password from:
# Supabase Dashboard → Settings → Database → Connection string
# ──────────────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     "aws-0-ap-northeast-1.pooler.supabase.com",   # Supabase pooler host
    "port":     6543,                                          # Supabase pooler port (Transaction mode)
    "user":     "postgres.rorsnqtkwkzcoultokvs",              # Supabase project user
    "password": "YOUR_SUPABASE_DB_PASSWORD",                  # ← replace with your DB password
    "dbname":   "postgres",
}

app = FastAPI(title="NexaBank API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(**DB_CONFIG, cursor_factory=psycopg2.extras.RealDictCursor)

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def gen_ref(prefix="LN") -> str:
    return prefix + "-" + "".join(random.choices(string.digits, k=8))

def next_customer_id(cursor) -> str:
    cursor.execute("SELECT MAX(id) AS max_id FROM users")
    row = cursor.fetchone()
    nxt = (row["max_id"] or 10000) + 1
    return f"CUST-{nxt}"

# ──────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ──────────────────────────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    first_name: str
    last_name:  str
    email:      str
    mobile:     str
    dob:        Optional[str] = None
    gender:     Optional[str] = None
    password:   str

class LoginIn(BaseModel):
    identifier: str   # customer_id OR email OR mobile
    password:   str

class LoanIn(BaseModel):
    loan_type:      str
    loan_type_name: str
    amount:         float
    tenure_years:   int
    cibil_score:    Optional[int]   = None
    income_annum:   Optional[float] = None
    education:      Optional[str]   = None
    self_employed:  Optional[str]   = None
    ai_assessment:  Optional[str]   = None   # 'approved' | 'rejected'

class PredictIn(BaseModel):
    education:     str
    self_employed: str
    income_annum:  float
    loan_amount:   float
    loan_term:     int
    cibil_score:   int

# ──────────────────────────────────────────────────────────────────────────────
# AUTH routes
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/auth/register")
def register(body: RegisterIn):
    conn = get_conn()
    cur  = conn.cursor()

    # duplicate checks
    cur.execute("SELECT id FROM users WHERE email=%s", (body.email,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(400, "Email already registered")
    cur.execute("SELECT id FROM users WHERE mobile=%s", (body.mobile,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(400, "Mobile number already registered")

    cid = next_customer_id(cur)

    cur.execute("""
        INSERT INTO users (customer_id, first_name, last_name, email, mobile, dob, gender, password)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (cid, body.first_name, body.last_name, body.email, body.mobile,
          body.dob, body.gender, hash_pw(body.password)))

    conn.commit()
    conn.close()
    return {"message": "Account created", "customer_id": cid}


@app.post("/auth/login")
def login(body: LoginIn):
    conn = get_conn()
    cur  = conn.cursor()

    ident = body.identifier.strip()
    cur.execute("""
        SELECT * FROM users
        WHERE customer_id=%s OR email=%s OR mobile=%s
    """, (ident, ident, ident))
    user = cur.fetchone()
    conn.close()

    if not user or user["password"] != hash_pw(body.password):
        raise HTTPException(401, "Invalid credentials")

    safe = {k: v for k, v in user.items() if k != "password"}
    # convert datetime/date to string
    for key, val in safe.items():
        if isinstance(val, (datetime,)):
            safe[key] = val.isoformat()

    return {"message": "Login successful", "user": safe}


# ──────────────────────────────────────────────────────────────────────────────
# LOAN routes
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/loans/{customer_id}")
def create_loan(customer_id: str, body: LoanIn):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT id FROM users WHERE customer_id=%s", (customer_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "User not found")
    user_id = row["id"]

    ref    = gen_ref("LN")
    status = body.ai_assessment if body.ai_assessment in ("approved", "rejected") else "pending"

    cur.execute("""
        INSERT INTO loans
          (user_id, reference, loan_type, loan_type_name, amount, tenure_years,
           cibil_score, income_annum, education, self_employed, ai_assessment, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (user_id, ref, body.loan_type, body.loan_type_name, body.amount,
          body.tenure_years, body.cibil_score, body.income_annum,
          body.education, body.self_employed, body.ai_assessment, status))

    conn.commit()
    conn.close()
    return {"message": "Loan saved", "reference": ref, "status": status}


@app.get("/loans/{customer_id}")
def get_loans(customer_id: str):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT id FROM users WHERE customer_id=%s", (customer_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "User not found")

    cur.execute("""
        SELECT * FROM loans WHERE user_id=%s ORDER BY created_at DESC
    """, (row["id"],))
    loans = cur.fetchall()
    conn.close()

    result = []
    for loan in loans:
        loan_dict = dict(loan)
        if isinstance(loan_dict.get("created_at"), datetime):
            loan_dict["created_at"] = loan_dict["created_at"].isoformat()
        result.append(loan_dict)

    return {"loans": result, "total": len(result)}


@app.get("/loans/{customer_id}/stats")
def loan_stats(customer_id: str):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT id FROM users WHERE customer_id=%s", (customer_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "User not found")

    cur.execute("""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved,
          SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected
        FROM loans WHERE user_id=%s
    """, (row["id"],))
    stats = cur.fetchone()
    conn.close()

    return {
        "total":    int(stats["total"]    or 0),
        "approved": int(stats["approved"] or 0),
        "rejected": int(stats["rejected"] or 0),
    }


# ──────────────────────────────────────────────────────────────────────────────
# AI PREDICT route  (rule-based — swap for ML model if available)
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/predict")
def predict(body: PredictIn):
    score     = 0
    threshold = 5

    # CIBIL score weight (max 3 pts)
    if body.cibil_score >= 750:   score += 3
    elif body.cibil_score >= 650: score += 2
    elif body.cibil_score >= 550: score += 1

    # Debt-to-income ratio (max 2 pts)
    ratio = body.loan_amount / max(body.income_annum, 1)
    if ratio <= 0.3:   score += 2
    elif ratio <= 0.5: score += 1

    # Education bonus
    if body.education == "Graduate": score += 1

    # Loan term bonus
    if body.loan_term <= 15: score += 1

    approved = score >= threshold

    return {
        "approved": approved,
        "details": {
            "total_score": score,
            "threshold":   threshold,
            "cibil_band":  "Good" if body.cibil_score >= 700 else "Fair" if body.cibil_score >= 550 else "Poor",
            "dti_ratio":   round(ratio * 100, 1),
        }
    }


# ──────────────────────────────────────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return FileResponse("user.html")

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 7860))
    )