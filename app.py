import os
import sqlite3
import random
import smtplib
from functools import wraps
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

MAINTENANCE_MODE = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")

DATABASE = "database.db"
UPLOAD_FOLDER = os.path.join("static", "uploads")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

MAIL_ENABLED = False
MAIL_HOST = "smtp.resend.com"
MAIL_PORT = 587
MAIL_USERNAME = "resend"
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
MAIL_FROM = "noreply@web-pubble.com"

MAX_POST_LENGTH = 1000
MAX_COMMENT_LENGTH = 500
MAX_MESSAGE_LENGTH = 2000
MAX_BIO_LENGTH = 300
MAX_POSTS_PER_24H = 3


def is_admin_user(conn, user_id):
    if not user_id:
        return False

    user = conn.execute(
        "SELECT is_admin FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    return bool(user and user["is_admin"] == 1)


def update_last_seen(user_id):
    if not user_id:
        return

    with get_db_connection() as conn:
        conn.execute("""
            UPDATE users
            SET last_seen_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (user_id,))
        conn.commit()


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Сначала войди в аккаунт")
            return redirect(url_for("login"))

        with get_db_connection() as conn:
            if not is_admin_user(conn, session["user_id"]):
                flash("Доступ запрещён")
                return redirect(url_for("index"))

        return view_func(*args, **kwargs)
    return wrapper


def check_ban_or_logout():
    if not session.get("user_id"):
        return None

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (session["user_id"],)
        ).fetchone()

        if user and user["is_banned"] == 1:
            session.clear()
            flash("Ваш аккаунт заблокирован")
            return redirect(url_for("login"))

    return None

def get_db_connection():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def delete_uploaded_file(relative_path):
    if not relative_path:
        return

    safe_path = relative_path.strip().replace("\\", "/")

    if not safe_path.startswith("uploads/"):
        return

    full_path = os.path.join("static", safe_path)

    if os.path.exists(full_path) and os.path.isfile(full_path):
        try:
            os.remove(full_path)
        except OSError:
            pass


def save_uploaded_file(file):
    if not file or file.filename == "":
        return ""

    filename = secure_filename(file.filename)
    base, ext = os.path.splitext(filename)
    unique_name = f"{base}_{os.urandom(8).hex()}{ext}"
    full_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file.save(full_path)

    return f"uploads/{unique_name}"


def get_avatar_url(user):
    avatar = user["avatar_url"] if user and "avatar_url" in user.keys() else ""
    return avatar if avatar else "default-avatar.jpg"


def generate_verification_code():
    return f"{random.randint(100000, 999999)}"


def generate_reset_expiry():
    return (datetime.utcnow() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")


def is_reset_code_expired(expires_at):
    if not expires_at:
        return True

    try:
        expiry = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        return datetime.utcnow() > expiry
    except ValueError:
        return True


def send_email_message(to_email, subject, body):
    if not MAIL_ENABLED:\
        return False
    
    try:
        print("\n================ EMAIL DEBUG =======================")
        print(f"Email: {to_email}")
        print(f"Subject: {subject}")
        print(body)
        print("===================================================\n")

        msg = MIMEMultipart()
        msg["From"] = MAIL_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        server = smtplib.SMTP(MAIL_HOST, MAIL_PORT)
        server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.sendmail(MAIL_FROM, to_email, msg.as_string())
        server.quit()
    
        return True
    
    except Exception as e:
        print("EMAIL ERROR:", e)
        return False


def send_verification_email(to_email, code):
    subject = "Подтверждение email для Pubble"
    body = f"""
Привет!

Твой код подтверждения для Pubble:

{code}

Если это был не ты, просто проигнорируй это письмо.
"""
    return send_email_message(to_email, subject, body)


def send_reset_password_email(to_email, code):
    subject = "Восстановление пароля для Pubble"
    body = f"""
Привет!

Твой код для восстановления пароля в Pubble:

{code}

Код действует 15 минут.

Если это был не ты, просто проигнорируй это письмо.
"""
    return send_email_message(to_email, subject, body)


def are_friends(conn, user_a_id, user_b_id):
    if not user_a_id or not user_b_id or user_a_id == user_b_id:
        return False

    user1_id = min(user_a_id, user_b_id)
    user2_id = max(user_a_id, user_b_id)

    friendship = conn.execute("""
        SELECT id
        FROM friendships
        WHERE user1_id = ? AND user2_id = ?
    """, (user1_id, user2_id)).fetchone()

    return friendship is not None


def get_friend_request_status(conn, current_user_id, target_user_id):
    if not current_user_id or not target_user_id or current_user_id == target_user_id:
        return None

    if are_friends(conn, current_user_id, target_user_id):
        return "friends"

    sent = conn.execute("""
        SELECT id
        FROM friend_requests
        WHERE sender_id = ? AND receiver_id = ? AND status = 'pending'
    """, (current_user_id, target_user_id)).fetchone()

    if sent:
        return "sent"

    received = conn.execute("""
        SELECT id
        FROM friend_requests
        WHERE sender_id = ? AND receiver_id = ? AND status = 'pending'
    """, (target_user_id, current_user_id)).fetchone()

    if received:
        return "received"

    return None


def can_send_message(conn, sender_id, recipient):
    if not sender_id or not recipient:
        return False

    if sender_id == recipient["id"]:
        return False

    privacy = recipient["message_privacy"] if "message_privacy" in recipient.keys() else "everyone"

    if privacy == "everyone":
        return True

    if privacy == "nobody":
        return False

    if privacy == "friends":
        return are_friends(conn, sender_id, recipient["id"])

    return False


def can_view_posts(conn, viewer_id, profile_user):
    if not profile_user:
        return False

    if viewer_id and viewer_id == profile_user["id"]:
        return True

    visibility = profile_user["posts_visibility"] if "posts_visibility" in profile_user.keys() else "everyone"

    if visibility == "everyone":
        return True

    if visibility == "friends":
        return viewer_id and are_friends(conn, viewer_id, profile_user["id"])

    if visibility == "nobody":
        return False

    return True


def can_view_profile_photos(conn, viewer_id, profile_user):
    if not profile_user:
        return False

    if viewer_id and viewer_id == profile_user["id"]:
        return True

    visibility = profile_user["photos_visibility"] if "photos_visibility" in profile_user.keys() else "everyone"

    if visibility == "everyone":
        return True

    if visibility == "friends":
        return viewer_id and are_friends(conn, viewer_id, profile_user["id"])

    if visibility == "nobody":
        return False

    return True

def is_strong_password(password):
    return len(password) >= 8


def get_profile_photo_with_meta(conn, photo_id, current_user_id=None):
    params = [photo_id]
    liked_sql = "0 AS is_liked"

    if current_user_id:
        liked_sql = """
            MAX(CASE WHEN profile_photo_likes.user_id = ? THEN 1 ELSE 0 END) AS is_liked
        """
        params = [current_user_id, photo_id]

    query = f"""
        SELECT
            profile_photos.id,
            profile_photos.user_id,
            profile_photos.image_url,
            profile_photos.created_at,
            users.username,
            users.avatar_url,
            COUNT(DISTINCT profile_photo_likes.id) AS likes_count,
            COUNT(DISTINCT profile_photo_comments.id) AS comments_count,
            {liked_sql}
        FROM profile_photos
        JOIN users ON users.id = profile_photos.user_id
        LEFT JOIN profile_photo_likes ON profile_photo_likes.photo_id = profile_photos.id
        LEFT JOIN profile_photo_comments ON profile_photo_comments.photo_id = profile_photos.id
        WHERE profile_photos.id = ?
        GROUP BY profile_photos.id
    """

    return conn.execute(query, params).fetchone()


def get_notifications(conn, user_id):
    if not user_id:
        return 0, 0

    friends_notifications = conn.execute("""
        SELECT COUNT(*)
        FROM friend_requests
        WHERE receiver_id = ? AND status = 'pending'
    """, (user_id,)).fetchone()[0]

    messages_notifications = conn.execute("""
        SELECT COUNT(*)
        FROM messages
        WHERE is_read = 0
          AND sender_id != ?
          AND conversation_id IN (
              SELECT id FROM conversations
              WHERE user1_id = ? OR user2_id = ?
          )
    """, (user_id, user_id, user_id)).fetchone()[0]

    return friends_notifications, messages_notifications


def get_or_create_conversation(user_a_id, user_b_id):
    user1_id = min(user_a_id, user_b_id)
    user2_id = max(user_a_id, user_b_id)

    with get_db_connection() as conn:
        conversation = conn.execute(
            "SELECT * FROM conversations WHERE user1_id = ? AND user2_id = ?",
            (user1_id, user2_id)
        ).fetchone()

        if not conversation:
            conn.execute(
                "INSERT INTO conversations (user1_id, user2_id) VALUES (?, ?)",
                (user1_id, user2_id)
            )
            conn.commit()

            conversation = conn.execute(
                "SELECT * FROM conversations WHERE user1_id = ? AND user2_id = ?",
                (user1_id, user2_id)
            ).fetchone()

    return conversation


def can_create_post_now(conn, user_id):
    count = conn.execute("""
        SELECT COUNT(*)
        FROM post_publish_log
        WHERE user_id = ?
          AND datetime(created_at) >= datetime('now', '-1 day')
    """, (user_id,)).fetchone()[0]

    return count < MAX_POSTS_PER_24H

def create_notification(conn, user_id, actor_id, notif_type, text, link=""):
    if not user_id:
        return

    if actor_id and user_id == actor_id:
        return

    conn.execute("""
        INSERT INTO notifications (user_id, actor_id, type, text, link)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, actor_id, notif_type, text, link))


