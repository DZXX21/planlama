import os
import io
import random
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from flask_bcrypt import Bcrypt
from pymongo import MongoClient
from bson.objectid import ObjectId
import pandas as pd
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

app = Flask(__name__)
# GÜVENLİK: Secret key yoksa rastgele oluşturulur, production'da .env'den gelmeli
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))
bcrypt = Bcrypt(app)

# Session timeout (30 dakika)
app.permanent_session_lifetime = timedelta(minutes=30)

# --- MONGO BAĞLANTISI ---
# GÜVENLİK: Varsayılan şifre koddan kaldırıldı. .env kullanılmalı.
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("✅ MongoDB Bağlantısı Başarılı")
except Exception as e:
    print(f"❌ MongoDB Bağlantı Hatası: {e}")

db = client["corporate_planner"]
calendar_col = db["tasks"]
users_col = db["users"]

# --- SABİTLER ---
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
HOURS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]

# --- YARDIMCI FONKSİYONLAR ---
def login_required(func):
    """Kullanıcı girişi kontrolü yapan decorator"""
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

def get_week_metadata():
    """Haftalık tarihleri hesaplar"""
    today = datetime.now()
    start = today - timedelta(days=today.weekday())
    dates = []
    tr_days = {
        "Monday": "Pazartesi", "Tuesday": "Salı", "Wednesday": "Çarşamba",
        "Thursday": "Perşembe", "Friday": "Cuma", "Saturday": "Cumartesi", "Sunday": "Pazar"
    }
    for i in range(7):
        d = start + timedelta(days=i)
        dates.append({
            "day_en": DAYS[i],
            "day_tr": tr_days[DAYS[i]],
            "date": d.strftime("%d.%m"),
            "full_date": d.strftime("%Y-%m-%d"),
            "is_today": d.date() == today.date()
        })
    return dates

def serialize_doc(doc):
    """MongoDB dökümanını JSON formatına çevirir"""
    doc["_id"] = str(doc["_id"])
    return doc

def time_to_minutes(time_str):
    """Saat stringini (HH:MM) dakikaya çevirir"""
    try:
        h, m = map(int, time_str.split(':'))
        return h * 60 + m
    except:
        return 0

def get_next_day(current_day):
    """Bir sonraki günü döndürür"""
    if current_day not in DAYS:
        return current_day
    idx = DAYS.index(current_day)
    return DAYS[(idx + 1) % 7]

def calculate_new_time_and_day(current_day, time_str, minutes_to_add):
    """Dakika ekleyerek yeni gün ve saati hesaplar"""
    try:
        total = time_to_minutes(time_str) + minutes_to_add
        days_added = total // 1440
        remaining_minutes = total % 1440
        
        new_hour = remaining_minutes // 60
        new_min = remaining_minutes % 60
        # Dakikayı en yakın 00 veya 30'a yuvarlama (Opsiyonel ama UI için iyi)
        if new_min < 15: new_min = 0
        elif new_min < 45: new_min = 30
        else: 
            new_min = 0
            new_hour += 1
        
        # Saat 24 olduysa gün atlat
        if new_hour >= 24:
            new_hour = 0
            days_added += 1

        final_day = current_day
        for _ in range(days_added):
            final_day = get_next_day(final_day)
            
        return final_day, f"{new_hour:02d}:{new_min:02d}"
    except:
        return current_day, time_str

# --- ROUTE: AUTH ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username").lower().strip()
        password = request.form.get("password")

        user = users_col.find_one({"username": username})
        
        if user and bcrypt.check_password_hash(user["password"], password):
            session["user"] = username
            session.permanent = True
            return redirect("/")
        else:
            return render_template("login.html", error="Kullanıcı adı veya şifre hatalı.")
    
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username").lower().strip()
        password = request.form.get("password").strip()

        if len(username) < 3 or len(password) < 3:
            return render_template("register.html", error="Kullanıcı adı ve şifre en az 3 karakter olmalıdır.")

        if users_col.find_one({"username": username}):
            return render_template("register.html", error="Bu kullanıcı adı zaten mevcut.")

        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')

        users_col.insert_one({
            "username": username,
            "password": hashed_pw,
            "created_at": datetime.now()
        })

        session["user"] = username
        session.permanent = True
        return redirect("/")

    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# --- ROUTE: ANA SAYFA ---
@app.route("/")
@login_required
def index():
    return render_template("index.html", hours=HOURS, days=DAYS, week_meta=get_week_metadata(), user=session["user"])

# --- API İŞLEMLERİ ---

