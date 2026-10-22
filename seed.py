"""Creates a first admin and two sample doctors. Run once after loading schema.sql."""
from werkzeug.security import generate_password_hash
from app import app, execute, query

PEOPLE = [
    ("Admin", "admin@medicare.local", "Admin@12345", "admin", None),
    ("Dr. Meera Kulkarni", "meera@medicare.local", "Doctor@12345", "doctor", "General Medicine"),
    ("Dr. Rohan Patil", "rohan@medicare.local", "Doctor@12345", "doctor", "Cardiology"),
]

with app.app_context():
    for name, email, pw, role, spec in PEOPLE:
        if query("SELECT id FROM users WHERE email=%s", (email,), one=True):
            continue
        uid = execute("INSERT INTO users (name,email,password_hash,role) VALUES (%s,%s,%s,%s)",
                      (name, email, generate_password_hash(pw), role))
        if role == "doctor":
            execute("INSERT INTO doctors (user_id,specialization,experience_years) VALUES (%s,%s,%s)", (uid, spec, 8))
        print(f"Created {role}: {email} / {pw}")
    print("Done. Change these passwords before using real data.")
