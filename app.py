import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import pg8000.native
import hashlib, random, string
from datetime import datetime, date
from fastapi.responses import FileResponse
import uvicorn

# ──────────────────────────────────────────────────────────────────────────────
# Supabase PostgreSQL connection config
# ──────────────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     "aws-0-ap-northeast-1.pooler.supabase.com",
    "port":     6543,
    "user":     "postgres.rorsnqtkwkzcoultokvs",
    "password": "YOUR_SUPABASE_DB_PASSWORD",   # ← replace with your DB password
    "database": "postgres",
    "ssl_context": True,
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
    return pg8000.native.Connection(**DB_CONFIG)

def rows_as_dicts(conn, query, params=()):
    """Execute query and return list of dicts."""
    result = conn.run(query, *params)
    cols   = [c["name"] for c in conn.columns]
    return [dict(zip(cols, row)) for row in result]

def row_as_dict(conn, query, params=()):
    rows = rows_as_dicts(conn, query, params)
    return rows[0] if rows else None

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def gen_ref(prefix="LN") -> str:
    return prefix + "-" + "".join(random.choices(string.digits, k=8))

def next_customer_id(conn) -> str:
    row = row_as_dict(conn, "SELECT MAX(id) AS max_id FROM users")
    nxt = (row["max_id"] or 10000) + 1
    return f"CUST-{nxt}"

def serialize(obj):
    """Convert datetime/date to string for JSON."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj

def serialize_row(row: dict) -> dict:
    return {k: serialize(v) for k, v in row.items()}

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
    try:
        if row_as_dict(conn, "SELECT id FROM users WHERE email=:1", (body.email,)):
            raise HTTPException(400, "Email already registered")
        if row_as_dict(conn, "SELECT id FROM users WHERE mobile=:1", (body.mobile,)):
            raise HTTPException(400, "Mobile number already registered")

        cid = next_customer_id(conn)
        conn.run("""
            INSERT INTO users (customer_id, first_name, last_name, email, mobile, dob, gender, password)
            VALUES (:1, :2, :3, :4, :5, :6, :7, :8)
        """, cid, body.first_name, body.last_name, body.email, body.mobile,
             body.dob, body.gender, hash_pw(body.password))
    finally:
        conn.close()

    return {"message": "Account created", "customer_id": cid}


@app.post("/auth/login")
def login(body: LoginIn):
    conn = get_conn()
    try:
        ident = body.identifier.strip()
        user  = row_as_dict(conn, """
            SELECT * FROM users
            WHERE customer_id=:1 OR email=:2 OR mobile=:3
        """, (ident, ident, ident))
    finally:
        conn.close()

    if not user or user["password"] != hash_pw(body.password):
        raise HTTPException(401, "Invalid credentials")

    safe = {k: serialize(v) for k, v in user.items() if k != "password"}
    return {"message": "Login successful", "user": safe}


# ──────────────────────────────────────────────────────────────────────────────
# LOAN routes
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/loans/{customer_id}")
def create_loan(customer_id: str, body: LoanIn):
    conn = get_conn()
    try:
        row = row_as_dict(conn, "SELECT id FROM users WHERE customer_id=:1", (customer_id,))
        if not row:
            raise HTTPException(404, "User not found")
        user_id = row["id"]

        ref    = gen_ref("LN")
        status = body.ai_assessment if body.ai_assessment in ("approved", "rejected") else "pending"

        conn.run("""
            INSERT INTO loans
              (user_id, reference, loan_type, loan_type_name, amount, tenure_years,
               cibil_score, income_annum, education, self_employed, ai_assessment, status)
            VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9, :10, :11, :12)
        """, user_id, ref, body.loan_type, body.loan_type_name, body.amount,
             body.tenure_years, body.cibil_score, body.income_annum,
             body.education, body.self_employed, body.ai_assessment, status)
    finally:
        conn.close()

    return {"message": "Loan saved", "reference": ref, "status": status}


@app.get("/loans/{customer_id}")
def get_loans(customer_id: str):
    conn = get_conn()
    try:
        row = row_as_dict(conn, "SELECT id FROM users WHERE customer_id=:1", (customer_id,))
        if not row:
            raise HTTPException(404, "User not found")

        loans = rows_as_dicts(conn, """
            SELECT * FROM loans WHERE user_id=:1 ORDER BY created_at DESC
        """, (row["id"],))
    finally:
        conn.close()

    return {"loans": [serialize_row(l) for l in loans], "total": len(loans)}


@app.get("/loans/{customer_id}/stats")
def loan_stats(customer_id: str):
    conn = get_conn()
    try:
        row = row_as_dict(conn, "SELECT id FROM users WHERE customer_id=:1", (customer_id,))
        if not row:
            raise HTTPException(404, "User not found")

        stats = row_as_dict(conn, """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS approved,
              SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected
            FROM loans WHERE user_id=:1
        """, (row["id"],))
    finally:
        conn.close()

    return {
        "total":    int(stats["total"]    or 0),
        "approved": int(stats["approved"] or 0),
        "rejected": int(stats["rejected"] or 0),
    }


# ──────────────────────────────────────────────────────────────────────────────
# AI PREDICT route
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/predict")
def predict(body: PredictIn):
    score     = 0
    threshold = 5

    if body.cibil_score >= 750:   score += 3
    elif body.cibil_score >= 650: score += 2
    elif body.cibil_score >= 550: score += 1

    ratio = body.loan_amount / max(body.income_annum, 1)
    if ratio <= 0.3:   score += 2
    elif ratio <= 0.5: score += 1

    if body.education == "Graduate": score += 1
    if body.loan_term <= 15:         score += 1

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