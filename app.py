import os
from datetime import date, datetime, timedelta
from functools import wraps

import mysql.connector
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-before-deploying")

DB = dict(host=os.getenv("DB_HOST", "localhost"),
          user=os.getenv("DB_USER", "root"),
          password=os.getenv("DB_PASSWORD", ""),
          database=os.getenv("DB_NAME", "medicare_connect"))

SLOTS = [f"{h:02d}:{m:02d}" for h in range(9, 17) for m in (0, 30)]  # 09:00-16:30

APPT_SQL = """SELECT a.*, p.name AS patient_name, du.name AS doctor_name, d.specialization
              FROM appointments a
              JOIN patients p ON p.id = a.patient_id
              JOIN doctors d ON d.id = a.doctor_id
              JOIN users du ON du.id = d.user_id """


# ---------- database helpers ----------
def get_db():
    if "db" not in g:
        g.db = mysql.connector.connect(**DB)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db:
        db.close()


def query(sql, args=(), one=False):
    cur = get_db().cursor(dictionary=True)
    cur.execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, args)
    db.commit()
    last_id = cur.lastrowid
    cur.close()
    return last_id


def hhmm(value):
    """MySQL TIME arrives as timedelta; show it as HH:MM."""
    if isinstance(value, timedelta):
        secs = int(value.total_seconds())
        return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}"
    return str(value)[:5]


app.jinja_env.filters["hhmm"] = hhmm


def initials(name):
    parts = [w for w in (name or "?").split() if w.lower().strip(".") != "dr"] or [name or "?"]
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


app.jinja_env.filters["initials"] = initials


