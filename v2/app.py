import os
import io
import random
import secrets
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from flask_bcrypt import Bcrypt
from pymongo import MongoClient
from bson.objectid import ObjectId
import pandas as pd
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message
import json

# .env dosyasını yükle
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))
bcrypt = Bcrypt(app)

# Session timeout (30 dakika)
app.permanent_session_lifetime = timedelta(minutes=30)

# Email Configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')
mail = Mail(app)

# File Upload Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'zip'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# MongoDB Connection
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
teams_col = db["teams"]
notifications_col = db["notifications"]
comments_col = db["comments"]
attachments_col = db["attachments"]

# Sabitler
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
HOURS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]

# Yardımcı Fonksiyonlar
def login_required(func):
    """Kullanıcı girişi kontrolü yapan decorator"""
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def send_notification_email(to_email, subject, body):
    """Email gönderme fonksiyonu"""
    try:
        if not app.config['MAIL_USERNAME']:
            return False
        msg = Message(subject, recipients=[to_email])
        msg.body = body
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Email gönderim hatası: {e}")
        return False

def create_notification(user, title, message, task_id=None, notification_type="info"):
    """Bildirim oluşturma"""
    notification = {
        "user": user,
        "title": title,
        "message": message,
        "task_id": task_id,
        "type": notification_type,
        "read": False,
        "created_at": datetime.now()
    }
    notifications_col.insert_one(notification)

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
    if "created_at" in doc and isinstance(doc["created_at"], datetime):
        doc["created_at"] = doc["created_at"].isoformat()
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
        
        if new_min < 15: new_min = 0
        elif new_min < 45: new_min = 30
        else: 
            new_min = 0
            new_hour += 1
        
        if new_hour >= 24:
            new_hour = 0
            days_added += 1

        final_day = current_day
        for _ in range(days_added):
            final_day = get_next_day(final_day)
            
        return final_day, f"{new_hour:02d}:{new_min:02d}"
    except:
        return current_day, time_str

# AUTH ROUTES
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username").lower().strip()
        password = request.form.get("password")

        user = users_col.find_one({"username": username})
        
        if user and bcrypt.check_password_hash(user["password"], password):
            session["user"] = username
            session["user_id"] = str(user["_id"])
            session.permanent = True
            
            # Email varsa session'a ekle
            if "email" in user:
                session["email"] = user["email"]
            
            return redirect("/")
        else:
            return render_template("login.html", error="Kullanıcı adı veya şifre hatalı.")
    
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username").lower().strip()
        password = request.form.get("password").strip()
        email = request.form.get("email", "").strip()

        if len(username) < 3 or len(password) < 3:
            return render_template("register.html", error="Kullanıcı adı ve şifre en az 3 karakter olmalıdır.")

        if users_col.find_one({"username": username}):
            return render_template("register.html", error="Bu kullanıcı adı zaten mevcut.")

        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')

        user_doc = {
            "username": username,
            "password": hashed_pw,
            "email": email,
            "email_notifications": True,
            "created_at": datetime.now()
        }
        
        result = users_col.insert_one(user_doc)

        session["user"] = username
        session["user_id"] = str(result.inserted_id)
        session["email"] = email
        session.permanent = True
        
        return redirect("/")

    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# MAIN ROUTES
@app.route("/")
@login_required
def index():
    return render_template("index.html", 
                         hours=HOURS, 
                         days=DAYS, 
                         week_meta=get_week_metadata(), 
                         user=session["user"])

