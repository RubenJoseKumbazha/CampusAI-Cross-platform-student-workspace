import os, json, re, sqlite3, uuid, hashlib, secrets, base64, threading
from time import perf_counter
from pathlib import Path
from datetime import datetime, date, timedelta, time
from typing import Optional, Literal
from dotenv import load_dotenv
import httpx
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")
DB_PATH = APP_DIR / "campusai.db"
# "localhost" can add ~1-2 s per request on Windows (it tries IPv6 first), so use the IPv4 address directly.
BIONIC_BASE_URL = os.getenv("BIONIC_BASE_URL", "http://127.0.0.1:1235/v1").rstrip("/").replace("//localhost","//127.0.0.1")
BIONIC_MODEL = os.getenv("BIONIC_MODEL", "qwen3.5-4b")
BIONIC_API_KEY = os.getenv("BIONIC_API_KEY", "")
BIONIC_NO_THINK = os.getenv("BIONIC_NO_THINK", "1") == "1"          # ask Qwen-style models to skip "thinking"
BIONIC_NO_THINK_TAG = os.getenv("BIONIC_NO_THINK_TAG", "0") == "1"   # also append /no_think to the prompt
BIONIC_WARMUP = os.getenv("BIONIC_WARMUP", "1") == "1"               # load the model when the app starts
SESSION_DAYS = 7

app = FastAPI(title="CampusAI", version="0.2.0")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")
    return con