# ---------- auth ----------
def login_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if "user" not in session:
                return redirect(url_for("login"))
            if roles and session["user"]["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


@app.errorhandler(403)
def forbidden(_e):
    return "You don't have access to this page.", 403


@app.route("/")
def index():
    return redirect(url_for("dashboard" if "user" in session else "login"))


def start_session(user):
    pat = query("SELECT id FROM patients WHERE user_id=%s", (user["id"],), one=True)
    doc = query("SELECT id FROM doctors WHERE user_id=%s", (user["id"],), one=True)
    session.clear()
    session["user"] = {"id": user["id"], "name": user["name"], "role": user["role"],
                       "patient_id": pat["id"] if pat else None,
                       "doctor_id": doc["id"] if doc else None}


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        name, email, pw = f.get("name", "").strip(), f.get("email", "").strip().lower(), f.get("password", "")
        role = f.get("role") if f.get("role") in ("patient", "doctor") else "patient"
        spec = f.get("specialization", "").strip()
        if not name or "@" not in email or "." not in email.split("@")[-1]:
            flash("Enter your name and a valid email address.", "error")
        elif len(pw) < 6:
            flash("Choose a password with at least 6 characters.", "error")
        elif role == "doctor" and not spec:
            flash("Doctors need to enter a specialization.", "error")
        elif query("SELECT id FROM users WHERE email=%s", (email,), one=True):
            flash("That email already has an account. Sign in instead.", "error")
        else:
            uid = execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,%s)",
                          (name, email, generate_password_hash(pw), role))
            if role == "doctor":
                try:
                    years = max(0, int(f.get("experience_years") or 0))
                except ValueError:
                    years = 0
                execute("INSERT INTO doctors (user_id,specialization,phone,experience_years) VALUES (%s,%s,%s,%s)",
                        (uid, spec, f.get("phone") or None, years))
            else:
                execute("INSERT INTO patients (user_id,name,phone,dob,gender,blood_group) VALUES (%s,%s,%s,%s,%s,%s)",
                        (uid, name, f.get("phone") or None, f.get("dob") or None,
                         f.get("gender") or None, f.get("blood_group") or None))
            start_session(query("SELECT * FROM users WHERE id=%s", (uid,), one=True))
            flash("Welcome to MediCare Connect.", "ok")
            return redirect(url_for("dashboard"))
    return render_template("register.html", f=request.form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email, pw = request.form.get("email", "").strip().lower(), request.form.get("password", "")
        user = query("SELECT * FROM users WHERE email=%s", (email,), one=True)
        if user and check_password_hash(user["password_hash"], pw):
            start_session(user)
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- dashboard ----------
def first_name(full):
    return next((w for w in full.split() if w.lower().strip(".") != "dr"), full)


def day_label(d, today):
    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    return f"In {(d - today).days} days"


def build_board(day, only_doctor=None, show_names=False):
    """Doctors x 30-minute slots for one day: each cell is booked, free or past."""
    docs = query("SELECT d.id, u.name, d.specialization FROM doctors d "
                 "JOIN users u ON u.id = d.user_id ORDER BY u.name")
    if only_doctor:
        docs = [d for d in docs if d["id"] == only_doctor]
    booked = {(r["doctor_id"], hhmm(r["appt_time"])): r["name"] for r in query(
        "SELECT a.doctor_id, a.appt_time, p.name FROM appointments a "
        "JOIN patients p ON p.id = a.patient_id WHERE a.appt_date=%s AND a.status<>'cancelled'", (day,))}
    today, now = date.today(), datetime.now().strftime("%H:%M")
    board = []
    for d in docs:
        cells = []
        for s in SLOTS:
            who = ""
            if (d["id"], s) in booked:
                state, who = "booked", (booked[(d["id"], s)] if show_names else "")
            elif day < today or (day == today and s <= now):
                state = "past"
            else:
                state = "free"
            cells.append({"time": s, "state": state, "who": who})
        board.append({**d, "cells": cells, "free": sum(c["state"] == "free" for c in cells)})
    return board


@app.route("/dashboard")
@login_required()
def dashboard():
    u, today = session["user"], date.today()
    try:
        day = date.fromisoformat(request.args.get("day", ""))
    except ValueError:
        day = today
    now = datetime.now().strftime("%H:%M")
    hour = datetime.now().hour
    ctx = dict(day=day, today=today, first=first_name(u["name"]),
               greeting="morning" if hour < 12 else "afternoon" if hour < 17 else "evening",
               prev_day=(day - timedelta(days=1)).isoformat(),
               next_day=(day + timedelta(days=1)).isoformat(),
               hours=range(9, 17), appts=[], board=[], next_up=None)

    if u["role"] == "admin":
        n = lambda sql, a=(): query(sql, a, one=True)["n"]
        ctx["stats"] = {
            "Patients": n("SELECT COUNT(*) AS n FROM patients"),
            "Doctors": n("SELECT COUNT(*) AS n FROM doctors"),
            "Appointments today": n("SELECT COUNT(*) AS n FROM appointments WHERE appt_date=%s AND status<>'cancelled'", (today,)),
            "Booked ahead": n("SELECT COUNT(*) AS n FROM appointments WHERE appt_date>%s AND status='scheduled'", (today,)),
        }
        counts = {r["appt_date"]: r["n"] for r in query(
            "SELECT appt_date, COUNT(*) AS n FROM appointments WHERE appt_date BETWEEN %s AND %s "
            "AND status<>'cancelled' GROUP BY appt_date", (today, today + timedelta(days=6)))}
        week = [{"label": (today + timedelta(days=i)).strftime("%a"),
                 "n": counts.get(today + timedelta(days=i), 0)} for i in range(7)]
        peak = max([w["n"] for w in week] + [1])
        for w in week:
            w["pct"] = max(4, round(w["n"] / peak * 100))
        ctx["week"] = week
        mix = {r["status"]: r["n"] for r in query("SELECT status, COUNT(*) AS n FROM appointments GROUP BY status")}
        total, s, c = sum(mix.values()), mix.get("scheduled", 0), mix.get("completed", 0)
        a = round(s / total * 100) if total else 0
        ctx["mix"] = {"total": total, "scheduled": s, "completed": c, "cancelled": mix.get("cancelled", 0),
                      "a": a, "b": a + (round(c / total * 100) if total else 0)}
        ctx["board"] = build_board(day, show_names=True)
        ctx["appts"] = query(APPT_SQL + "WHERE a.appt_date=%s AND a.status<>'cancelled' ORDER BY a.appt_time", (day,))

    elif u["role"] == "doctor":
        appts = query(APPT_SQL + "WHERE a.doctor_id=%s AND a.appt_date=%s AND a.status<>'cancelled' "
                      "ORDER BY a.appt_time", (u["doctor_id"], day))
        sched = [a for a in appts if a["status"] == "scheduled"]
        if day > today:
            upcoming = sched
        elif day == today:
            upcoming = [a for a in sched if hhmm(a["appt_time"]) >= now]
        else:
            upcoming = []
        ctx.update(appts=appts, next_up=upcoming[0] if upcoming else None,
                   counts={"total": len(appts), "done": sum(a["status"] == "completed" for a in appts),
                           "left": len(upcoming)},
                   board=build_board(day, only_doctor=u["doctor_id"], show_names=True))

    else:
        appts = query(APPT_SQL + "WHERE a.patient_id=%s AND a.appt_date>=%s AND a.status='scheduled' "
                      "ORDER BY a.appt_date, a.appt_time", (u["patient_id"], today))
        ctx.update(appts=appts, next_up=appts[0] if appts else None,
                   profile=query("SELECT * FROM patients WHERE id=%s", (u["patient_id"],), one=True),
                   latest=query("""SELECT r.*, du.name AS doctor_name FROM records r
                                   JOIN doctors d ON d.id = r.doctor_id JOIN users du ON du.id = d.user_id
                                   WHERE r.patient_id=%s ORDER BY r.created_at DESC LIMIT 1""",
                                (u["patient_id"],), one=True),
                   board=build_board(day))
        if appts:
            ctx["when"] = day_label(appts[0]["appt_date"], today)
    return render_template("dashboard.html", **ctx)


# ---------- patients ----------
@app.route("/patients")
@login_required("admin", "doctor")
def patients():
    q = request.args.get("q", "").strip()
    rows = query("SELECT * FROM patients WHERE name LIKE %s OR phone LIKE %s ORDER BY name",
                 (f"%{q}%", f"%{q}%"))
    return render_template("patients.html", rows=rows, q=q)


@app.route("/patients/<int:pid>")
@login_required()
def patient_detail(pid):
    u = session["user"]
    if u["role"] == "patient" and u["patient_id"] != pid:
        abort(403)
    p = query("SELECT * FROM patients WHERE id=%s", (pid,), one=True) or abort(404)
    records = query("""SELECT r.*, du.name AS doctor_name FROM records r
                       JOIN doctors d ON d.id = r.doctor_id JOIN users du ON du.id = d.user_id
                       WHERE r.patient_id=%s ORDER BY r.created_at DESC""", (pid,))
    appts = query(APPT_SQL + "WHERE a.patient_id=%s ORDER BY a.appt_date DESC, a.appt_time DESC", (pid,))
    open_appts = [a for a in appts if a["status"] == "scheduled" and a["doctor_id"] == u["doctor_id"]]
    return render_template("patient_detail.html", p=p, records=records, appts=appts, open_appts=open_appts)


PATIENT_FIELDS = ("name", "phone", "dob", "gender", "blood_group", "address", "allergies")


@app.route("/patients/new", methods=["GET", "POST"])
@app.route("/patients/<int:pid>/edit", methods=["GET", "POST"])
@login_required("admin")
def patient_form(pid=None):
    p = query("SELECT * FROM patients WHERE id=%s", (pid,), one=True) if pid else {}
    if pid and not p:
        abort(404)
    if request.method == "POST":
        vals = [request.form.get(k, "").strip() or None for k in PATIENT_FIELDS]
        if not vals[0]:
            flash("Enter the patient's name.", "error")
        elif pid:
            execute("UPDATE patients SET name=%s,phone=%s,dob=%s,gender=%s,blood_group=%s,address=%s,allergies=%s WHERE id=%s",
                    (*vals, pid))
            flash("Patient updated.", "ok")
            return redirect(url_for("patient_detail", pid=pid))
        else:
            new_id = execute("INSERT INTO patients (name,phone,dob,gender,blood_group,address,allergies) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s)", vals)
            flash("Patient added.", "ok")
            return redirect(url_for("patient_detail", pid=new_id))
        p = request.form
    return render_template("patient_form.html", p=p, editing=bool(pid))


@app.post("/patients/<int:pid>/records")
@login_required("doctor")
def add_record(pid):
    f, doc_id = request.form, session["user"]["doctor_id"]
    if not f.get("diagnosis", "").strip():
        flash("Enter a diagnosis before saving the record.", "error")
    else:
        appt_id = f.get("appointment_id") or None
        execute("INSERT INTO records (patient_id,doctor_id,appointment_id,diagnosis,prescription,notes) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (pid, doc_id, appt_id, f["diagnosis"].strip(),
                 f.get("prescription", "").strip() or None, f.get("notes", "").strip() or None))
        if appt_id:
            execute("UPDATE appointments SET status='completed' WHERE id=%s AND doctor_id=%s AND patient_id=%s",
                    (appt_id, doc_id, pid))
        flash("Record saved.", "ok")
    return redirect(url_for("patient_detail", pid=pid))


