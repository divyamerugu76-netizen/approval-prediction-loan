import os
import ssl
import hashlib
import random
import string
from datetime import datetime, date
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import pg8000.native
import uvicorn

# ── Load environment variables from .env file ─────────────────────────────────
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── SSL Context for Supabase ──────────────────────────────────────────────────
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# ── DB Config (reads from .env file) ──────────────────────────────────────────
DB_CONFIG = {
    "host":        os.environ.get("DB_HOST"),
    "port":        int(os.environ.get("DB_PORT", 5432)),
    "user":        os.environ.get("DB_USER", "postgres"),
    "password":    os.environ.get("DB_PASSWORD"),
    "database":    os.environ.get("DB_NAME", "postgres"),
    "ssl_context": ssl_ctx,
    "timeout":20,
}

# ── Validate required env vars ────────────────────────────────────────────────
if not DB_CONFIG["host"] or not DB_CONFIG["password"]:
    raise RuntimeError(
        "Missing required environment variables: DB_HOST and DB_PASSWORD must be set in your .env file."
    )

app = FastAPI(title="NexaBank API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DB helpers ────────────────────────────────────────────────────────────────
def get_conn():
    try:
        return pg8000.native.Connection(**DB_CONFIG)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database connection failed: {str(e)}")

def run_query(conn, query, **kwargs):
    result = conn.run(query, **kwargs)
    cols   = [c["name"] for c in conn.columns]
    return [dict(zip(cols, row)) for row in result]

def run_one(conn, query, **kwargs):
    rows = run_query(conn, query, **kwargs)
    return rows[0] if rows else None

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def gen_ref(prefix="LN") -> str:
    return prefix + "-" + "".join(random.choices(string.digits, k=8))

def next_customer_id(conn) -> str:
    row = run_one(conn, "SELECT MAX(id) AS max_id FROM users")
    nxt = (row["max_id"] or 10000) + 1
    return f"CUST-{nxt}"

def serialize(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj

def serialize_row(row: dict) -> dict:
    return {k: serialize(v) for k, v in row.items()}

# ── Models ────────────────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    first_name: str
    last_name:  str
    email:      str
    mobile:     str
    dob:        Optional[str] = None
    gender:     Optional[str] = None
    password:   str

class LoginIn(BaseModel):
    identifier: str
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
    ai_assessment:  Optional[str]   = None

class PredictIn(BaseModel):
    education:     str
    self_employed: str
    income_annum:  float
    loan_amount:   float
    loan_term:     int
    cibil_score:   int

# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    html_path = os.path.join(BASE_DIR, "user.html")
    if not os.path.isfile(html_path):
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "app": "NexaBank API v1.0.0"}
        )
    return FileResponse(html_path)

# ── Health check (also tests DB connection) ───────────────────────────────────
@app.get("/health")
def health():
    try:
        conn = get_conn()
        conn.run("SELECT 1")
        conn.close()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    return {
        "status":    "ok",
        "database":  db_status,
        "timestamp": datetime.utcnow().isoformat()
    }

# ── AUTH ──────────────────────────────────────────────────────────────────────
@app.post("/auth/register")
def register(body: RegisterIn):
    conn = get_conn()
    try:
        if run_one(conn, "SELECT id FROM users WHERE email=:email", email=body.email):
            raise HTTPException(400, "Email already registered")
        if run_one(conn, "SELECT id FROM users WHERE mobile=:mobile", mobile=body.mobile):
            raise HTTPException(400, "Mobile number already registered")

        cid = next_customer_id(conn)
        conn.run(
            """
            INSERT INTO users
              (customer_id, first_name, last_name, email, mobile, dob, gender, password)
            VALUES (:cid, :fn, :ln, :email, :mobile, :dob, :gender, :pw)
            """,
            cid=cid,
            fn=body.first_name,
            ln=body.last_name,
            email=body.email,
            mobile=body.mobile,
            dob=body.dob,
            gender=body.gender or "Prefer not to say",
            pw=hash_pw(body.password),
        )
    finally:
        conn.close()
    return {"message": "Account created", "customer_id": cid}


