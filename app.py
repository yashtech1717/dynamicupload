# ============================================================
# YASH WORLD - Private Messaging & QA Platform
# Complete Firebase-Native Cloud Architecture
# Primary DB: Firebase Firestore
# Primary Storage: Firebase Storage
# ============================================================

import os
import json
import logging
import uuid
import base64
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote, quote
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

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

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ============================================================
# FIREBASE STORAGE & FIRESTORE CONFIGURATION
# ============================================================

firebase_initialized = False
db_firestore = None
firebase_bucket = None

def init_firebase():
    global firebase_initialized, db_firestore, firebase_bucket
    if firebase_initialized:
        return

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
            logger.error("❌ CRITICAL: No Firebase credentials found. Please set FIREBASE_SERVICE_ACCOUNT env var!")
    except Exception as e:
        logger.error(f"❌ Firebase initialization error: {e}")

# Call Firebase init
init_firebase()

# ============================================================
# FIRESTORE DATA WRAPPER & HELPER CLASSES
# ============================================================

class FirestoreDoc:
    def __init__(self, data_dict):
        if not data_dict:
            data_dict = {}
        self._data = dict(data_dict)
        for k, v in self._data.items():
            if isinstance(v, dict):
                setattr(self, k, FirestoreDoc(v))
            elif k in ('created_at', 'updated_at', 'last_login') and isinstance(v, str):
                try:
                    setattr(self, k, datetime.fromisoformat(v))
                except Exception:
                    setattr(self, k, v)
            else:
                setattr(self, k, v)

    def __getitem__(self, key):
        return getattr(self, key, None)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def to_dict(self):
        return self._data

class FirestoreUser(UserMixin):
    def __init__(self, doc_data):
        self._data = doc_data or {}
        self.id = str(self._data.get('id', ''))
        self.username = self._data.get('username', '')
        self.password_hash = self._data.get('password_hash', '')
        self.is_admin = bool(self._data.get('is_admin', False))
        self.is_friend = bool(self._data.get('is_friend', False))
        
        c_at = self._data.get('created_at')
        if isinstance(c_at, str):
            try:
                self.created_at = datetime.fromisoformat(c_at)
            except Exception:
                self.created_at = c_at
        else:
            self.created_at = c_at

        l_in = self._data.get('last_login')
        if isinstance(l_in, str):
            try:
                self.last_login = datetime.fromisoformat(l_in)
            except Exception:
                self.last_login = l_in
        else:
            self.last_login = l_in

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return str(self.id)

# ============================================================
# FIRESTORE DATA ACCESS LAYER
# ============================================================

def get_user_by_id(user_id):
    if not db_firestore or not user_id:
        return None
    try:
        doc = db_firestore.collection('users').document(str(user_id)).get()
        if doc.exists:
            return FirestoreUser(doc.to_dict())
    except Exception as e:
        logger.error(f"Error fetching user by id {user_id}: {e}")
    return None

def get_user_by_username(username):
    if not db_firestore or not username:
        return None
    try:
        docs = db_firestore.collection('users').where('username', '==', username).limit(1).get()
        for doc in docs:
            return FirestoreUser(doc.to_dict())
    except Exception as e:
        logger.error(f"Error fetching user by username {username}: {e}")
    return None

def get_all_users():
    if not db_firestore:
        return []
    try:
        docs = db_firestore.collection('users').get()
        users = [FirestoreUser(doc.to_dict()) for doc in docs]
        users.sort(key=lambda u: int(u.id) if str(u.id).isdigit() else u.id)
        return users
    except Exception as e:
        logger.error(f"Error fetching all users: {e}")
        return []

def save_user(user_dict):
    if not db_firestore:
        return None
    try:
        u_id = str(user_dict.get('id'))
        db_firestore.collection('users').document(u_id).set(user_dict, merge=True)
        return get_user_by_id(u_id)
    except Exception as e:
        logger.error(f"Error saving user: {e}")
        return None

def get_next_id(collection_name):
    if not db_firestore:
        return 1
    try:
        docs = db_firestore.collection(collection_name).get()
        max_id = 0
        for doc in docs:
            data = doc.to_dict()
            cid = data.get('id')
            if isinstance(cid, int) and cid > max_id:
                max_id = cid
            elif isinstance(cid, str) and cid.isdigit() and int(cid) > max_id:
                max_id = int(cid)
        return max_id + 1
    except Exception:
        return int(datetime.utcnow().timestamp())

def get_all_questions():
    if not db_firestore:
        return []
    try:
        docs = db_firestore.collection('questions').get()
        questions = []
        for doc in docs:
            d = doc.to_dict()
            asker = get_user_by_id(d.get('user_id'))
            if asker:
                d['asker'] = {'username': asker.username, 'id': asker.id}
            questions.append(FirestoreDoc(d))
        
        questions.sort(key=lambda q: q.created_at if isinstance(q.created_at, datetime) else datetime.min)
        return questions
    except Exception as e:
        logger.error(f"Error fetching all questions: {e}")
        return []