# ---------- doctors ----------
@app.route("/doctors")
@login_required()
def doctors():
    rows = query("""SELECT d.id, u.name, u.email, d.specialization, d.phone, d.experience_years
                    FROM doctors d JOIN users u ON u.id = d.user_id ORDER BY u.name""")
    return render_template("doctors.html", rows=rows)


@app.route("/doctors/new", methods=["GET", "POST"])
@login_required("admin")
def doctor_new():
    if request.method == "POST":
        f = request.form
        name, email, pw = f["name"].strip(), f["email"].strip().lower(), f["password"]
        if not (name and email and f["specialization"].strip() and len(pw) >= 6):
            flash("Fill in name, email, specialization and a password of at least 6 characters.", "error")
        elif query("SELECT id FROM users WHERE email=%s", (email,), one=True):
            flash("That email already has an account.", "error")
        else:
            uid = execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,'doctor')",
                          (name, email, generate_password_hash(pw)))
            execute("INSERT INTO doctors (user_id,specialization,phone,experience_years) VALUES (%s,%s,%s,%s)",
                    (uid, f["specialization"].strip(), f.get("phone") or None, f.get("experience_years") or 0))
            flash("Doctor added.", "ok")
            return redirect(url_for("doctors"))
    return render_template("doctor_form.html")


