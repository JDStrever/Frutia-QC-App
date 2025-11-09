# app.py
import os
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_file
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
import logging

# Basic setup
# Basic setup (explicit template/static folders)
basedir = os.path.abspath(os.path.dirname(__file__))

# Force Flask to load templates and static files from exact folders in the repo root
template_dir = os.path.join(basedir, "templates")
static_dir = os.path.join(basedir, "static")
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

app.secret_key = os.environ.get("FLASK_SECRET", "change-me-in-prod")
db_path = os.environ.get("QC_DB_PATH", os.path.join(basedir, "qc.db"))

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + db_path
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
logging.basicConfig(level=logging.INFO)


# Models
class Crate(db.Model):
    __tablename__ = "crates"
    id = db.Column(db.Integer, primary_key=True)
    run_number = db.Column(db.String(64), nullable=True)
    puc = db.Column(db.String(64), nullable=False)
    farm_name = db.Column(db.String(120), nullable=False)
    commodity = db.Column(db.String(64), nullable=False)
    variety = db.Column(db.String(64), nullable=True)
    grade_class = db.Column(db.String(32), nullable=True)
    size = db.Column(db.String(32), nullable=True)
    weight = db.Column(db.Float, nullable=True)
    date_received = db.Column(db.Date, nullable=False, default=func.current_date())
    inspector_notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "run_number": self.run_number,
            "puc": self.puc,
            "farm_name": self.farm_name,
            "commodity": self.commodity,
            "variety": self.variety,
            "grade_class": self.grade_class,
            "size": self.size,
            "weight": self.weight,
            "date_received": self.date_received.isoformat() if self.date_received else None,
            "inspector_notes": self.inspector_notes,
            "created_at": self.created_at.isoformat(),
        }


# Helpers
def ensure_db():
    if not os.path.exists(db_path):
        db.create_all()
        app.logger.info(f"Database created at {db_path}")


# Routes
@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    ensure_db()
    # Basic filters: run_number, puc, commodity, farm
    q = Crate.query.order_by(Crate.date_received.desc(), Crate.id.desc())
    run = request.args.get("run")
    puc = request.args.get("puc")
    commodity = request.args.get("commodity")
    farm = request.args.get("farm")

    if run:
        q = q.filter(Crate.run_number == run)
    if puc:
        q = q.filter(Crate.puc.ilike(f"%{puc}%"))
    if commodity:
        q = q.filter(Crate.commodity.ilike(f"%{commodity}%"))
    if farm:
        q = q.filter(Crate.farm_name.ilike(f"%{farm}%"))

    crates = q.all()
    totals = {
        "count": len(crates),
        "total_weight": sum(c.weight or 0 for c in crates),
    }
    return render_template("dashboard.html", crates=crates, totals=totals, filters={"run": run, "puc": puc, "commodity": commodity, "farm": farm})


@app.route("/add", methods=["GET", "POST"])
def add_crate():
    ensure_db()
    if request.method == "POST":
        data = request.form
        try:
            c = Crate(
                run_number = data.get("run_number") or None,
                puc = data["puc"],
                farm_name = data["farm_name"],
                commodity = data["commodity"],
                variety = data.get("variety") or None,
                grade_class = data.get("grade_class") or None,
                size = data.get("size") or None,
                weight = float(data["weight"]) if data.get("weight") else None,
                date_received = datetime.strptime(data["date_received"], "%Y-%m-%d").date() if data.get("date_received") else datetime.utcnow().date(),
                inspector_notes = data.get("inspector_notes") or None,
            )
            db.session.add(c)
            db.session.commit()
            flash("Crate added.", "success")
            return redirect(url_for("dashboard"))
        except Exception as e:
            app.logger.exception("Failed to add crate")
            flash(f"Failed to add crate: {e}", "danger")
            return redirect(url_for("add_crate"))
    # GET
    today = datetime.utcnow().date().isoformat()
    return render_template("add.html", today=today)


@app.route("/crate/<int:crate_id>")
def crate_detail(crate_id):
    ensure_db()
    crate = Crate.query.get_or_404(crate_id)
    return render_template("details.html", crate=crate)


@app.route("/export/csv")
def export_csv():
    ensure_db()
    import csv
    from io import StringIO
    crates = Crate.query.order_by(Crate.date_received.desc()).all()
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["id","run_number","puc","farm_name","commodity","variety","grade_class","size","weight","date_received","inspector_notes"])
    for c in crates:
        writer.writerow([
            c.id, c.run_number, c.puc, c.farm_name, c.commodity, c.variety,
            c.grade_class, c.size, c.weight, c.date_received.isoformat() if c.date_received else "",
            (c.inspector_notes or "").replace("\n","\\n")
        ])
    si.seek(0)
    return send_file(
        bytes(si.getvalue(), "utf-8"),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"qc_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    )


# small API endpoint for frontends or integrations
@app.route("/api/crates", methods=["GET", "POST"])
def api_crates():
    ensure_db()
    if request.method == "GET":
        crates = Crate.query.order_by(Crate.id.desc()).all()
        return jsonify([c.to_dict() for c in crates])
    else:
        payload = request.json or {}
        # minimal validation
        puc = payload.get("puc")
        farm_name = payload.get("farm_name")
        commodity = payload.get("commodity")
        if not puc or not farm_name or not commodity:
            return jsonify({"error": "puc, farm_name and commodity are required"}), 400
        c = Crate(
            run_number = payload.get("run_number"),
            puc = puc,
            farm_name = farm_name,
            commodity = commodity,
            variety = payload.get("variety"),
            grade_class = payload.get("grade_class"),
            size = payload.get("size"),
            weight = float(payload["weight"]) if payload.get("weight") else None,
            date_received = datetime.strptime(payload["date_received"], "%Y-%m-%d").date() if payload.get("date_received") else datetime.utcnow().date(),
            inspector_notes = payload.get("inspector_notes")
        )
        db.session.add(c)
        db.session.commit()
        return jsonify(c.to_dict()), 201


if __name__ == "__main__":
    ensure_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=os.environ.get("FLASK_DEBUG","0") == "1")