# TASK API
@app.route("/api/tasks", methods=["GET"])
@login_required
def get_tasks():
    try:
        current_user = session["user"]
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
            "owner": current_user,
            "day": data["day"],
            "hour": data["hour"],
            "title": data["title"],
            "desc": data.get("desc", ""),
            "priority": data.get("priority", "normal"),
            "category": data.get("category", "Genel"),
            "completed": False,
            "tags": data.get("tags", []),
            "subtasks": data.get("subtasks", []),
            "dependencies": data.get("dependencies", []),
            "assigned_to": data.get("assigned_to", []),
            "team_id": data.get("team_id"),
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        
        res = calendar_col.insert_one(new_task)
        new_task["_id"] = str(res.inserted_id)
        
        # Bildirim oluştur
        create_notification(
            current_user,
            "Yeni Görev",
            f"'{data['title']}' görevi oluşturuldu",
            str(res.inserted_id),
            "success"
        )
        
        # Atanan kişilere email gönder
        if data.get("assigned_to"):
            for assigned_user in data["assigned_to"]:
                user_doc = users_col.find_one({"username": assigned_user})
                if user_doc and user_doc.get("email") and user_doc.get("email_notifications"):
                    send_notification_email(
                        user_doc["email"],
                        "Yeni Görev Atandı",
                        f"{current_user} tarafından size '{data['title']}' görevi atandı."
                    )
        
        return jsonify({"success": True, "task": serialize_doc(new_task)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>", methods=["PUT"])
@login_required
def update_task(task_id):
    try:
        data = request.json
        current_user = session["user"]
        allowed = ["title", "desc", "priority", "category", "completed", "day", "hour", "tags", "subtasks", "dependencies", "assigned_to"]
        update_data = {k: v for k, v in data.items() if k in allowed}
        update_data["updated_at"] = datetime.now()
        
        result = calendar_col.update_one(
            {"_id": ObjectId(task_id), "owner": current_user}, 
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
             return jsonify({"success": False, "error": "Görev bulunamadı veya yetkiniz yok"}), 404

        # Görev tamamlandıysa bildirim gönder
        if data.get("completed"):
            task = calendar_col.find_one({"_id": ObjectId(task_id)})
            create_notification(
                current_user,
                "Görev Tamamlandı",
                f"'{task['title']}' görevi tamamlandı",
                task_id,
                "success"
            )

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    try:
        current_user = session["user"]
        task = calendar_col.find_one({"_id": ObjectId(task_id), "owner": current_user})
        
        if not task:
             return jsonify({"success": False, "error": "Görev bulunamadı veya yetkiniz yok"}), 404
        
        # Görev bağlı dosyaları sil
        attachments = attachments_col.find({"task_id": task_id})
        for att in attachments:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], att['filename'])
            if os.path.exists(file_path):
                os.remove(file_path)
        
        attachments_col.delete_many({"task_id": task_id})
        comments_col.delete_many({"task_id": task_id})
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
        current_user = session["user"]

        target = calendar_col.find_one({"_id": ObjectId(task_id), "owner": current_user})
        if not target:
            return jsonify({"success": False, "error": "Görev bulunamadı"}), 404

        day = target["day"]
        target_min = time_to_minutes(target["hour"])

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
                {"$set": {"day": new_day, "hour": new_hour, "updated_at": datetime.now()}}
            )
            count += 1

        return jsonify({"success": True, "message": f"{count} görev ertelendi."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# SUBTASK API
@app.route("/api/tasks/<task_id>/subtasks", methods=["POST"])
@login_required
def add_subtask(task_id):
    try:
        data = request.json
        current_user = session["user"]
        
        subtask = {
            "id": secrets.token_hex(8),
            "title": data["title"],
            "completed": False,
            "created_at": datetime.now().isoformat()
        }
        
        result = calendar_col.update_one(
            {"_id": ObjectId(task_id), "owner": current_user},
            {"$push": {"subtasks": subtask}, "$set": {"updated_at": datetime.now()}}
        )
        
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Görev bulunamadı"}), 404
            
        return jsonify({"success": True, "subtask": subtask})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>/subtasks/<subtask_id>", methods=["PUT"])
@login_required
def update_subtask(task_id, subtask_id):
    try:
        data = request.json
        current_user = session["user"]
        
        result = calendar_col.update_one(
            {
                "_id": ObjectId(task_id), 
                "owner": current_user,
                "subtasks.id": subtask_id
            },
            {
                "$set": {
                    "subtasks.$.completed": data.get("completed", False),
                    "updated_at": datetime.now()
                }
            }
        )
        
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Alt görev bulunamadı"}), 404
            
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>/subtasks/<subtask_id>", methods=["DELETE"])
@login_required
def delete_subtask(task_id, subtask_id):
    try:
        current_user = session["user"]
        
        result = calendar_col.update_one(
            {"_id": ObjectId(task_id), "owner": current_user},
            {"$pull": {"subtasks": {"id": subtask_id}}, "$set": {"updated_at": datetime.now()}}
        )
        
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Alt görev bulunamadı"}), 404
            
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ATTACHMENT API
@app.route("/api/tasks/<task_id>/attachments", methods=["POST"])
@login_required
def upload_attachment(task_id):
    try:
        current_user = session["user"]
        
        # Görev kontrolü
        task = calendar_col.find_one({"_id": ObjectId(task_id), "owner": current_user})
        if not task:
            return jsonify({"success": False, "error": "Görev bulunamadı"}), 404
        
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "Dosya bulunamadı"}), 400
            
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"success": False, "error": "Dosya seçilmedi"}), 400
            
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"{secrets.token_hex(8)}_{filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(filepath)
            
            attachment = {
                "task_id": task_id,
                "filename": unique_filename,
                "original_name": filename,
                "uploaded_by": current_user,
                "file_size": os.path.getsize(filepath),
                "uploaded_at": datetime.now()
            }
            
            result = attachments_col.insert_one(attachment)
            attachment["_id"] = str(result.inserted_id)
            
            return jsonify({"success": True, "attachment": serialize_doc(attachment)})
        
        return jsonify({"success": False, "error": "Geçersiz dosya tipi"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>/attachments", methods=["GET"])
@login_required
def get_attachments(task_id):
    try:
        attachments = list(attachments_col.find({"task_id": task_id}))
        return jsonify({
            "success": True, 
            "attachments": [serialize_doc(a) for a in attachments]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/attachments/<attachment_id>/download", methods=["GET"])
@login_required
def download_attachment(attachment_id):
    try:
        attachment = attachments_col.find_one({"_id": ObjectId(attachment_id)})
        if not attachment:
            return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404
            
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], attachment['filename'])
        
        if not os.path.exists(filepath):
            return jsonify({"success": False, "error": "Dosya sistemde bulunamadı"}), 404
            
        return send_file(
            filepath, 
            as_attachment=True, 
            download_name=attachment['original_name']
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/attachments/<attachment_id>", methods=["DELETE"])
@login_required
def delete_attachment(attachment_id):
    try:
        current_user = session["user"]
        attachment = attachments_col.find_one({"_id": ObjectId(attachment_id)})
        
        if not attachment:
            return jsonify({"success": False, "error": "Dosya bulunamadı"}), 404
        
        # Sadece yükleyen kişi silebilir
        if attachment["uploaded_by"] != current_user:
            return jsonify({"success": False, "error": "Yetkiniz yok"}), 403
        
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], attachment['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        
        attachments_col.delete_one({"_id": ObjectId(attachment_id)})
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# COMMENT API
@app.route("/api/tasks/<task_id>/comments", methods=["GET"])
@login_required
def get_comments(task_id):
    try:
        comments = list(comments_col.find({"task_id": task_id}).sort("created_at", -1))
        return jsonify({
            "success": True,
            "comments": [serialize_doc(c) for c in comments]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tasks/<task_id>/comments", methods=["POST"])
@login_required
def add_comment(task_id):
    try:
        data = request.json
        current_user = session["user"]
        
        comment = {
            "task_id": task_id,
            "user": current_user,
            "text": data["text"],
            "created_at": datetime.now()
        }
        
        result = comments_col.insert_one(comment)
        comment["_id"] = str(result.inserted_id)
        
        # Görev sahibine bildirim gönder
        task = calendar_col.find_one({"_id": ObjectId(task_id)})
        if task and task["owner"] != current_user:
            create_notification(
                task["owner"],
                "Yeni Yorum",
                f"{current_user} görevinize yorum yaptı: {data['text'][:50]}...",
                task_id,
                "info"
            )
        
        return jsonify({"success": True, "comment": serialize_doc(comment)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/comments/<comment_id>", methods=["DELETE"])
@login_required
def delete_comment(comment_id):
    try:
        current_user = session["user"]
        comment = comments_col.find_one({"_id": ObjectId(comment_id)})
        
        if not comment:
            return jsonify({"success": False, "error": "Yorum bulunamadı"}), 404
        
        if comment["user"] != current_user:
            return jsonify({"success": False, "error": "Yetkiniz yok"}), 403
        
        comments_col.delete_one({"_id": ObjectId(comment_id)})
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# NOTIFICATION API
@app.route("/api/notifications", methods=["GET"])
@login_required
def get_notifications():
    try:
        current_user = session["user"]
        notifications = list(notifications_col.find({"user": current_user}).sort("created_at", -1).limit(50))
        return jsonify({
            "success": True,
            "notifications": [serialize_doc(n) for n in notifications]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/notifications/<notification_id>/read", methods=["PUT"])
@login_required
def mark_notification_read(notification_id):
    try:
        current_user = session["user"]
        result = notifications_col.update_one(
            {"_id": ObjectId(notification_id), "user": current_user},
            {"$set": {"read": True}}
        )
        
        if result.matched_count == 0:
            return jsonify({"success": False, "error": "Bildirim bulunamadı"}), 404
            
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/notifications/mark-all-read", methods=["PUT"])
@login_required
def mark_all_notifications_read():
    try:
        current_user = session["user"]
        notifications_col.update_many(
            {"user": current_user, "read": False},
            {"$set": {"read": True}}
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# TEAM API
@app.route("/api/teams", methods=["GET"])
@login_required
def get_teams():
    try:
        current_user = session["user"]
        teams = list(teams_col.find({
            "$or": [
                {"owner": current_user},
                {"members": current_user}
            ]
        }))
        return jsonify({
            "success": True,
            "teams": [serialize_doc(t) for t in teams]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/teams", methods=["POST"])
@login_required
def create_team():
    try:
        data = request.json
        current_user = session["user"]
        
        team = {
            "name": data["name"],
            "description": data.get("description", ""),
            "owner": current_user,
            "members": data.get("members", []),
            "created_at": datetime.now()
        }
        
        result = teams_col.insert_one(team)
        team["_id"] = str(result.inserted_id)
        
        return jsonify({"success": True, "team": serialize_doc(team)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/teams/<team_id>/members", methods=["POST"])
@login_required
def add_team_member(team_id):
    try:
        data = request.json
        current_user = session["user"]
        
        team = teams_col.find_one({"_id": ObjectId(team_id), "owner": current_user})
        if not team:
            return jsonify({"success": False, "error": "Takım bulunamadı veya yetkiniz yok"}), 404
        
        username = data["username"]
        user = users_col.find_one({"username": username})
        
        if not user:
            return jsonify({"success": False, "error": "Kullanıcı bulunamadı"}), 404
        
        teams_col.update_one(
            {"_id": ObjectId(team_id)},
            {"$addToSet": {"members": username}}
        )
        
        create_notification(
            username,
            "Takıma Eklendi",
            f"{current_user} tarafından '{team['name']}' takımına eklendiniz",
            None,
            "info"
        )
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ADVANCED REPORTING
@app.route("/api/reports/productivity", methods=["GET"])
@login_required
def get_productivity_report():
    try:
        current_user = session["user"]
        
        # Tamamlanma oranları
        total_tasks = calendar_col.count_documents({"owner": current_user})
        completed_tasks = calendar_col.count_documents({"owner": current_user, "completed": True})
        
        # Kategori bazlı istatistikler
        pipeline = [
            {"$match": {"owner": current_user}},
            {"$group": {
                "_id": "$category",
                "total": {"$sum": 1},
                "completed": {"$sum": {"$cond": ["$completed", 1, 0]}}
            }}
        ]
        category_stats = list(calendar_col.aggregate(pipeline))
        
        # Öncelik bazlı istatistikler
        pipeline[1]["$group"]["_id"] = "$priority"
        priority_stats = list(calendar_col.aggregate(pipeline))
        
        # Haftalık trend
        week_start = datetime.now() - timedelta(days=7)
        weekly_completed = calendar_col.count_documents({
            "owner": current_user,
            "completed": True,
            "updated_at": {"$gte": week_start}
        })
        
        return jsonify({
            "success": True,
            "report": {
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "completion_rate": (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0,
                "category_stats": category_stats,
                "priority_stats": priority_stats,
                "weekly_completed": weekly_completed
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# USER SETTINGS
@app.route("/api/settings", methods=["GET"])
@login_required
def get_settings():
    try:
        current_user = session["user"]
        user = users_col.find_one({"username": current_user})
        if user:
            return jsonify({
                "success": True,
                "settings": {
                    "email": user.get("email", ""),
                    "email_notifications": user.get("email_notifications", True)
                }
            })
        return jsonify({"success": False, "error": "Kullanıcı bulunamadı"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/settings", methods=["PUT"])
@login_required
def update_settings():
    try:
        data = request.json
        current_user = session["user"]
        
        update_data = {}
        if "email" in data:
            update_data["email"] = data["email"]
        if "email_notifications" in data:
            update_data["email_notifications"] = data["email_notifications"]
        
        users_col.update_one(
            {"username": current_user},
            {"$set": update_data}
        )
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# EXPORT
@app.route("/api/export", methods=["GET"])
@login_required
def export_excel():
    try:
        current_user = session["user"]
        tasks = list(calendar_col.find({"owner": current_user}, {"_id": 0, "created_at": 0, "owner": 0}))
        
        df = pd.DataFrame(tasks)
        if df.empty:
            df = pd.DataFrame(columns=["day", "hour", "title", "desc", "priority", "category", "completed"])

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
                "owner": current_user,
                "day": random.choice(DAYS),
                "hour": random.choice(HOURS[16:36]),
                "title": random.choice(titles),
                "desc": "Otomatik oluşturulan görev",
                "priority": random.choice(priorities),
                "category": random.choice(cats),
                "completed": False,
                "tags": [],
                "subtasks": [],
                "dependencies": [],
                "assigned_to": [],
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            })

        if new_tasks:
            calendar_col.insert_many(new_tasks)

        return jsonify({"success": True, "count": len(new_tasks)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)