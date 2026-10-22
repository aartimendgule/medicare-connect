# MediCare Connect

Healthcare management web app built with Flask and MySQL.
Roles: **admin** (manages patients, doctors, all appointments), **doctor** (daily schedule, patient records) and **patient** (self-registers, books appointments, views own records).

## Setup

1. Install MySQL and Python 3.10+.
2. Create the tables:
   ```
   mysql -u root -p < schema.sql
   ```
3. Install dependencies (a virtual environment is recommended):
   ```
   python -m venv venv
   venv\Scripts\activate        # macOS/Linux: source venv/bin/activate
   pip install -r requirements.txt
   ```
4. Set your database login (defaults: host `localhost`, user `root`, empty password):
   ```
   set DB_PASSWORD=your_mysql_password     # macOS/Linux: export DB_PASSWORD=...
   set SECRET_KEY=any-long-random-string
   ```
5. Create the first admin and two sample doctors, then start the app:
   ```
   python seed.py
   python app.py
   ```
6. Open http://127.0.0.1:5000

Anyone can use **Create an account** with any email and a password of 6+ characters, choosing Patient or Doctor, and is signed in straight away. Admin accounts come only from `seed.py`.

Sample logins from `seed.py`: `admin@medicare.local` / `Admin@12345`, `meera@medicare.local` / `Doctor@12345`. Change these before using real data.

## Dashboard

- **Day board:** every doctor against 30-minute slots for any day. Patients and admins click a free slot to open the booking form pre-filled.
- **Admin:** totals, a 7-day workload chart, an appointment status ring, and the day's schedule.
- **Doctor:** next patient card, counts for the day, own timeline.
- **Patient:** next visit, health profile, latest record.

## Project layout

| File | Purpose |
|---|---|
| `schema.sql` | users, patients, doctors, appointments, records tables |
| `app.py` | routes, role checks, booking logic |
| `seed.py` | first admin and sample doctors |
| `templates/`, `static/` | pages and styling |

## Ideas for next steps

- CSRF protection on forms (Flask-WTF)
- Per-doctor working hours and leave days
- Email or SMS appointment reminders
- Prescription PDF download (ReportLab)
- Charts on the admin dashboard (patients per month, busiest doctors)
- Deploy on PythonAnywhere or Render