# ---------- appointments ----------
@app.route("/appointments")
@login_required()
def appointments():
    u = session["user"]
    where, args = "", ()
    if u["role"] == "doctor":
        where, args = "WHERE a.doctor_id=%s ", (u["doctor_id"],)
    elif u["role"] == "patient":
        where, args = "WHERE a.patient_id=%s ", (u["patient_id"],)
    rows = query(APPT_SQL + where + "ORDER BY a.appt_date DESC, a.appt_time DESC", args)
    return render_template("appointments.html", rows=rows)


@app.get("/api/slots")
@login_required()
def slots():
    doctor_id = request.args.get("doctor_id", type=int)
    try:
        day = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return {"slots": []}
    booked = {hhmm(r["appt_time"]) for r in query(
        "SELECT appt_time FROM appointments WHERE doctor_id=%s AND appt_date=%s AND status<>'cancelled'",
        (doctor_id, day))}
    free = [s for s in SLOTS if s not in booked]
    if day < date.today():
        free = []
    elif day == date.today():
        now = datetime.now().strftime("%H:%M")
        free = [s for s in free if s > now]
    return {"slots": free}


@app.route("/appointments/new", methods=["GET", "POST"])
@login_required("patient", "admin")
def book():
    u = session["user"]
    docs = query("""SELECT d.id, u.name, d.specialization FROM doctors d
                    JOIN users u ON u.id = d.user_id ORDER BY u.name""")
    pats = query("SELECT id, name FROM patients ORDER BY name") if u["role"] == "admin" else []
    if request.method == "POST":
        f = request.form
        pid = u["patient_id"] if u["role"] == "patient" else f.get("patient_id", type=int)
        try:
            day = date.fromisoformat(f.get("appt_date", ""))
        except ValueError:
            day = None
        t, doctor_id = f.get("appt_time", ""), f.get("doctor_id", type=int)
        if not (pid and doctor_id and day and t in SLOTS) or day < date.today():
            flash("Choose a doctor, a future date and a time.", "error")
        elif query("""SELECT id FROM appointments WHERE doctor_id=%s AND appt_date=%s
                      AND appt_time=%s AND status<>'cancelled'""", (doctor_id, day, t + ":00"), one=True):
            flash("That time was just taken. Choose another time.", "error")
        else:
            execute("INSERT INTO appointments (patient_id,doctor_id,appt_date,appt_time,reason) "
                    "VALUES (%s,%s,%s,%s,%s)", (pid, doctor_id, day, t + ":00", f.get("reason", "").strip() or None))
            flash("Appointment booked.", "ok")
            return redirect(url_for("appointments"))
    src = request.form if request.method == "POST" else request.args
    pre = {"doctor_id": src.get("doctor_id", type=int), "date": src.get("date") or src.get("appt_date") or "",
           "time": src.get("time") or src.get("appt_time") or "", "patient_id": src.get("patient_id", type=int)}
    return render_template("book.html", doctors=docs, patients=pats, today=date.today().isoformat(), pre=pre)


@app.post("/appointments/<int:aid>/<action>")
@login_required()
def appointment_action(aid, action):
    if action not in ("cancel", "complete"):
        abort(404)
    a = query("SELECT * FROM appointments WHERE id=%s", (aid,), one=True) or abort(404)
    u = session["user"]
    allowed = (u["role"] == "admin"
               or (u["role"] == "doctor" and a["doctor_id"] == u["doctor_id"])
               or (u["role"] == "patient" and a["patient_id"] == u["patient_id"]))
    if not allowed or (action == "complete" and u["role"] == "patient"):
        abort(403)
    if a["status"] != "scheduled":
        flash("Only scheduled appointments can be changed.", "error")
    else:
        execute("UPDATE appointments SET status=%s WHERE id=%s",
                ("cancelled" if action == "cancel" else "completed", aid))
        flash("Appointment cancelled." if action == "cancel" else "Appointment marked complete.", "ok")
    return redirect(request.referrer or url_for("appointments"))


if __name__ == "__main__":
    app.run(debug=True)