def get_question_by_id(question_id):
    if not db_firestore or not question_id:
        return None
    try:
        doc = db_firestore.collection('questions').document(str(question_id)).get()
        if doc.exists:
            d = doc.to_dict()
            asker = get_user_by_id(d.get('user_id'))
            if asker:
                d['asker'] = {'username': asker.username, 'id': asker.id}
            return FirestoreDoc(d)
    except Exception as e:
        logger.error(f"Error fetching question {question_id}: {e}")
    return None

def save_question(q_dict):
    if not db_firestore:
        return None
    try:
        if 'id' not in q_dict or not q_dict['id']:
            q_dict['id'] = get_next_id('questions')
        
        q_id = str(q_dict['id'])
        if 'created_at' not in q_dict or not q_dict['created_at']:
            q_dict['created_at'] = datetime.utcnow().isoformat()
        q_dict['updated_at'] = datetime.utcnow().isoformat()
        
        clean_dict = {k: v for k, v in q_dict.items() if k not in ('asker', 'replies')}
        db_firestore.collection('questions').document(q_id).set(clean_dict, merge=True)
        logger.info(f"🔥 Question {q_id} saved to Firestore")
        return get_question_by_id(q_id)
    except Exception as e:
        logger.error(f"Error saving question: {e}")
        return None

def delete_question_doc(question_id):
    if not db_firestore or not question_id:
        return False
    try:
        q = get_question_by_id(question_id)
        if q:
            for m_attr in ('image_data', 'video_data', 'audio_data', 'answer_image_data', 'answer_video_data', 'answer_audio_data'):
                url = getattr(q, m_attr, None)
                if url:
                    delete_file_from_firebase(url)
            
            replies = get_replies_for_question(question_id)
            for r in replies:
                delete_reply_doc(r.id)

            db_firestore.collection('questions').document(str(question_id)).delete()
            logger.info(f"🔥 Question {question_id} deleted from Firestore")
            return True
    except Exception as e:
        logger.error(f"Error deleting question {question_id}: {e}")
    return False

def get_replies_for_question(question_id):
    if not db_firestore or not question_id:
        return []
    try:
        docs = db_firestore.collection('replies').where('question_id', '==', int(question_id)).get()
        replies = []
        for doc in docs:
            d = doc.to_dict()
            replier = get_user_by_id(d.get('user_id'))
            if replier:
                d['replier'] = {'username': replier.username, 'id': replier.id}
            
            q = get_question_by_id(question_id)
            if q:
                d['question'] = q.to_dict()
            replies.append(FirestoreDoc(d))
        
        replies.sort(key=lambda r: r.created_at if isinstance(r.created_at, datetime) else datetime.min)
        return replies
    except Exception as e:
        logger.error(f"Error fetching replies for question {question_id}: {e}")
        return []

def get_all_replies():
    if not db_firestore:
        return []
    try:
        docs = db_firestore.collection('replies').get()
        replies = []
        for doc in docs:
            d = doc.to_dict()
            replier = get_user_by_id(d.get('user_id'))
            if replier:
                d['replier'] = {'username': replier.username, 'id': replier.id}
            
            q_id = d.get('question_id')
            if q_id:
                q = get_question_by_id(q_id)
                if q:
                    d['question'] = q.to_dict()
            replies.append(FirestoreDoc(d))
            
        replies.sort(key=lambda r: r.created_at if isinstance(r.created_at, datetime) else datetime.min)
        return replies
    except Exception as e:
        logger.error(f"Error fetching all replies: {e}")
        return []

def save_reply(r_dict):
    if not db_firestore:
        return None
    try:
        if 'id' not in r_dict or not r_dict['id']:
            r_dict['id'] = get_next_id('replies')
            
        r_id = str(r_dict['id'])
        if 'created_at' not in r_dict or not r_dict['created_at']:
            r_dict['created_at'] = datetime.utcnow().isoformat()
        r_dict['updated_at'] = datetime.utcnow().isoformat()
        
        clean_dict = {k: v for k, v in r_dict.items() if k not in ('replier', 'question')}
        db_firestore.collection('replies').document(r_id).set(clean_dict, merge=True)
        logger.info(f"🔥 Reply {r_id} saved to Firestore")
        return FirestoreDoc(r_dict)
    except Exception as e:
        logger.error(f"Error saving reply: {e}")
        return None

def delete_reply_doc(reply_id):
    if not db_firestore or not reply_id:
        return False
    try:
        doc = db_firestore.collection('replies').document(str(reply_id)).get()
        if doc.exists:
            d = doc.to_dict()
            for m_attr in ('image_data', 'video_data', 'audio_data'):
                url = d.get(m_attr)
                if url:
                    delete_file_from_firebase(url)
            db_firestore.collection('replies').document(str(reply_id)).delete()
            logger.info(f"🔥 Reply {reply_id} deleted from Firestore")
            return True
    except Exception as e:
        logger.error(f"Error deleting reply {reply_id}: {e}")
    return False

