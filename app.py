import secrets
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import (
    check_password_hash,
    generate_password_hash,
)


app = Flask(__name__)

app.config.update(
    SECRET_KEY=secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
)

CSRFProtect(app)

PASSWORD_HASH = generate_password_hash("EnergyMonitor123!")

# One source of truth for faculty accounts, charts, and reports.
# Names follow UNILAG's official faculty directory and restructuring notice.
FACULTIES = {
    "architecture": "Architecture",
    "arts": "Arts",
    "basic_clinical_sciences": "Basic Clinical Sciences",
    "basic_medical_sciences": "Basic Medical Sciences",
    "clinical_sciences": "Clinical Sciences",
    "communication_media_studies": "Communication and Media Studies",
    "computing_informatics": "Computing and Informatics",
    "creative_arts": "Creative Arts",
    "dental_sciences": "Dental Sciences",
    "education": "Education",
    "engineering": "Engineering",
    "environmental_sciences": "Environmental Sciences",
    "health_professions": "Health Professions",
    "law": "Law",
    "life_sciences": "Life Sciences",
    "management_sciences": "Management Sciences",
    "pharmacy": "Pharmacy",
    "physical_earth_sciences": "Physical and Earth Sciences",
    "social_sciences": "Social Sciences",
}

# Each faculty uses its displayed name in lowercase as its prototype password.
# The VC retains its existing password.
ACCOUNTS = {
    "vc": {
        "role": "admin",
        "name": "VC / Administrator",
        "password_hash": PASSWORD_HASH,
    },
    **{
        key: {
            "role": "department",
            "name": f"Faculty of {name}",
            "password_hash": generate_password_hash(name.lower()),
        }
        for key, name in FACULTIES.items()
    },
}


def faculty_name(key):
    """Keep display labels independent of stored database identifiers."""
    return FACULTIES.get(key, f"Legacy / unassigned — {key.replace('_', ' ').title()}")


@app.context_processor
def faculty_context():
    return {"faculties": FACULTIES, "faculty_name": faculty_name}


def build_line_chart(trend, expected):
    """Render offline SVG coordinates; missing days are gaps, not zeros."""
    upper = max(1, max((d["total"] for d in trend if d["reported"]), default=0) * 1.15)
    points = []
    for index, day in enumerate(trend):
        points.append({
            **day,
            "x": round(95 + index * 120, 2),
            "y": round(260 - day["total"] / upper * 215, 2),
            "complete": day["reported"] == expected,
        })
    segments = []
    for first, second in zip(points, points[1:]):
        if first["reported"] and second["reported"]:
            segments.append({
                "x1": first["x"], "y1": first["y"],
                "x2": second["x"], "y2": second["y"],
                "complete": first["complete"] and second["complete"],
            })
    ticks = [{"y": 260 - i * 53.75, "value": upper * i / 4} for i in range(5)]
    return {"points": points, "segments": segments, "ticks": ticks,
            "has_data": any(d["reported"] for d in trend)}


# SQLite data will be kept in this folder.
Path(app.instance_path).mkdir(parents=True, exist_ok=True)

DATABASE = Path(app.instance_path) / "readings.db"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row

    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)

    if db is not None:
        db.close()


