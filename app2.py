import os
import io
import random
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for, flash
from pymongo import MongoClient, UpdateOne
from bson.objectid import ObjectId
import pandas as pd
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

app = Flask(__name__)

# --- GÜVENLİK AYARLARI ---
# Session güvenliği için rastgele bir anahtar. (Her restartta değişir, production'da sabitlenmelidir)
app.secret_key = os.urandom(24)

# KULLANICI BİLGİLERİ
ADMIN_USER = "demir"
ADMIN_PASS = "Demirsw123!"

# --- KONFİGÜRASYON ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/corporate_planner")

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping') 
    print("✅ MongoDB Bağlantısı Başarılı")
except Exception as e:
    print(f"❌ MongoDB Bağlantı Hatası: {e}")

db = client["corporate_planner"]
calendar_col = db["tasks"]

# --- SABİTLER ---
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
HOURS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]

# --- LOGIN DECORATOR (GÜVENLİK DUVARI) ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- YARDIMCI FONKSİYONLAR ---
def get_week_metadata():
    today = datetime.now()
    start = today - timedelta(days=today.weekday())
    dates = []
    tr_days = {
        "Monday": "Pazartesi", "Tuesday": "Salı", "Wednesday": "Çarşamba",
        "Thursday": "Perşembe", "Friday": "Cuma", "Saturday": "Cumartesi", "Sunday": "Pazar"
    }
    for i in range(7):
        d = start + timedelta(days=i)
        day_name = DAYS[i]
        dates.append({
            "day_en": day_name,
            "day_tr": tr_days.get(day_name, day_name),
            "date": d.strftime("%d.%m"),
            "full_date": d.strftime("%Y-%m-%d"),
            "is_today": d.date() == today.date()
        })
    return dates

def serialize_doc(doc):
    doc["_id"] = str(doc["_id"])
    return doc

def time_to_minutes(time_str):
    try:
        h, m = map(int, time_str.split(':'))
        return h * 60 + m
    except:
        return 0

def get_next_day(current_day):
    try:
        idx = DAYS.index(current_day)
        return DAYS[(idx + 1) % 7]
    except:
        return current_day

def calculate_new_time_and_day(current_day, time_str, minutes_to_add):
    try:
        original_minutes = time_to_minutes(time_str)
        new_total_minutes = original_minutes + minutes_to_add
        days_passed = new_total_minutes // 1440
        remaining_minutes = new_total_minutes % 1440
        new_hour = remaining_minutes // 60
        new_min = remaining_minutes % 60
        new_time_str = f"{new_hour:02d}:{new_min:02d}"
        final_day = current_day
        for _ in range(days_passed):
            final_day = get_next_day(final_day)
        return final_day, new_time_str
    except Exception as e:
        print(f"Zaman hesaplama hatası: {e}")
        return current_day, time_str

# --- AUTH ROUTE'LARI ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USER and password == ADMIN_PASS:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Kullanıcı adı veya şifre hatalı!")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# --- ANA ROUTE'LAR (Hepsi Korumalı) ---

@app.route("/")
@login_required
def index():
    return render_template("index.html", hours=HOURS, days=DAYS, week_meta=get_week_metadata())

@app.route("/api/tasks", methods=["GET"])
@login_required
def get_tasks():
    try:
        tasks = list(calendar_col.find({}))
        formatted_tasks = {day: {hour: [] for hour in HOURS} for day in DAYS}
        for task in tasks:
            task = serialize_doc(task)
            d, h = task.get("day"), task.get("hour")
            if d in formatted_tasks:
                if h not in formatted_tasks[d]:
                      formatted_tasks[d][h] = []
                formatted_tasks[d][h].append(task)
        return jsonify({"success": True, "data": formatted_tasks})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks", methods=["POST"])
@login_required
def create_task():
    try:
        data = request.json
        new_task = {
            "day": data["day"],
            "hour": data["hour"],
            "title": data["title"],
            "desc": data.get("desc", ""),
            "priority": data.get("priority", "normal"),
            "category": data.get("category", "Genel"),
            "completed": False,
            "created_at": datetime.now()
        }
        res = calendar_col.insert_one(new_task)
        new_task["_id"] = str(res.inserted_id)
        del new_task["created_at"] 
        return jsonify({"success": True, "task": new_task})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>", methods=["PUT"])
@login_required
def update_task(task_id):
    try:
        data = request.json
        allowed_fields = ["title", "desc", "priority", "category", "completed", "day", "hour"]
        update_data = {k: v for k, v in data.items() if k in allowed_fields}
        calendar_col.update_one({"_id": ObjectId(task_id)}, {"$set": update_data})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    try:
        calendar_col.delete_one({"_id": ObjectId(task_id)})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>/snooze", methods=["POST"])
@login_required
def snooze_task(task_id):
    try:
        data = request.json
        minutes = int(data.get("minutes", 30))
        target_task = calendar_col.find_one({"_id": ObjectId(task_id)})
        if not target_task:
            return jsonify({"success": False, "error": "Görev bulunamadı"}), 404
        target_day = target_task["day"]
        target_hour = target_task["hour"]
        target_id_str = str(target_task["_id"])
        target_minutes_val = time_to_minutes(target_hour)
        
        day_tasks = list(calendar_col.find({"day": target_day}))
        tasks_to_update = []
        
        for t in day_tasks:
            current_minutes = time_to_minutes(t["hour"])
            t_id_str = str(t["_id"])
            if t_id_str == target_id_str:
                tasks_to_update.append(t)
            elif current_minutes > target_minutes_val:
                tasks_to_update.append(t)
            else:
                pass

        bulk_ops = []
        count = 0
        for t in tasks_to_update:
            new_day, new_hour = calculate_new_time_and_day(t["day"], t["hour"], minutes)
            bulk_ops.append(UpdateOne({"_id": t["_id"]}, {"$set": {"day": new_day, "hour": new_hour}}))
            count += 1
        if bulk_ops:
            calendar_col.bulk_write(bulk_ops)
        return jsonify({"success": True, "message": f"{count} görev {minutes} dakika ertelendi."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/export", methods=["GET"])
@login_required
def export_excel():
    try:
        tasks = list(calendar_col.find({}, {"_id": 0, "created_at": 0}))
        df = pd.DataFrame(tasks)
        if df.empty:
            df = pd.DataFrame(columns=["day", "hour", "title", "desc", "priority", "category", "completed"])
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Plan')
        output.seek(0)
        return send_file(output, download_name=f"KurumsalPlan_{datetime.now().strftime('%Y%m%d')}.xlsx", as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/auto-generate", methods=["POST"])
@login_required
def auto_generate():
    try:
        titles = ["Strateji Toplantısı", "Müşteri Sunumu", "Kod Review", "Bütçe Planlama", "Haftalık Sync", "E-posta Kontrolü", "Mola"]
        cats = ["İş", "Yönetim", "Satış", "Genel"]
        priorities = ["high", "medium", "normal"]
        new_tasks = []
        for _ in range(random.randint(5, 8)):
            day = random.choice(DAYS)
            hour = random.choice(HOURS[16:36]) 
            new_tasks.append({
                "day": day, "hour": hour, "title": random.choice(titles), "desc": "Otomatik oluşturulan demo görevi.",
                "priority": random.choice(priorities), "category": random.choice(cats), "completed": False, "created_at": datetime.now()
            })
        if new_tasks:
            calendar_col.insert_many(new_tasks)
        return jsonify({"success": True, "count": len(new_tasks)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)