# ============================================================
# YASH WORLD - Private Messaging & QA Platform
# Complete Version with Cloudinary Support
# ============================================================

import os
import json
import logging
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from sqlalchemy import func, desc, text
from sqlalchemy.orm import joinedload
import base64

# ============================================================
# LOGGING CONFIGURATION
# ============================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'yash-world-secret-key-2024')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max payload limit

# Thread pool for asynchronous background Firestore syncs
from concurrent.futures import ThreadPoolExecutor
sync_executor = ThreadPoolExecutor(max_workers=3)

# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    logger.info("✅ PostgreSQL database configured")
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///yash_world.db'
    logger.warning("⚠️ SQLite database configured")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

# ============================================================
# DATABASE MODELS
# ============================================================

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ============================================================
# FIREBASE STORAGE & FIRESTORE CONFIGURATION
# ============================================================
import uuid
from urllib.parse import quote

firebase_initialized = False
db_firestore = None
firebase_bucket = None

try:
    import firebase_admin
    from firebase_admin import credentials, storage, firestore

    cred = None
    service_account_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
    service_account_path = os.environ.get('FIREBASE_SERVICE_ACCOUNT_PATH', 'service-account.json')

    if service_account_json:
        try:
            sa_str = service_account_json.strip()
            if not sa_str.startswith('{'):
                try:
                    decoded = base64.b64decode(sa_str).decode('utf-8')
                    if decoded.strip().startswith('{'):
                        sa_str = decoded.strip()
                except Exception:
                    pass
            if (sa_str.startswith("'") and sa_str.endswith("'")) or (sa_str.startswith('"') and sa_str.endswith('"') and not sa_str.startswith('{')):
                sa_str = sa_str[1:-1].strip()
            cred_dict = json.loads(sa_str)
            if isinstance(cred_dict, dict) and 'private_key' in cred_dict:
                if isinstance(cred_dict['private_key'], str) and '\\n' in cred_dict['private_key']:
                    cred_dict['private_key'] = cred_dict['private_key'].replace('\\n', '\n')
            cred = credentials.Certificate(cred_dict)
            logger.info("🔑 Loaded Firebase credentials from FIREBASE_SERVICE_ACCOUNT env var")
        except Exception as e:
            logger.error(f"Error parsing FIREBASE_SERVICE_ACCOUNT env var: {e}")

    if not cred and os.path.exists(service_account_path):
        cred = credentials.Certificate(service_account_path)
        logger.info(f"🔑 Loaded Firebase credentials from file: {service_account_path}")

    if cred:
        storage_bucket_name = os.environ.get('FIREBASE_STORAGE_BUCKET', 'happybirthday-a287a.appspot.com')
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {
                'storageBucket': storage_bucket_name
            })
        firebase_initialized = True
        db_firestore = firestore.client()
        firebase_bucket = storage.bucket()
        logger.info("🔥 Firebase Storage & Firestore initialized successfully!")
    else:
        logger.warning("⚠️ No Firebase service account credentials found")
except Exception as e:
    logger.error(f"⚠️ Firebase initialization error: {e}")


def upload_file_to_firebase(file, media_type='video'):
    """
    Uploads a file directly from user device to Firebase Storage.
    Returns (url, filename).
    """
    if not file or not file.filename:
        return None, None

    filename = secure_filename(file.filename)
    if not filename:
        filename = f"{media_type}_{uuid.uuid4().hex[:6]}"

    # Attempt Firebase Storage upload
    if firebase_initialized and firebase_bucket:
        try:
            unique_name = f"{media_type}s/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}_{filename}"
            blob = firebase_bucket.blob(unique_name)
            file.seek(0)
            content_type = file.content_type
            if not content_type:
                ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
                content_type = f"{media_type}/{ext}" if ext else 'application/octet-stream'

            blob.upload_from_file(file, content_type=content_type)

            try:
                blob.make_public()
                url = blob.public_url
            except Exception as pe:
                logger.warning(f"Could not make blob public directly: {pe}. Using Firebase media URL.")
                encoded_name = quote(blob.name, safe='')
                url = f"https://firebasestorage.googleapis.com/v0/b/{firebase_bucket.name}/o/{encoded_name}?alt=media"

            logger.info(f"✅ File uploaded directly from device to Firebase Storage: {url}")
            return url, filename
        except Exception as e:
            logger.error(f"⚠️ Firebase Storage upload failed: {e}. Falling back to base64.")

    # Fallback to Base64
    file.seek(0)
    b64 = file_to_base64(file)
    return b64, filename


def sync_question_to_firestore(question):
    if not firebase_initialized or not db_firestore:
        return
    try:
        doc_ref = db_firestore.collection('questions').document(str(question.id))
        doc_ref.set({
            'id': question.id,
            'user_id': question.user_id,
            'text': question.text,
            'image_data': question.image_data,
            'video_data': question.video_data,
            'audio_data': question.audio_data,
            'image_filename': question.image_filename,
            'video_filename': question.video_filename,
            'audio_filename': question.audio_filename,
            'answer_text': question.answer_text,
            'answer_image_data': question.answer_image_data,
            'answer_video_data': question.answer_video_data,
            'answer_audio_data': question.answer_audio_data,
            'has_answer': question.has_answer,
            'is_answered': question.is_answered,
            'created_at': question.created_at.isoformat() if question.created_at else None,
            'updated_at': question.updated_at.isoformat() if question.updated_at else None
        }, merge=True)
        logger.info(f"🔥 Question {question.id} synced to Firestore")
    except Exception as e:
        logger.error(f"Firestore question sync error: {e}")