def initialize_database():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department TEXT NOT NULL,
            reading_date TEXT NOT NULL,
            energy_hundredths INTEGER NOT NULL
                CHECK (energy_hundredths >= 0),
            UNIQUE (department, reading_date)
        )
    """)

    db.commit()


with app.app_context():
    initialize_database()


@app.route("/", methods=["GET", "POST"])
def login():
    if session.get("username") in ACCOUNTS:
        return redirect(url_for("dashboard"))

    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        if (
            username in ACCOUNTS
            and check_password_hash(
                ACCOUNTS[username]["password_hash"], password
            )
        ):
            session.clear()
            session["username"] = username
            session.permanent = True

            return redirect(url_for("dashboard"))

        error = "Incorrect account or password. Please try again."

    return render_template("login.html", error=error)


@app.route("/dashboard")
def dashboard():
    username = session.get("username")

    if username not in ACCOUNTS:
        return redirect(url_for("login"))

    account = ACCOUNTS[username]
    db = get_db()

    # Get the records this account is allowed to view.
    if account["role"] == "admin":
        readings = db.execute("""
            SELECT *
            FROM readings
            ORDER BY reading_date DESC, department ASC
        """).fetchall()

    else:
        readings = db.execute(
            """
            SELECT *
            FROM readings
            WHERE department = ?
            ORDER BY reading_date DESC
            """,
            (username,),
        ).fetchall()

    legacy_readings = [row for row in readings if row["department"] not in FACULTIES]
    readings = [row for row in readings if row["department"] in FACULTIES]

    today_date = date.today()
    today = today_date.isoformat()

    today_readings = [
        row for row in readings
        if row["reading_date"] == today
    ]

    today_total = sum(
        row["energy_hundredths"]
        for row in today_readings
    ) / 100

    reported_today = len(today_readings)
    expected_today = len(FACULTIES) if account["role"] == "admin" else 1

    # Build the seven-day chart.
    trend = []

    for days_ago in range(6, -1, -1):
        chart_date = today_date - timedelta(days=days_ago)
        chart_date_text = chart_date.isoformat()

        day_readings = [
            row for row in readings
            if row["reading_date"] == chart_date_text
        ]

        total_hundredths = sum(
            row["energy_hundredths"]
            for row in day_readings
        )

        trend.append({
            "date": chart_date_text,
            "label": chart_date.strftime("%d %b"),
            "total": total_hundredths / 100,
            "reported": len(day_readings),
        })

    line_chart = build_line_chart(trend, expected_today)

    # Check recent readings for high consumption.
    alerts = []
    alert_start = (
        today_date - timedelta(days=6)
    ).isoformat()

    for reading in readings:
        if reading["reading_date"] < alert_start:
            continue

        previous_readings = [
            row for row in readings
            if (
                row["department"] == reading["department"]
                and row["reading_date"] < reading["reading_date"]
            )
        ]

        previous_readings.sort(
            key=lambda row: row["reading_date"],
            reverse=True,
        )

        previous_seven = previous_readings[:7]

        if len(previous_seven) < 7:
            continue

        baseline = sum(
            row["energy_hundredths"]
            for row in previous_seven
        ) / 7

        if baseline <= 0:
            continue

        consumption = reading["energy_hundredths"]

        if consumption > baseline * 1.30:
            increase = (
                (consumption - baseline) / baseline
            ) * 100

            alerts.append({
                "department": faculty_name(reading["department"]),
                "date": reading["reading_date"],
                "consumption": consumption / 100,
                "baseline": baseline / 100,
                "increase": increase,
            })

    # Return the page AFTER all calculations finish.
    return render_template(
        "dashboard.html",
        account=account,
        readings=readings,
        today=today,
        today_total=today_total,
        reported_today=reported_today,
        expected_today=expected_today,
        trend=trend,
        line_chart=line_chart,
        alerts=alerts,
        legacy_readings=legacy_readings,
        dashboard_data={
            "today": today,
            "is_admin": account["role"] == "admin",
            "faculties": FACULTIES if account["role"] == "admin" else {
                username: FACULTIES[username]
            },
            "readings": [
                {"id": row["id"], "faculty": row["department"],
                 "date": row["reading_date"],
                 "hundredths": row["energy_hundredths"]}
                for row in readings
            ],
        },
    )


@app.route("/submit-reading", methods=["POST"])
def submit_reading():
    username = session.get("username")

    if username not in ACCOUNTS:
        return redirect(url_for("login"))

    # Only department accounts may submit readings.
    if ACCOUNTS[username]["role"] != "department":
        return "Only faculty accounts can submit readings.", 403

    reading_date = request.form.get("reading_date", "")
    energy_text = request.form.get("energy", "")

    try:
        selected_date = date.fromisoformat(reading_date)

        if selected_date > date.today():
            raise ValueError

        energy = Decimal(energy_text)

        if (
            not energy.is_finite()
            or energy < 0
            or energy > 1_000_000
        ):
            raise ValueError

        # Accept no more than two decimal places.
        if energy != energy.quantize(Decimal("0.01")):
            raise ValueError

        energy_hundredths = int(energy * 100)

    except (ValueError, InvalidOperation):
        flash(
            "Enter a valid date up to today and consumption "
            "from 0 to 1,000,000 kWh, with at most two decimal places.",
            "error",
        )
        return redirect(url_for("dashboard"))

    db = get_db()

    try:
        db.execute(
            """
            INSERT INTO readings (
                department,
                reading_date,
                energy_hundredths
            )
            VALUES (?, ?, ?)
            """,
            (
                username,
                selected_date.isoformat(),
                energy_hundredths,
            ),
        )

        db.commit()

    except sqlite3.IntegrityError:
        db.rollback()

        flash(
            "Your faculty already has a reading for that date.",
            "error",
        )

        return redirect(url_for("dashboard"))

    flash(
        f"Saved {energy:.2f} kWh for {selected_date.isoformat()}.",
        "success",
    )

    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.after_request
def prevent_page_caching(response):
    response.headers["Cache-Control"] = "no-store"
    return response

@app.route("/reports")
def monthly_report():
    username = session.get("username")

    if username not in ACCOUNTS:
        return redirect(url_for("login"))

    account = ACCOUNTS[username]

    # Use the current month unless another month is selected.
    current_month = date.today().strftime("%Y-%m")
    selected_month = request.args.get("month", current_month)

    try:
        month_start = date.fromisoformat(
            selected_month + "-01"
        )

        if month_start.strftime("%Y-%m") != selected_month:
            raise ValueError

    except ValueError:
        flash("Please select a valid month.", "error")
        return redirect(url_for("monthly_report"))

    db = get_db()

    if account["role"] == "admin":
        month_readings = db.execute(
            """
            SELECT *
            FROM readings
            WHERE substr(reading_date, 1, 7) = ?
            ORDER BY reading_date ASC, department ASC
            """,
            (selected_month,),
        ).fetchall()

        departments = list(FACULTIES)

    else:
        month_readings = db.execute(
            """
            SELECT *
            FROM readings
            WHERE substr(reading_date, 1, 7) = ?
              AND department = ?
            ORDER BY reading_date ASC
            """,
            (selected_month, username),
        ).fetchall()

        departments = [username]

    legacy_readings = [row for row in month_readings if row["department"] not in FACULTIES]
    month_readings = [row for row in month_readings if row["department"] in FACULTIES]
    summaries = []

    for department in departments:
        department_readings = [
            row for row in month_readings
            if row["department"] == department
        ]

        total_hundredths = sum(
            row["energy_hundredths"]
            for row in department_readings
        )

        days_reported = len(department_readings)
        total_kwh = total_hundredths / 100

        average_kwh = (
            total_kwh / days_reported
            if days_reported > 0 else None
        )

        summaries.append({
            "department": faculty_name(department),
            "total": total_kwh,
            "days": days_reported,
            "average": average_kwh,
        })

    monthly_total = sum(
        row["energy_hundredths"]
        for row in month_readings
    ) / 100

    return render_template(
        "reports.html",
        account=account,
        selected_month=selected_month,
        month_label=month_start.strftime("%B %Y"),
        summaries=summaries,
        monthly_total=monthly_total,
        record_count=len(month_readings),
        legacy_readings=legacy_readings,
        legacy_total=sum(row["energy_hundredths"] for row in legacy_readings) / 100,
    )

@app.route(
    "/readings/<int:reading_id>/edit",
    methods=["GET", "POST"],
)
def edit_reading(reading_id):
    username = session.get("username")

    if username not in ACCOUNTS:
        return redirect(url_for("login"))

    account = ACCOUNTS[username]

    if account["role"] != "department":
        return "Only faculties can edit their own readings.", 403

    db = get_db()

    # Check both the reading ID and the signed-in department.
    reading = db.execute(
        """
        SELECT *
        FROM readings
        WHERE id = ? AND department = ?
        """,
        (reading_id, username),
    ).fetchone()

    if reading is None:
        return "Reading not found for your faculty.", 404

    error = None

    energy_value = (
        f"{reading['energy_hundredths'] / 100:.2f}"
    )

    if request.method == "POST":
        energy_value = request.form.get("energy", "")

        try:
            energy = Decimal(energy_value)

            if (
                not energy.is_finite()
                or energy < 0
                or energy > 1_000_000
            ):
                raise ValueError

            if energy != energy.quantize(Decimal("0.01")):
                raise ValueError

            energy_hundredths = int(energy * 100)

        except (ValueError, InvalidOperation):
            error = (
                "Enter consumption from 0 to 1,000,000 kWh, "
                "with at most two decimal places."
            )

        else:
            db.execute(
                """
                UPDATE readings
                SET energy_hundredths = ?
                WHERE id = ? AND department = ?
                """,
                (
                    energy_hundredths,
                    reading_id,
                    username,
                ),
            )

            db.commit()

            flash(
                "Reading corrected successfully.",
                "success",
            )

            return redirect(url_for("dashboard"))

    return render_template(
        "edit_reading.html",
        account=account,
        reading=reading,
        energy_value=energy_value,
        error=error,
    )

if __name__ == "__main__":
    app.run()