def is_admin_user(conn, user_id):
    if not user_id:
        return False

    user = conn.execute(
        "SELECT is_admin FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    return bool(user and "is_admin" in user.keys() and user["is_admin"] == 1)


def is_moderator_user(conn, user_id):
    if not user_id:
        return False

    user = conn.execute(
        "SELECT is_moderator FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    return bool(user and "is_moderator" in user.keys() and user["is_moderator"] == 1)


def can_manage_feed(conn, user_id):
    return is_admin_user(conn, user_id) or is_moderator_user(conn, user_id)

def get_last_seen_status(last_seen_at):
    if not last_seen_at:
        return "давно не заходил"

    try:
        last_seen = datetime.strptime(last_seen_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "давно не заходил"

    now = datetime.utcnow()
    diff = now - last_seen

    if diff <= timedelta(minutes=5):
        return "в сети"
    if diff <= timedelta(hours=1):
        return "был недавно"
    if diff <= timedelta(hours=24):
        return "был сегодня"
    if diff <= timedelta(hours=48):
        return "был вчера"

    return "давно не заходил"

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                bio TEXT DEFAULT '',
                avatar_url TEXT DEFAULT '',
                message_privacy TEXT DEFAULT 'everyone',
                posts_visibility TEXT DEFAULT 'everyone',
                photos_visibility TEXT DEFAULT 'everyone',
                email_verified INTEGER DEFAULT 0,
                email_verification_code TEXT DEFAULT '',
                password_reset_code TEXT DEFAULT '',
                password_reset_expires TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_admin INTEGER DEFAULT 0,
                is_moderator INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                ban_reason TEXT DEFAULT '',
                banned_at TIMESTAMP,
                last_seen_at TIMESTAMP
            )
        """)

        cursor.execute("PRAGMA table_info(users)")
        user_columns = [column["name"] for column in cursor.fetchall()]

        if "bio" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")

        if "avatar_url" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT DEFAULT ''")

        if "message_privacy" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN message_privacy TEXT DEFAULT 'everyone'")

        if "posts_visibility" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN posts_visibility TEXT DEFAULT 'everyone'")

        if "photos_visibility" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN photos_visibility TEXT DEFAULT 'everyone'")

        if "email_verified" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0")

        if "email_verification_code" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN email_verification_code TEXT DEFAULT ''")

        if "password_reset_code" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN password_reset_code TEXT DEFAULT ''")

        if "password_reset_expires" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN password_reset_expires TEXT DEFAULT ''")

        if "is_admin" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")

        if "is_banned" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")

        if "ban_reason" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT ''")

        if "banned_at" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN banned_at TIMESTAMP")

        if "last_seen_at" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN last_seen_at TIMESTAMP")

        if "is_admin" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")

        if "is_moderator" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_moderator INTEGER DEFAULT 0")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id INTEGER NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                image_url TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (author_id) REFERENCES users (id)
            )
        """)

        cursor.execute("PRAGMA table_info(posts)")
        post_columns = [column["name"] for column in cursor.fetchall()]

        if "image_url" not in post_columns:
            cursor.execute("ALTER TABLE posts ADD COLUMN image_url TEXT DEFAULT ''")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS likes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                post_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, post_id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (post_id) REFERENCES posts (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (post_id) REFERENCES posts (id),
                FOREIGN KEY (author_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user1_id, user2_id),
                FOREIGN KEY (user1_id) REFERENCES users (id),
                FOREIGN KEY (user2_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations (id),
                FOREIGN KEY (sender_id) REFERENCES users (id)
            )
        """)

        cursor.execute("PRAGMA table_info(messages)")
        message_columns = [column["name"] for column in cursor.fetchall()]

        if "is_read" not in message_columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN is_read INTEGER DEFAULT 0")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS friend_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(sender_id, receiver_id),
                FOREIGN KEY (sender_id) REFERENCES users (id),
                FOREIGN KEY (receiver_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS friendships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user1_id, user2_id),
                FOREIGN KEY (user1_id) REFERENCES users (id),
                FOREIGN KEY (user2_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS profile_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                image_url TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS post_publish_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS profile_photo_likes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                photo_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, photo_id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (photo_id) REFERENCES profile_photos (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS profile_photo_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (photo_id) REFERENCES profile_photos (id),
                FOREIGN KEY (author_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                actor_id INTEGER,
                type TEXT NOT NULL,
                text TEXT NOT NULL,
                link TEXT DEFAULT '',
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (actor_id) REFERENCES users (id)
            )
        """)

        conn.commit()

@app.before_request
def chek_maintenance():
    if MAINTENANCE_MODE:
        return "Сайт на техническом обслуживании.", 503

@app.before_request
def before_request_handler():
    if session.get("user_id"):
        update_last_seen(session["user_id"])


@app.route("/")
def index():
    with get_db_connection() as conn:
        if session.get("user_id"):
            posts = conn.execute("""
                SELECT
                    posts.id,
                    posts.author_id,
                    posts.content,
                    posts.image_url,
                    posts.created_at,
                    users.username,
                    users.avatar_url,
                    COUNT(DISTINCT likes.id) AS likes_count,
                    COUNT(DISTINCT comments.id) AS comments_count,
                    MAX(CASE WHEN likes.user_id = ? THEN 1 ELSE 0 END) AS is_liked
                FROM posts
                JOIN users ON posts.author_id = users.id
                LEFT JOIN likes ON posts.id = likes.post_id
                LEFT JOIN comments ON posts.id = comments.post_id
                GROUP BY posts.id
                ORDER BY posts.created_at DESC
            """, (session["user_id"],)).fetchall()
        else:
            posts = conn.execute("""
                SELECT
                    posts.id,
                    posts.author_id,
                    posts.content,
                    posts.image_url,
                    posts.created_at,
                    users.username,
                    users.avatar_url,
                    COUNT(DISTINCT likes.id) AS likes_count,
                    COUNT(DISTINCT comments.id) AS comments_count,
                    0 AS is_liked
                FROM posts
                JOIN users ON posts.author_id = users.id
                LEFT JOIN likes ON posts.id = likes.post_id
                LEFT JOIN comments ON posts.id = comments.post_id
                GROUP BY posts.id
                ORDER BY posts.created_at DESC
            """).fetchall()

        friends_notifications, messages_notifications = get_notifications(conn, session.get("user_id"))

    return render_template(
        "index.html",
        posts=posts,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/search")
def search_users():
    query = request.args.get("q", "").strip()
    users = []

    with get_db_connection() as conn:
        if query:
            users = conn.execute("""
                SELECT
                    users.id,
                    users.username,
                    users.bio,
                    users.avatar_url,
                    users.message_privacy,
                    users.posts_visibility,
                    users.photos_visibility,
                    users.created_at,
                    COUNT(DISTINCT posts.id) AS posts_count
                FROM users
                LEFT JOIN posts ON users.id = posts.author_id
                WHERE users.username LIKE ?
                GROUP BY users.id
                ORDER BY users.username ASC
            """, (f"%{query}%",)).fetchall()

        friends_notifications, messages_notifications = get_notifications(conn, session.get("user_id"))

    return render_template(
        "search.html",
        users=users,
        query=query,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/privacy", methods=["GET", "POST"])
def privacy():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (session["user_id"],)
        ).fetchone()

        if request.method == "POST":
            message_privacy = request.form.get("message_privacy", "everyone").strip()
            posts_visibility = request.form.get("posts_visibility", "everyone").strip()
            photos_visibility = request.form.get("photos_visibility", "everyone").strip()

            if message_privacy not in ["everyone", "friends", "nobody"]:
                message_privacy = "everyone"

            if posts_visibility not in ["everyone", "friends", "nobody"]:
                posts_visibility = "everyone"

            if photos_visibility not in ["everyone", "friends", "nobody"]:
                photos_visibility = "everyone"

            conn.execute("""
                UPDATE users
                SET message_privacy = ?, posts_visibility = ?, photos_visibility = ?
                WHERE id = ?
            """, (
                message_privacy,
                posts_visibility,
                photos_visibility,
                session["user_id"]
            ))
            conn.commit()

            flash("Настройки приватности сохранены")
            return redirect(url_for("privacy"))

        friends_notifications, messages_notifications = get_notifications(conn, session["user_id"])

    return render_template(
        "privacy.html",
        user=user,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/friends")
def friends_page():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    user_id = session["user_id"]

    with get_db_connection() as conn:
        friends = conn.execute("""
            SELECT users.id, users.username, users.avatar_url, users.bio
            FROM friendships
            JOIN users
              ON users.id = CASE
                    WHEN friendships.user1_id = ? THEN friendships.user2_id
                    ELSE friendships.user1_id
                 END
            WHERE friendships.user1_id = ? OR friendships.user2_id = ?
            ORDER BY users.username ASC
        """, (user_id, user_id, user_id)).fetchall()

        incoming_requests = conn.execute("""
            SELECT friend_requests.id, users.id AS user_id, users.username, users.avatar_url, users.bio
            FROM friend_requests
            JOIN users ON users.id = friend_requests.sender_id
            WHERE friend_requests.receiver_id = ? AND friend_requests.status = 'pending'
            ORDER BY friend_requests.created_at DESC
        """, (user_id,)).fetchall()

        outgoing_requests = conn.execute("""
            SELECT friend_requests.id, users.id AS user_id, users.username, users.avatar_url, users.bio
            FROM friend_requests
            JOIN users ON users.id = friend_requests.receiver_id
            WHERE friend_requests.sender_id = ? AND friend_requests.status = 'pending'
            ORDER BY friend_requests.created_at DESC
        """, (user_id,)).fetchall()

        friends_notifications, messages_notifications = get_notifications(conn, user_id)

    return render_template(
        "friends.html",
        friends=friends,
        incoming_requests=incoming_requests,
        outgoing_requests=outgoing_requests,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/friends/request/<username>", methods=["POST"])
def send_friend_request(username):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        target = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if not target:
            flash("Пользователь не найден")
            return redirect(url_for("index"))

        if target["id"] == session["user_id"]:
            flash("Нельзя добавить себя в друзья")
            return redirect(url_for("profile", username=username))

        if are_friends(conn, session["user_id"], target["id"]):
            flash("Вы уже друзья")
            return redirect(url_for("profile", username=username))

        existing = conn.execute("""
            SELECT *
            FROM friend_requests
            WHERE sender_id = ? AND receiver_id = ? AND status = 'pending'
        """, (session["user_id"], target["id"])).fetchone()

        if existing:
            flash("Заявка уже отправлена")
            return redirect(url_for("profile", username=username))

        reverse_existing = conn.execute("""
            SELECT *
            FROM friend_requests
            WHERE sender_id = ? AND receiver_id = ? AND status = 'pending'
        """, (target["id"], session["user_id"])).fetchone()

        if reverse_existing:
            flash("Этот пользователь уже отправил тебе заявку. Открой друзей.")
            return redirect(url_for("profile", username=username))

        conn.execute("""
            INSERT INTO friend_requests (sender_id, receiver_id, status)
            VALUES (?, ?, 'pending')
        """, (session["user_id"], target["id"]))

        create_notification(
            conn,
            target["id"],
            session["user_id"],
            "friend_request",
            f"{session['username']} отправил тебе заявку в друзья",
            "/friends"
        )

        conn.commit()

    flash("Заявка в друзья отправлена")
    return redirect(url_for("profile", username=username))


@app.route("/friends/accept/<int:request_id>", methods=["POST"])
def accept_friend_request(request_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        friend_request = conn.execute("""
            SELECT *
            FROM friend_requests
            WHERE id = ? AND receiver_id = ? AND status = 'pending'
        """, (request_id, session["user_id"])).fetchone()

        if not friend_request:
            flash("Заявка не найдена")
            return redirect(url_for("friends_page"))

        sender_id = friend_request["sender_id"]
        receiver_id = friend_request["receiver_id"]

        user1_id = min(sender_id, receiver_id)
        user2_id = max(sender_id, receiver_id)

        if not are_friends(conn, user1_id, user2_id):
            conn.execute("""
                INSERT INTO friendships (user1_id, user2_id)
                VALUES (?, ?)
            """, (user1_id, user2_id))

        conn.execute("""
            UPDATE friend_requests
            SET status = 'accepted'
            WHERE id = ?
        """, (request_id,))
        conn.commit()

    flash("Заявка принята")
    return redirect(url_for("friends_page"))


@app.route("/friends/decline/<int:request_id>", methods=["POST"])
def decline_friend_request(request_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        friend_request = conn.execute("""
            SELECT *
            FROM friend_requests
            WHERE id = ? AND receiver_id = ? AND status = 'pending'
        """, (request_id, session["user_id"])).fetchone()

        if not friend_request:
            flash("Заявка не найдена")
            return redirect(url_for("friends_page"))

        conn.execute("""
            UPDATE friend_requests
            SET status = 'declined'
            WHERE id = ?
        """, (request_id,))
        conn.commit()

    flash("Заявка отклонена")
    return redirect(url_for("friends_page"))


@app.route("/friends/cancel/<int:request_id>", methods=["POST"])
def cancel_friend_request(request_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        friend_request = conn.execute("""
            SELECT *
            FROM friend_requests
            WHERE id = ? AND sender_id = ? AND status = 'pending'
        """, (request_id, session["user_id"])).fetchone()

        if not friend_request:
            flash("Заявка не найдена")
            return redirect(url_for("friends_page"))

        conn.execute("DELETE FROM friend_requests WHERE id = ?", (request_id,))
        conn.commit()

    flash("Заявка отменена")
    return redirect(url_for("friends_page"))


@app.route("/friends/remove/<username>", methods=["POST"])
def remove_friend(username):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        target = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if not target:
            flash("Пользователь не найден")
            return redirect(url_for("friends_page"))

        user1_id = min(session["user_id"], target["id"])
        user2_id = max(session["user_id"], target["id"])

        if not are_friends(conn, user1_id, user2_id):
            flash("Вы не друзья")
            return redirect(url_for("friends_page"))

        conn.execute("""
            DELETE FROM friendships
            WHERE user1_id = ? AND user2_id = ?
        """, (user1_id, user2_id))
        conn.commit()

    flash("Пользователь удалён из друзей")
    return redirect(url_for("friends_page"))


@app.route("/profile/photos/upload", methods=["POST"])
def upload_profile_photo():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))
    
        banned_redirect = check_ban_or_logout()
        if banned_redirect:
            return banned_redirect

    file = request.files.get("profile_photo")
    image_path = save_uploaded_file(file)

    if not image_path:
        flash("Выбери фото")
        return redirect(url_for("profile", username=session["username"]))

    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO profile_photos (user_id, image_url)
            VALUES (?, ?)
        """, (session["user_id"], image_path))
        conn.commit()

    flash("Фото добавлено в профиль")
    return redirect(url_for("profile", username=session["username"]))


@app.route("/profile/photos/delete/<int:photo_id>", methods=["POST"])
def delete_profile_photo(photo_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        photo = conn.execute("""
            SELECT * FROM profile_photos
            WHERE id = ? AND user_id = ?
        """, (photo_id, session["user_id"])).fetchone()

        if not photo:
            flash("Фото не найдено")
            return redirect(url_for("profile", username=session["username"]))

        image_path = photo["image_url"]

        conn.execute("DELETE FROM profile_photo_likes WHERE photo_id = ?", (photo_id,))
        conn.execute("DELETE FROM profile_photo_comments WHERE photo_id = ?", (photo_id,))
        conn.execute("DELETE FROM profile_photos WHERE id = ?", (photo_id,))
        conn.commit()

    delete_uploaded_file(image_path)

    flash("Фото удалено")
    return redirect(url_for("profile", username=session["username"]))


@app.route("/register", methods=["GET", "POST"])
def register():
    form_data = {"username": "", "email": ""}

    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip()
        password = request.form.get("password", "")
        password_repeat = request.form.get("password_repeat", "")

        if password != password_repeat:
            flash("Пароли не совпадают")
            return redirect(url_for("register"))


        form_data["username"] = username
        form_data["email"] = email

        if not username or not email or not password:
            flash("Заполни все поля")
            return render_template("register.html", form_data=form_data)

        if not is_strong_password(password):
            flash("Пароль должен быть минимум 8 символов")
            return render_template("register.html", form_data=form_data)

        verification_code = generate_verification_code()
        hashed_password = generate_password_hash(password)

        try:
            with get_db_connection() as conn:
                conn.execute("""
                    INSERT INTO users (
                        username, email, password,
                        email_verified, email_verification_code
                    )
                    VALUES (?, ?, ?, 0, ?)
                """, (username, email, hashed_password, verification_code))
                conn.commit()
            try:
                sent = send_verification_email(email, verification_code)

                if not sent:
                    flash ("Ошибка отправки почты")
                    return redirect(url_for("register"))
            except Exception as e:
                print("EMAIL ERROR:", e)
                sent = False

            if sent:
                flash("Аккаунт создан. Код подтверждения отправлен на email.")
            else:
                flash("Аккаунт создан, но письмо не отправилось. Проверь настройки почты.")

            return redirect(url_for("verify_email", email=email))

        except sqlite3.IntegrityError:
            flash("Такой логин или email уже существует")
            return render_template("register.html", form_data=form_data)

    return render_template("register.html", form_data=form_data)


@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    email = request.args.get("email", "").strip()

    if not email:
        flash("Не указан email для подтверждения")
        return redirect(url_for("register"))

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("register"))

        if user["email_verified"] == 1:
            flash("Email уже подтверждён. Можешь войти.")
            return redirect(url_for("login"))

        if request.method == "POST":
            code = request.form["code"].strip()

            if code == user["email_verification_code"]:
                conn.execute("""
                    UPDATE users
                    SET email_verified = 1,
                        email_verification_code = ''
                    WHERE id = ?
                """, (user["id"],))
                conn.commit()

                flash("Email подтверждён. Теперь можешь войти.")
                return redirect(url_for("login"))
            else:
                flash("Неверный код подтверждения")
                return redirect(url_for("verify_email", email=email))

    return render_template("verify_email.html", email=email)


@app.route("/resend-verification/<email>", methods=["POST"])
def resend_verification(email):
    email = email.strip()

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("register"))

        if user["email_verified"] == 1:
            flash("Email уже подтверждён")
            return redirect(url_for("login"))

        new_code = generate_verification_code()

        conn.execute("""
            UPDATE users
            SET email_verification_code = ?
            WHERE id = ?
        """, (new_code, user["id"]))
        conn.commit()

    sent = send_verification_email(email, new_code)

    if sent:
        flash("Новый код отправлен на email")
    else:
        flash("Не удалось отправить новый код. Проверь настройки почты.")

    return redirect(url_for("verify_email", email=email))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"].strip()

        if not email:
            flash("Введите email")
            return redirect(url_for("forgot_password"))

        with get_db_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            ).fetchone()

            if not user:
                flash("Пользователь с таким email не найден")
                return redirect(url_for("forgot_password"))

            reset_code = generate_verification_code()
            reset_expires = generate_reset_expiry()

            conn.execute("""
                UPDATE users
                SET password_reset_code = ?, password_reset_expires = ?
                WHERE id = ?
            """, (reset_code, reset_expires, user["id"]))
            conn.commit()

        sent = send_reset_password_email(email, reset_code)

        if sent:
            flash("Код для восстановления пароля отправлен на email")
        else:
            flash("Не удалось отправить код. Проверь настройки почты.")

        return redirect(url_for("verify_reset_code", email=email))

    return render_template("forgot_password.html")


@app.route("/verify-reset-code", methods=["GET", "POST"])
def verify_reset_code():
    email = request.args.get("email", "").strip()

    if not email:
        flash("Не указан email")
        return redirect(url_for("forgot_password"))

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("forgot_password"))

        if request.method == "POST":
            code = request.form["code"].strip()

            if not user["password_reset_code"]:
                flash("Сначала запроси код восстановления")
                return redirect(url_for("forgot_password"))

            if is_reset_code_expired(user["password_reset_expires"]):
                flash("Код истёк. Запроси новый.")
                return redirect(url_for("forgot_password"))

            if code != user["password_reset_code"]:
                flash("Неверный код")
                return redirect(url_for("verify_reset_code", email=email))

            session["reset_email"] = email
            flash("Код подтверждён. Теперь задай новый пароль.")
            return redirect(url_for("reset_password"))

    return render_template("verify_reset_code.html", email=email)


@app.route("/resend-reset-code/<email>", methods=["POST"])
def resend_reset_code(email):
    email = email.strip()

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("forgot_password"))

        reset_code = generate_verification_code()
        reset_expires = generate_reset_expiry()

        conn.execute("""
            UPDATE users
            SET password_reset_code = ?, password_reset_expires = ?
            WHERE id = ?
        """, (reset_code, reset_expires, user["id"]))
        conn.commit()

    sent = send_reset_password_email(email, reset_code)

    if sent:
        flash("Новый код отправлен на email")
    else:
        flash("Не удалось отправить новый код")

    return redirect(url_for("verify_reset_code", email=email))


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    email = session.get("reset_email")

    if not email:
        flash("Сначала пройди проверку кода")
        return redirect(url_for("forgot_password"))

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if not user:
            session.pop("reset_email", None)
            flash("Пользователь не найден")
            return redirect(url_for("forgot_password"))

        if request.method == "POST":
            password = request.form["password"].strip()
            password2 = request.form["password2"].strip()

            if not password or not password2:
                flash("Заполни все поля")
                return redirect(url_for("reset_password"))

            if password != password2:
                flash("Пароли не совпадают")
                return redirect(url_for("reset_password"))

            if not is_strong_password(password):
                flash("Пароль должен быть минимум 8 символов и содержать хотя бы 1 букву")
                return redirect(url_for("reset_password"))
            
            hashed_password = generate_password_hash(password)

            conn.execute("""
                UPDATE users
                SET password = ?,
                    password_reset_code = '',
                    password_reset_expires = ''
                WHERE id = ?
            """, (hashed_password, user["id"]))
            conn.commit()

            session.pop("reset_email", None)

            flash("Пароль успешно изменён. Теперь можешь войти.")
            return redirect(url_for("login"))

    return render_template("reset_password.html", email=email)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        with get_db_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,)
            ).fetchone()

            if user and check_password_hash(user["password"], password):
                if user["email_verified"] != 1:
                    new_code = generate_verification_code()

                    conn.execute("""
                        UPDATE users
                        SET email_verification_code = ?
                        WHERE id = ?
                    """, (new_code, user["id"]))
                    conn.commit()

                    sent = send_verification_email(user["email"], new_code)

                    if sent:
                        flash("Сначала подтверди email. Новый код отправлен на почту.")
                    else:
                        flash("Сначала подтверди email. Но письмо не отправилось — проверь настройки почты.")

                    return redirect(url_for("verify_email", email=user["email"]))

                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["avatar_url"] = user["avatar_url"] or ""

                flash("Вход выполнен успешно")
                return redirect(url_for("index"))
            else:
                flash("Неверный логин или пароль")
                return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    session.pop("reset_email", None)
    flash("Вы вышли из аккаунта")
    return redirect(url_for("index"))


@app.route("/create-post", methods=["GET", "POST"])
def create_post():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))
    
        banned_redirect = check_ban_or_logout()
        if banned_redirect:
            return banned_redirect

    with get_db_connection() as conn:
        if request.method == "POST":
            content = request.form["content"].strip()
            file = request.files.get("image")
            image_path = save_uploaded_file(file)

            if len(content) > MAX_POST_LENGTH:
                flash(f"Пост слишком длинный. Максимум {MAX_POST_LENGTH} символов")
                return redirect(url_for("create_post"))

            if not content and not image_path:
                flash("Нужно добавить текст или фото")
                return redirect(url_for("create_post"))

            if not is_admin_user(conn, session["user_id"]):
                if not can_create_post_now(conn, session["user_id"]):
                    flash(f"Можно публиковать только {MAX_POSTS_PER_24H} поста за 24 часа")
                    return redirect(url_for("create_post"))
                
            conn.execute(
                "INSERT INTO posts (author_id, content, image_url) VALUES (?, ?, ?)",
                (session["user_id"], content, image_path)
            )
            if not can_manage_feed(conn, session["user_id"]):
                conn.execute(
                    "INSERT INTO post_publish_log (user_id) VALUES (?)",
                    (session["user_id"],)
                )                
                conn.commit()

            flash("Пост опубликован")
            return redirect(url_for("index"))

        friends_notifications, messages_notifications = get_notifications(conn, session["user_id"])

    return render_template(
        "create_post.html",
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/edit-post/<int:post_id>", methods=["GET", "POST"])
def edit_post(post_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        post = conn.execute(
            "SELECT * FROM posts WHERE id = ?",
            (post_id,)
        ).fetchone()

        if not post:
            flash("Пост не найден")
            return redirect(url_for("index"))

        if post["author_id"] != session["user_id"]:
            flash("Нельзя редактировать чужой пост")
            return redirect(url_for("index"))

        if request.method == "POST":
            content = request.form["content"].strip()
            file = request.files.get("image")
            image_path = post["image_url"] or ""
            old_image_path = post["image_url"] or ""

            if len(content) > MAX_POST_LENGTH:
                flash(f"Пост слишком длинный. Максимум {MAX_POST_LENGTH} символов")
                return redirect(url_for("edit_post", post_id=post_id))

            new_image_path = save_uploaded_file(file)
            if new_image_path:
                image_path = new_image_path

            if not content and not image_path:
                flash("Нужно добавить текст или фото")
                return redirect(url_for("edit_post", post_id=post_id))

            conn.execute(
                "UPDATE posts SET content = ?, image_url = ? WHERE id = ?",
                (content, image_path, post_id)
            )
            conn.commit()

            if new_image_path and old_image_path and old_image_path != new_image_path:
                delete_uploaded_file(old_image_path)

            flash("Пост обновлён")
            return redirect(url_for("post_detail", post_id=post_id))

        friends_notifications, messages_notifications = get_notifications(conn, session["user_id"])

    return render_template(
        "edit_post.html",
        post=post,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/delete-post/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        post = conn.execute(
            "SELECT * FROM posts WHERE id = ?",
            (post_id,)
        ).fetchone()

        if not post:
            flash("Пост не найден")
            return redirect(url_for("index"))

        can_delete = (
            post["author_id"] == session["user_id"]
            or can_manage_feed(conn, session["user_id"])
        )

        if not can_delete:
            flash("Нельзя удалить этот пост")
            return redirect(url_for("index"))

        image_path = post["image_url"]

        conn.execute("DELETE FROM likes WHERE post_id = ?", (post_id,))
        conn.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        conn.commit()

    delete_uploaded_file(image_path)

    flash("Пост удалён")
    return redirect(request.referrer or url_for("index"))


@app.route("/like/<int:post_id>", methods=["POST"])
def toggle_like(post_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))
    
        banned_redirect = check_ban_or_logout()
        if banned_redirect:
            return banned_redirect

    with get_db_connection() as conn:
        existing_like = conn.execute(
            "SELECT * FROM likes WHERE user_id = ? AND post_id = ?",
            (session["user_id"], post_id)
        ).fetchone()

        if existing_like:
            conn.execute(
                "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
                (session["user_id"], post_id)
            )
        else:
            conn.execute(
                "INSERT INTO likes (user_id, post_id) VALUES (?, ?)",
                (session["user_id"], post_id)
            )

            post = conn.execute(
                "SELECT * FROM posts WHERE id = ?",
                (post_id,)
            ).fetchone()

            if post:
                create_notification(
                    conn,
                    post["author_id"],
                    session["user_id"],
                    "post_like",
                    f"{session['username']} лайкнул твой пост",
                    f"/post/{post_id}"
                )

        conn.commit()

    return redirect(request.referrer or url_for("index"))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
def add_comment(post_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))
    
        banned_redirect = check_ban_or_logout()
        if banned_redirect:
            return banned_redirect
        
    content = request.form["content"].strip()

    if not content:
        flash("Комментарий не может быть пустым")
        return redirect(url_for("post_detail", post_id=post_id))

    if len(content) > MAX_COMMENT_LENGTH:
        flash(f"Комментарий слишком длинный. Максимум {MAX_COMMENT_LENGTH} символов")
        return redirect(url_for("post_detail", post_id=post_id))

    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO comments (post_id, author_id, content) VALUES (?, ?, ?)",
            (post_id, session["user_id"], content)
        )

        post = conn.execute(
            "SELECT * FROM posts WHERE id = ?",
            (post_id,)
        ).fetchone()

        if post:
            create_notification(
                conn,
                post["author_id"],
                session["user_id"],
                "post_comment",
                f"{session['username']} прокомментировал твой пост",
                f"/post/{post_id}"
            )

        conn.commit()

    flash("Комментарий добавлен")
    return redirect(url_for("post_detail", post_id=post_id))


@app.route("/edit-comment/<int:comment_id>", methods=["GET", "POST"])
def edit_comment(comment_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        comment = conn.execute(
            "SELECT * FROM comments WHERE id = ?",
            (comment_id,)
        ).fetchone()

        if not comment:
            flash("Комментарий не найден")
            return redirect(url_for("index"))

        if comment["author_id"] != session["user_id"]:
            flash("Нельзя редактировать чужой комментарий")
            return redirect(url_for("index"))

        if request.method == "POST":
            content = request.form["content"].strip()

            if not content:
                flash("Комментарий не может быть пустым")
                return redirect(url_for("edit_comment", comment_id=comment_id))

            if len(content) > MAX_COMMENT_LENGTH:
                flash(f"Комментарий слишком длинный. Максимум {MAX_COMMENT_LENGTH} символов")
                return redirect(url_for("edit_comment", comment_id=comment_id))

            conn.execute(
                "UPDATE comments SET content = ? WHERE id = ?",
                (content, comment_id)
            )
            conn.commit()

            flash("Комментарий обновлён")
            return redirect(url_for("post_detail", post_id=comment["post_id"]))

        friends_notifications, messages_notifications = get_notifications(conn, session["user_id"])

    return render_template(
        "edit_comment.html",
        comment=comment,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/delete-comment/<int:comment_id>", methods=["POST"])
def delete_comment(comment_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        comment = conn.execute(
            "SELECT * FROM comments WHERE id = ?",
            (comment_id,)
        ).fetchone()

        if not comment:
            flash("Комментарий не найден")
            return redirect(url_for("index"))

        can_delete = (
            comment["author_id"] == session["user_id"]
            or can_manage_feed(conn, session["user_id"])
        )

        if not can_delete:
            flash("Нельзя удалить этот комментарий")
            return redirect(url_for("index"))

        post_id = comment["post_id"]

        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        conn.commit()

    flash("Комментарий удалён")
    return redirect(url_for("post_detail", post_id=post_id))

@app.route("/profile/<username>")
def profile(username):
    with get_db_connection() as conn:
        user = conn.execute("""
            SELECT
                users.id,
                users.username,
                users.bio,
                users.avatar_url,
                users.message_privacy,
                users.posts_visibility,
                users.photos_visibility,
                users.created_at,
                COUNT(DISTINCT posts.id) AS posts_count
            FROM users
            LEFT JOIN posts ON users.id = posts.author_id
            WHERE users.username = ?
            GROUP BY users.id
        """, (username,)).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("index"))

        can_message = can_send_message(conn, session.get("user_id"), user)
        friend_status = get_friend_request_status(conn, session.get("user_id"), user["id"]) if session.get("user_id") else None

        allow_posts = can_view_posts(conn, session.get("user_id"), user)
        allow_photos = can_view_profile_photos(conn, session.get("user_id"), user)

        posts = []
        profile_photos = []

        if allow_posts:
            if session.get("user_id"):
                posts = conn.execute("""
                    SELECT
                        posts.id,
                        posts.author_id,
                        posts.content,
                        posts.image_url,
                        posts.created_at,
                        users.username,
                        users.avatar_url,
                        COUNT(DISTINCT likes.id) AS likes_count,
                        COUNT(DISTINCT comments.id) AS comments_count,
                        MAX(CASE WHEN likes.user_id = ? THEN 1 ELSE 0 END) AS is_liked
                    FROM posts
                    JOIN users ON posts.author_id = users.id
                    LEFT JOIN likes ON posts.id = likes.post_id
                    LEFT JOIN comments ON posts.id = comments.post_id
                    WHERE users.username = ?
                    GROUP BY posts.id
                    ORDER BY posts.created_at DESC
                """, (session["user_id"], username)).fetchall()
            else:
                posts = conn.execute("""
                    SELECT
                        posts.id,
                        posts.author_id,
                        posts.content,
                        posts.image_url,
                        posts.created_at,
                        users.username,
                        users.avatar_url,
                        COUNT(DISTINCT likes.id) AS likes_count,
                        COUNT(DISTINCT comments.id) AS comments_count,
                        0 AS is_liked
                    FROM posts
                    JOIN users ON posts.author_id = users.id
                    LEFT JOIN likes ON posts.id = likes.post_id
                    LEFT JOIN comments ON posts.id = comments.post_id
                    WHERE users.username = ?
                    GROUP BY posts.id
                    ORDER BY posts.created_at DESC
                """, (username,)).fetchall()

        if allow_photos:
            profile_photos = conn.execute("""
                SELECT *
                FROM profile_photos
                WHERE user_id = ?
                ORDER BY created_at DESC
            """, (user["id"],)).fetchall()

        friends_notifications, messages_notifications = get_notifications(conn, session.get("user_id"))

    return render_template(
        "profile.html",
        user=user,
        posts=posts,
        can_message=can_message,
        friend_status=friend_status,
        profile_photos=profile_photos,
        allow_posts=allow_posts,
        allow_photos=allow_photos,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/post/<int:post_id>")
def post_detail(post_id):
    with get_db_connection() as conn:
        if session.get("user_id"):
            post = conn.execute("""
                SELECT
                    posts.id,
                    posts.author_id,
                    posts.content,
                    posts.image_url,
                    posts.created_at,
                    users.username,
                    users.avatar_url,
                    COUNT(DISTINCT likes.id) AS likes_count,
                    COUNT(DISTINCT comments.id) AS comments_count,
                    MAX(CASE WHEN likes.user_id = ? THEN 1 ELSE 0 END) AS is_liked
                FROM posts
                JOIN users ON posts.author_id = users.id
                LEFT JOIN likes ON posts.id = likes.post_id
                LEFT JOIN comments ON posts.id = comments.post_id
                WHERE posts.id = ?
                GROUP BY posts.id
            """, (session["user_id"], post_id)).fetchone()
        else:
            post = conn.execute("""
                SELECT
                    posts.id,
                    posts.author_id,
                    posts.content,
                    posts.image_url,
                    posts.created_at,
                    users.username,
                    users.avatar_url,
                    COUNT(DISTINCT likes.id) AS likes_count,
                    COUNT(DISTINCT comments.id) AS comments_count,
                    0 AS is_liked
                FROM posts
                JOIN users ON posts.author_id = users.id
                LEFT JOIN likes ON posts.id = likes.post_id
                LEFT JOIN comments ON posts.id = comments.post_id
                WHERE posts.id = ?
                GROUP BY posts.id
            """, (post_id,)).fetchone()

        comments = conn.execute("""
            SELECT
                comments.id,
                comments.post_id,
                comments.author_id,
                comments.content,
                comments.created_at,
                users.username
            FROM comments
            JOIN users ON comments.author_id = users.id
            WHERE comments.post_id = ?
            ORDER BY comments.created_at DESC
        """, (post_id,)).fetchall()

        friends_notifications, messages_notifications = get_notifications(conn, session.get("user_id"))

    if not post:
        flash("Пост не найден")
        return redirect(url_for("index"))

    return render_template(
        "post_detail.html",
        post=post,
        comments=comments,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/messages")
def conversations_list():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        conversations = conn.execute("""
            SELECT
                conversations.id,
                CASE
                    WHEN conversations.user1_id = ? THEN u2.username
                    ELSE u1.username
                END AS other_username,
                (
                    SELECT messages.content
                    FROM messages
                    WHERE messages.conversation_id = conversations.id
                    ORDER BY messages.created_at DESC, messages.id DESC
                    LIMIT 1
                ) AS last_message,
                (
                    SELECT messages.sender_id
                    FROM messages
                    WHERE messages.conversation_id = conversations.id
                    ORDER BY messages.created_at DESC, messages.id DESC
                    LIMIT 1
                ) AS last_sender_id,
                (
                    SELECT users.username
                    FROM messages
                    JOIN users ON users.id = messages.sender_id
                    WHERE messages.conversation_id = conversations.id
                    ORDER BY messages.created_at DESC, messages.id DESC
                    LIMIT 1
                ) AS last_sender_username,
                (
                    SELECT messages.created_at
                    FROM messages
                    WHERE messages.conversation_id = conversations.id
                    ORDER BY messages.created_at DESC, messages.id DESC
                    LIMIT 1
                ) AS last_message_time,
                (
                    SELECT COUNT(*)
                    FROM messages
                    WHERE messages.conversation_id = conversations.id
                      AND messages.is_read = 0
                      AND messages.sender_id != ?
                ) AS unread_count
            FROM conversations
            JOIN users u1 ON conversations.user1_id = u1.id
            JOIN users u2 ON conversations.user2_id = u2.id
            WHERE conversations.user1_id = ? OR conversations.user2_id = ?
            ORDER BY last_message_time DESC, conversations.created_at DESC
        """, (
            session["user_id"],
            session["user_id"],
            session["user_id"],
            session["user_id"]
        )).fetchall()

        friends_notifications, messages_notifications = get_notifications(conn, session["user_id"])

    return render_template(
        "messages.html",
        conversations=conversations,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/start-chat/<username>")
def start_chat(username):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))
    
        banned_redirect = check_ban_or_logout()
        if banned_redirect:
            return banned_redirect

    with get_db_connection() as conn:
        other_user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if not other_user:
            flash("Пользователь не найден")
            return redirect(url_for("index"))

        if other_user["id"] == session["user_id"]:
            flash("Нельзя написать самому себе")
            return redirect(url_for("profile", username=username))

        if not can_send_message(conn, session["user_id"], other_user):
            if other_user["message_privacy"] == "friends":
                flash("Этому пользователю могут писать только друзья")
            elif other_user["message_privacy"] == "nobody":
                flash("Этот пользователь запретил личные сообщения")
            else:
                flash("Нельзя начать диалог")
            return redirect(url_for("profile", username=username))

    conversation = get_or_create_conversation(session["user_id"], other_user["id"])
    return redirect(url_for("chat_detail", conversation_id=conversation["id"]))


@app.route("/messages/<int:conversation_id>", methods=["GET", "POST"])
def chat_detail(conversation_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))
    
        banned_redirect = check_ban_or_logout()
        if banned_redirect:
            return banned_redirect

    with get_db_connection() as conn:
        conversation = conn.execute("""
            SELECT *
            FROM conversations
            WHERE id = ?
              AND (user1_id = ? OR user2_id = ?)
        """, (conversation_id, session["user_id"], session["user_id"])).fetchone()

        if not conversation:
            flash("Диалог не найден")
            return redirect(url_for("conversations_list"))

        other_user = conn.execute("""
            SELECT id, username, avatar_url, last_seen_at
            FROM users
            WHERE id = ?
        """, (
            conversation["user2_id"] if conversation["user1_id"] == session["user_id"] else conversation["user1_id"],
        )).fetchone()
        
        other_user_status = get_last_seen_status(other_user["last_seen_at"]) if other_user and "last_seen_at" in other_user.keys() else "давно не заходил"

        if request.method == "POST":
            content = request.form["content"].strip()

            if not content:
                flash("Сообщение не может быть пустым")
                return redirect(url_for("chat_detail", conversation_id=conversation_id))

            if len(content) > MAX_MESSAGE_LENGTH:
                flash(f"Сообщение слишком длинное. Максимум {MAX_MESSAGE_LENGTH} символов")
                return redirect(url_for("chat_detail", conversation_id=conversation_id))

            conn.execute("""
                INSERT INTO messages (conversation_id, sender_id, content, is_read)
                VALUES (?, ?, ?, 0)
            """, (conversation_id, session["user_id"], content))
            conn.commit()

        conn.execute("""
            UPDATE messages
            SET is_read = 1
            WHERE conversation_id = ?
              AND sender_id != ?
        """, (conversation_id, session["user_id"]))
        conn.commit()

        messages = conn.execute("""
            SELECT
                messages.id,
                messages.content,
                messages.created_at,
                messages.sender_id,
                users.username
            FROM messages
            JOIN users ON messages.sender_id = users.id
            WHERE messages.conversation_id = ?
            ORDER BY messages.created_at ASC, messages.id ASC
        """, (conversation_id,)).fetchall()

        friends_notifications, messages_notifications = get_notifications(conn, session["user_id"])

    return render_template(
        "chat_detail.html",
        conversation=conversation,
        other_user=other_user,
        other_user_status=other_user_status,
        messages=messages,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/edit-message/<int:message_id>", methods=["GET", "POST"])
def edit_message(message_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        message = conn.execute("""
            SELECT *
            FROM messages
            WHERE id = ?
        """, (message_id,)).fetchone()

        if not message:
            flash("Сообщение не найдено")
            return redirect(url_for("conversations_list"))

        conversation = conn.execute("""
            SELECT *
            FROM conversations
            WHERE id = ?
              AND (user1_id = ? OR user2_id = ?)
        """, (message["conversation_id"], session["user_id"], session["user_id"])).fetchone()

        if not conversation:
            flash("Диалог не найден")
            return redirect(url_for("conversations_list"))

        if message["sender_id"] != session["user_id"]:
            flash("Нельзя редактировать чужое сообщение")
            return redirect(url_for("chat_detail", conversation_id=message["conversation_id"]))

        if request.method == "POST":
            content = request.form["content"].strip()

            if not content:
                flash("Сообщение не может быть пустым")
                return redirect(url_for("edit_message", message_id=message_id))

            if len(content) > MAX_MESSAGE_LENGTH:
                flash(f"Сообщение слишком длинное. Максимум {MAX_MESSAGE_LENGTH} символов")
                return redirect(url_for("edit_message", message_id=message_id))

            conn.execute(
                "UPDATE messages SET content = ? WHERE id = ?",
                (content, message_id)
            )
            conn.commit()

            flash("Сообщение обновлено")
            return redirect(url_for("chat_detail", conversation_id=message["conversation_id"]))

        friends_notifications, messages_notifications = get_notifications(conn, session["user_id"])

    return render_template(
        "edit_message.html",
        message=message,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/delete-message/<int:message_id>", methods=["POST"])
def delete_message(message_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        message = conn.execute("""
            SELECT *
            FROM messages
            WHERE id = ?
        """, (message_id,)).fetchone()

        if not message:
            flash("Сообщение не найдено")
            return redirect(url_for("conversations_list"))

        conversation = conn.execute("""
            SELECT *
            FROM conversations
            WHERE id = ?
              AND (user1_id = ? OR user2_id = ?)
        """, (message["conversation_id"], session["user_id"], session["user_id"])).fetchone()

        if not conversation:
            flash("Диалог не найден")
            return redirect(url_for("conversations_list"))

        if message["sender_id"] != session["user_id"]:
            flash("Нельзя удалить чужое сообщение")
            return redirect(url_for("chat_detail", conversation_id=message["conversation_id"]))

        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        conn.commit()

    flash("Сообщение удалено")
    return redirect(url_for("chat_detail", conversation_id=message["conversation_id"]))


@app.route("/edit-profile", methods=["GET", "POST"])
def edit_profile():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (session["user_id"],)
        ).fetchone()

        if request.method == "POST":
            bio = request.form["bio"].strip()
            file = request.files.get("avatar")

            if len(bio) > MAX_BIO_LENGTH:
                flash(f"Описание слишком длинное. Максимум {MAX_BIO_LENGTH} символов")
                return redirect(url_for("edit_profile"))

            avatar_path = user["avatar_url"] or ""
            old_avatar_path = user["avatar_url"] or ""

            new_avatar_path = save_uploaded_file(file)
            if new_avatar_path:
                avatar_path = new_avatar_path

            conn.execute(
                "UPDATE users SET bio = ?, avatar_url = ? WHERE id = ?",
                (bio, avatar_path, session["user_id"])
            )
            conn.commit()

            session["avatar_url"] = avatar_path

            if new_avatar_path and old_avatar_path and old_avatar_path != new_avatar_path:
                delete_uploaded_file(old_avatar_path)

            flash("Профиль обновлён")
            return redirect(url_for("profile", username=session["username"]))

        friends_notifications, messages_notifications = get_notifications(conn, session["user_id"])

    return render_template(
        "edit_profile.html",
        user=user,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )

@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_server_error(error):
    return render_template("500.html"), 500


@app.route("/delete-account", methods=["POST"])
def delete_account():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    username = session["username"]

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

        if not user:
            session.clear()
            flash("Аккаунт не найден")
            return redirect(url_for("index"))

        # удаляем аватар
        if user["avatar_url"]:
            delete_uploaded_file(user["avatar_url"])

        # удаляем фото профиля
        profile_photos = conn.execute(
            "SELECT image_url FROM profile_photos WHERE user_id = ?",
            (user_id,)
        ).fetchall()

        for photo in profile_photos:
            if photo["image_url"]:
                delete_uploaded_file(photo["image_url"])

        # удаляем фото постов
        posts = conn.execute(
            "SELECT image_url FROM posts WHERE author_id = ?",
            (user_id,)
        ).fetchall()

        for post in posts:
            if post["image_url"]:
                delete_uploaded_file(post["image_url"])

        # очищаем связанные данные
        conn.execute("DELETE FROM likes WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM likes WHERE post_id IN (SELECT id FROM posts WHERE author_id = ?)", (user_id,))
        conn.execute("DELETE FROM comments WHERE author_id = ?", (user_id,))
        conn.execute("DELETE FROM comments WHERE post_id IN (SELECT id FROM posts WHERE author_id = ?)", (user_id,))
        conn.execute("DELETE FROM messages WHERE sender_id = ?", (user_id,))
        conn.execute("DELETE FROM profile_photos WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM posts WHERE author_id = ?", (user_id,))
        conn.execute("DELETE FROM friend_requests WHERE sender_id = ? OR receiver_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM friendships WHERE user1_id = ? OR user2_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM conversations WHERE user1_id = ? OR user2_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    session.clear()
    flash(f"Аккаунт {username} удалён")
    return redirect(url_for("index"))

@app.route("/liked")
def liked_posts():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        posts = conn.execute("""
            SELECT
                posts.id,
                posts.author_id,
                posts.content,
                posts.image_url,
                posts.created_at,
                users.username,
                users.avatar_url,
                COUNT(DISTINCT l2.id) AS likes_count,
                COUNT(DISTINCT comments.id) AS comments_count,
                1 AS is_liked
            FROM likes l
            JOIN posts ON l.post_id = posts.id
            JOIN users ON posts.author_id = users.id
            LEFT JOIN likes l2 ON posts.id = l2.post_id
            LEFT JOIN comments ON posts.id = comments.post_id
            WHERE l.user_id = ?
            GROUP BY posts.id
            ORDER BY l.created_at DESC
        """, (session["user_id"],)).fetchall()

        friends_notifications, messages_notifications = get_notifications(conn, session["user_id"])

    return render_template(
        "liked.html",
        posts=posts,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )

@app.route("/remove-avatar", methods=["POST"])
def remove_avatar():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (session["user_id"],)
        ).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("index"))

        old_avatar = user["avatar_url"] or ""

        conn.execute(
            "UPDATE users SET avatar_url = '' WHERE id = ?",
            (session["user_id"],)
        )
        conn.commit()

    if old_avatar:
        delete_uploaded_file(old_avatar)

    session["avatar_url"] = ""
    flash("Аватарка удалена")
    return redirect(url_for("edit_profile"))

@app.route("/profile-photo/<int:photo_id>")
def profile_photo_detail(photo_id):
    with get_db_connection() as conn:
        photo = get_profile_photo_with_meta(conn, photo_id, session.get("user_id"))

        if not photo:
            flash("Фото не найдено")
            return redirect(url_for("index"))

        comments = conn.execute("""
            SELECT
                profile_photo_comments.id,
                profile_photo_comments.photo_id,
                profile_photo_comments.author_id,
                profile_photo_comments.content,
                profile_photo_comments.created_at,
                users.username,
                users.avatar_url
            FROM profile_photo_comments
            JOIN users ON users.id = profile_photo_comments.author_id
            WHERE profile_photo_comments.photo_id = ?
            ORDER BY profile_photo_comments.created_at DESC, profile_photo_comments.id DESC
        """, (photo_id,)).fetchall()

        owner = session.get("user_id") == photo["user_id"]
        friends_notifications, messages_notifications = get_notifications(conn, session.get("user_id"))

    return render_template(
        "profile_photo_detail.html",
        photo=photo,
        comments=comments,
        owner=owner,
        friends_notifications=friends_notifications,
        messages_notifications=messages_notifications
    )


@app.route("/profile-photo/<int:photo_id>/like", methods=["POST"])
def toggle_profile_photo_like(photo_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        photo = conn.execute(
            "SELECT * FROM profile_photos WHERE id = ?",
            (photo_id,)
        ).fetchone()

        if not photo:
            flash("Фото не найдено")
            return redirect(url_for("index"))

        existing_like = conn.execute("""
            SELECT *
            FROM profile_photo_likes
            WHERE user_id = ? AND photo_id = ?
        """, (session["user_id"], photo_id)).fetchone()

        if existing_like:
            conn.execute("""
                DELETE FROM profile_photo_likes
                WHERE user_id = ? AND photo_id = ?
            """, (session["user_id"], photo_id))
        else:
            conn.execute("""
                INSERT INTO profile_photo_likes (user_id, photo_id)
                VALUES (?, ?)
            """, (session["user_id"], photo_id))

        conn.commit()

    return redirect(request.referrer or url_for("profile_photo_detail", photo_id=photo_id))


@app.route("/profile-photo/<int:photo_id>/comment", methods=["POST"])
def add_profile_photo_comment(photo_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    content = request.form["content"].strip()

    if not content:
        flash("Комментарий не может быть пустым")
        return redirect(url_for("profile_photo_detail", photo_id=photo_id))

    if len(content) > MAX_COMMENT_LENGTH:
        flash(f"Комментарий слишком длинный. Максимум {MAX_COMMENT_LENGTH} символов")
        return redirect(url_for("profile_photo_detail", photo_id=photo_id))

    with get_db_connection() as conn:
        photo = conn.execute(
            "SELECT * FROM profile_photos WHERE id = ?",
            (photo_id,)
        ).fetchone()

        if not photo:
            flash("Фото не найдено")
            return redirect(url_for("index"))

        conn.execute("""
            INSERT INTO profile_photo_comments (photo_id, author_id, content)
            VALUES (?, ?, ?)
        """, (photo_id, session["user_id"], content))
        conn.commit()

    flash("Комментарий добавлен")
    return redirect(url_for("profile_photo_detail", photo_id=photo_id))


@app.route("/profile-photo-comment/<int:comment_id>/delete", methods=["POST"])
def delete_profile_photo_comment(comment_id):
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        comment = conn.execute("""
            SELECT *
            FROM profile_photo_comments
            WHERE id = ?
        """, (comment_id,)).fetchone()

        if not comment:
            flash("Комментарий не найден")
            return redirect(url_for("index"))

        if comment["author_id"] != session["user_id"]:
            flash("Нельзя удалить чужой комментарий")
            return redirect(url_for("profile_photo_detail", photo_id=comment["photo_id"]))

        photo_id = comment["photo_id"]

        conn.execute(
            "DELETE FROM profile_photo_comments WHERE id = ?",
            (comment_id,)
        )
        conn.commit()

    flash("Комментарий удалён")
    return redirect(url_for("profile_photo_detail", photo_id=photo_id))

@app.route("/notifications")
def notifications_page():
    if not session.get("user_id"):
        flash("Сначала войди в аккаунт")
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        conn.execute("""
            UPDATE notifications
            SET is_read = 1
            WHERE user_id = ?
        """, (session["user_id"],))
        conn.commit()

        notifications = conn.execute("""
            SELECT
                notifications.*,
                users.username AS actor_username,
                users.avatar_url AS actor_avatar
            FROM notifications
            LEFT JOIN users ON users.id = notifications.actor_id
            WHERE notifications.user_id = ?
            ORDER BY notifications.created_at DESC, notifications.id DESC
        """, (session["user_id"],)).fetchall()

    return render_template("notifications.html", notifications=notifications)

@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy_policy.html")

@app.route("/admin")
@admin_required
def admin_dashboard():
    with get_db_connection() as conn:
        stats = {
            "users_total": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "users_verified": conn.execute("SELECT COUNT(*) FROM users WHERE email_verified = 1").fetchone()[0],
            "users_banned": conn.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1").fetchone()[0],
            "users_new_24h": conn.execute("""
                SELECT COUNT(*)
                FROM users
                WHERE datetime(created_at) >= datetime('now', '-1 day')
            """).fetchone()[0],
            "posts_total": conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
            "comments_total": conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
            "messages_total": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "users_online": conn.execute("""
                SELECT COUNT(*)
                FROM users
                WHERE last_seen_at IS NOT NULL
                  AND datetime(last_seen_at) >= datetime('now', '-5 minutes')
            """).fetchone()[0],
        }

        recent_users = conn.execute("""
            SELECT id, username, email, created_at, is_banned, is_admin
            FROM users
            ORDER BY created_at DESC
            LIMIT 8
        """).fetchall()

    return render_template("admin.html", stats=stats, recent_users=recent_users)


@app.route("/admin/users")
@admin_required
def admin_users():
    query = request.args.get("q", "").strip()

    with get_db_connection() as conn:
        if query:
            users = conn.execute("""
                SELECT *
                FROM users
                WHERE username LIKE ?
                   OR email LIKE ?
                   OR CAST(id AS TEXT) LIKE ?
                ORDER BY created_at DESC
            """, (f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
        else:
            users = conn.execute("""
                SELECT *
                FROM users
                ORDER BY created_at DESC
                LIMIT 100
            """).fetchall()

    return render_template("admin_users.html", users=users, query=query)


@app.route("/admin/user/<int:user_id>")
@admin_required
def admin_user_detail(user_id):
    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("admin_users"))

        posts_count = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE author_id = ?",
            (user_id,)
        ).fetchone()[0]

        comments_count = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE author_id = ?",
            (user_id,)
        ).fetchone()[0]

        messages_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sender_id = ?",
            (user_id,)
        ).fetchone()[0]

    return render_template(
        "admin_user_detail.html",
        user=user,
        posts_count=posts_count,
        comments_count=comments_count,
        messages_count=messages_count
    )


@app.route("/admin/ban/<int:user_id>", methods=["POST"])
@admin_required
def admin_ban_user(user_id):
    reason = request.form.get("ban_reason", "").strip()

    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("admin_users"))

        if user["is_admin"] == 1:
            flash("Нельзя забанить администратора")
            return redirect(url_for("admin_user_detail", user_id=user_id))

        conn.execute("""
            UPDATE users
            SET is_banned = 1,
                ban_reason = ?,
                banned_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (reason, user_id))
        conn.commit()

    flash(f"Пользователь {user['username']} забанен")
    return redirect(url_for("admin_user_detail", user_id=user_id))


@app.route("/admin/unban/<int:user_id>", methods=["POST"])
@admin_required
def admin_unban_user(user_id):
    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("admin_users"))

        conn.execute("""
            UPDATE users
            SET is_banned = 0,
                ban_reason = '',
                banned_at = NULL
            WHERE id = ?
        """, (user_id,))
        conn.commit()

    flash(f"Пользователь {user['username']} разбанен")
    return redirect(url_for("admin_user_detail", user_id=user_id))


@app.route("/admin/delete-user/<int:user_id>", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    with get_db_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

        if not user:
            flash("Пользователь не найден")
            return redirect(url_for("admin_users"))

        if user["is_admin"] == 1:
            flash("Нельзя удалить администратора")
            return redirect(url_for("admin_user_detail", user_id=user_id))

        if user["avatar_url"]:
            delete_uploaded_file(user["avatar_url"])

        profile_photos = conn.execute(
            "SELECT image_url FROM profile_photos WHERE user_id = ?",
            (user_id,)
        ).fetchall()

        for photo in profile_photos:
            if photo["image_url"]:
                delete_uploaded_file(photo["image_url"])

        posts = conn.execute(
            "SELECT image_url FROM posts WHERE author_id = ?",
            (user_id,)
        ).fetchall()

        for post in posts:
            if post["image_url"]:
                delete_uploaded_file(post["image_url"])

        conn.execute("DELETE FROM likes WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM likes WHERE post_id IN (SELECT id FROM posts WHERE author_id = ?)", (user_id,))
        conn.execute("DELETE FROM comments WHERE author_id = ?", (user_id,))
        conn.execute("DELETE FROM comments WHERE post_id IN (SELECT id FROM posts WHERE author_id = ?)", (user_id,))
        conn.execute("DELETE FROM messages WHERE sender_id = ?", (user_id,))
        conn.execute("DELETE FROM profile_photo_likes WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM profile_photo_comments WHERE author_id = ?", (user_id,))
        conn.execute("DELETE FROM profile_photo_likes WHERE photo_id IN (SELECT id FROM profile_photos WHERE user_id = ?)", (user_id,))
        conn.execute("DELETE FROM profile_photo_comments WHERE photo_id IN (SELECT id FROM profile_photos WHERE user_id = ?)", (user_id,))
        conn.execute("DELETE FROM profile_photos WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM notifications WHERE user_id = ? OR actor_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM post_publish_log WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM posts WHERE author_id = ?", (user_id,))
        conn.execute("DELETE FROM friend_requests WHERE sender_id = ? OR receiver_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM friendships WHERE user1_id = ? OR user2_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM conversations WHERE user1_id = ? OR user2_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    flash(f"Аккаунт {user['username']} удалён администратором")
    return redirect(url_for("admin_users"))

@app.context_processor
def inject_header_data():
    activity_notifications = 0
    unread_messages = 0
    friends_notifications = 0
    is_admin = False
    is_moderator = False

    if session.get("user_id"):
        with get_db_connection() as conn:
            if "notifications" in [row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
                activity_notifications = conn.execute("""
                    SELECT COUNT(*)
                    FROM notifications
                    WHERE user_id = ? AND is_read = 0
                """, (session["user_id"],)).fetchone()[0]

            if "messages" in [row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
                unread_messages = conn.execute("""
                    SELECT COUNT(*)
                    FROM messages
                    WHERE is_read = 0
                      AND sender_id != ?
                      AND conversation_id IN (
                          SELECT id FROM conversations
                          WHERE user1_id = ? OR user2_id = ?
                      )
                """, (session["user_id"], session["user_id"], session["user_id"])).fetchone()[0]

            if "friend_requests" in [row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
                friends_notifications = conn.execute("""
                    SELECT COUNT(*)
                    FROM friend_requests
                    WHERE receiver_id = ? AND status = 'pending'
                """, (session["user_id"],)).fetchone()[0]

            is_admin = is_admin_user(conn, session["user_id"])
            is_moderator = is_moderator_user(conn, session["user_id"])

    return {
        "activity_notifications": activity_notifications,
        "unread_messages": unread_messages,
        "friends_notifications": friends_notifications,
        "is_admin": is_admin,
        "is_moderator": is_moderator
    }

@app.route("/admin/conversations")
@admin_required
def admin_conversations():
    with get_db_connection() as conn:
        conversations = conn.execute("""
            SELECT
                conversations.id,
                u1.username AS user1_username,
                u2.username AS user2_username,
                (
                    SELECT messages.content
                    FROM messages
                    WHERE messages.conversation_id = conversations.id
                    ORDER BY messages.created_at DESC, messages.id DESC
                    LIMIT 1
                ) AS last_message,
                (
                    SELECT messages.created_at
                    FROM messages
                    WHERE messages.conversation_id = conversations.id
                    ORDER BY messages.created_at DESC, messages.id DESC
                    LIMIT 1
                ) AS last_message_time
            FROM conversations
            JOIN users u1 ON conversations.user1_id = u1.id
            JOIN users u2 ON conversations.user2_id = u2.id
            ORDER BY last_message_time DESC, conversations.created_at DESC
        """).fetchall()

    return render_template("admin_conversations.html", conversations=conversations)


@app.route("/admin/conversation/<int:conversation_id>")
@admin_required
def admin_conversation_detail(conversation_id):
    with get_db_connection() as conn:
        conversation = conn.execute("""
            SELECT
                conversations.*,
                u1.username AS user1_username,
                u2.username AS user2_username
            FROM conversations
            JOIN users u1 ON conversations.user1_id = u1.id
            JOIN users u2 ON conversations.user2_id = u2.id
            WHERE conversations.id = ?
        """, (conversation_id,)).fetchone()

        if not conversation:
            flash("Диалог не найден")
            return redirect(url_for("admin_conversations"))

        messages = conn.execute("""
            SELECT
                messages.id,
                messages.content,
                messages.created_at,
                messages.sender_id,
                users.username
            FROM messages
            JOIN users ON users.id = messages.sender_id
            WHERE messages.conversation_id = ?
            ORDER BY messages.created_at ASC, messages.id ASC
        """, (conversation_id,)).fetchall()

    return render_template(
        "admin_conversation_detail.html",
        conversation=conversation,
        messages=messages
    )


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)