def sync_reply_to_firestore(reply):
    if not firebase_initialized or not db_firestore:
        return
    try:
        doc_ref = db_firestore.collection('replies').document(str(reply.id))
        doc_ref.set({
            'id': reply.id,
            'question_id': reply.question_id,
            'user_id': reply.user_id,
            'text': reply.text,
            'image_data': reply.image_data,
            'video_data': reply.video_data,
            'audio_data': reply.audio_data,
            'image_filename': reply.image_filename,
            'video_filename': reply.video_filename,
            'audio_filename': reply.audio_filename,
            'created_at': reply.created_at.isoformat() if reply.created_at else None,
            'updated_at': reply.updated_at.isoformat() if reply.updated_at else None
        }, merge=True)
        logger.info(f"🔥 Reply {reply.id} synced to Firestore")
    except Exception as e:
        logger.error(f"Firestore reply sync error: {e}")


def async_sync_question(question_id):
    def _task():
        with app.app_context():
            q = Question.query.get(question_id)
            if q:
                sync_question_to_firestore(q)
    sync_executor.submit(_task)


def async_sync_reply(reply_id):
    def _task():
        with app.app_context():
            r = Reply.query.get(reply_id)
            if r:
                sync_reply_to_firestore(r)
    sync_executor.submit(_task)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_friend = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    questions = db.relationship('Question', backref='asker', lazy=True)
    replies = db.relationship('Reply', backref='replier', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class TypingText(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    
    # Question media - can be Base64 OR Cloudinary URL
    image_data = db.Column(db.Text)
    video_data = db.Column(db.Text)
    audio_data = db.Column(db.Text)
    image_filename = db.Column(db.String(200))
    video_filename = db.Column(db.String(200))
    audio_filename = db.Column(db.String(200))
    
    # Optional Answer fields
    answer_text = db.Column(db.Text)
    answer_image_data = db.Column(db.Text)
    answer_video_data = db.Column(db.Text)
    answer_audio_data = db.Column(db.Text)
    answer_image_filename = db.Column(db.String(200))
    answer_video_filename = db.Column(db.String(200))
    answer_audio_filename = db.Column(db.String(200))
    
    has_answer = db.Column(db.Boolean, default=False)
    is_answered = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    replies = db.relationship('Reply', backref='question', cascade='all, delete-orphan', lazy=True)

class Reply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    
    # Reply media - can be Base64 OR Cloudinary URL
    image_data = db.Column(db.Text)
    video_data = db.Column(db.Text)
    audio_data = db.Column(db.Text)
    image_filename = db.Column(db.String(200))
    video_filename = db.Column(db.String(200))
    audio_filename = db.Column(db.String(200))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class FeedbackQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(500), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class FeedbackResponse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    question_id = db.Column(db.Integer, db.ForeignKey('feedback_question.id', ondelete='CASCADE'), nullable=False, index=True)
    rating = db.Column(db.Integer)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='feedback_responses', lazy=True)
    question = db.relationship('FeedbackQuestion', backref='responses', lazy=True)

class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    site_title = db.Column(db.String(200), default='YASH WORLD')
    site_tagline = db.Column(db.String(200), default='Private Messaging Platform')
    welcome_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ============================================================
# LOGIN MANAGER
# ============================================================

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('You need admin access for this page.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ============================================================
# CONTEXT PROCESSOR & MEDIA HELPERS
# ============================================================

def get_media_url(data, media_type='image'):
    if not data:
        return ''
    data = data.strip()

    # 1. Transform Cloudinary Embed Player URLs (e.g., https://player.cloudinary.com/embed/?cloud_name=...&public_id=...)
    if 'player.cloudinary.com' in data:
        try:
            parsed = urlparse(data)
            params = parse_qs(parsed.query)
            cloud_name = params.get('cloud_name', [''])[0]
            public_id = params.get('public_id', [''])[0]
            if cloud_name and public_id:
                public_id = unquote(public_id)
                ext = '' if any(public_id.lower().endswith(e) for e in ['.mp4', '.webm', '.mov', '.m4v', '.mkv', '.avi']) else '.mp4'
                return f"https://res.cloudinary.com/{cloud_name}/video/upload/{public_id}{ext}"
        except Exception as e:
            logger.error(f"Error parsing Cloudinary embed URL: {e}")

    # 2. Handle HTTP/HTTPS URLs
    if data.startswith('http://') or data.startswith('https://') or data.startswith('//'):
        if data.startswith('http://'):
            data = 'https://' + data[7:]
        elif data.startswith('//'):
            data = 'https:' + data
            
        # Cloudinary direct transformations
        if 'res.cloudinary.com' in data:
            # Fix resource type if saved under /image/upload/ for video
            if media_type == 'video' and '/image/upload/' in data:
                data = data.replace('/image/upload/', '/video/upload/')
            
            # Ensure video URLs have direct video extensions (.mp4) for HTML5 video tag compatibility
            if media_type == 'video' and '/video/upload/' in data:
                base_path = data.split('?')[0] if '?' in data else data
                if not any(base_path.lower().endswith(ext) for ext in ['.mp4', '.webm', '.mov', '.m4v', '.mkv', '.avi', '.ogv', '.flv']):
                    if '?' in data:
                        parts = data.split('?', 1)
                        data = f"{parts[0]}.mp4?{parts[1]}"
                    else:
                        data = f"{data}.mp4"
                        
        return data

    # 3. Check if it already has a data: prefix
    if data.startswith('data:'):
        return data

    # 4. Otherwise, it's raw Base64 data
    if media_type == 'video':
        return f"data:video/mp4;base64,{data}"
    elif media_type == 'audio':
        return f"data:audio/mpeg;base64,{data}"
    else:
        return f"data:image/jpeg;base64,{data}"

@app.context_processor
def utility_processor():
    def get_site_settings():
        settings = SiteSettings.query.first()
        if not settings:
            settings = SiteSettings()
            db.session.add(settings)
            db.session.commit()
        return settings
    return dict(get_site_settings=get_site_settings, get_media_url=get_media_url)

@app.template_filter('media_url')
def media_url_filter(data, media_type='image'):
    return get_media_url(data, media_type)

ALLOWED_IMAGES = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_VIDEOS = {'mp4', 'webm', 'mov', 'avi', 'mkv'}
ALLOWED_AUDIO = {'mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'}

def get_file_extension(filename):
    return filename.rsplit('.', 1)[1].lower() if '.' in filename else ''

def is_allowed_image(filename):
    return get_file_extension(filename) in ALLOWED_IMAGES

def is_allowed_video(filename):
    return get_file_extension(filename) in ALLOWED_VIDEOS

def is_allowed_audio(filename):
    return get_file_extension(filename) in ALLOWED_AUDIO

def file_to_base64(file):
    if file and file.filename:
        try:
            file_data = file.read()
            base64_data = base64.b64encode(file_data).decode('utf-8')
            return base64_data
        except Exception as e:
            logger.error(f"Error converting file to base64: {e}")
            return None
    return None

# ============================================================
# ROUTES - AUTHENTICATION
# ============================================================

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    try:
        logout_user()
    except Exception as e:
        logger.error(f"Error logging out user: {e}")
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# ============================================================
# ROUTES - DASHBOARD
# ============================================================

@app.route('/dashboard')
@login_required
def dashboard():
    total_questions = Question.query.count()
    total_replies_count = Reply.query.count()
    is_admin = current_user.is_admin
    is_friend = current_user.is_friend
    
    # Get active typing text and birthday intro state for friend
    typing_text = None
    show_typing = False
    show_birthday_intro = False
    
    if is_friend and not is_admin:
        typing_text = TypingText.query.filter_by(is_active=True).first()
        seen_birthday = session.get('seen_birthday_' + str(current_user.id), False)
        if not seen_birthday:
            show_birthday_intro = True
        
        seen_typing = session.get('seen_typing_' + str(current_user.id), False)
        if typing_text and not seen_typing:
            show_typing = True
        else:
            show_typing = False
    
    current_index = session.get('current_question_index', 0)
    
    if total_questions == 0:
        return render_template('dashboard.html', 
            questions=[],
            current_question=None,
            current_index=0,
            total_questions=0,
            total_replies_count=total_replies_count,
            is_admin=is_admin,
            is_friend=is_friend,
            current_user=current_user,
            feedback_questions=[],
            typing_text=typing_text,
            show_typing=show_typing,
            show_birthday_intro=show_birthday_intro
        )
    
    if current_index >= total_questions:
        current_index = 0
        session['current_question_index'] = 0
    
    current_question = Question.query.order_by(Question.created_at.asc()).offset(current_index).first()
    replies = Reply.query.filter_by(question_id=current_question.id).order_by(Reply.created_at.asc()).all() if current_question else []
    
    feedback_questions = []
    if is_friend:
        feedback_questions = FeedbackQuestion.query.filter_by(is_active=True).all()
    
    return render_template('dashboard.html', 
        questions=[current_question] if current_question else [],
        current_question=current_question,
        current_index=current_index,
        total_questions=total_questions,
        total_replies_count=total_replies_count,
        replies=replies,
        is_admin=is_admin,
        is_friend=is_friend,
        current_user=current_user,
        feedback_questions=feedback_questions,
        typing_text=typing_text,
        show_typing=show_typing,
        show_birthday_intro=show_birthday_intro
    )

# ============================================================
# ROUTES - NAVIGATE QUESTIONS
# ============================================================

@app.route('/navigate-question', methods=['POST'])
@login_required
def navigate_question():
    direction = request.form.get('direction')
    current_index = session.get('current_question_index', 0)
    total_questions = Question.query.count()
    
    if direction == 'next':
        current_index = min(current_index + 1, total_questions - 1)
    elif direction == 'prev':
        current_index = max(current_index - 1, 0)
    
    session['current_question_index'] = current_index
    return redirect(url_for('dashboard'))

# ============================================================
# ROUTES - SEEN TYPING & BIRTHDAY
# ============================================================

@app.route('/seen-typing', methods=['POST'])
@login_required
def seen_typing():
    session['seen_typing_' + str(current_user.id)] = True
    session.modified = True
    return jsonify({'success': True})

@app.route('/seen-birthday', methods=['POST'])
@login_required
def seen_birthday():
    session['seen_birthday_' + str(current_user.id)] = True
    session.modified = True
    return jsonify({'success': True})

# ============================================================
# ROUTES - ASK QUESTION
# ============================================================

@app.route('/ask', methods=['GET', 'POST'])
@login_required
def ask_question():
    if not current_user.is_admin:
        flash('Only admin can ask questions.', 'danger')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        text = request.form.get('text', '').strip()
        
        if not text:
            flash('Please enter a question.', 'danger')
            return redirect(url_for('ask_question'))
        
        question = Question(
            user_id=current_user.id,
            text=text
        )
        
        # Handle Question Media - Direct device upload to Firebase Storage
        
        # Image - File Upload
        if 'image' in request.files and request.files['image'].filename:
            file = request.files['image']
            if is_allowed_image(file.filename):
                url, fname = upload_file_to_firebase(file, 'image')
                if url:
                    question.image_data = url
                    question.image_filename = fname
        
        # Image - Optional Fallback URL
        image_url = request.form.get('image_url', '').strip()
        if image_url and not question.image_data:
            question.image_data = get_media_url(image_url, 'image')
            question.image_filename = request.form.get('image_filename', 'external_image')
        
        # Video - File Upload (Direct from device)
        if 'video' in request.files and request.files['video'].filename:
            file = request.files['video']
            if is_allowed_video(file.filename):
                url, fname = upload_file_to_firebase(file, 'video')
                if url:
                    question.video_data = url
                    question.video_filename = fname
        
        # Video - Optional Fallback URL
        video_url = request.form.get('video_url', '').strip()
        if video_url and not question.video_data:
            question.video_data = get_media_url(video_url, 'video')
            question.video_filename = request.form.get('video_filename', 'external_video')
        
        # Audio - File Upload
        if 'audio' in request.files and request.files['audio'].filename:
            file = request.files['audio']
            if is_allowed_audio(file.filename):
                url, fname = upload_file_to_firebase(file, 'audio')
                if url:
                    question.audio_data = url
                    question.audio_filename = fname
        
        # Audio - Optional Fallback URL
        audio_url = request.form.get('audio_url', '').strip()
        if audio_url and not question.audio_data:
            question.audio_data = get_media_url(audio_url, 'audio')
            question.audio_filename = request.form.get('audio_filename', 'external_audio')
        
        # Answer Section
        answer_text = request.form.get('answer_text', '').strip()
        if answer_text:
            question.answer_text = answer_text
            question.has_answer = True
            question.is_answered = True
            
            # Answer Image - File Upload
            if 'answer_image' in request.files and request.files['answer_image'].filename:
                file = request.files['answer_image']
                if is_allowed_image(file.filename):
                    url, fname = upload_file_to_firebase(file, 'image')
                    if url:
                        question.answer_image_data = url
                        question.answer_image_filename = fname
            
            # Answer Image - URL
            answer_image_url = request.form.get('answer_image_url', '').strip()
            if answer_image_url and not question.answer_image_data:
                question.answer_image_data = get_media_url(answer_image_url, 'image')
                question.answer_image_filename = request.form.get('answer_image_filename', 'external_answer_image')
            
            # Answer Video - File Upload (Direct from device)
            if 'answer_video' in request.files and request.files['answer_video'].filename:
                file = request.files['answer_video']
                if is_allowed_video(file.filename):
                    url, fname = upload_file_to_firebase(file, 'video')
                    if url:
                        question.answer_video_data = url
                        question.answer_video_filename = fname
            
            # Answer Video - URL
            answer_video_url = request.form.get('answer_video_url', '').strip()
            if answer_video_url and not question.answer_video_data:
                question.answer_video_data = get_media_url(answer_video_url, 'video')
                question.answer_video_filename = request.form.get('answer_video_filename', 'external_answer_video')
            
            # Answer Audio - File Upload
            if 'answer_audio' in request.files and request.files['answer_audio'].filename:
                file = request.files['answer_audio']
                if is_allowed_audio(file.filename):
                    url, fname = upload_file_to_firebase(file, 'audio')
                    if url:
                        question.answer_audio_data = url
                        question.answer_audio_filename = fname
            
            # Answer Audio - URL
            answer_audio_url = request.form.get('answer_audio_url', '').strip()
            if answer_audio_url and not question.answer_audio_data:
                question.answer_audio_data = get_media_url(answer_audio_url, 'audio')
                question.answer_audio_filename = request.form.get('answer_audio_filename', 'external_answer_audio')
        
        db.session.add(question)
        db.session.commit()
        async_sync_question(question.id)
        
        flash('Question asked successfully!' + (' Answer added!' if answer_text else ''), 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('ask.html')

# ============================================================
# ROUTES - REPLY TO QUESTION
# ============================================================

@app.route('/reply/<int:question_id>', methods=['GET', 'POST'])
@login_required
def reply_question(question_id):
    question = Question.query.get_or_404(question_id)
    
    if not current_user.is_friend and not current_user.is_admin:
        flash('Only friend can reply.', 'danger')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        text = request.form.get('text', '').strip()
        
        if not text:
            flash('Please enter a reply.', 'danger')
            return redirect(url_for('reply_question', question_id=question_id))
        
        reply = Reply(
            question_id=question_id,
            user_id=current_user.id,
            text=text
        )
        
        # Reply Image - File Upload
        if 'image' in request.files and request.files['image'].filename:
            file = request.files['image']
            if is_allowed_image(file.filename):
                url, fname = upload_file_to_firebase(file, 'image')
                if url:
                    reply.image_data = url
                    reply.image_filename = fname
        
        # Reply Image - URL
        image_url = request.form.get('image_url', '').strip()
        if image_url and not reply.image_data:
            reply.image_data = get_media_url(image_url, 'image')
            reply.image_filename = request.form.get('image_filename', 'external_image')
        
        # Reply Video - Direct Device File Upload
        if 'video' in request.files and request.files['video'].filename:
            file = request.files['video']
            if is_allowed_video(file.filename):
                url, fname = upload_file_to_firebase(file, 'video')
                if url:
                    reply.video_data = url
                    reply.video_filename = fname
        
        # Reply Video - URL
        video_url = request.form.get('video_url', '').strip()
        if video_url and not reply.video_data:
            reply.video_data = get_media_url(video_url, 'video')
            reply.video_filename = request.form.get('video_filename', 'external_video')
        
        # Reply Audio - File Upload
        if 'audio' in request.files and request.files['audio'].filename:
            file = request.files['audio']
            if is_allowed_audio(file.filename):
                url, fname = upload_file_to_firebase(file, 'audio')
                if url:
                    reply.audio_data = url
                    reply.audio_filename = fname
        
        # Reply Audio - URL
        audio_url = request.form.get('audio_url', '').strip()
        if audio_url and not reply.audio_data:
            reply.audio_data = get_media_url(audio_url, 'audio')
            reply.audio_filename = request.form.get('audio_filename', 'external_audio')
        
        db.session.add(reply)
        question.is_answered = True
        db.session.commit()
        
        async_sync_reply(reply.id)
        async_sync_question(question.id)
        
        flash('Reply sent successfully! Your response has been sent to the Admin Dashboard.', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('reply.html', question=question)

# ============================================================
# ROUTES - DELETE QUESTION & REPLY
# ============================================================

@app.route('/question/<int:question_id>/delete', methods=['POST'])
@login_required
def delete_question(question_id):
    if not current_user.is_admin:
        flash('Only admin can delete questions.', 'danger')
        return redirect(url_for('dashboard'))
    
    question = Question.query.get_or_404(question_id)
    db.session.delete(question)
    db.session.commit()
    flash('Question deleted successfully!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/reply/<int:reply_id>/delete', methods=['POST'])
@login_required
def delete_reply(reply_id):
    if not current_user.is_admin:
        flash('Only admin can delete replies.', 'danger')
        return redirect(url_for('dashboard'))
    
    reply = Reply.query.get_or_404(reply_id)
    db.session.delete(reply)
    db.session.commit()
    flash('Reply deleted successfully!', 'success')
    return redirect(url_for('dashboard'))

# ============================================================
# ROUTES - EDIT QUESTION
# ============================================================

@app.route('/question/<int:question_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_question(question_id):
    question = Question.query.get_or_404(question_id)
    
    if request.method == 'POST':
        new_text = request.form.get('text', '').strip()
        if new_text:
            question.text = new_text
        
        new_answer = request.form.get('answer_text', '').strip()
        if new_answer:
            question.answer_text = new_answer
            question.has_answer = True
            question.is_answered = True
        
        # Direct File Uploads from Device - Question Media
        if 'image' in request.files and request.files['image'].filename:
            file = request.files['image']
            if is_allowed_image(file.filename):
                url, fname = upload_file_to_firebase(file, 'image')
                if url:
                    question.image_data = url
                    question.image_filename = fname
                
        if 'video' in request.files and request.files['video'].filename:
            file = request.files['video']
            if is_allowed_video(file.filename):
                url, fname = upload_file_to_firebase(file, 'video')
                if url:
                    question.video_data = url
                    question.video_filename = fname
                
        if 'audio' in request.files and request.files['audio'].filename:
            file = request.files['audio']
            if is_allowed_audio(file.filename):
                url, fname = upload_file_to_firebase(file, 'audio')
                if url:
                    question.audio_data = url
                    question.audio_filename = fname

        # External URLs - Question Media
        image_url = request.form.get('image_url', '').strip()
        if image_url:
            question.image_data = get_media_url(image_url, 'image')
            question.image_filename = request.form.get('image_filename', 'external_image')

        video_url = request.form.get('video_url', '').strip()
        if video_url:
            question.video_data = get_media_url(video_url, 'video')
            question.video_filename = request.form.get('video_filename', 'external_video')

        audio_url = request.form.get('audio_url', '').strip()
        if audio_url:
            question.audio_data = get_media_url(audio_url, 'audio')
            question.audio_filename = request.form.get('audio_filename', 'external_audio')
        
        # External URLs - Answer Media
        answer_image_url = request.form.get('answer_image_url', '').strip()
        if answer_image_url:
            question.answer_image_data = get_media_url(answer_image_url, 'image')
            question.answer_image_filename = request.form.get('answer_image_filename', 'external_answer_image')

        answer_video_url = request.form.get('answer_video_url', '').strip()
        if answer_video_url:
            question.answer_video_data = get_media_url(answer_video_url, 'video')
            question.answer_video_filename = request.form.get('answer_video_filename', 'external_answer_video')

        answer_audio_url = request.form.get('answer_audio_url', '').strip()
        if answer_audio_url:
            question.answer_audio_data = get_media_url(answer_audio_url, 'audio')
            question.answer_audio_filename = request.form.get('answer_audio_filename', 'external_answer_audio')
        
        db.session.commit()
        async_sync_question(question.id)
        flash('Question updated successfully!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('edit_question.html', question=question)

@app.route('/question/<int:question_id>/delete-media', methods=['POST'])
@login_required
@admin_required
def delete_question_media(question_id):
    question = Question.query.get_or_404(question_id)
    media_type = request.form.get('media_type')
    
    if media_type == 'image':
        question.image_data = None
        question.image_filename = None
    elif media_type == 'video':
        question.video_data = None
        question.video_filename = None
    elif media_type == 'audio':
        question.audio_data = None
        question.audio_filename = None
    elif media_type == 'answer_image':
        question.answer_image_data = None
        question.answer_image_filename = None
    elif media_type == 'answer_video':
        question.answer_video_data = None
        question.answer_video_filename = None
    elif media_type == 'answer_audio':
        question.answer_audio_data = None
        question.answer_audio_filename = None
        
    db.session.commit()
    flash('Media deleted successfully!', 'success')
    return redirect(url_for('edit_question', question_id=question_id))

# ============================================================
# ROUTES - TYPING TEXT ADMIN
# ============================================================

@app.route('/admin/typing-text', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_typing_text():
    if request.method == 'POST':
        text = request.form.get('typing_text', '').strip()
        if text:
            TypingText.query.update({TypingText.is_active: False})
            new_text = TypingText(text=text, is_active=True)
            db.session.add(new_text)
            db.session.commit()
            flash('Typing text updated successfully!', 'success')
        else:
            flash('Please enter some text.', 'danger')
        return redirect(url_for('admin_typing_text'))
    
    typing_texts = TypingText.query.order_by(TypingText.created_at.desc()).all()
    active_text = TypingText.query.filter_by(is_active=True).first()
    
    return render_template('admin_typing_text.html', 
        typing_texts=typing_texts,
        active_text=active_text
    )

@app.route('/admin/typing-text/<int:text_id>/activate')
@login_required
@admin_required
def admin_activate_typing_text(text_id):
    TypingText.query.update({TypingText.is_active: False})
    text = TypingText.query.get_or_404(text_id)
    text.is_active = True
    db.session.commit()
    flash('Typing text activated!', 'success')
    return redirect(url_for('admin_typing_text'))

@app.route('/admin/typing-text/<int:text_id>/edit', methods=['POST'])
@login_required
@admin_required
def admin_edit_typing_text(text_id):
    typing_obj = TypingText.query.get_or_404(text_id)
    text_content = request.form.get('typing_text', '').strip()
    if text_content:
        typing_obj.text = text_content
        db.session.commit()
        flash('Typing message updated successfully!', 'success')
    else:
        flash('Text content cannot be empty.', 'danger')
    return redirect(url_for('admin_typing_text'))

@app.route('/admin/typing-text/<int:text_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_typing_text(text_id):
    text = TypingText.query.get_or_404(text_id)
    db.session.delete(text)
    db.session.commit()
    flash('Typing text deleted!', 'success')
    return redirect(url_for('admin_typing_text'))

# ============================================================
# ROUTES - FEEDBACK SYSTEM
# ============================================================

@app.route('/admin/feedback', methods=['GET'])
@login_required
@admin_required
def admin_feedback():
    questions = FeedbackQuestion.query.order_by(FeedbackQuestion.created_at.desc()).all()
    responses = FeedbackResponse.query.order_by(FeedbackResponse.created_at.desc()).all()
    
    return render_template('admin_feedback.html', 
        questions=questions,
        responses=responses
    )

@app.route('/admin/feedback/add', methods=['POST'])
@login_required
@admin_required
def add_feedback_question():
    question_text = request.form.get('question', '').strip()
    if question_text:
        new_question = FeedbackQuestion(question=question_text)
        db.session.add(new_question)
        db.session.commit()
        flash('Feedback question added successfully!', 'success')
    else:
        flash('Please enter a question.', 'danger')
    return redirect(url_for('admin_feedback'))

@app.route('/admin/feedback/toggle/<int:q_id>', methods=['POST'])
@login_required
@admin_required
def toggle_feedback_question(q_id):
    question = FeedbackQuestion.query.get_or_404(q_id)
    question.is_active = not question.is_active
    db.session.commit()
    status = 'activated' if question.is_active else 'deactivated'
    flash(f'Feedback question {status}!', 'success')
    return redirect(url_for('admin_feedback'))

@app.route('/admin/feedback/delete/<int:q_id>', methods=['POST'])
@login_required
@admin_required
def delete_feedback_question(q_id):
    question = FeedbackQuestion.query.get_or_404(q_id)
    db.session.delete(question)
    db.session.commit()
    flash('Feedback question deleted!', 'success')
    return redirect(url_for('admin_feedback'))

@app.route('/submit-feedback', methods=['POST'])
@login_required
def submit_feedback():
    if not current_user.is_friend:
        flash('Only friends can submit feedback.', 'danger')
        return redirect(url_for('dashboard'))
    
    existing = FeedbackResponse.query.filter_by(user_id=current_user.id).first()
    if existing:
        flash('You have already submitted feedback.', 'warning')
        return redirect(url_for('dashboard'))
    
    question_ids = request.form.getlist('question_id')
    comment = request.form.get('comment', '').strip()
    
    saved_count = 0
    for q_id in question_ids:
        rating_key = f'rating_{q_id}'
        rating_value = request.form.get(rating_key)
        
        if q_id and rating_value:
            try:
                rating = int(rating_value)
                if rating > 0:
                    response = FeedbackResponse(
                        user_id=current_user.id,
                        question_id=int(q_id),
                        rating=rating,
                        comment=comment if comment else None
                    )
                    db.session.add(response)
                    saved_count += 1
            except ValueError:
                pass
    
    if saved_count > 0:
        db.session.commit()
        flash(f'Thank you for your feedback! 🌟 ({saved_count} responses saved)', 'success')
    else:
        flash('No ratings were submitted. Please try again.', 'danger')
    
    return redirect(url_for('dashboard'))

# ============================================================
# ROUTES - ADMIN RESPONSES
# ============================================================

@app.route('/admin')
@app.route('/admin/dashboard')
@app.route('/admin/responses')
@login_required
@admin_required
def admin_responses():
    all_replies = Reply.query.options(
        joinedload(Reply.question),
        joinedload(Reply.replier)
    ).order_by(Reply.created_at.desc()).all()
    total_questions = Question.query.count()
    unique_questions = db.session.query(Reply.question_id).distinct().count()
    
    completion_percentage = 0
    if total_questions > 0:
        completion_percentage = int((unique_questions / total_questions) * 100)
    
    return render_template('admin_responses.html',
        all_replies=all_replies,
        total_replies=len(all_replies),
        unique_questions=unique_questions,
        total_questions=total_questions,
        completion_percentage=completion_percentage
    )

# ============================================================
# ROUTES - ADMIN USERS
# ============================================================

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.all()
    return render_template('admin_users.html', users=users)

@app.route('/admin/user/<int:user_id>/toggle-friend', methods=['POST'])
@login_required
@admin_required
def toggle_friend(user_id):
    user = User.query.get_or_404(user_id)
    user.is_friend = not user.is_friend
    db.session.commit()
    status = 'enabled' if user.is_friend else 'disabled'
    flash(f'Friend access {status} for {user.username}', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<int:user_id>/reset-typing', methods=['POST'])
@login_required
@admin_required
def reset_typing(user_id):
    session.pop('seen_typing_' + str(user_id), None)
    flash('Typing animation reset for user.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_settings():
    settings = SiteSettings.query.first()
    if not settings:
        settings = SiteSettings()
        db.session.add(settings)
        db.session.commit()
    
    if request.method == 'POST':
        settings.site_title = request.form.get('site_title', 'YASH WORLD')
        settings.site_tagline = request.form.get('site_tagline', 'Private Messaging Platform')
        settings.welcome_message = request.form.get('welcome_message', '')
        db.session.commit()
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('admin_settings'))
    
    return render_template('admin_settings.html', settings=settings)

# ============================================================
# ROUTES - MEDIA SERVE (Deprecated - kept for compatibility)
# ============================================================

@app.route('/media/question/image/<int:question_id>')
def question_image(question_id):
    question = Question.query.get_or_404(question_id)
    if question.image_data:
        if question.image_data.startswith('http'):
            return redirect(question.image_data)
        return question.image_data, 200, {'Content-Type': 'image/jpeg'}
    return '', 404

@app.route('/media/question/video/<int:question_id>')
def question_video(question_id):
    question = Question.query.get_or_404(question_id)
    if question.video_data:
        if question.video_data.startswith('http'):
            return redirect(question.video_data)
        return question.video_data, 200, {'Content-Type': 'video/mp4'}
    return '', 404

@app.route('/media/question/audio/<int:question_id>')
def question_audio(question_id):
    question = Question.query.get_or_404(question_id)
    if question.audio_data:
        if question.audio_data.startswith('http'):
            return redirect(question.audio_data)
        return question.audio_data, 200, {'Content-Type': 'audio/mpeg'}
    return '', 404

@app.route('/media/reply/image/<int:reply_id>')
def reply_image(reply_id):
    reply = Reply.query.get_or_404(reply_id)
    if reply.image_data:
        if reply.image_data.startswith('http'):
            return redirect(reply.image_data)
        return reply.image_data, 200, {'Content-Type': 'image/jpeg'}
    return '', 404

@app.route('/media/reply/video/<int:reply_id>')
def reply_video(reply_id):
    reply = Reply.query.get_or_404(reply_id)
    if reply.video_data:
        if reply.video_data.startswith('http'):
            return redirect(reply.video_data)
        return reply.video_data, 200, {'Content-Type': 'video/mp4'}
    return '', 404

@app.route('/media/reply/audio/<int:reply_id>')
def reply_audio(reply_id):
    reply = Reply.query.get_or_404(reply_id)
    if reply.audio_data:
        if reply.audio_data.startswith('http'):
            return redirect(reply.audio_data)
        return reply.audio_data, 200, {'Content-Type': 'audio/mpeg'}
    return '', 404

# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):
    return render_template('error.html', error_code=404, message='Page not found'), 404

@app.errorhandler(500)
def server_error(error):
    return render_template('error.html', error_code=500, message='Internal server error'), 500

# ============================================================
# DATABASE INITIALIZATION
# ============================================================

ADMIN_USERNAME = "yash"
ADMIN_PASSWORD = "admin123"
FRIEND_USERNAME = "Glory"
FRIEND_PASSWORD = "lory"

def init_db():
    with app.app_context():
        try:
            db.create_all()
            logger.info("✅ Database tables created/verified")
            
            # Create admin user
            admin = User.query.filter_by(username=ADMIN_USERNAME).first()
            if not admin:
                admin = User(username=ADMIN_USERNAME, is_admin=True, is_friend=True)
                admin.set_password(ADMIN_PASSWORD)
                db.session.add(admin)
                db.session.commit()
                logger.info(f"✅ Admin user created: {ADMIN_USERNAME} / {ADMIN_PASSWORD}")
            else:
                logger.info(f"✅ Admin user already exists: {ADMIN_USERNAME}")
            
            # Create friend user
            friend = User.query.filter_by(username=FRIEND_USERNAME).first()
            if not friend:
                friend = User(username=FRIEND_USERNAME, is_admin=False, is_friend=True)
                friend.set_password(FRIEND_PASSWORD)
                db.session.add(friend)
                db.session.commit()
                logger.info(f"✅ Friend user created: {FRIEND_USERNAME} / {FRIEND_PASSWORD}")
            else:
                logger.info(f"✅ Friend user already exists: {FRIEND_USERNAME}")
            
            # Create default settings
            settings = SiteSettings.query.first()
            if not settings:
                settings = SiteSettings(
                    site_title='YASH WORLD',
                    site_tagline='Private Messaging & QA Platform'
                )
                db.session.add(settings)
                db.session.commit()
                logger.info("✅ Default settings created")
            
            # Create default feedback questions
            if FeedbackQuestion.query.count() == 0:
                default_questions = [
                    "How would you rate me as a friend?",
                    "How caring am I?",
                    "How supportive am I?",
                    "How trustworthy am I?",
                    "How loyal am I?",
                    "Would you recommend me as a friend?"
                ]
                for q in default_questions:
                    fq = FeedbackQuestion(question=q, is_active=True)
                    db.session.add(fq)
                db.session.commit()
                logger.info("✅ Default feedback questions created")
            
            # Create default typing text if none exists
            if TypingText.query.count() == 0:
                default_typing = TypingText(
                    text="Happy Birthday 🎂❤️ Wishing you lots of happiness, peace, and success in life. Take care and stay happy always. And from now on, just forget about me. 🙂",
                    is_active=True
                )
                db.session.add(default_typing)
                db.session.commit()
                logger.info("✅ Default typing text created")
            
            # Auto-migrate any existing player.cloudinary.com embed links in DB
            try:
                questions = Question.query.all()
                updated_q = 0
                for q in questions:
                    if q.video_data and ('player.cloudinary.com' in q.video_data or (q.video_data.startswith('http') and not q.video_data.endswith('.mp4'))):
                        new_url = get_media_url(q.video_data, 'video')
                        if new_url != q.video_data:
                            q.video_data = new_url
                            updated_q += 1
                    if q.answer_video_data and ('player.cloudinary.com' in q.answer_video_data or (q.answer_video_data.startswith('http') and not q.answer_video_data.endswith('.mp4'))):
                        new_url = get_media_url(q.answer_video_data, 'video')
                        if new_url != q.answer_video_data:
                            q.answer_video_data = new_url
                            updated_q += 1
                replies = Reply.query.all()
                updated_r = 0
                for r in replies:
                    if r.video_data and ('player.cloudinary.com' in r.video_data or (r.video_data.startswith('http') and not r.video_data.endswith('.mp4'))):
                        new_url = get_media_url(r.video_data, 'video')
                        if new_url != r.video_data:
                            r.video_data = new_url
                            updated_r += 1
                if updated_q > 0 or updated_r > 0:
                    db.session.commit()
                    logger.info(f"✅ Converted {updated_q} question videos and {updated_r} reply videos to direct stream URLs")
            except Exception as e:
                logger.error(f"⚠️ Error migrating database video URLs: {e}")
            
            logger.info("\n" + "="*60)
            logger.info("🚀 YASH WORLD - Private Messaging & QA Platform")
            logger.info("="*60)
            logger.info(f"📊 Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
            logger.info(f"🔑 Admin: {ADMIN_USERNAME} / {ADMIN_PASSWORD}")
            logger.info(f"👤 Friend: {FRIEND_USERNAME} / {FRIEND_PASSWORD}")
            logger.info("💾 ALL DATA stored in Database - PERMANENT!")
            logger.info("📹 Videos support both Base64 and Cloudinary URLs")
            logger.info("="*60 + "\n")
            
        except Exception as e:
            logger.error(f"❌ Database initialization error: {e}")
            db.session.rollback()

# Auto-initialize database tables on app import (e.g. Gunicorn / Render production environment)
try:
    init_db()
except Exception as e:
    logger.error(f"Error during app startup init_db: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)