@app.post("/auth/login")
def login(body: LoginIn):
    conn = get_conn()
    try:
        ident = body.identifier.strip()
        user  = run_one(
            conn,
            "SELECT * FROM users WHERE customer_id=:id OR email=:id OR mobile=:id",
            id=ident,
        )
    finally:
        conn.close()

    if not user or user["password"] != hash_pw(body.password):
        raise HTTPException(401, "Invalid credentials")

    safe = {k: serialize(v) for k, v in user.items() if k != "password"}
    return {"message": "Login successful", "user": safe}


# ── LOANS ─────────────────────────────────────────────────────────────────────
@app.post("/loans/{customer_id}")
def create_loan(customer_id: str, body: LoanIn):
    conn = get_conn()
    try:
        row = run_one(conn, "SELECT id FROM users WHERE customer_id=:cid", cid=customer_id)
        if not row:
            raise HTTPException(404, "User not found")
        user_id = row["id"]

        ref    = gen_ref("LN")
        status = (
            body.ai_assessment
            if body.ai_assessment in ("approved", "rejected")
            else "pending"
        )

        conn.run(
            """
            INSERT INTO loans
              (user_id, reference, loan_type, loan_type_name, amount, tenure_years,
               cibil_score, income_annum, education, self_employed, ai_assessment, status)
            VALUES (:uid, :ref, :ltype, :ltname, :amt, :tenure,
                    :cibil, :income, :edu, :emp, :ai_assess, :status)
            """,
            uid=user_id,
            ref=ref,
            ltype=body.loan_type,
            ltname=body.loan_type_name,
            amt=body.amount,
            tenure=body.tenure_years,
            cibil=body.cibil_score,
            income=body.income_annum,
            edu=body.education,
            emp=body.self_employed,
            ai_assess=body.ai_assessment,
            status=status,
        )
    finally:
        conn.close()
    return {"message": "Loan saved", "reference": ref, "status": status}


@app.get("/loans/{customer_id}")
def get_loans(customer_id: str):
    conn = get_conn()
    try:
        row = run_one(conn, "SELECT id FROM users WHERE customer_id=:cid", cid=customer_id)
        if not row:
            raise HTTPException(404, "User not found")
        loans = run_query(
            conn,
            "SELECT * FROM loans WHERE user_id=:uid ORDER BY created_at DESC",
            uid=row["id"],
        )
    finally:
        conn.close()
    return {"loans": [serialize_row(l) for l in loans], "total": len(loans)}


@app.get("/loans/{customer_id}/stats")
def loan_stats(customer_id: str):
    conn = get_conn()
    try:
        row = run_one(conn, "SELECT id FROM users WHERE customer_id=:cid", cid=customer_id)
        if not row:
            raise HTTPException(404, "User not found")
        stats = run_one(
            conn,
            """
            SELECT
              COUNT(*)                                                  AS total,
              SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END)       AS approved,
              SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END)       AS rejected
            FROM loans
            WHERE user_id=:uid
            """,
            uid=row["id"],
        )
    finally:
        conn.close()
    return {
        "total":    int(stats["total"]    or 0),
        "approved": int(stats["approved"] or 0),
        "rejected": int(stats["rejected"] or 0),
    }


# ── PREDICT ───────────────────────────────────────────────────────────────────
@app.post("/predict")
def predict(body: PredictIn):
    score     = 0
    threshold = 5

    # CIBIL score band
    if body.cibil_score >= 750:
        score += 3
    elif body.cibil_score >= 650:
        score += 2
    elif body.cibil_score >= 550:
        score += 1

    # Debt-to-income ratio
    ratio = body.loan_amount / max(body.income_annum, 1)
    if ratio <= 0.3:
        score += 2
    elif ratio <= 0.5:
        score += 1

    # Education bonus
    if body.education == "Graduate":
        score += 1

    # Short tenure bonus
    if body.loan_term <= 15:
        score += 1

    approved = score >= threshold

    return {
        "approved": approved,
        "details": {
            "total_score": score,
            "threshold":   threshold,
            "cibil_band":  (
                "Good" if body.cibil_score >= 700
                else "Fair" if body.cibil_score >= 550
                else "Poor"
            ),
            "dti_ratio": round(ratio * 100, 1),
        },
    }


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 7860)),
        reload=False,
    )