@app.route("/api/tasks", methods=["GET"])
@login_required
def get_tasks():
    try:
        current_user = session["user"]
        # İZOLASYON: Sadece oturum açan kullanıcının verilerini getir
        tasks = list(calendar_col.find({"owner": current_user}))
        formatted = {d: {h: [] for h in HOURS} for d in DAYS}

        for task in tasks:
            task = serialize_doc(task)
            d, h = task["day"], task["hour"]
            if d in formatted and h in formatted[d]:
                formatted[d][h].append(task)

        return jsonify({"success": True, "data": formatted})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks", methods=["POST"])
@login_required
def create_task():
    try:
        data = request.json
        current_user = session["user"]

        if data["day"] not in DAYS:
             return jsonify({"success": False, "error": "Geçersiz gün"}), 400

        new_task = {
            "owner": current_user,  # <--- SAHİPLİK EKLENDİ
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
        return jsonify({"success": True, "task": new_task})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>", methods=["PUT"])
@login_required
def update_task(task_id):
    try:
        data = request.json
        current_user = session["user"]
        allowed = ["title", "desc", "priority", "category", "completed", "day", "hour"]
        update_data = {k: v for k, v in data.items() if k in allowed}
        
        # Sadece kendi görevini güncelleyebilsin
        result = calendar_col.update_one(
            {"_id": ObjectId(task_id), "owner": current_user}, 
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
             return jsonify({"success": False, "error": "Görev bulunamadı veya yetkiniz yok"}), 404

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    try:
        current_user = session["user"]
        # Sadece kendi görevini silebilsin
        result = calendar_col.delete_one({"_id": ObjectId(task_id), "owner": current_user})
        
        if result.deleted_count == 0:
             return jsonify({"success": False, "error": "Görev bulunamadı veya yetkiniz yok"}), 404

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>/snooze", methods=["POST"])
@login_required
def snooze_task(task_id):
    try:
        data = request.json
        minutes = int(data.get("minutes", 30))
        current_user = session["user"]

        # Hedef görevi bul (Sadece kullanıcıya aitse)
        target = calendar_col.find_one({"_id": ObjectId(task_id), "owner": current_user})
        if not target:
            return jsonify({"success": False, "error": "Görev bulunamadı"}), 404

        day = target["day"]
        target_min = time_to_minutes(target["hour"])

        # O günkü, o saatten sonraki KULLANICININ diğer görevlerini bul
        same_day_tasks = list(calendar_col.find({
            "day": day, 
            "owner": current_user 
        }))

        to_update = [t for t in same_day_tasks if time_to_minutes(t["hour"]) >= target_min]

        count = 0
        for t in to_update:
            new_day, new_hour = calculate_new_time_and_day(t["day"], t["hour"], minutes)
            calendar_col.update_one(
                {"_id": t["_id"]}, 
                {"$set": {"day": new_day, "hour": new_hour}}
            )
            count += 1

        return jsonify({"success": True, "message": f"{count} görev ertelendi."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/export", methods=["GET"])
@login_required
def export_excel():
    try:
        current_user = session["user"]
        # Sadece kullanıcının görevlerini export et
        tasks = list(calendar_col.find({"owner": current_user}, {"_id": 0, "created_at": 0, "owner": 0}))
        
        df = pd.DataFrame(tasks)
        if df.empty:
            df = pd.DataFrame(columns=["day", "hour", "title", "desc", "priority", "category", "completed"])

        # Sütun isimlerini Türkçeleştirme (Opsiyonel)
        df.rename(columns={
            "day": "Gün", "hour": "Saat", "title": "Başlık", 
            "desc": "Açıklama", "priority": "Öncelik", 
            "category": "Kategori", "completed": "Tamamlandı"
        }, inplace=True)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Plan")

        output.seek(0)
        return send_file(output, download_name=f"Plan_{current_user}.xlsx", as_attachment=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/auto-generate", methods=["POST"])
@login_required
def auto_generate():
    try:
        current_user = session["user"]
        titles = ["Strateji Toplantısı", "Müşteri Sunumu", "Kod Review", "Bütçe Planlama", "Haftalık Sync", "E-posta Kontrolü", "Mola"]
        cats = ["İş", "Yönetim", "Satış", "Genel"]
        priorities = ["high", "medium", "normal"]

        new_tasks = []
        for _ in range(random.randint(3, 6)):
            new_tasks.append({
                "owner": current_user, # <--- SAHİPLİK
                "day": random.choice(DAYS),
                "hour": random.choice(HOURS[16:36]), # 08:00 - 18:00 arası (indeks bazlı)
                "title": random.choice(titles),
                "desc": "Otomatik oluşturulan görev",
                "priority": random.choice(priorities),
                "category": random.choice(cats),
                "completed": False,
                "created_at": datetime.now()
            })

        if new_tasks:
            calendar_col.insert_many(new_tasks)

        return jsonify({"success": True, "count": len(new_tasks)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    # Production'da debug=False olmalı
    app.run(host="0.0.0.0", port=5000, debug=True)