def get_all_typing_texts():
    if not db_firestore:
        return []
    try:
        docs = db_firestore.collection('typing_texts').get()
        texts = [FirestoreDoc(doc.to_dict()) for doc in docs]
        texts.sort(key=lambda t: t.created_at if isinstance(t.created_at, datetime) else datetime.min, reverse=True)
        return texts
    except Exception as e:
        logger.error(f"Error fetching typing texts: {e}")
        return []

def get_active_typing_text():
    if not db_firestore:
        return None
    try:
        docs = db_firestore.collection('typing_texts').where('is_active', '==', True).limit(1).get()
        for doc in docs:
            return FirestoreDoc(doc.to_dict())
    except Exception as e:
        logger.error(f"Error fetching active typing text: {e}")
    return None

def save_typing_text(text_str, is_active=True):
    if not db_firestore:
        return None
    try:
        if is_active:
            docs = db_firestore.collection('typing_texts').get()
            for doc in docs:
                db_firestore.collection('typing_texts').document(doc.id).update({'is_active': False})
        
        t_id = get_next_id('typing_texts')
        t_dict = {
            'id': t_id,
            'text': text_str,
            'is_active': is_active,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        db_firestore.collection('typing_texts').document(str(t_id)).set(t_dict)
        return FirestoreDoc(t_dict)
    except Exception as e:
        logger.error(f"Error saving typing text: {e}")
        return None

def update_typing_text(text_id, text_str):
    if not db_firestore or not text_id:
        return None
    try:
        db_firestore.collection('typing_texts').document(str(text_id)).update({
            'text': text_str,
            'updated_at': datetime.utcnow().isoformat()
        })
        doc = db_firestore.collection('typing_texts').document(str(text_id)).get()
        return FirestoreDoc(doc.to_dict()) if doc.exists else None
    except Exception as e:
        logger.error(f"Error updating typing text {text_id}: {e}")
        return None

def activate_typing_text(text_id):
    if not db_firestore or not text_id:
        return False
    try:
        docs = db_firestore.collection('typing_texts').get()
        for doc in docs:
            db_firestore.collection('typing_texts').document(doc.id).update({'is_active': False})
        db_firestore.collection('typing_texts').document(str(text_id)).update({'is_active': True})
        return True
    except Exception as e:
        logger.error(f"Error activating typing text {text_id}: {e}")
        return False

def delete_typing_text_doc(text_id):
    if not db_firestore or not text_id:
        return False
    try:
        db_firestore.collection('typing_texts').document(str(text_id)).delete()
        return True
    except Exception as e:
        logger.error(f"Error deleting typing text {text_id}: {e}")
        return False

def get_all_feedback_questions():
    if not db_firestore:
        return []
    try:
        docs = db_firestore.collection('feedback_questions').get()
        questions = []
        for doc in docs:
            d = doc.to_dict()
            r_docs = db_firestore.collection('feedback_responses').where('question_id', '==', d.get('id')).get()
            responses = []
            for rd in r_docs:
                r_dict = rd.to_dict()
                u = get_user_by_id(r_dict.get('user_id'))
                if u:
                    r_dict['user'] = {'username': u.username, 'id': u.id}
                responses.append(FirestoreDoc(r_dict))
            d['responses'] = responses
            questions.append(FirestoreDoc(d))
        
        questions.sort(key=lambda q: q.created_at if isinstance(q.created_at, datetime) else datetime.min, reverse=True)
        return questions
    except Exception as e:
        logger.error(f"Error fetching feedback questions: {e}")
        return []

def get_active_feedback_questions():
    all_q = get_all_feedback_questions()
    return [q for q in all_q if getattr(q, 'is_active', True)]

def save_feedback_question(question_str):
    if not db_firestore:
        return None
    try:
        q_id = get_next_id('feedback_questions')
        q_dict = {
            'id': q_id,
            'question': question_str,
            'is_active': True,
            'created_at': datetime.utcnow().isoformat()
        }
        db_firestore.collection('feedback_questions').document(str(q_id)).set(q_dict)
        return FirestoreDoc(q_dict)
    except Exception as e:
        logger.error(f"Error saving feedback question: {e}")
        return None

def toggle_feedback_question(q_id):
    if not db_firestore or not q_id:
        return False
    try:
        doc_ref = db_firestore.collection('feedback_questions').document(str(q_id))
        doc = doc_ref.get()
        if doc.exists:
            curr = doc.to_dict().get('is_active', True)
            doc_ref.update({'is_active': not curr})
            return not curr
    except Exception as e:
        logger.error(f"Error toggling feedback question {q_id}: {e}")
    return False

def delete_feedback_question_doc(q_id):
    if not db_firestore or not q_id:
        return False
    try:
        db_firestore.collection('feedback_questions').document(str(q_id)).delete()
        return True
    except Exception as e:
        logger.error(f"Error deleting feedback question {q_id}: {e}")
    return False

def save_feedback_response(user_id, question_id, rating, comment=None):
    if not db_firestore:
        return None
    try:
        r_id = get_next_id('feedback_responses')
        r_dict = {
            'id': r_id,
            'user_id': user_id,
            'question_id': question_id,
            'rating': rating,
            'comment': comment,
            'created_at': datetime.utcnow().isoformat()
        }
        db_firestore.collection('feedback_responses').document(str(r_id)).set(r_dict)
        return FirestoreDoc(r_dict)
    except Exception as e:
        logger.error(f"Error saving feedback response: {e}")
        return None

def get_all_feedback_responses():
    if not db_firestore:
        return []
    try:
        docs = db_firestore.collection('feedback_responses').get()
        responses = []
        for doc in docs:
            d = doc.to_dict()
            u = get_user_by_id(d.get('user_id'))
            if u:
                d['user'] = {'username': u.username, 'id': u.id}
            q = db_firestore.collection('feedback_questions').document(str(d.get('question_id'))).get()
            if q.exists:
                d['question'] = q.to_dict()
            responses.append(FirestoreDoc(d))
        responses.sort(key=lambda r: r.created_at if isinstance(r.created_at, datetime) else datetime.min, reverse=True)
        return responses
    except Exception as e:
        logger.error(f"Error fetching feedback responses: {e}")
        return []

def get_site_settings():
    if not db_firestore:
        return FirestoreDoc({'site_title': 'YASH WORLD', 'site_tagline': 'Private Messaging Platform'})
    try:
        doc = db_firestore.collection('site_settings').document('settings').get()
        if doc.exists:
            return FirestoreDoc(doc.to_dict())
        else:
            s_dict = {
                'site_title': 'YASH WORLD',
                'site_tagline': 'Private Messaging Platform',
                'welcome_message': '',
                'created_at': datetime.utcnow().isoformat()
            }
            db_firestore.collection('site_settings').document('settings').set(s_dict)
            return FirestoreDoc(s_dict)
    except Exception as e:
        logger.error(f"Error fetching site settings: {e}")
        return FirestoreDoc({'site_title': 'YASH WORLD', 'site_tagline': 'Private Messaging Platform'})

def save_site_settings(title, tagline, welcome):
    if not db_firestore:
        return None
    try:
        s_dict = {
            'site_title': title,
            'site_tagline': tagline,
            'welcome_message': welcome,
            'updated_at': datetime.utcnow().isoformat()
        }
        db_firestore.collection('site_settings').document('settings').set(s_dict, merge=True)
        return get_site_settings()
    except Exception as e:
        logger.error(f"Error saving site settings: {e}")
        return None

# ============================================================
# PERMANENT FIREBASE STORAGE UPLOAD & DELETE HELPERS
# ============================================================

def upload_file_to_firebase(file, media_type='video'):
    if not file or not file.filename:
        return None, None

    filename = secure_filename(file.filename)
    if not filename:
        filename = f"{media_type}_{uuid.uuid4().hex[:6]}"

    if not firebase_initialized or not firebase_bucket:
        raise RuntimeError("Firebase Storage is not initialized. Please verify FIREBASE_SERVICE_ACCOUNT and FIREBASE_STORAGE_BUCKET environment variables.")

    try:
        unique_name = f"{media_type}s/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}_{filename}"
        blob = firebase_bucket.blob(unique_name)
        file.seek(0)
        content_type = file.content_type
        if not content_type:
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            content_type = f"{media_type}/{ext}" if ext else 'application/octet-stream'

        media_token = str(uuid.uuid4())
        blob.metadata = {'firebaseStorageDownloadTokens': media_token}

        blob.upload_from_file(file, content_type=content_type)

        try:
            blob.make_public()
            url = blob.public_url
        except Exception:
            encoded_name = quote(blob.name, safe='')
            url = f"https://firebasestorage.googleapis.com/v0/b/{firebase_bucket.name}/o/{encoded_name}?alt=media&token={media_token}"

        logger.info(f"✅ File uploaded synchronously to Firebase Storage: {url}")
        return url, filename
    except Exception as e:
        logger.error(f"❌ Firebase Storage upload failed: {e}")
        raise e

def delete_file_from_firebase(url_or_path):
    if not firebase_initialized or not firebase_bucket or not url_or_path:
        return False
    try:
        if 'firebasestorage.googleapis.com' in url_or_path:
            parts = url_or_path.split('/o/')
            if len(parts) > 1:
                blob_name = unquote(parts[1].split('?')[0])
                blob = firebase_bucket.blob(blob_name)
                blob.delete()
                logger.info(f"🗑️ Deleted blob from Firebase Storage: {blob_name}")
                return True
    except Exception as e:
        logger.warning(f"Error deleting file from Firebase Storage: {e}")
    return False

# ============================================================
# LOGIN MANAGER USER LOADER
# ============================================================

@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(user_id)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('You need admin access for this page.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# ============================================================
# MEDIA HELPERS
# ============================================================

def get_media_url(data, media_type='image'):
    if not data:
        return ''
    data = data.strip()

    if data.startswith('http://') or data.startswith('https://') or data.startswith('//'):
        if data.startswith('http://'):
            data = 'https://' + data[7:]
        elif data.startswith('//'):
            data = 'https:' + data
        return data

    if data.startswith('data:'):
        return data

    if media_type == 'video':
        return f"data:video/mp4;base64,{data}"
    elif media_type == 'audio':
        return f"data:audio/mpeg;base64,{data}"
    else:
        return f"data:image/jpeg;base64,{data}"

@app.context_processor
def utility_processor():
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
        
        user = get_user_by_username(username)
        
        if user and user.check_password(password):
            login_user(user, remember=remember)
            save_user({
                'id': user.id,
                'username': user.username,
                'last_login': datetime.utcnow().isoformat()
            })
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    logout_user()
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# ============================================================
# ROUTES - DASHBOARD
# ============================================================

@app.route('/dashboard')
@login_required
def dashboard():
    all_questions = get_all_questions()
    total_questions = len(all_questions)
    all_replies = get_all_replies()
    total_replies_count = len(all_replies)
    
    is_admin = current_user.is_admin
    is_friend = current_user.is_friend
    
    typing_text = None
    show_typing = False
    show_birthday_intro = False
    
    if is_friend and not is_admin:
        typing_text = get_active_typing_text()
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
    
    current_question = all_questions[current_index] if current_index < total_questions else None
    replies = get_replies_for_question(current_question.id) if current_question else []
    
    feedback_questions = []
    if is_friend:
        feedback_questions = get_active_feedback_questions()
    
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

@app.route('/navigate-question', methods=['POST'])
@login_required
def navigate_question():
    direction = request.form.get('direction')
    current_index = session.get('current_question_index', 0)
    total_questions = len(get_all_questions())
    
    if direction == 'next':
        current_index = min(current_index + 1, total_questions - 1)
    elif direction == 'prev':
        current_index = max(current_index - 1, 0)
    
    session['current_question_index'] = current_index
    return redirect(url_for('dashboard'))

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
        
        q_data = {
            'user_id': current_user.id,
            'text': text,
            'has_answer': False,
            'is_answered': False
        }
        
        try:
            if 'image' in request.files and request.files['image'].filename:
                file = request.files['image']
                if is_allowed_image(file.filename):
                    url, fname = upload_file_to_firebase(file, 'image')
                    if url:
                        q_data['image_data'] = url
                        q_data['image_filename'] = fname
            
            image_url = request.form.get('image_url', '').strip()
            if image_url and not q_data.get('image_data'):
                q_data['image_data'] = get_media_url(image_url, 'image')
                q_data['image_filename'] = request.form.get('image_filename', 'external_image')
            
            if 'video' in request.files and request.files['video'].filename:
                file = request.files['video']
                if is_allowed_video(file.filename):
                    url, fname = upload_file_to_firebase(file, 'video')
                    if url:
                        q_data['video_data'] = url
                        q_data['video_filename'] = fname
            
            video_url = request.form.get('video_url', '').strip()
            if video_url and not q_data.get('video_data'):
                q_data['video_data'] = get_media_url(video_url, 'video')
                q_data['video_filename'] = request.form.get('video_filename', 'external_video')
            
            if 'audio' in request.files and request.files['audio'].filename:
                file = request.files['audio']
                if is_allowed_audio(file.filename):
                    url, fname = upload_file_to_firebase(file, 'audio')
                    if url:
                        q_data['audio_data'] = url
                        q_data['audio_filename'] = fname
            
            audio_url = request.form.get('audio_url', '').strip()
            if audio_url and not q_data.get('audio_data'):
                q_data['audio_data'] = get_media_url(audio_url, 'audio')
                q_data['audio_filename'] = request.form.get('audio_filename', 'external_audio')
            
            answer_text = request.form.get('answer_text', '').strip()
            if answer_text:
                q_data['answer_text'] = answer_text
                q_data['has_answer'] = True
                q_data['is_answered'] = True
                
                if 'answer_image' in request.files and request.files['answer_image'].filename:
                    file = request.files['answer_image']
                    if is_allowed_image(file.filename):
                        url, fname = upload_file_to_firebase(file, 'image')
                        if url:
                            q_data['answer_image_data'] = url
                            q_data['answer_image_filename'] = fname
                
                answer_image_url = request.form.get('answer_image_url', '').strip()
                if answer_image_url and not q_data.get('answer_image_data'):
                    q_data['answer_image_data'] = get_media_url(answer_image_url, 'image')
                    q_data['answer_image_filename'] = request.form.get('answer_image_filename', 'external_answer_image')
                
                if 'answer_video' in request.files and request.files['answer_video'].filename:
                    file = request.files['answer_video']
                    if is_allowed_video(file.filename):
                        url, fname = upload_file_to_firebase(file, 'video')
                        if url:
                            q_data['answer_video_data'] = url
                            q_data['answer_video_filename'] = fname
                
                answer_video_url = request.form.get('answer_video_url', '').strip()
                if answer_video_url and not q_data.get('answer_video_data'):
                    q_data['answer_video_data'] = get_media_url(answer_video_url, 'video')
                    q_data['answer_video_filename'] = request.form.get('answer_video_filename', 'external_answer_video')
                
                if 'answer_audio' in request.files and request.files['answer_audio'].filename:
                    file = request.files['answer_audio']
                    if is_allowed_audio(file.filename):
                        url, fname = upload_file_to_firebase(file, 'audio')
                        if url:
                            q_data['answer_audio_data'] = url
                            q_data['answer_audio_filename'] = fname
                
                answer_audio_url = request.form.get('answer_audio_url', '').strip()
                if answer_audio_url and not q_data.get('answer_audio_data'):
                    q_data['answer_audio_data'] = get_media_url(answer_audio_url, 'audio')
                    q_data['answer_audio_filename'] = request.form.get('answer_audio_filename', 'external_answer_audio')
            
            save_question(q_data)
            flash('Question asked successfully!' + (' Answer added!' if answer_text else ''), 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f"Error asking question: {e}")
            flash(f'Failed to ask question: {str(e)}', 'danger')
            return redirect(url_for('ask_question'))
    
    return render_template('ask.html')

@app.route('/reply/<int:question_id>', methods=['GET', 'POST'])
@login_required
def reply_question(question_id):
    question = get_question_by_id(question_id)
    if not question:
        flash('Question not found.', 'danger')
        return redirect(url_for('dashboard'))
    
    if not current_user.is_friend and not current_user.is_admin:
        flash('Only friend can reply.', 'danger')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        text = request.form.get('text', '').strip()
        
        if not text:
            flash('Please enter a reply.', 'danger')
            return redirect(url_for('reply_question', question_id=question_id))
        
        r_data = {
            'question_id': int(question_id),
            'user_id': current_user.id,
            'text': text
        }
        
        try:
            if 'image' in request.files and request.files['image'].filename:
                file = request.files['image']
                if is_allowed_image(file.filename):
                    url, fname = upload_file_to_firebase(file, 'image')
                    if url:
                        r_data['image_data'] = url
                        r_data['image_filename'] = fname
            
            image_url = request.form.get('image_url', '').strip()
            if image_url and not r_data.get('image_data'):
                r_data['image_data'] = get_media_url(image_url, 'image')
                r_data['image_filename'] = request.form.get('image_filename', 'external_image')
            
            if 'video' in request.files and request.files['video'].filename:
                file = request.files['video']
                if is_allowed_video(file.filename):
                    url, fname = upload_file_to_firebase(file, 'video')
                    if url:
                        r_data['video_data'] = url
                        r_data['video_filename'] = fname
            
            video_url = request.form.get('video_url', '').strip()
            if video_url and not r_data.get('video_data'):
                r_data['video_data'] = get_media_url(video_url, 'video')
                r_data['video_filename'] = request.form.get('video_filename', 'external_video')
            
            if 'audio' in request.files and request.files['audio'].filename:
                file = request.files['audio']
                if is_allowed_audio(file.filename):
                    url, fname = upload_file_to_firebase(file, 'audio')
                    if url:
                        r_data['audio_data'] = url
                        r_data['audio_filename'] = fname
            
            audio_url = request.form.get('audio_url', '').strip()
            if audio_url and not r_data.get('audio_data'):
                r_data['audio_data'] = get_media_url(audio_url, 'audio')
                r_data['audio_filename'] = request.form.get('audio_filename', 'external_audio')
            
            save_reply(r_data)
            save_question({'id': int(question_id), 'is_answered': True})
            
            flash('Reply sent successfully! Your response has been sent to the Admin Dashboard.', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            logger.error(f"Error submitting reply: {e}")
            flash(f'Failed to submit reply: {str(e)}', 'danger')
            return redirect(url_for('reply_question', question_id=question_id))
    
    return render_template('reply.html', question=question)

@app.route('/question/<int:question_id>/delete', methods=['POST'])
@login_required
def delete_question(question_id):
    if not current_user.is_admin:
        flash('Only admin can delete questions.', 'danger')
        return redirect(url_for('dashboard'))
    
    delete_question_doc(question_id)
    flash('Question deleted successfully!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/reply/<int:reply_id>/delete', methods=['POST'])
@login_required
def delete_reply(reply_id):
    if not current_user.is_admin:
        flash('Only admin can delete replies.', 'danger')
        return redirect(url_for('dashboard'))
    
    delete_reply_doc(reply_id)
    flash('Reply deleted successfully!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/question/<int:question_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_question(question_id):
    question = get_question_by_id(question_id)
    if not question:
        flash('Question not found.', 'danger')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        q_dict = question.to_dict()
        new_text = request.form.get('text', '').strip()
        if new_text:
            q_dict['text'] = new_text
        
        new_answer = request.form.get('answer_text', '').strip()
        if new_answer:
            q_dict['answer_text'] = new_answer
            q_dict['has_answer'] = True
            q_dict['is_answered'] = True
        
        if 'image' in request.files and request.files['image'].filename:
            file = request.files['image']
            if is_allowed_image(file.filename):
                url, fname = upload_file_to_firebase(file, 'image')
                if url:
                    q_dict['image_data'] = url
                    q_dict['image_filename'] = fname
                
        if 'video' in request.files and request.files['video'].filename:
            file = request.files['video']
            if is_allowed_video(file.filename):
                url, fname = upload_file_to_firebase(file, 'video')
                if url:
                    q_dict['video_data'] = url
                    q_dict['video_filename'] = fname
                
        if 'audio' in request.files and request.files['audio'].filename:
            file = request.files['audio']
            if is_allowed_audio(file.filename):
                url, fname = upload_file_to_firebase(file, 'audio')
                if url:
                    q_dict['audio_data'] = url
                    q_dict['audio_filename'] = fname

        image_url = request.form.get('image_url', '').strip()
        if image_url:
            q_dict['image_data'] = get_media_url(image_url, 'image')
            q_dict['image_filename'] = request.form.get('image_filename', 'external_image')

        video_url = request.form.get('video_url', '').strip()
        if video_url:
            q_dict['video_data'] = get_media_url(video_url, 'video')
            q_dict['video_filename'] = request.form.get('video_filename', 'external_video')

        audio_url = request.form.get('audio_url', '').strip()
        if audio_url:
            q_dict['audio_data'] = get_media_url(audio_url, 'audio')
            q_dict['audio_filename'] = request.form.get('audio_filename', 'external_audio')
        
        save_question(q_dict)
        flash('Question updated successfully!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('edit_question.html', question=question)

@app.route('/question/<int:question_id>/delete-media', methods=['POST'])
@login_required
@admin_required
def delete_question_media(question_id):
    question = get_question_by_id(question_id)
    if not question:
        flash('Question not found.', 'danger')
        return redirect(url_for('dashboard'))
        
    media_type = request.form.get('media_type')
    q_dict = question.to_dict()
    
    if media_type == 'image':
        if q_dict.get('image_data'):
            delete_file_from_firebase(q_dict.get('image_data'))
        q_dict['image_data'] = None
        q_dict['image_filename'] = None
    elif media_type == 'video':
        if q_dict.get('video_data'):
            delete_file_from_firebase(q_dict.get('video_data'))
        q_dict['video_data'] = None
        q_dict['video_filename'] = None
    elif media_type == 'audio':
        if q_dict.get('audio_data'):
            delete_file_from_firebase(q_dict.get('audio_data'))
        q_dict['audio_data'] = None
        q_dict['audio_filename'] = None
        
    save_question(q_dict)
    flash(f'{media_type.capitalize()} deleted.', 'success')
    return redirect(url_for('edit_question', question_id=question_id))

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_panel():
    questions = get_all_questions()
    total_questions = len(questions)
    answered_questions = sum(1 for q in questions if getattr(q, 'is_answered', False))
    unanswered_questions = total_questions - answered_questions
    recent_responses = get_all_replies()[:5]
    
    return render_template('admin_panel.html',
        total_questions=total_questions,
        answered_questions=answered_questions,
        unanswered_questions=unanswered_questions,
        recent_responses=recent_responses,
        questions=questions
    )

@app.route('/admin/responses')
@login_required
@admin_required
def admin_responses():
    all_replies = get_all_replies()
    total_questions = len(get_all_questions())
    answered_questions = len(set(r.question_id for r in all_replies if hasattr(r, 'question_id')))
    
    return render_template('admin_responses.html',
        all_replies=all_replies,
        total_questions=total_questions,
        answered_questions=answered_questions
    )

@app.route('/admin/typing-text', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_typing_text():
    if request.method == 'POST':
        text = request.form.get('typing_text', '').strip()
        if text:
            save_typing_text(text, is_active=True)
            flash('Typing text updated successfully!', 'success')
        else:
            flash('Please enter some text.', 'danger')
        return redirect(url_for('admin_typing_text'))
    
    typing_texts = get_all_typing_texts()
    active_text = get_active_typing_text()
    
    return render_template('admin_typing_text.html', 
        typing_texts=typing_texts,
        active_text=active_text
    )

@app.route('/admin/typing-text/<int:text_id>/activate')
@login_required
@admin_required
def admin_activate_typing_text(text_id):
    activate_typing_text(text_id)
    flash('Typing text activated!', 'success')
    return redirect(url_for('admin_typing_text'))

@app.route('/admin/typing-text/<int:text_id>/edit', methods=['POST'])
@login_required
@admin_required
def admin_edit_typing_text(text_id):
    text_content = request.form.get('typing_text', '').strip()
    if text_content:
        update_typing_text(text_id, text_content)
        flash('Typing message updated successfully!', 'success')
    else:
        flash('Text content cannot be empty.', 'danger')
    return redirect(url_for('admin_typing_text'))

@app.route('/admin/typing-text/<int:text_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_typing_text(text_id):
    delete_typing_text_doc(text_id)
    flash('Typing text deleted!', 'success')
    return redirect(url_for('admin_typing_text'))

@app.route('/admin/feedback', methods=['GET'])
@login_required
@admin_required
def admin_feedback():
    questions = get_all_feedback_questions()
    responses = get_all_feedback_responses()
    
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
        save_feedback_question(question_text)
        flash('Feedback question added successfully!', 'success')
    else:
        flash('Please enter a question.', 'danger')
    return redirect(url_for('admin_feedback'))

@app.route('/admin/feedback/toggle/<int:q_id>', methods=['POST'])
@login_required
@admin_required
def toggle_feedback_q(q_id):
    status = toggle_feedback_question(q_id)
    flash(f'Feedback question {"activated" if status else "deactivated"}!', 'success')
    return redirect(url_for('admin_feedback'))

@app.route('/admin/feedback/delete/<int:q_id>', methods=['POST'])
@login_required
@admin_required
def delete_feedback_q(q_id):
    delete_feedback_question_doc(q_id)
    flash('Feedback question deleted!', 'success')
    return redirect(url_for('admin_feedback'))

@app.route('/submit-feedback', methods=['POST'])
@login_required
def submit_feedback():
    if not current_user.is_friend:
        flash('Only friends can submit feedback.', 'danger')
        return redirect(url_for('dashboard'))
    
    existing = [r for r in get_all_feedback_responses() if getattr(r, 'user_id', None) == current_user.id]
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
                    save_feedback_response(current_user.id, int(q_id), rating, comment if comment else None)
                    saved_count += 1
            except Exception as e:
                logger.error(f"Error saving feedback rating: {e}")
    
    if saved_count > 0:
        flash('Thank you for your feedback! ❤️', 'success')
    else:
        flash('Please rate at least one question.', 'warning')
        
    return redirect(url_for('dashboard'))

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = get_all_users()
    return render_template('admin_users.html', users=users)

@app.route('/admin/user/<user_id>/toggle-friend', methods=['POST'])
@login_required
@admin_required
def toggle_friend(user_id):
    user = get_user_by_id(user_id)
    if user:
        new_status = not user.is_friend
        save_user({'id': user.id, 'is_friend': new_status})
        flash(f'Friend access {"enabled" if new_status else "disabled"} for {user.username}', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/user/<user_id>/reset-typing', methods=['POST'])
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
    settings = get_site_settings()
    
    if request.method == 'POST':
        title = request.form.get('site_title', 'YASH WORLD')
        tagline = request.form.get('site_tagline', 'Private Messaging Platform')
        welcome = request.form.get('welcome_message', '')
        save_site_settings(title, tagline, welcome)
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('admin_settings'))
    
    return render_template('admin_settings.html', settings=settings)

# ============================================================
# SEEDING & DEFAULTS INITIALIZATION
# ============================================================

def seed_firestore_defaults():
    if not db_firestore:
        return
    try:
        # 1. Admin User
        admin_username = os.environ.get('ADMIN_USERNAME', 'yash')
        admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
        if not get_user_by_username(admin_username):
            save_user({
                'id': 1,
                'username': admin_username,
                'password_hash': generate_password_hash(admin_password),
                'is_admin': True,
                'is_friend': True,
                'created_at': datetime.utcnow().isoformat()
            })
            logger.info(f"✅ Created default Firestore Admin user: {admin_username}")

        # 2. Friend User
        friend_username = os.environ.get('FRIEND_USERNAME', 'Glory')
        friend_password = os.environ.get('FRIEND_PASSWORD', 'lory')
        if not get_user_by_username(friend_username):
            save_user({
                'id': 2,
                'username': friend_username,
                'password_hash': generate_password_hash(friend_password),
                'is_admin': False,
                'is_friend': True,
                'created_at': datetime.utcnow().isoformat()
            })
            logger.info(f"✅ Created default Firestore Friend user: {friend_username}")

        # 3. Default Settings
        get_site_settings()

        # 4. Default Feedback Questions
        if len(get_all_feedback_questions()) == 0:
            default_qs = [
                "How would you rate me as a friend?",
                "How caring am I?",
                "How supportive am I?",
                "How trustworthy am I?",
                "How loyal am I?",
                "Would you recommend me as a friend?"
            ]
            for q in default_qs:
                save_feedback_question(q)
            logger.info("✅ Seeded default Firestore feedback questions")

        # 5. Default Typing Text Message
        if not get_active_typing_text():
            birthday_msg = "Happy Birthday 🎂❤️ Wishing you happiness, peace, and success always. I hope you’re happy with the new people in your life and make beautiful memories with them. Take care and stay happy. 🤍"
            save_typing_text(birthday_msg, is_active=True)
            logger.info("✅ Seeded default Firestore typing text message")

    except Exception as e:
        logger.error(f"Error seeding Firestore defaults: {e}")

# Seed defaults on app import
try:
    seed_firestore_defaults()
except Exception as e:
    logger.error(f"Error in seed_firestore_defaults: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)