def init_db():
    with db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS students(
            user_id TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '', course TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES students(user_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS timetable(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            day TEXT NOT NULL, subject TEXT NOT NULL, start_time TEXT NOT NULL,
            end_time TEXT NOT NULL, room TEXT DEFAULT '', instructor TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES students(user_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS attendance(
            student_id TEXT PRIMARY KEY, attended INTEGER NOT NULL, total INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS lab_slots(
            id INTEGER PRIMARY KEY, lab TEXT, slot_date TEXT, start_time TEXT, end_time TEXT, available INTEGER
        );
        CREATE TABLE IF NOT EXISTS bookings(
            id TEXT PRIMARY KEY, student_id TEXT, slot_id INTEGER, status TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS leave_requests(
            id TEXT PRIMARY KEY, student_id TEXT, start_date TEXT, end_date TEXT, reason TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS maintenance_tickets(
            id TEXT PRIMARY KEY, student_id TEXT, location TEXT, issue TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS library_items(
            id INTEGER PRIMARY KEY, title TEXT, item_type TEXT, available INTEGER
        );
        CREATE TABLE IF NOT EXISTS library_reservations(
            id TEXT PRIMARY KEY, student_id TEXT, item_id INTEGER, status TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS decision_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, timestamp TEXT, event TEXT, details TEXT
        );
        CREATE TABLE IF NOT EXISTS evaluations(
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, mode TEXT, completed INTEGER, created_at TEXT
        );
        """)
        if con.execute("SELECT COUNT(*) FROM lab_slots").fetchone()[0] == 0:
            con.executemany("INSERT INTO lab_slots(lab,slot_date,start_time,end_time,available) VALUES(?,?,?,?,?)", [
                ("Computer Lab 1", str(date.today()+timedelta(days=1)), "10:00", "12:00", 1),
                ("Computer Lab 1", str(date.today()+timedelta(days=1)), "14:00", "16:00", 1),
                ("Computer Lab 2", str(date.today()+timedelta(days=1)), "14:00", "16:00", 0),
                ("Computer Lab 2", str(date.today()+timedelta(days=2)), "11:00", "13:00", 1),
            ])
        if con.execute("SELECT COUNT(*) FROM library_items").fetchone()[0] == 0:
            con.executemany("INSERT INTO library_items(title,item_type,available) VALUES(?,?,?)", [
                ("Introduction to Algorithms", "Book", 1),
                ("Artificial Intelligence: A Modern Approach", "Book", 1),
                ("Database Systems", "Book", 0),
                ("GPU Workstation 04", "Equipment", 1),
            ])
init_db()

def migrate_db():
    with db() as con:
        cols=[r["name"] for r in con.execute("PRAGMA table_info(students)").fetchall()]
        if "avatar" not in cols:
            con.execute("ALTER TABLE students ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
        con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("""CREATE TABLE IF NOT EXISTS deadlines(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            title TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'Assignment',
            subject TEXT NOT NULL DEFAULT '', due_date TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES students(user_id) ON DELETE CASCADE)""")
migrate_db()

# Default timetable transcribed from the supplied LPU timetable PDF.
DEFAULT_TIMETABLE = [
    # Monday
    ("Monday","CSE205 — Data Structures and Algorithms","11:00","12:40","34-704",""),
    ("Monday","INT428 — Artificial Intelligence Essentials","13:30","14:20","26-502A",""),
    ("Monday","CSE202 — Object Oriented Programming","14:20","16:00","26-502A","Practical, Group 0"),
    # Tuesday
    ("Tuesday","CSC201 — Network Configuration and Management","09:20","10:10","38-714",""),
    ("Tuesday","INT428 — Artificial Intelligence Essentials","10:10","11:00","38-714",""),
    ("Tuesday","CSD233 — Design Thinking","11:00","11:50","26-604",""),
    ("Tuesday","CSE202 — Object Oriented Programming","11:50","13:30","38-714",""),
    ("Tuesday","CSE205 — Data Structures and Algorithms","14:20","16:00","26-109",""),
    ("Tuesday","PEL136 — Advanced Communication Skills-II","16:00","16:50","37-906","Practical, Group 2"),
    # Wednesday
    ("Wednesday","PEL136 — Advanced Communication Skills-II","09:20","10:10","37-906",""),
    ("Wednesday","CSD233 — Design Thinking","10:10","11:00","38-716",""),
    ("Wednesday","CSC201 — Network Configuration and Management","11:00","12:40","38-716","Practical, Group 0"),
    ("Wednesday","INT428 — Artificial Intelligence Essentials","12:40","13:30","38-716","Practical, Group 0"),
    ("Wednesday","CSE205 — Data Structures and Algorithms","14:20","16:00","38-906","Practical, Group 1"),
    # Thursday
    ("Thursday","CSC104 — IT Fundamentals","09:20","10:10","26-502A","Practical makeup class, Group 1"),
    ("Thursday","CSC201 — Network Configuration and Management","10:10","11:00","38-714",""),
    ("Thursday","CSC104 — IT Fundamentals","11:00","12:40","38-714",""),
    ("Thursday","INT428 — Artificial Intelligence Essentials","12:40","13:30","38-714",""),
    ("Thursday","CSD233 — Design Thinking","14:20","15:10","26-604",""),
    ("Thursday","CSE205 — Data Structures and Algorithms","16:00","16:50","26-308A","Practical makeup class, Group 1"),
    # Friday
    ("Friday","PEL136 — Advanced Communication Skills-II","09:20","11:00","37-906","Practical, Group 2"),
    ("Friday","CSE202 — Object Oriented Programming","11:00","12:40","26-601A",""),
    ("Friday","CSC104 — IT Fundamentals","13:30","14:20","27-404X",""),
    ("Friday","CSC104 — IT Fundamentals","14:20","15:10","27-404X","Practical, Group 0"),
]

def ensure_default_timetable(user_id):
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM timetable WHERE user_id=?", (user_id,)).fetchone()[0]
        if count == 0:
            now = datetime.now().isoformat(timespec="seconds")
            con.executemany("""INSERT INTO timetable(user_id,day,subject,start_time,end_time,room,instructor,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", [(user_id,*row,now) for row in DEFAULT_TIMETABLE])

def password_digest(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240000)
    return salt.hex() + "$" + digest.hex()

def password_matches(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        return secrets.compare_digest(password_digest(password, bytes.fromhex(salt_hex)), salt_hex + "$" + digest_hex)
    except Exception:
        return False

def get_current_user(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Please log in.")
    token = authorization.split(" ", 1)[1].strip()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db() as con:
        row = con.execute("""SELECT s.user_id, s.name, s.course, s.avatar FROM sessions x
            JOIN students s ON s.user_id=x.user_id
            WHERE x.token_hash=? AND x.expires_at>?""",
            (token_hash, datetime.now().isoformat(timespec="seconds"))).fetchone()
    if not row:
        raise HTTPException(401, "Your session expired. Please log in again.")
    return dict(row)

def issue_session(user_id):
    token = secrets.token_urlsafe(36)
    expires = (datetime.now()+timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with db() as con:
        con.execute("INSERT INTO sessions VALUES(?,?,?)",
                    (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
    return token

def log(task_id, event, details):
    with db() as con:
        con.execute("INSERT INTO decision_logs(task_id,timestamp,event,details) VALUES(?,?,?,?)",
                    (task_id, datetime.now().isoformat(timespec="seconds"), event, json.dumps(details, ensure_ascii=False)))

TOOLS = {
    "check_attendance": {"description":"Read the logged-in student's demo attendance percentage.", "write":False},
    "list_lab_slots": {"description":"Find available computer lab slots for a date (YYYY-MM-DD).", "write":False},
    "book_lab_slot": {"description":"Book a lab slot using its slot_id. Requires human confirmation.", "write":True},
    "check_leave_balance": {"description":"Read the logged-in student's remaining demo leave days.", "write":False},
    "submit_leave_request": {"description":"Submit a leave request with start_date, end_date and reason. Requires human confirmation.", "write":True},
    "create_maintenance_ticket": {"description":"Create a maintenance ticket with location and issue. Requires human confirmation.", "write":True},
    "search_library": {"description":"Search library items by a title keyword.", "write":False},
    "reserve_library_resource": {"description":"Reserve a library item using item_id. Requires human confirmation.", "write":True},
    "list_timetable": {"description":"List the logged-in student's classes. Optional day is a weekday name, or 'all'.", "write":False},
    "add_class": {"description":"Add a class. Required: day (Monday-Sunday), subject, start_time and end_time in HH:MM. Optional room and instructor. Requires human confirmation.", "write":True},
    "update_class": {"description":"Update an existing timetable class by class_id. Include only fields to change. Requires human confirmation.", "write":True},
    "delete_class": {"description":"Delete an existing timetable class by class_id. Requires human confirmation.", "write":True},
}
WRITE_TOOLS = {k for k,v in TOOLS.items() if v["write"]}

def validate_time_range(start, end):
    try:
        a = datetime.strptime(start, "%H:%M").time()
        b = datetime.strptime(end, "%H:%M").time()
    except Exception:
        raise ValueError("Class times must use 24-hour HH:MM format, such as 09:30.")
    if a >= b:
        raise ValueError("Class end time must be later than its start time.")

def run_tool(name: str, args: dict, student_id: str):
    if name == "check_attendance":
        with db() as con:
            row = con.execute("SELECT attended,total FROM attendance WHERE student_id=?", (student_id,)).fetchone()
        if not row:
            return {"success":True,"attended":0,"total":0,"percentage":None,"note":"No attendance data has been added for this account yet."}
        pct = round(row["attended"] / row["total"] * 100, 2) if row["total"] else 0
        return {"success":True, "attended":row["attended"], "total":row["total"], "percentage":pct, "meets_75_percent":pct >= 75}
    if name == "list_lab_slots":
        day = str(args.get("date", date.today()+timedelta(days=1)))
        with db() as con:
            rows = con.execute("SELECT id,lab,slot_date,start_time,end_time FROM lab_slots WHERE slot_date=? AND available=1", (day,)).fetchall()
        return {"success":True, "slots":[dict(r) for r in rows], "count":len(rows)}
    if name == "book_lab_slot":
        slot_id = int(args["slot_id"])
        with db() as con:
            row = con.execute("SELECT * FROM lab_slots WHERE id=? AND available=1", (slot_id,)).fetchone()
            if not row: return {"success":False, "error":"That slot is no longer available."}
            booking_id = str(uuid.uuid4())[:8].upper()
            con.execute("INSERT INTO bookings VALUES(?,?,?,?,?)", (booking_id,student_id,slot_id,"confirmed",datetime.now().isoformat(timespec="seconds")))
            con.execute("UPDATE lab_slots SET available=0 WHERE id=?", (slot_id,))
        return {"success":True, "booking_id":booking_id, "slot_id":slot_id, "message":"Demo lab booking confirmed."}
    if name == "check_leave_balance":
        return {"success":True, "remaining_days":8, "note":"Demo data"}
    if name == "submit_leave_request":
        start, end, reason = args["start_date"], args["end_date"], args["reason"]
        req_id = str(uuid.uuid4())[:8].upper()
        with db() as con:
            con.execute("INSERT INTO leave_requests VALUES(?,?,?,?,?,?,?)",
                        (req_id,student_id,start,end,reason,"submitted",datetime.now().isoformat(timespec="seconds")))
        return {"success":True, "request_id":req_id, "status":"submitted"}
    if name == "create_maintenance_ticket":
        ticket = str(uuid.uuid4())[:8].upper()
        with db() as con:
            con.execute("INSERT INTO maintenance_tickets VALUES(?,?,?,?,?,?)",
                        (ticket,student_id,args["location"],args["issue"],"open",datetime.now().isoformat(timespec="seconds")))
        return {"success":True, "ticket_id":ticket, "status":"open"}
    if name == "search_library":
        keyword = "%" + str(args.get("query","")).strip() + "%"
        with db() as con:
            rows = con.execute("SELECT id,title,item_type,available FROM library_items WHERE title LIKE ?", (keyword,)).fetchall()
        return {"success":True, "items":[dict(r) for r in rows], "count":len(rows)}
    if name == "reserve_library_resource":
        item_id = int(args["item_id"])
        with db() as con:
            row = con.execute("SELECT * FROM library_items WHERE id=? AND available=1", (item_id,)).fetchone()
            if not row: return {"success":False, "error":"This resource is not available."}
            reservation = str(uuid.uuid4())[:8].upper()
            con.execute("INSERT INTO library_reservations VALUES(?,?,?,?,?)",
                        (reservation,student_id,item_id,"reserved",datetime.now().isoformat(timespec="seconds")))
            con.execute("UPDATE library_items SET available=0 WHERE id=?", (item_id,))
        return {"success":True, "reservation_id":reservation, "status":"reserved"}
    if name == "list_timetable":
        day = str(args.get("day","all")).strip().title()
        with db() as con:
            if day.lower() == "all":
                rows = con.execute("SELECT id AS class_id,day,subject,start_time,end_time,room,instructor FROM timetable WHERE user_id=? ORDER BY CASE day WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END,start_time", (student_id,)).fetchall()
            else:
                rows = con.execute("SELECT id AS class_id,day,subject,start_time,end_time,room,instructor FROM timetable WHERE user_id=? AND lower(day)=lower(?) ORDER BY start_time", (student_id,day)).fetchall()
        return {"success":True,"classes":[dict(r) for r in rows],"count":len(rows)}
    if name == "add_class":
        day = str(args["day"]).strip().title()
        if day not in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
            raise ValueError("Use a weekday name from Monday to Sunday.")
        subject = str(args["subject"]).strip()
        if not subject: raise ValueError("Class subject is required.")
        start, end = str(args["start_time"]), str(args["end_time"])
        validate_time_range(start,end)
        with db() as con:
            conflict = con.execute("""SELECT subject FROM timetable WHERE user_id=? AND day=?
                AND start_time < ? AND end_time > ?""",(student_id,day,end,start)).fetchone()
            if conflict: return {"success":False,"error":f"Time conflict with {conflict['subject']}."}
            cur = con.execute("INSERT INTO timetable(user_id,day,subject,start_time,end_time,room,instructor,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (student_id,day,subject,start,end,str(args.get("room","")),str(args.get("instructor","")),datetime.now().isoformat(timespec="seconds")))
            return {"success":True,"class_id":cur.lastrowid,"message":f"{subject} added to {day}."}
    if name == "update_class":
        class_id = int(args["class_id"])
        allowed = {"day","subject","start_time","end_time","room","instructor"}
        changes = {k:v for k,v in args.items() if k in allowed}
        if not changes: raise ValueError("No class fields were provided to update.")
        with db() as con:
            row = con.execute("SELECT * FROM timetable WHERE id=? AND user_id=?",(class_id,student_id)).fetchone()
            if not row: return {"success":False,"error":"Class not found in your timetable."}
            merged = {k:row[k] for k in allowed}
            merged.update(changes)
            merged["day"] = str(merged["day"]).title()
            if merged["day"] not in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]:
                raise ValueError("Use a weekday name from Monday to Sunday.")
            validate_time_range(merged["start_time"],merged["end_time"])
            conflict = con.execute("""SELECT subject FROM timetable WHERE user_id=? AND day=? AND id<>?
                AND start_time < ? AND end_time > ?""",(student_id,merged["day"],class_id,merged["end_time"],merged["start_time"])).fetchone()
            if conflict: return {"success":False,"error":f"Time conflict with {conflict['subject']}."}
            con.execute("""UPDATE timetable SET day=?,subject=?,start_time=?,end_time=?,room=?,instructor=? WHERE id=? AND user_id=?""",
                (merged["day"],merged["subject"],merged["start_time"],merged["end_time"],merged["room"],merged["instructor"],class_id,student_id))
        return {"success":True,"class_id":class_id,"message":"Class updated."}
    if name == "delete_class":
        class_id = int(args["class_id"])
        with db() as con:
            cur = con.execute("DELETE FROM timetable WHERE id=? AND user_id=?",(class_id,student_id))
        return {"success":bool(cur.rowcount),"class_id":class_id,"message":"Class deleted." if cur.rowcount else "Class not found."}
    raise ValueError("Unknown tool")

SYSTEM_PROMPT = """You are CampusAI, the student's university assistant. Do not describe yourself as ChatGPT.
Return ONLY valid JSON in this exact shape:
{"message":"one short friendly sentence","steps":[{"tool":"tool_name","arguments":{}}]}
Allowed tools and purposes:
""" + json.dumps(TOOLS) + """
Rules:
- Break multi-part requests into ordered steps.
- For timetable questions, call list_timetable and use only the returned classes. Never invent a class, room, instructor or time.
- For a timetable add/edit/delete, gather a class_id with list_timetable first when needed. Do not guess IDs.
- Write actions require human confirmation; do not claim they are complete.
- If required information is missing, return no steps and ask one concise question.
- Use only the logged-in student's records. Never refer to a shared demo student ID.
- Be transparent that campus services outside the personal timetable use sample/demo data.
- Maximum 6 steps. Do not create unrelated steps.
"""

class ModelError(Exception):
    pass

# ---------- model connection settings (editable from the web UI, saved in the database) ----------
CFG = {"base_url":BIONIC_BASE_URL, "model":BIONIC_MODEL, "api_key":BIONIC_API_KEY, "no_think":BIONIC_NO_THINK,
       "chat_max_tokens":int(os.getenv("BIONIC_CHAT_MAX_TOKENS","1500") or 1500)}
_server_rejects_no_think = False      # set automatically if the model server refuses the no-thinking option

def normalize_base_url(u):
    u = (u or "").strip().rstrip("/")
    if u and not re.match(r"^https?://", u, re.I): u = "http://"+u
    return u.replace("//localhost","//127.0.0.1")

def load_saved_settings():
    with db() as con:
        rows = con.execute("SELECT key,value FROM app_settings").fetchall()
    for r in rows:
        k, v = r["key"], r["value"]
        if k == "base_url" and v: CFG["base_url"] = normalize_base_url(v)
        elif k == "model" and v: CFG["model"] = v
        elif k == "api_key": CFG["api_key"] = v
        elif k == "no_think": CFG["no_think"] = (v == "1")
load_saved_settings()

def save_setting(key, value):
    with db() as con:
        con.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))

_http = httpx.Client(timeout=httpx.Timeout(90.0, connect=5.0))   # one keep-alive connection pool for all model calls

def _model_headers(cfg=None):
    cfg = cfg or CFG
    h = {"Content-Type":"application/json"}
    if cfg.get("api_key"): h["Authorization"] = f"Bearer {cfg['api_key']}"
    return h

def _model_payload(messages, max_tokens, temperature, stream=False, force_no_think=False, cfg=None):
    cfg = cfg or CFG
    msgs = [dict(m) for m in messages]
    if (BIONIC_NO_THINK_TAG or force_no_think) and msgs and msgs[-1]["role"] == "user":
        msgs[-1]["content"] += " /no_think"
    p = {"model":cfg["model"], "messages":msgs, "temperature":temperature, "max_tokens":max_tokens}
    if stream: p["stream"] = True
    if (cfg.get("no_think") or force_no_think) and not _server_rejects_no_think:
        p["chat_template_kwargs"] = {"enable_thinking": False}
    return p

def _model_error(e, cfg=None):
    cfg = cfg or CFG
    return ModelError(f"Could not get a reply from the AI model at {cfg['base_url']}. Check that Bionic (or LM Studio) is running with its API enabled, then open ⚙ AI settings and press Test connection. Details: {e}")

def strip_think_text(raw):
    raw = raw or ""
    if "<think>" in raw and "</think>" not in raw: return ""      # thinking was cut off before an answer
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    if "</think>" in raw: raw = raw.split("</think>")[-1]
    return raw.strip()

def _post_chat(messages, max_tokens, temperature, force=False, cfg=None, timeout=None):
    global _server_rejects_no_think
    cfg = cfg or CFG
    for attempt in (1, 2):
        try:
            kw = {"timeout":timeout} if timeout else {}
            r = _http.post(cfg["base_url"]+"/chat/completions", headers=_model_headers(cfg), json=_model_payload(messages,max_tokens,temperature,False,force,cfg), **kw)
            if r.status_code in (400, 422) and attempt == 1 and (cfg.get("no_think") or force) and not _server_rejects_no_think:
                _server_rejects_no_think = True      # this server rejects the extra field: retry without it
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise _model_error(e, cfg)

def call_bionic(messages, max_tokens=700, temperature=0.1):
    """Non-streaming call, used for tool planning. Retries once with thinking forced off if the model only 'thought'."""
    last = ""
    for force in (False, True):
        data = _post_chat(messages, max_tokens*(2 if force else 1), temperature, force)
        msg = ((data.get("choices") or [{}])[0].get("message")) or {}
        raw = msg.get("content") or ""
        text = strip_think_text(raw)
        if text: return text
        last = msg.get("reasoning_content") or msg.get("reasoning") or raw
    m = re.search(r"\{.*\}", last or "", re.S)       # last resort: a JSON plan hidden inside the reasoning
    if m: return m.group(0)
    raise ModelError("The model used its whole reply limit thinking and returned no answer. Open ⚙ AI settings and turn on “Disable thinking”, or switch off thinking for this model in Bionic / LM Studio.")

def _raw_events(messages, max_tokens, temperature, force=False):
    """Yields ('content'|'reasoning'|'finish', text) from the model's streamed reply."""
    global _server_rejects_no_think
    for attempt in (1, 2):
        try:
            with _http.stream("POST", CFG["base_url"]+"/chat/completions", headers=_model_headers(), json=_model_payload(messages,max_tokens,temperature,True,force)) as r:
                if r.status_code in (400, 422) and attempt == 1 and (CFG["no_think"] or force) and not _server_rejects_no_think:
                    r.read(); _server_rejects_no_think = True
                    continue
                if r.status_code >= 400:
                    r.read(); raise ModelError(f"The model server answered {r.status_code}: {r.text[:200]}")
                for line in r.iter_lines():
                    if not line.startswith("data:"): continue
                    data = line[5:].strip()
                    if data == "[DONE]": return
                    try: ch = json.loads(data)["choices"][0]
                    except Exception: continue
                    d = ch.get("delta") or ch.get("message") or {}
                    reasoning = d.get("reasoning_content") or d.get("reasoning") or ""
                    piece = d.get("content") or ch.get("text") or ""
                    if reasoning: yield ("reasoning", reasoning)
                    if piece: yield ("content", piece)
                    if ch.get("finish_reason"): yield ("finish", ch["finish_reason"])
                return
        except ModelError:
            raise
        except Exception as e:
            raise _model_error(e)

def split_think(events):
    """Separates <think>...</think> text from the answer. Yields ('text'|'reasoning'|'finish'|'reset', text)."""
    OPEN, CLOSE = "<think>", "</think>"
    buf, in_think, seen_open = "", False, False
    for kind, piece in events:
        if kind != "content":
            yield (kind, piece); continue
        buf += piece
        while True:
            if in_think:
                i = buf.find(CLOSE)
                if i == -1:
                    if len(buf) > len(CLOSE): yield ("reasoning", buf[:-len(CLOSE)]); buf = buf[-len(CLOSE):]
                    break
                if i > 0: yield ("reasoning", buf[:i])
                buf = buf[i+len(CLOSE):].lstrip("\n"); in_think = False
            else:
                i, j = buf.find(OPEN), buf.find(CLOSE)
                if j != -1 and (i == -1 or j < i) and not seen_open:
                    yield ("reset", ""); buf = buf[j+len(CLOSE):].lstrip("\n"); seen_open = True; continue   # template had already opened <think>
                if i == -1:
                    tags = (OPEN,) if seen_open else (OPEN, CLOSE)       # a split "</think>" can still arrive until a tag has been seen
                    keep = max((next((k for k in range(len(t)-1, 0, -1) if buf.endswith(t[:k])), 0) for t in tags))
                    if len(buf) > keep: yield ("text", buf[:len(buf)-keep]); buf = buf[len(buf)-keep:]
                    break
                if i > 0: yield ("text", buf[:i])
                buf = buf[i+len(OPEN):]; in_think = True; seen_open = True
    if buf: yield ("reasoning" if in_think else "text", buf)

def stream_events(messages, max_tokens=700, temperature=0.6, force=False):
    return split_think(_raw_events(messages, max_tokens, temperature, force))

def warm_model():
    """Load the model into memory at start-up so the first chat isn't slow."""
    if not BIONIC_WARMUP: return
    def run():
        try: _post_chat([{"role":"user","content":"hi"}], 1, 0, False)
        except Exception: pass
    threading.Thread(target=run, daemon=True).start()

@app.on_event("startup")
def _startup():
    warm_model()

def parse_plan(content):
    match = re.search(r"\{.*\}",content,re.S)
    if not match: raise ValueError("Model did not return JSON")
    obj = json.loads(match.group(0))
    if not isinstance(obj.get("steps",[]),list): raise ValueError("Invalid plan")
    return obj

class RegisterRequest(BaseModel):
    user_id: str = Field(min_length=3,max_length=40)
    password: str = Field(min_length=8,max_length=128)

class LoginRequest(BaseModel):
    user_id: str
    password: str

class ProfileRequest(BaseModel):
    name: str = Field(default="",max_length=100)
    course: str = Field(default="",max_length=120)

class AvatarRequest(BaseModel):
    image: str = Field(max_length=400_000)

class ChatTurn(BaseModel):
    role: Literal["user","assistant"]
    content: str = Field(max_length=4000)

class ChatRequest(BaseModel):
    message: str = Field(min_length=1,max_length=4000)
    mode: str = "agent"
    history: list[ChatTurn] = Field(default_factory=list,max_length=24)

class ConfirmRequest(BaseModel):
    task_id: str
    approve: bool

class ClassRequest(BaseModel):
    day: str
    subject: str
    start_time: str
    end_time: str
    room: str = ""
    instructor: str = ""

class ClassUpdateRequest(BaseModel):
    day: Optional[str] = None
    subject: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    room: Optional[str] = None
    instructor: Optional[str] = None

@app.get("/")
def home():
    return FileResponse(APP_DIR/"static"/"index.html")

@app.post("/api/register")
def register(req: RegisterRequest):
    user_id = req.user_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}",user_id):
        raise HTTPException(400,"User ID must be 3–40 characters and use letters, numbers, dot, underscore or hyphen.")
    with db() as con:
        if con.execute("SELECT 1 FROM students WHERE lower(user_id)=lower(?)",(user_id,)).fetchone():
            raise HTTPException(409,"That User ID is already registered.")
        con.execute("INSERT INTO students(user_id,password_hash,created_at) VALUES(?,?,?)",
                    (user_id,password_digest(req.password),datetime.now().isoformat(timespec="seconds")))
    return {"ok":True,"message":"Account created. Please log in."}

@app.post("/api/login")
def login(req: LoginRequest):
    user_id = req.user_id.strip()
    if not user_id or len(user_id) > 80:
        raise HTTPException(400,"Enter a username between 1 and 80 characters.")
    # Demo-mode sign-in: the username identifies the local workspace; password is accepted as entered.
    with db() as con:
        row=con.execute("SELECT user_id,name,course,avatar FROM students WHERE lower(user_id)=lower(?)",(user_id,)).fetchone()
        if not row:
            con.execute("INSERT INTO students(user_id,password_hash,created_at) VALUES(?,?,?)",
                        (user_id,password_digest(req.password or ""),datetime.now().isoformat(timespec="seconds")))
            row=con.execute("SELECT user_id,name,course,avatar FROM students WHERE lower(user_id)=lower(?)",(user_id,)).fetchone()
    ensure_default_timetable(row["user_id"])
    token=issue_session(row["user_id"])
    return {"token":token,"profile":{"user_id":row["user_id"],"name":row["name"],"course":row["course"],"avatar":row["avatar"]}}

@app.post("/api/logout")
def logout(user=Depends(get_current_user), authorization: Optional[str]=Header(default=None)):
    token=authorization.split(" ",1)[1].strip()
    with db() as con:
        con.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),))
    return {"ok":True}

@app.get("/api/me")
def me(user=Depends(get_current_user)):
    return user

@app.put("/api/profile")
def update_profile(req: ProfileRequest,user=Depends(get_current_user)):
    name=req.name.strip(); course=req.course.strip()
    with db() as con:
        con.execute("UPDATE students SET name=?,course=? WHERE user_id=?",(name,course,user["user_id"]))
    return {"ok":True,"profile":{"user_id":user["user_id"],"name":name,"course":course}}

AVATAR_PREFIXES = ("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")

@app.put("/api/profile/avatar")
def update_avatar(req: AvatarRequest, user=Depends(get_current_user)):
    image = req.image.strip()
    if not image.startswith(AVATAR_PREFIXES):
        raise HTTPException(400, "Photo must be a JPEG, PNG or WebP image.")
    try:
        raw = base64.b64decode(image.split(",", 1)[1], validate=True)
    except Exception:
        raise HTTPException(400, "Photo data is not valid.")
    if len(raw) > 250_000:
        raise HTTPException(413, "Photo is too large. Choose a smaller image.")
    with db() as con:
        con.execute("UPDATE students SET avatar=? WHERE user_id=?", (image, user["user_id"]))
    return {"ok": True, "avatar": image}

@app.delete("/api/profile/avatar")
def remove_avatar(user=Depends(get_current_user)):
    with db() as con:
        con.execute("UPDATE students SET avatar='' WHERE user_id=?", (user["user_id"],))
    return {"ok": True, "avatar": ""}

@app.get("/api/health")
def health():
    return {"ok":True,"model":CFG["model"],"base_url":CFG["base_url"],"api_key_configured":bool(CFG["api_key"])}

class ModelSettingsRequest(BaseModel):
    base_url: str = Field(min_length=1,max_length=300)
    model: str = Field(min_length=1,max_length=200)
    api_key: Optional[str] = Field(default=None,max_length=500)   # None keeps the saved key, "" clears it
    no_think: bool = True

LOCAL_HOSTS = {"127.0.0.1","::1","localhost","testclient"}

def require_local(request: Request):
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS and os.getenv("ALLOW_REMOTE_SETTINGS") != "1":
        raise HTTPException(403,"Model settings can only be changed on the computer that runs CampusAI.")

def settings_payload():
    return {"base_url":CFG["base_url"],"model":CFG["model"],"has_key":bool(CFG["api_key"]),"no_think":CFG["no_think"]}

def clean_model_settings(req: ModelSettingsRequest):
    url = normalize_base_url(req.base_url)
    if not re.match(r"^https?://[^\s/]+(/\S*)?$", url): raise HTTPException(400,"Enter a valid URL, for example http://127.0.0.1:1235/v1")
    model = req.model.strip()
    if not model: raise HTTPException(400,"Enter the model name.")
    key = CFG["api_key"] if req.api_key is None else req.api_key.strip()
    return {"base_url":url,"model":model,"api_key":key,"no_think":req.no_think,"chat_max_tokens":CFG["chat_max_tokens"]}

@app.get("/api/settings/model")
def get_model_settings(user=Depends(get_current_user)):
    return settings_payload()

@app.put("/api/settings/model")
def put_model_settings(req: ModelSettingsRequest, request: Request, user=Depends(get_current_user)):
    global _server_rejects_no_think
    require_local(request)
    cfg = clean_model_settings(req)
    CFG.update(cfg); _server_rejects_no_think = False
    for k in ("base_url","model","api_key"): save_setting(k, cfg[k])
    save_setting("no_think", "1" if cfg["no_think"] else "0")
    return settings_payload()

@app.post("/api/settings/model/test")
def test_model_settings(req: ModelSettingsRequest, request: Request, user=Depends(get_current_user)):
    require_local(request)
    cfg = clean_model_settings(req)
    out = {"ok":False,"models":[],"model_found":None,"suggested_base_url":None,"latency_ms":None,"reply_preview":"","thinking_detected":False,"error":""}
    base = cfg["base_url"]
    def list_models(b):
        r = _http.get(b+"/models", headers=_model_headers(cfg), timeout=httpx.Timeout(6.0, connect=4.0))
        r.raise_for_status()
        return [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
    try:
        try: out["models"] = list_models(base)
        except httpx.HTTPStatusError:
            if base.endswith("/v1"): raise
            out["models"] = list_models(base+"/v1"); base = base+"/v1"; out["suggested_base_url"] = base
    except Exception as e:
        out["error"] = f"Could not reach {base}/models. Is Bionic / LM Studio running with its API enabled? ({e})"
        return out
    cfg["base_url"] = base
    out["model_found"] = (cfg["model"] in out["models"]) if out["models"] else None
    t0 = perf_counter()
    try:
        data = _post_chat([{"role":"user","content":"Reply with one short greeting."}], 120, 0.2, False, cfg, httpx.Timeout(60.0, connect=5.0))
        msg = ((data.get("choices") or [{}])[0].get("message")) or {}
        raw = msg.get("content") or ""
        out["latency_ms"] = int((perf_counter()-t0)*1000)
        out["thinking_detected"] = bool(msg.get("reasoning_content") or msg.get("reasoning") or "<think>" in raw or "</think>" in raw)
        out["reply_preview"] = strip_think_text(raw)[:160]
        out["ok"] = bool(out["reply_preview"])
        if not out["ok"]:
            out["error"] = "The model answered, but only with hidden thinking and no text. Turn on “Disable thinking” (or switch thinking off for this model in Bionic / LM Studio) and test again." if out["thinking_detected"] else "The model returned an empty reply. Check the model name."
    except ModelError as e:
        out["error"] = str(e)
    return out

@app.get("/api/timetable")
def get_timetable(day: str = "all", user=Depends(get_current_user)):
    result=run_tool("list_timetable",{"day":day},user["user_id"])
    return result

@app.post("/api/timetable")
def create_class(req: ClassRequest,user=Depends(get_current_user)):
    result=run_tool("add_class",req.model_dump(),user["user_id"])
    if not result.get("success"): raise HTTPException(409,result.get("error","Could not add class."))
    return result

@app.put("/api/timetable/{class_id}")
def edit_class(class_id: int,req: ClassUpdateRequest,user=Depends(get_current_user)):
    args={k:v for k,v in req.model_dump(exclude_unset=True).items() if v is not None}
    args["class_id"]=class_id
    result=run_tool("update_class",args,user["user_id"])
    if not result.get("success"): raise HTTPException(409,result.get("error","Could not update class."))
    return result

@app.delete("/api/timetable/{class_id}")
def remove_class(class_id: int,user=Depends(get_current_user)):
    result=run_tool("delete_class",{"class_id":class_id},user["user_id"])
    if not result.get("success"): raise HTTPException(404,result.get("message","Class not found."))
    return result

DEADLINE_KINDS = ("Exam","Quiz","Assignment","Project","Other")

class DeadlineRequest(BaseModel):
    title: str = Field(min_length=1,max_length=140)
    kind: str = "Assignment"
    subject: str = Field(default="",max_length=120)
    due_date: str
    notes: str = Field(default="",max_length=300)

class DeadlineUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None,max_length=140)
    kind: Optional[str] = None
    subject: Optional[str] = Field(default=None,max_length=120)
    due_date: Optional[str] = None
    notes: Optional[str] = Field(default=None,max_length=300)
    done: Optional[bool] = None

def clean_deadline(d: dict) -> dict:
    out = {}
    if "title" in d:
        title = d["title"].strip()
        if not title: raise HTTPException(400,"Title is required.")
        out["title"] = title
    if "kind" in d:
        kind = d["kind"].strip().title()
        if kind not in DEADLINE_KINDS: raise HTTPException(400,"Choose Exam, Quiz, Assignment, Project or Other.")
        out["kind"] = kind
    if "due_date" in d:
        try: out["due_date"] = date.fromisoformat(d["due_date"].strip()).isoformat()
        except ValueError: raise HTTPException(400,"Enter a valid date.")
    for f in ("subject","notes"):
        if f in d: out[f] = d[f].strip()
    if "done" in d: out["done"] = 1 if d["done"] else 0
    return out

DEADLINE_COLS = "id,title,kind,subject,due_date,notes,done"

@app.get("/api/deadlines")
def list_deadlines(user=Depends(get_current_user)):
    with db() as con:
        rows = con.execute(f"SELECT {DEADLINE_COLS} FROM deadlines WHERE user_id=? ORDER BY due_date,id",(user["user_id"],)).fetchall()
    return {"deadlines":[{**dict(r),"done":bool(r["done"])} for r in rows]}

@app.post("/api/deadlines")
def create_deadline(req: DeadlineRequest,user=Depends(get_current_user)):
    d = clean_deadline(req.model_dump())
    with db() as con:
        cur = con.execute("INSERT INTO deadlines(user_id,title,kind,subject,due_date,notes,done,created_at) VALUES(?,?,?,?,?,?,0,?)",
            (user["user_id"],d["title"],d["kind"],d["subject"],d["due_date"],d["notes"],datetime.now().isoformat(timespec="seconds")))
    return {"ok":True,"id":cur.lastrowid}

@app.put("/api/deadlines/{deadline_id}")
def update_deadline(deadline_id: int,req: DeadlineUpdateRequest,user=Depends(get_current_user)):
    d = clean_deadline({k:v for k,v in req.model_dump(exclude_unset=True).items() if v is not None})
    if not d: raise HTTPException(400,"Nothing to update.")
    sets = ",".join(f"{k}=?" for k in d)
    with db() as con:
        cur = con.execute(f"UPDATE deadlines SET {sets} WHERE id=? AND user_id=?",(*d.values(),deadline_id,user["user_id"]))
        if cur.rowcount == 0: raise HTTPException(404,"Deadline not found.")
    return {"ok":True}

@app.delete("/api/deadlines/{deadline_id}")
def delete_deadline(deadline_id: int,user=Depends(get_current_user)):
    with db() as con:
        cur = con.execute("DELETE FROM deadlines WHERE id=? AND user_id=?",(deadline_id,user["user_id"]))
        if cur.rowcount == 0: raise HTTPException(404,"Deadline not found.")
    return {"ok":True}

def get_trace(task_id):
    with db() as con:
        rows=con.execute("SELECT timestamp,event,details FROM decision_logs WHERE task_id=? ORDER BY id",(task_id,)).fetchall()
    return [dict(r)|{"details":json.loads(r["details"])} for r in rows]

DAY_ALIASES = {"monday":"Monday","mon":"Monday","tuesday":"Tuesday","tue":"Tuesday","tues":"Tuesday","wednesday":"Wednesday","wed":"Wednesday",
    "thursday":"Thursday","thu":"Thursday","thur":"Thursday","thurs":"Thursday","friday":"Friday","fri":"Friday","saturday":"Saturday","sat":"Saturday","sunday":"Sunday","sun":"Sunday"}
DAY_REF = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues?|wed|thu(?:rs?)?|fri|today|tomorrow|tonight)\b|\b(?:on|for|this|next)\s+(sat|sun)\b")

def days_in_message(m):
    """Weekday names in the order they appear. Understands Fri, Thurs, today, tomorrow (sat/sun only after on/for/this/next)."""
    found = []
    for mt in DAY_REF.finditer(m):
        tok = mt.group(1) or mt.group(2)
        if tok in ("today","tonight"): d = date.today().strftime("%A")
        elif tok == "tomorrow": d = (date.today()+timedelta(days=1)).strftime("%A")
        else: d = DAY_ALIASES[tok]
        if d not in found: found.append(d)
    return found

def weekday_from_message(m):
    d = days_in_message(m)
    return d[0] if d else None

EDIT_VERBS = re.compile(r"\b(add|create|change|move|reschedule|shift|update|edit|rename|delete|remove|cancel|swap)\b")
TIMETABLE_WORDS = ("timetable","time table","schedule","timings")
CLASS_WORDS = re.compile(r"\b(class|classes|lecture|lectures|subject|subjects)\b")
PERSONAL = re.compile(r"\b(my|i have|do i|have i|am i|next|today|tomorrow|tonight|this week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\b\d{1,2}(:\d{2})?\s*(am|pm)\b|\b\d{1,2}:\d{2}\b")

def wants_timetable_edit(m):
    return bool(EDIT_VERBS.search(m)) and (bool(CLASS_WORDS.search(m)) or any(w in m for w in TIMETABLE_WORDS))

SCHEDULE_Q = re.compile(r"\b(what do i have|what have i got|what's on|whats on|what is on|do i have|am i free|are we free|anything on|free (?:period|periods|slot|slots|time|hour|hours)|any class(?:es)?|how many (?:class|classes|lectures)|next (?:class|lecture)|first (?:class|lecture)|last (?:class|lecture)|busy)\b")
NOT_TIMETABLE = re.compile(r"\b(exam|exams|quiz|test|assignment|deadline|deadlines|submission|project|homework|due)\b")
FREE_Q = re.compile(r"\b(free|busy|available)\b")

SHOW_Q = re.compile(r"\b(show|tell|give|list|display|view|check)\b")
FILLER = {"show","tell","give","list","display","see","view","check","get","me","my","the","for","on","of","please","pls","a","what","whats","what's","is","are","class","classes","schedule","timetable","lecture","lectures","i","have","do","can","you","to","and","about","how","then","also","same","next","this","how's"}

def only_filler(m):
    """True when, apart from day names, the message only has timetable-style filler words."""
    return all(t in FILLER for t in re.findall(r"[a-z']+", DAY_REF.sub(" ", m)))

def is_timetable_lookup(m, last_was_timetable=False):
    """Instant (no-AI) answers only for real questions about the student's own timetable."""
    if wants_timetable_edit(m): return False
    if any(w in m for w in TIMETABLE_WORDS): return True
    words = len(m.split()); days = days_in_message(m)
    if CLASS_WORDS.search(m) and PERSONAL.search(m) and words <= 12: return True
    if NOT_TIMETABLE.search(m): return False            # exams and deadlines are handled by the chat model, which knows them
    if SCHEDULE_Q.search(m) and (days or "week" in m) and words <= 14: return True
    if days and only_filler(m) and (SHOW_Q.search(m) or "?" in m or words == 1 or last_was_timetable): return True   # "show me Friday", "Friday?", "and Friday?"
    return False

TIMETABLE_REPLY = re.compile(r"^(Your classes on|Here is your saved weekly|At \d|You have no class|You have no classes|No classes are saved|Your timetable is empty)")

TOOL_INTENT = re.compile(r"attendance|\bleave\b|\blabs?\b|library|\bbook(ing)?\b|reserve|maintenance|broken|repair|ticket|not working")

def needs_tools(m):
    return wants_timetable_edit(m) or bool(TOOL_INTENT.search(m))

def parse_time_in(m):
    mt = re.search(r"\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b", m)
    if mt: h, mi, suf = int(mt.group(1)), int(mt.group(2) or 0), mt.group(3)
    else:
        mt = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", m)
        if mt: h, mi, suf = int(mt.group(1)), int(mt.group(2)), None
        else:
            mt = re.search(r"\bat\s+(\d{1,2})\b", m)
            if not mt: return None
            h, mi, suf = int(mt.group(1)), 0, None
            if 1 <= h <= 7: h += 12          # "at 3" almost always means 3 PM
    if suf == "pm" and h < 12: h += 12
    if suf == "am" and h == 12: h = 0
    return time(h, mi) if 0 <= h <= 23 else None

def timetable_direct_answer(message,user_id):
    m=message.lower()
    days=days_in_message(m)
    day=days[0] if days else None
    def fetch(d):
        with db() as con:
            if d:
                rows=con.execute("SELECT id AS class_id,day,subject,start_time,end_time,room,instructor FROM timetable WHERE user_id=? AND lower(day)=lower(?) ORDER BY start_time",(user_id,d)).fetchall()
            else:
                rows=con.execute("""SELECT id AS class_id,day,subject,start_time,end_time,room,instructor FROM timetable
                    WHERE user_id=? ORDER BY CASE day WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END,start_time""",(user_id,)).fetchall()
        return [dict(r) for r in rows]
    if not fetch(None):
        return "Your timetable is empty. Add your classes in the Timetable section, and I can answer questions about their timings."
    classes=fetch(day)
    requested=parse_time_in(m)
    if requested:
        matching=[c for c in classes if datetime.strptime(c["start_time"],"%H:%M").time()<=requested<datetime.strptime(c["end_time"],"%H:%M").time()]
        if matching:
            c=matching[0]
            return f"At {requested.strftime('%I:%M %p').lstrip('0')}, you have {c['subject']} from {c['start_time']} to {c['end_time']} on {c['day']}"+(f" in {c['room']}" if c["room"] else "")+"."
        return f"You have no class scheduled at {requested.strftime('%I:%M %p').lstrip('0')}"+(f" on {day}" if day else " for the saved timetable")+"."
    if re.search(r"\bnext (class|lecture)\b",m) and not days:
        now=datetime.now()
        for i in range(8):
            dt=now.date()+timedelta(days=i)
            for c in fetch(dt.strftime("%A")):
                if i>0 or c["start_time"]>now.strftime("%H:%M"):
                    when="today" if i==0 else "tomorrow" if i==1 else dt.strftime("%A")
                    return f"Your next class is {c['subject']}, {when}, {c['start_time']}–{c['end_time']}"+(f" in {c['room']}" if c["room"] else "")+"."
        return "I couldn't find an upcoming class in your saved timetable."
    if days:
        free_q=bool(FREE_Q.search(m)); parts=[]
        for d in days[:3]:
            cs=classes if d==day else fetch(d)
            if cs:
                lines=[f"{c['start_time']}–{c['end_time']}  {c['subject']}"+(f" · {c['room']}" if c["room"] else "") for c in cs]
                parts.append(f"Your classes on {d}:\n"+"\n".join(lines)+("\nYou're free outside these times." if free_q else ""))
            else:
                parts.append(f"You have no classes on {d}."+(" You're free all day." if free_q else ""))
        return "\n\n".join(parts)
    return "Here is your saved weekly timetable:\n"+"\n".join(f"{c['day']} · {c['start_time']}–{c['end_time']} · {c['subject']}"+(f" · {c['room']}" if c["room"] else "") for c in classes)

# ---------- conversational assistant ----------
def chat_system_prompt(user):
    today = date.today()
    name = user["name"] or user["user_id"]
    with db() as con:
        classes = con.execute("SELECT subject,start_time,end_time,room FROM timetable WHERE user_id=? AND day=? ORDER BY start_time LIMIT 8",(user["user_id"],today.strftime("%A"))).fetchall()
        due = con.execute("SELECT title,kind,due_date FROM deadlines WHERE user_id=? AND done=0 AND due_date>=? ORDER BY due_date LIMIT 5",(user["user_id"],today.isoformat())).fetchall()
    ctx = [f"Today is {today.strftime('%A, %d %B %Y')}.", f"Student: {name}"+(f", studying {user['course']}" if user["course"] else "")+"."]
    ctx.append("Classes today: "+("; ".join(f"{c['start_time']}-{c['end_time']} {c['subject']}"+(f" ({c['room']})" if c["room"] else "") for c in classes) if classes else "none saved."))
    ctx.append("Upcoming exams and deadlines: "+("; ".join(f"{d['kind']} {d['title']} on {d['due_date']}" for d in due) if due else "none saved."))
    return ("You are CampusAI, a friendly, capable assistant inside a student's campus workspace. Chat naturally like a helpful assistant: "
        "answer greetings and small talk, explain concepts, help with study plans, writing, coding and general questions. "
        "Be warm, direct and concise (usually 2-6 sentences); use a short list or bold only when it really helps. Reply in the language the student uses. "
        "Never invent personal facts: use only the student context below for their schedule, course and deadlines, and say so if you don't know. "
        "When mentioning a deadline give its date, not a countdown. "
        "If they want attendance, leave, lab slots, library, maintenance tickets or timetable changes, tell them they can just ask, for example “check my attendance” or “add a class on Monday at 2pm”. "
        "You can only see today's classes below, not the rest of the week. Never say a schedule is missing or not saved, and never mention plans, classes or events that are not in the context. If they ask about another day, tell them to ask for example “what classes do I have on Friday?”, which is answered instantly from their saved timetable. "
        "Data outside their own timetable and deadlines is demo data. Timetable changes need the student's approval, so never claim something was changed. "
        "Do not call yourself ChatGPT. Do not output JSON.\n\nStudent context:\n"+"\n".join(ctx))

def history_messages(history, n, limit):
    return [{"role":t.role,"content":t.content[:limit]} for t in history[-n:]]

def format_result(tool, r):
    if not r.get("success", False):
        return "That didn't work: "+str(r.get("error") or r.get("message") or "unknown error")
    if tool == "check_attendance":
        if r.get("percentage") is None: return r.get("note") or "There is no attendance data yet."
        status = "above" if r.get("meets_75_percent") else "below"
        return f"Your attendance is {r.get('percentage')}% ({r.get('attended')} of {r.get('total')} classes), which is {status} the 75% requirement. (Demo data)"
    if tool == "check_leave_balance":
        return f"You have {r.get('remaining_days', r.get('remaining', '?'))} leave days remaining. (Demo data)"
    if tool == "list_lab_slots":
        slots = r.get("slots") or []
        if not slots: return "There are no free lab slots for that date."
        return "Free lab slots:\n"+"\n".join(f"• {x.get('lab','Lab')} · {x.get('slot_date','')} · {x.get('start_time','')}–{x.get('end_time','')} (slot {x.get('id')})" for x in slots)
    if tool == "search_library":
        items = r.get("items") or []
        if not items: return "I couldn't find anything in the library matching that."
        return "Library results:\n"+"\n".join(f"• {i['title']} ({i['item_type']}), {'available' if i['available'] else 'checked out'} (item {i['id']})" for i in items)
    if tool == "list_timetable":
        cl = r.get("classes") or []
        if not cl: return "No classes found."
        return "\n".join(f"• {c['day'][:3]} {c['start_time']}–{c['end_time']} · {c['subject']}"+(f" · {c['room']}" if c.get("room") else "") for c in cl)
    if tool == "book_lab_slot": return f"Lab slot booked. Booking ID: {r.get('booking_id')}."
    if tool == "submit_leave_request": return f"Leave request submitted (ID {r.get('request_id')}). Status: {r.get('status')}."
    if tool == "create_maintenance_ticket": return f"Maintenance ticket {r.get('ticket_id')} created. Status: {r.get('status')}."
    if tool == "reserve_library_resource": return f"Reserved. Reservation ID: {r.get('reservation_id')}."
    return r.get("message") or "Done."

def describe_pending(p, uid):
    t, a = p["tool"], p.get("arguments") or {}
    def cls():
        try:
            with db() as con:
                return con.execute("SELECT subject,day,start_time FROM timetable WHERE id=? AND user_id=?",(int(a.get("class_id",0)),uid)).fetchone()
        except Exception: return None
    if t == "add_class":
        return f"add “{a.get('subject','?')}” on {a.get('day','?')}, {a.get('start_time','?')}–{a.get('end_time','?')}"+(f" in {a['room']}" if a.get("room") else "")
    if t == "delete_class":
        r = cls(); return f"delete “{r['subject']}” ({r['day']} {r['start_time']})" if r else "delete a class"
    if t == "update_class":
        r = cls(); ch = ", ".join(f"{k} → {v}" for k,v in a.items() if k != "class_id")
        return (f"change “{r['subject']}” ({r['day']} {r['start_time']}): " if r else "change a class: ")+ch
    if t == "book_lab_slot": return f"book lab slot {a.get('slot_id','?')}"
    if t == "submit_leave_request": return f"submit a leave request from {a.get('start_date','?')} to {a.get('end_date','?')}"
    if t == "create_maintenance_ticket": return f"report an issue at {a.get('location','?')}: {a.get('issue','')}"
    if t == "reserve_library_resource": return f"reserve library item {a.get('item_id','?')}"
    return f"run {t}"

def agent_turn(req: ChatRequest, user):
    """Yields events: status / delta (text pieces) / error / done (final payload)."""
    uid = user["user_id"]; m = req.message.lower()
    task_id = str(uuid.uuid4())[:8].upper()
    log(task_id,"user_request",{"user_id":uid,"message":req.message,"mode":req.mode})
    def done(message, steps=None, results=None, pending=None, completed=False):
        return {"type":"done","task_id":task_id,"message":message,"steps":steps or [],"results":results or [],"pending":pending,"trace":get_trace(task_id),"completed":completed}

    # 1. Instant answers about the student's own timetable (no AI call, so no waiting)
    last_bot = req.history[-1].content if req.history and req.history[-1].role == "assistant" else ""
    if is_timetable_lookup(m, bool(TIMETABLE_REPLY.match(last_bot))):
        direct = timetable_direct_answer(req.message, uid)
        if direct is not None:
            log(task_id,"timetable_lookup",{"answer":direct})
            yield {"type":"delta","text":direct}; yield done(direct, completed=True); return

    # 2. Rule-based baseline (no AI)
    if req.mode == "baseline":
        steps = []
        if "attendance" in m: steps.append({"tool":"check_attendance","arguments":{}})
        if "leave balance" in m or "remaining leave" in m: steps.append({"tool":"check_leave_balance","arguments":{}})
        if "lab" in m and any(x in m for x in ["slot","book","reserve","available"]):
            steps.append({"tool":"list_lab_slots","arguments":{"date":str(date.today()+timedelta(days=1))}})
        if "library" in m:
            steps.append({"tool":"search_library","arguments":{"query":"Artificial Intelligence" if "artificial" in m else ""}})
        if "maintenance" in m or "broken" in m:
            steps.append({"tool":"create_maintenance_ticket","arguments":{"location":"Not specified","issue":req.message}})
        if "timetable" in m or "class" in m:
            steps.append({"tool":"list_timetable","arguments":{"day":weekday_from_message(m) or "all"}})
        plan = {"message":"The rule-based baseline selected tools using fixed keyword rules.","steps":steps}

    # 3. Normal conversation: one streamed AI call, replies appear word by word
    elif not needs_tools(m):
        messages = [{"role":"system","content":chat_system_prompt(user)}]+history_messages(req.history,12,1500)+[{"role":"user","content":req.message}]
        log(task_id,"chat_reply_started",{"history_turns":len(req.history)})
        t0 = perf_counter(); first = None; text = ""; thinking = 0; finish = None; retried = False
        try:
            for attempt in (1, 2):
                parts = []; thinking = 0; finish = None
                tokens = CFG["chat_max_tokens"]*(2 if attempt == 2 else 1)
                for kind, piece in stream_events(messages, max_tokens=tokens, force=(attempt == 2)):
                    if kind == "text":
                        if first is None: first = perf_counter()-t0
                        parts.append(piece); yield {"type":"delta","text":piece}
                    elif kind == "reasoning":
                        if thinking == 0: yield {"type":"status","text":"Thinking…"}
                        thinking += len(piece)
                    elif kind == "reset":
                        parts = []; yield {"type":"reset"}
                    elif kind == "finish": finish = piece
                text = "".join(parts).strip()
                if text or not thinking or attempt == 2: break
                retried = True
                yield {"type":"status","text":"The model was thinking for too long. Retrying without thinking…"}
        except ModelError as e:
            log(task_id,"model_error",{"error":str(e)}); yield {"type":"error","message":str(e)}; return
        log(task_id,"chat_reply",{"first_token_seconds":round(first or 0,2),"total_seconds":round(perf_counter()-t0,2),"characters":len(text),"thinking_characters":thinking,"finish_reason":finish,"retried_without_thinking":retried})
        if not text:
            if thinking: msg = "The model spent its whole reply limit thinking and never wrote an answer. Open ⚙ AI settings and turn on “Disable thinking”, or switch off thinking for this model in Bionic / LM Studio."
            elif finish: msg = f"The model sent an empty reply (finish reason: {finish}). Open ⚙ AI settings and press Test connection."
            else: msg = "The model sent nothing back. Open ⚙ AI settings and press Test connection to check the API URL and model name."
            log(task_id,"model_error",{"error":msg}); yield {"type":"error","message":msg}; return
        yield done(text, completed=True); return

    # 4. Requests that need campus tools: the AI plans, the app runs the tools
    else:
        yield {"type":"status","text":"Working on it…"}
        profile = f"Logged-in student profile: User ID {uid}, name {user['name'] or 'not provided'}, course {user['course'] or 'not provided'}. Today is {date.today().strftime('%A %Y-%m-%d')}. Request: {req.message}"
        planner = [{"role":"system","content":SYSTEM_PROMPT}]+history_messages(req.history,4,400)+[{"role":"user","content":profile}]
        t0 = perf_counter()
        try: raw = call_bionic(planner, max_tokens=500)
        except ModelError as e:
            log(task_id,"model_error",{"error":str(e)}); yield {"type":"error","message":str(e)}; return
        log(task_id,"model_timing",{"planning_seconds":round(perf_counter()-t0,2)})
        try: plan = parse_plan(raw)
        except Exception:
            log(task_id,"planning_parse_error",{"raw":raw[:1200]})
            msg = "I couldn't interpret that safely. Could you rephrase it?"
            yield {"type":"delta","text":msg}; yield done(msg); return
        log(task_id,"plan_created",plan)

    results = []; pending = None
    for step in plan.get("steps",[])[:6]:
        name = step.get("tool"); args = step.get("arguments") or {}
        if name not in TOOLS:
            log(task_id,"invalid_tool_rejected",{"tool":name}); continue
        log(task_id,"tool_selected",{"tool":name,"arguments":args})
        if name in WRITE_TOOLS:
            pending = {"tool":name,"arguments":args}
            pending["summary"] = describe_pending(pending, uid)
            log(task_id,"human_approval_required",pending)
            break
        try:
            result = run_tool(name,args,uid)
            results.append({"tool":name,"result":result})
            log(task_id,"tool_result",{"tool":name,"result":result})
            if not result.get("success",False): log(task_id,"failure_observed",{"tool":name,"result":result})
        except Exception as e:
            results.append({"tool":name,"result":{"success":False,"error":str(e)}})
            log(task_id,"tool_error",{"tool":name,"error":str(e)})
    completed = bool(results) and pending is None and all(x["result"].get("success") for x in results)
    with db() as con:
        con.execute("INSERT INTO evaluations(task_id,mode,completed,created_at) VALUES(?,?,?,?)",(task_id,req.mode,int(completed),datetime.now().isoformat(timespec="seconds")))

    blocks = [format_result(x["tool"],x["result"]) for x in results]
    if pending:
        blocks.append(f"I can {pending['summary']}. Please approve it below and I'll go ahead.")
    elif not results:
        blocks.append((plan.get("message") or "").strip() or "I'm not sure what to do with that. Could you give me a bit more detail?")
    elif req.mode != "baseline" and (plan.get("message") or "").strip():
        blocks.insert(0,plan["message"].strip())
    message = "\n\n".join(b for b in blocks if b)
    yield done(message, steps=plan.get("steps",[]), results=results, pending=pending, completed=completed)

@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest,user=Depends(get_current_user)):
    def gen():
        try:
            for ev in agent_turn(req,user):
                yield json.dumps(ev,ensure_ascii=False)+"\n"
        except Exception as e:
            yield json.dumps({"type":"error","message":f"Something went wrong: {e}"})+"\n"
    return StreamingResponse(gen(),media_type="application/x-ndjson",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.post("/api/chat")
def chat(req: ChatRequest,user=Depends(get_current_user)):
    final = None
    for ev in agent_turn(req,user):
        if ev["type"] == "error": raise HTTPException(502,ev["message"])
        if ev["type"] == "done": final = ev
    return {k:v for k,v in final.items() if k != "type"}

@app.post("/api/confirm")
def confirm(req: ConfirmRequest,user=Depends(get_current_user)):
    with db() as con:
        row=con.execute("SELECT details FROM decision_logs WHERE task_id=? AND event='human_approval_required' ORDER BY id DESC LIMIT 1",(req.task_id,)).fetchone()
        owner=con.execute("SELECT details FROM decision_logs WHERE task_id=? AND event='user_request' LIMIT 1",(req.task_id,)).fetchone()
    if not row or not owner or json.loads(owner["details"]).get("user_id")!=user["user_id"]:
        raise HTTPException(404,"No pending action for this account.")
    if not req.approve:
        log(req.task_id,"human_rejected_action",{})
        return {"ok":True,"message":"Action cancelled.","trace":get_trace(req.task_id)}
    pending=json.loads(row["details"])
    log(req.task_id,"human_approved_action",pending)
    try: result=run_tool(pending["tool"],pending["arguments"],user["user_id"])
    except Exception as e: result={"success":False,"error":str(e)}
    log(req.task_id,"tool_result_after_approval",{"tool":pending["tool"],"result":result})
    with db() as con:
        con.execute("INSERT INTO evaluations(task_id,mode,completed,created_at) VALUES(?,?,?,?)",(req.task_id,"agent",int(bool(result.get("success"))),datetime.now().isoformat(timespec="seconds")))
    return {"ok":True,"message":format_result(pending["tool"],result),"result":result,"trace":get_trace(req.task_id)}

@app.get("/api/trace/{task_id}")
def trace(task_id: str,user=Depends(get_current_user)):
    with db() as con:
        owner=con.execute("SELECT details FROM decision_logs WHERE task_id=? AND event='user_request' LIMIT 1",(task_id,)).fetchone()
    if not owner or json.loads(owner["details"]).get("user_id")!=user["user_id"]: raise HTTPException(404,"Trace not found.")
    return {"task_id":task_id,"trace":get_trace(task_id)}

@app.get("/api/evaluation")
def evaluation(user=Depends(get_current_user)):
    with db() as con:
        rows=con.execute("""SELECT mode,COUNT(*) total,SUM(completed) completed FROM evaluations e
            JOIN decision_logs l ON l.task_id=e.task_id AND l.event='user_request'
            WHERE json_extract(l.details,'$.user_id')=? GROUP BY mode""",(user["user_id"],)).fetchall()
    return {r["mode"]:{"total":r["total"],"completed":r["completed"] or 0,
        "rate":round((r["completed"] or 0)/r["total"]*100,1) if r["total"] else 0} for r in rows}
