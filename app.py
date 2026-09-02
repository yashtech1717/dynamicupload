# ============================================================
# YASH WORLD - Private Messaging & QA Platform
# Complete Supabase-Native Cloud Architecture
# Primary DB: Supabase PostgreSQL
# Primary Storage: Supabase Storage
# ============================================================

import os
import json
import logging
import uuid
from datetime import datetime, date
from urllib.parse import urlparse, unquote
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
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'yash-world-secret-key-2026-static-production-key-v1')
app.config['REMEMBER_COOKIE_NAME'] = 'yash_remember_token'
app.config['SESSION_PROTECTION'] = 'basic'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max payload limit

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ============================================================
# DUAL DATABASE & STORAGE CONFIGURATION (SUPABASE + FIREBASE)
# ============================================================

supabase_initialized = False
supabase_client = None
supabase_url = None
supabase_key = None
supabase_bucket_name = 'media'

firebase_initialized = False
db_firestore = None
firebase_bucket = None

def init_supabase():
    global supabase_initialized, supabase_client, supabase_url, supabase_key, supabase_bucket_name
    if supabase_initialized:
        return True

    try:
        from supabase import create_client, Client

        supabase_url = os.environ.get('SUPABASE_URL', '').strip()
        supabase_key = os.environ.get('SUPABASE_KEY', '').strip()
        supabase_bucket_name = os.environ.get('SUPABASE_BUCKET', 'media').strip()

        if not supabase_url or not supabase_key:
            env_file = os.path.join(os.path.dirname(__file__), '.env')
            if os.path.exists(env_file):
                with open(env_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.startswith('SUPABASE_URL='):
                            supabase_url = line.split('=', 1)[1].strip()
                        elif line.startswith('SUPABASE_KEY='):
                            supabase_key = line.split('=', 1)[1].strip()

        if supabase_url and supabase_key:
            supabase_client = create_client(supabase_url, supabase_key)
            supabase_initialized = True
            logger.info(f"⚡ Supabase PostgreSQL ({supabase_url}) & Storage ('{supabase_bucket_name}') initialized successfully!")
            return True
        else:
            logger.warning("⚠️ No Supabase credentials found in env. Falling back to Firebase / local storage.")
            return False
    except Exception as e:
        logger.warning(f"⚠️ Supabase initialization warning: {e}")
        return False

def init_firebase():
    global firebase_initialized, db_firestore, firebase_bucket
    if firebase_initialized:
        return True

    try:
        import base64
        import firebase_admin
        from firebase_admin import credentials, storage, firestore

        service_account_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
        service_account_path = os.path.join(os.path.dirname(__file__), 'service-account.json')
        cred = None

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
            try:
                cred = credentials.Certificate(service_account_path)
                logger.info(f"🔑 Loaded Firebase credentials from file: {service_account_path}")
            except Exception as e:
                logger.error(f"Error loading {service_account_path}: {e}")

        if cred:
            storage_bucket_name = os.environ.get('FIREBASE_STORAGE_BUCKET', 'happybirthday-a287a.appspot.com')
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, {'storageBucket': storage_bucket_name})
            db_firestore = firestore.client()
            firebase_bucket = storage.bucket()
            firebase_initialized = True
            logger.info("🔥 Firebase Storage & Firestore initialized successfully!")
            return True
    except Exception as e:
        logger.warning(f"⚠️ Firebase initialization warning: {e}")
    return False

def fetch_firestore_collection(coll_name):
    if not firebase_initialized or not db_firestore:
        return []
    try:
        docs = db_firestore.collection(coll_name).get()
        results = []
        for doc in docs:
            d = doc.to_dict() or {}
            if 'id' not in d or not d['id']:
                d['id'] = int(doc.id) if doc.id.isdigit() else doc.id
            results.append(d)
        return results
    except Exception as e:
        logger.error(f"Error fetching Firestore collection {coll_name}: {e}")
        return []

def save_firestore_doc(coll_name, doc_dict):
    if not firebase_initialized or not db_firestore or not doc_dict:
        return None
    try:
        doc_id = str(doc_dict.get('id', ''))
        clean_d = {k: _sanitize_for_json(v) for k, v in doc_dict.items()}
        if doc_id:
            db_firestore.collection(coll_name).document(doc_id).set(clean_d, merge=True)
        else:
            new_ref = db_firestore.collection(coll_name).document()
            clean_d['id'] = new_ref.id
            new_ref.set(clean_d)
        return clean_d
    except Exception as e:
        logger.error(f"Error saving Firestore doc to {coll_name}: {e}")
        return None

LOCAL_DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'yash_world.db')

def fetch_sqlite_collection(table_name):
    if not os.path.exists(LOCAL_DB_PATH):
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(LOCAL_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        t_map = {
            'questions': 'question',
            'replies': 'reply',
            'users': 'user',
            'feedback_questions': 'feedback_question',
            'typing_text': 'typing_text',
            'typing_texts': 'typing_text',
            'reels': 'reels'
        }
        t_name = t_map.get(table_name, table_name)
        
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t_name}';")
        if not cursor.fetchone():
            conn.close()
            return []
            
        cursor.execute(f"SELECT * FROM {t_name}")
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"Note reading SQLite table {table_name}: {e}")
        return []

def sync_sqlite_to_firebase():
    if not firebase_initialized or not db_firestore:
        return
    try:
        # Check if Firestore questions collection already has records
        try:
            qs = fetch_firestore_collection('questions')
            if qs and len(qs) > 0:
                logger.info("🔥 Firestore questions collection already contains records. Skipping SQLite import.")
                return
        except Exception:
            pass

        if not os.path.exists(LOCAL_DB_PATH):
            return

        logger.info("📦 Checking local SQLite database (instance/yash_world.db) for Firebase data sync...")
        
        # 1. Sync Users
        u_rows = fetch_sqlite_collection('users')
        for u in u_rows:
            clean_u = {
                'id': int(u['id']) if str(u.get('id', '')).isdigit() else None,
                'username': u.get('username'),
                'password_hash': u.get('password_hash'),
                'is_admin': bool(u.get('is_admin', False)),
                'is_friend': bool(u.get('is_friend', False)),
                'created_at': u.get('created_at'),
                'last_login': u.get('last_login')
            }
            clean_u = {k: v for k, v in clean_u.items() if v is not None}
            save_firestore_doc('users', clean_u)

        # 2. Sync Questions
        q_rows = fetch_sqlite_collection('questions')
        for q in q_rows:
            clean_q = {
                'id': int(q['id']) if str(q.get('id', '')).isdigit() else None,
                'user_id': int(q['user_id']) if str(q.get('user_id', '')).isdigit() else None,
                'text': q.get('text'),
                'image_data': q.get('image_data'),
                'image_filename': q.get('image_filename'),
                'video_data': q.get('video_data'),
                'video_filename': q.get('video_filename'),
                'audio_data': q.get('audio_data'),
                'audio_filename': q.get('audio_filename'),
                'answer_text': q.get('answer_text'),
                'answer_image_data': q.get('answer_image_data'),
                'answer_image_filename': q.get('answer_image_filename'),
                'answer_video_data': q.get('answer_video_data'),
                'answer_video_filename': q.get('answer_video_filename'),
                'answer_audio_data': q.get('answer_audio_data'),
                'answer_audio_filename': q.get('answer_audio_filename'),
                'has_answer': bool(q.get('has_answer', False)),
                'is_answered': bool(q.get('is_answered', False)),
                'created_at': q.get('created_at'),
                'updated_at': q.get('updated_at')
            }
            clean_q = {k: v for k, v in clean_q.items() if v is not None}
            save_firestore_doc('questions', clean_q)

        # 3. Sync Replies
        r_rows = fetch_sqlite_collection('replies')
        for r in r_rows:
            clean_r = {
                'id': int(r['id']) if str(r.get('id', '')).isdigit() else None,
                'question_id': int(r['question_id']) if str(r.get('question_id', '')).isdigit() else None,
                'user_id': int(r['user_id']) if str(r.get('user_id', '')).isdigit() else None,
                'text': r.get('text'),
                'image_data': r.get('image_data'),
                'image_filename': r.get('image_filename'),
                'video_data': r.get('video_data'),
                'video_filename': r.get('video_filename'),
                'audio_data': r.get('audio_data'),
                'audio_filename': r.get('audio_filename'),
                'created_at': r.get('created_at'),
                'updated_at': r.get('updated_at')
            }
            clean_r = {k: v for k, v in clean_r.items() if v is not None}
            save_firestore_doc('replies', clean_r)

        # 4. Sync Typing Text
        tt_rows = fetch_sqlite_collection('typing_text')
        for tt in tt_rows:
            clean_tt = {
                'id': int(tt['id']) if str(tt.get('id', '')).isdigit() else None,
                'text': tt.get('text'),
                'is_active': bool(tt.get('is_active', True)),
                'created_at': tt.get('created_at'),
                'updated_at': tt.get('updated_at')
            }
            clean_tt = {k: v for k, v in clean_tt.items() if v is not None}
            save_firestore_doc('typing_text', clean_tt)

        # 5. Sync Feedback Questions
        fq_rows = fetch_sqlite_collection('feedback_questions')
        for fq in fq_rows:
            clean_fq = {
                'id': int(fq['id']) if str(fq.get('id', '')).isdigit() else None,
                'question': fq.get('question'),
                'is_active': bool(fq.get('is_active', True)),
                'created_at': fq.get('created_at')
            }
            clean_fq = {k: v for k, v in clean_fq.items() if v is not None}
            save_firestore_doc('feedback_questions', clean_fq)

        logger.info("🔥 Successfully synced SQLite data to Firebase Firestore!")
    except Exception as e:
        logger.warning(f"Note during SQLite to Firebase sync: {e}")

def sync_sqlite_to_supabase():
    if not supabase_initialized or not supabase_client:
        return
    try:
        # Check if Supabase questions table already has records
        try:
            res = supabase_client.table('questions').select('id').limit(1).execute()
            if res.data and len(res.data) > 0:
                logger.info("⚡ Supabase PostgreSQL questions table already contains records. Skipping SQLite import.")
                return
        except Exception:
            pass

        if not os.path.exists(LOCAL_DB_PATH):
            return

        logger.info("📦 Checking local SQLite database (instance/yash_world.db) for Supabase data sync...")
        
        # 1. Sync Users
        u_rows = fetch_sqlite_collection('users')
        u_synced = 0
        for u in u_rows:
            clean_u = {
                'id': int(u['id']) if str(u.get('id', '')).isdigit() else None,
                'username': u.get('username'),
                'password_hash': u.get('password_hash'),
                'is_admin': bool(u.get('is_admin', False)),
                'is_friend': bool(u.get('is_friend', False)),
                'created_at': u.get('created_at'),
                'last_login': u.get('last_login')
            }
            clean_u = {k: v for k, v in clean_u.items() if v is not None}
            try:
                supabase_client.table('users').upsert(clean_u).execute()
                u_synced += 1
            except Exception:
                pass

        # 2. Sync Questions
        q_rows = fetch_sqlite_collection('questions')
        q_synced = 0
        for q in q_rows:
            clean_q = {
                'id': int(q['id']) if str(q.get('id', '')).isdigit() else None,
                'user_id': int(q['user_id']) if str(q.get('user_id', '')).isdigit() else None,
                'text': q.get('text'),
                'image_data': q.get('image_data'),
                'image_filename': q.get('image_filename'),
                'video_data': q.get('video_data'),
                'video_filename': q.get('video_filename'),
                'audio_data': q.get('audio_data'),
                'audio_filename': q.get('audio_filename'),
                'answer_text': q.get('answer_text'),
                'answer_image_data': q.get('answer_image_data'),
                'answer_image_filename': q.get('answer_image_filename'),
                'answer_video_data': q.get('answer_video_data'),
                'answer_video_filename': q.get('answer_video_filename'),
                'answer_audio_data': q.get('answer_audio_data'),
                'answer_audio_filename': q.get('answer_audio_filename'),
                'has_answer': bool(q.get('has_answer', False)),
                'is_answered': bool(q.get('is_answered', False)),
                'created_at': q.get('created_at'),
                'updated_at': q.get('updated_at')
            }
            clean_q = {k: v for k, v in clean_q.items() if v is not None}
            try:
                supabase_client.table('questions').upsert(clean_q).execute()
                q_synced += 1
            except Exception:
                pass

        # 3. Sync Replies
        r_rows = fetch_sqlite_collection('replies')
        r_synced = 0
        for r in r_rows:
            clean_r = {
                'id': int(r['id']) if str(r.get('id', '')).isdigit() else None,
                'question_id': int(r['question_id']) if str(r.get('question_id', '')).isdigit() else None,
                'user_id': int(r['user_id']) if str(r.get('user_id', '')).isdigit() else None,
                'text': r.get('text'),
                'image_data': r.get('image_data'),
                'image_filename': r.get('image_filename'),
                'video_data': r.get('video_data'),
                'video_filename': r.get('video_filename'),
                'audio_data': r.get('audio_data'),
                'audio_filename': r.get('audio_filename'),
                'created_at': r.get('created_at'),
                'updated_at': r.get('updated_at')
            }
            clean_r = {k: v for k, v in clean_r.items() if v is not None}
            try:
                supabase_client.table('replies').upsert(clean_r).execute()
                r_synced += 1
            except Exception:
                pass

        # 4. Sync Typing Text
        tt_rows = fetch_sqlite_collection('typing_text')
        for tt in tt_rows:
            clean_tt = {
                'id': int(tt['id']) if str(tt.get('id', '')).isdigit() else None,
                'text': tt.get('text'),
                'is_active': bool(tt.get('is_active', True)),
                'created_at': tt.get('created_at'),
                'updated_at': tt.get('updated_at')
            }
            clean_tt = {k: v for k, v in clean_tt.items() if v is not None}
            try:
                supabase_client.table('typing_texts').upsert(clean_tt).execute()
            except Exception:
                pass

        # 5. Sync Feedback Questions
        fq_rows = fetch_sqlite_collection('feedback_questions')
        for fq in fq_rows:
            clean_fq = {
                'id': int(fq['id']) if str(fq.get('id', '')).isdigit() else None,
                'question': fq.get('question'),
                'is_active': bool(fq.get('is_active', True)),
                'created_at': fq.get('created_at')
            }
            clean_fq = {k: v for k, v in clean_fq.items() if v is not None}
            try:
                supabase_client.table('feedback_questions').upsert(clean_fq).execute()
            except Exception:
                pass

        logger.info(f"⚡ Successfully synced {q_synced}/{len(q_rows)} questions, {r_synced}/{len(r_rows)} replies, and {u_synced}/{len(u_rows)} users from SQLite to Supabase PostgreSQL!")
    except Exception as e:
        logger.warning(f"Note during SQLite to Supabase sync: {e}")

# Call Cloud Inits
init_supabase()
init_firebase()

@app.template_filter('format_datetime')
def format_datetime_filter(value, fmt='%d %b %Y, %H:%M'):
    if not value:
        return 'N/A'
    if hasattr(value, 'strftime'):
        try:
            return value.strftime(fmt)
        except Exception:
            pass
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return dt.strftime(fmt)
        except Exception:
            return value[:16]
    return str(value)

# ============================================================
# SUPABASE DATA WRAPPER & HELPER CLASSES
# ============================================================

class SafeDateTime:
    def __init__(self, val):
        self.val = val
        self.dt = None
        if isinstance(val, datetime):
            self.dt = val
        elif isinstance(val, str):
            try:
                self.dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
            except Exception:
                pass

    def strftime(self, fmt='%d %b %Y, %H:%M'):
        if self.dt:
            return self.dt.strftime(fmt)
        return str(self.val)[:16] if self.val else 'N/A'

    def __lt__(self, other):
        t1 = self.dt or datetime.min
        t2 = getattr(other, 'dt', None) or (other if isinstance(other, datetime) else datetime.min)
        return t1 < t2

    def __gt__(self, other):
        t1 = self.dt or datetime.min
        t2 = getattr(other, 'dt', None) or (other if isinstance(other, datetime) else datetime.min)
        return t1 > t2

    def __le__(self, other):
        return not (self > other)

    def __ge__(self, other):
        return not (self < other)

    def __eq__(self, other):
        t1 = self.dt or datetime.min
        t2 = getattr(other, 'dt', None) or (other if isinstance(other, datetime) else datetime.min)
        return t1 == t2

    def __str__(self):
        return self.strftime()

class SupabaseDoc:
    def __init__(self, data_dict):
        if not data_dict:
            data_dict = {}
        self._data = dict(data_dict)
        for k, v in self._data.items():
            if isinstance(v, dict):
                setattr(self, k, SupabaseDoc(v))
            elif k in ('created_at', 'updated_at', 'last_login', 'submitted_at'):
                setattr(self, k, SafeDateTime(v))
            else:
                setattr(self, k, v)

    def __getitem__(self, key):
        return getattr(self, key, None)

    def get(self, key, default=None):
        val = getattr(self, key, default)
        return val if val is not None else default

    def to_dict(self):
        return self._data

class SupabaseUser(UserMixin):
    def __init__(self, doc_data):
        self._data = doc_data or {}
        raw_id = self._data.get('id')
        username = str(self._data.get('username', '')).strip()
        if not raw_id:
            if username.lower() == 'yash':
                raw_id = 1
            elif username.lower() in ('glory', 'lory'):
                raw_id = 2
            else:
                raw_id = str(abs(hash(username)) % 100000 + 10) if username else '1'
        self.id = str(raw_id)
        self.username = username
        self.password_hash = self._data.get('password_hash', '')
        self.is_admin = bool(self._data.get('is_admin', False)) or (username.lower() == 'yash')
        self.is_friend = bool(self._data.get('is_friend', False)) or (username.lower() in ('yash', 'glory', 'lory'))
        self.created_at = SafeDateTime(self._data.get('created_at'))
        self.last_login = SafeDateTime(self._data.get('last_login'))

    def check_password(self, password):
        if not self.password_hash:
            return False
        pwd = password.strip() if password else ''
        if not pwd:
            return False
        
        # 1. Exact password check
        if check_password_hash(self.password_hash, pwd):
            return True
        # 2. Lowercase variation check (e.g. 'glory', 'lory')
        if check_password_hash(self.password_hash, pwd.lower()):
            return True
        # 3. Capitalized variation check (e.g. 'Glory', 'Lory')
        if check_password_hash(self.password_hash, pwd.capitalize()):
            return True
        # 4. Upper case variation check (e.g. 'GLORY', 'LORY')
        if check_password_hash(self.password_hash, pwd.upper()):
            return True
        return False

    def get_id(self):
        return str(self.id)

# ============================================================
# SUPABASE DATA ACCESS LAYER (POSTGRESQL)
# ============================================================

_DEFAULT_ADMIN_USER = os.environ.get('ADMIN_USERNAME', 'yash')
_DEFAULT_FRIEND_USER = os.environ.get('FRIEND_USERNAME', 'Glory')
_DEFAULT_ADMIN_HASH = generate_password_hash(os.environ.get('ADMIN_PASSWORD', 'admin123'))
_DEFAULT_FRIEND_HASH = generate_password_hash(os.environ.get('FRIEND_PASSWORD', 'lory'))

_USER_CACHE = {}

def safe_supabase_query(fn, retries=3, delay=0.5):
    last_err = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if any(k in err_str for k in ('name or service not known', 'connecterror', 'connection', 'timeout')):
                logger.warning(f"⚠️ Supabase network glitch (attempt {attempt+1}/{retries}): {e}. Retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2
            else:
                raise e
    raise last_err

def get_user_by_id(user_id):
    if not user_id:
        return None
    u_id_str = str(user_id)
    if u_id_str in _USER_CACHE:
        return _USER_CACHE[u_id_str]

    if firebase_initialized and db_firestore:
        try:
            doc = db_firestore.collection('users').document(u_id_str).get()
            if doc.exists:
                u_obj = SupabaseUser(doc.to_dict())
                _USER_CACHE[str(u_obj.id)] = u_obj
                _USER_CACHE[u_obj.username.lower()] = u_obj
                return u_obj
        except Exception:
            pass

    all_u = get_all_users()
    for u in all_u:
        if str(u.id) == u_id_str:
            return u
    return None

def get_user_by_username(username):
    if not username:
        return None
    clean_user = username.strip().lower()
    if clean_user in _USER_CACHE:
        return _USER_CACHE[clean_user]

    if firebase_initialized and db_firestore:
        try:
            fs_users = fetch_firestore_collection('users')
            for u_dict in fs_users:
                if str(u_dict.get('username', '')).strip().lower() == clean_user:
                    u_obj = SupabaseUser(u_dict)
                    _USER_CACHE[str(u_obj.id)] = u_obj
                    _USER_CACHE[u_obj.username.lower()] = u_obj
                    return u_obj
        except Exception:
            pass

    all_u = get_all_users()
    for u in all_u:
        if u.username.lower() == clean_user:
            return u
    return None

def get_all_users():
    def _fetch():
        users = []
        if firebase_initialized and db_firestore:
            try:
                fs_users = fetch_firestore_collection('users')
                if fs_users:
                    users = [SupabaseUser(d) for d in fs_users]
            except Exception as e:
                logger.warning(f"Firebase get_all_users warning: {e}")

        if not users and supabase_initialized and supabase_client:
            try:
                def _exec():
                    res = supabase_client.table('users').select('*').order('id').execute()
                    return [SupabaseUser(d) for d in (res.data or [])]
                users = safe_supabase_query(_exec)
            except Exception as e:
                logger.warning(f"Supabase get_all_users warning: {e}")

        for u in users:
            _USER_CACHE[str(u.id)] = u
            _USER_CACHE[u.username.lower()] = u
            
        return users or list({v for k, v in _USER_CACHE.items() if isinstance(v, SupabaseUser)})
    return get_cached('all_users', _fetch, ttl=10)

def save_user(user_dict):
    if not user_dict:
        return None
    try:
        clean_user_dict = {k: _sanitize_for_json(v) for k, v in user_dict.items()}
        if firebase_initialized and db_firestore:
            save_firestore_doc('users', clean_user_dict)
        res = None
        if supabase_initialized and supabase_client:
            if 'id' in clean_user_dict and clean_user_dict['id']:
                target_id = int(clean_user_dict['id']) if str(clean_user_dict['id']).isdigit() else clean_user_dict['id']
                update_payload = {k: v for k, v in clean_user_dict.items() if k != 'id'}
                try:
                    res = supabase_client.table('users').update(update_payload).eq('id', target_id).select().execute()
                except Exception:
                    try:
                        res = supabase_client.table('users').upsert(clean_user_dict).select().execute()
                    except Exception:
                        pass
            else:
                try:
                    res = supabase_client.table('users').insert(clean_user_dict).select().execute()
                except Exception:
                    try:
                        res = supabase_client.table('users').upsert(clean_user_dict).select().execute()
                    except Exception:
                        pass

        if res and hasattr(res, 'data') and res.data and len(res.data) > 0:
            u_obj = SupabaseUser(res.data[0])
            _USER_CACHE[str(u_obj.id)] = u_obj
            _USER_CACHE[u_obj.username.lower()] = u_obj
            return u_obj

        u_by_name = get_user_by_username(clean_user_dict.get('username'))
        if u_by_name:
            return u_by_name
        fallback_u = SupabaseUser(clean_user_dict)
        _USER_CACHE[str(fallback_u.id or '1')] = fallback_u
        _USER_CACHE[fallback_u.username.lower()] = fallback_u
        return fallback_u
    except Exception as e:
        logger.error(f"Error saving user: {e}")
        fallback_u = SupabaseUser(user_dict)
        _USER_CACHE[str(fallback_u.id or '1')] = fallback_u
        _USER_CACHE[fallback_u.username.lower()] = fallback_u
        return fallback_u

def get_next_id(table_name):
    if not supabase_initialized or not supabase_client:
        return int(datetime.utcnow().timestamp() * 1000)
    try:
        # Atomic counter synchronization via Supabase 'counters' table
        res = supabase_client.table('counters').select('last_id').eq('name', table_name).execute()
        if res.data and len(res.data) > 0:
            cur_val = res.data[0].get('last_id', 0)
            new_val = cur_val + 1
            supabase_client.table('counters').update({'last_id': new_val}).eq('name', table_name).execute()
        else:
            # Query max ID from table if counter not seeded
            max_res = supabase_client.table(table_name).select('id').order('id', desc=True).limit(1).execute()
            new_val = (max_res.data[0]['id'] + 1) if max_res.data and len(max_res.data) > 0 and 'id' in max_res.data[0] else 1
            supabase_client.table('counters').insert({'name': table_name, 'last_id': new_val}).execute()
        return new_val
    except Exception as e:
        logger.warning(f"Note generating ID via counters for {table_name}: {e}")
        return int(datetime.utcnow().timestamp() * 1000)

_CACHE_STORE = {}
_CACHE_TIME = {}

def get_cached(key, fetch_fn, ttl=10):
    now = datetime.utcnow()
    if key in _CACHE_STORE and key in _CACHE_TIME:
        if (now - _CACHE_TIME[key]).total_seconds() < ttl:
            return _CACHE_STORE[key]
    val = fetch_fn()
    _CACHE_STORE[key] = val
    _CACHE_TIME[key] = now
    return val

def invalidate_cache(key=None):
    if key:
        _CACHE_STORE.pop(key, None)
        _CACHE_TIME.pop(key, None)
    else:
        _CACHE_STORE.clear()
        _CACHE_TIME.clear()

QUESTION_ORDER_FILE = os.path.join(os.path.dirname(__file__), 'question_order.json')
REELS_FILE = os.path.join(os.path.dirname(__file__), 'reels.json')

def load_local_reels():
    if os.path.exists(REELS_FILE):
        try:
            with open(REELS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_local_reels(reels_list):
    try:
        with open(REELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(reels_list, f)
    except Exception:
        pass

def get_all_reels():
    def _fetch():
        if firebase_initialized and db_firestore:
            try:
                fs_reels = fetch_firestore_collection('reels')
                if fs_reels:
                    return [SupabaseDoc(r) for r in fs_reels]
            except Exception as e:
                logger.warning(f"Firebase get_all_reels warning: {e}")

        if supabase_initialized and supabase_client:
            try:
                res = supabase_client.table('reels').select('*').order('created_at', desc=True).execute()
                if res.data and len(res.data) > 0:
                    return [SupabaseDoc(d) for d in res.data]
            except Exception as e:
                logger.info(f"Note fetching reels from Supabase: {e}")
        return []
    return get_cached('all_reels', _fetch, ttl=5)

def save_reel_doc(title, video_url):
    reel_id = uuid.uuid4().hex[:10]
    reel_dict = {
        'id': reel_id,
        'title': title or 'Video Reel',
        'video_url': video_url,
        'created_at': datetime.utcnow().isoformat()
    }
    
    if firebase_initialized and db_firestore:
        save_firestore_doc('reels', reel_dict)

    if supabase_initialized and supabase_client:
        try:
            supabase_client.table('reels').upsert(reel_dict).execute()
        except Exception as e:
            logger.warning(f"Note saving reel to Supabase: {e}")

    invalidate_cache('all_reels')
    return SupabaseDoc(reel_dict)

def delete_reel_doc(reel_id):
    if not reel_id:
        return False

    if firebase_initialized and db_firestore:
        try:
            db_firestore.collection('reels').document(str(reel_id)).delete()
        except Exception as e:
            logger.warning(f"Error deleting reel from Firebase: {e}")

    if supabase_initialized and supabase_client:
        try:
            supabase_client.table('reels').delete().eq('id', reel_id).execute()
        except Exception as e:
            logger.warning(f"Error deleting reel from Supabase: {e}")

    invalidate_cache('all_reels')
    return True
    return True

def load_local_question_order():
    if os.path.exists(QUESTION_ORDER_FILE):
        try:
            with open(QUESTION_ORDER_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_local_question_order(ordered_ids):
    try:
        with open(QUESTION_ORDER_FILE, 'w', encoding='utf-8') as f:
            json.dump([str(i) for i in ordered_ids], f)
    except Exception:
        pass

def get_all_questions(force_refresh=False):
    if force_refresh:
        invalidate_cache('all_questions')
    def _fetch():
        questions_raw = []
        if firebase_initialized and db_firestore:
            try:
                questions_raw = fetch_firestore_collection('questions')
            except Exception as e:
                logger.warning(f"Firebase get_all_questions warning: {e}")

        if not questions_raw and supabase_initialized and supabase_client:
            try:
                res = supabase_client.table('questions').select('*').execute()
                questions_raw = res.data or []
            except Exception as e:
                logger.warning(f"Supabase get_all_questions warning: {e}")

        users_map = {str(u.id): u for u in get_all_users()}
        questions = []
        for d in questions_raw:
            if d.get('text') == '[FRIEND SNAPSHOT]':
                continue
            u_id = str(d.get('user_id'))
            asker = users_map.get(u_id)
            if asker:
                d['asker'] = {'username': asker.username, 'id': asker.id}
            questions.append(SupabaseDoc(d))
        
        def sort_key(q):
            d_order = getattr(q, 'display_order', None)
            if d_order is not None and str(d_order).isdigit() and int(d_order) > 0:
                return (0, int(d_order))
                
            created = getattr(q, 'created_at', None)
            dt_str = str(created.val) if hasattr(created, 'val') and created.val else str(created or '')
            return (1, dt_str)

        questions.sort(key=sort_key)
        return questions
    return get_cached('all_questions', _fetch, ttl=2)

def update_question_order_list(ordered_ids):
    if not ordered_ids:
        return False
    try:
        clean_ids = [str(x) for x in ordered_ids if x]
        save_local_question_order(clean_ids)
        
        if firebase_initialized and db_firestore:
            try:
                batch = db_firestore.batch()
                for order_idx, q_id in enumerate(clean_ids, start=1):
                    ref = db_firestore.collection('questions').document(str(q_id))
                    batch.set(ref, {'display_order': order_idx}, merge=True)
                batch.commit()
            except Exception as ex_fs:
                logger.warning(f"Note updating question order batch in Firebase: {ex_fs}")

        if supabase_initialized and supabase_client:
            for order_idx, q_id in enumerate(clean_ids, start=1):
                t_id = int(q_id) if str(q_id).isdigit() else q_id
                try:
                    supabase_client.table('questions').update({'display_order': order_idx}).eq('id', t_id).execute()
                except Exception as ex1:
                    pass
        invalidate_cache('all_questions')
        return True
    except Exception as e:
        logger.error(f"Error updating question order: {e}")
        return False

def insert_question_at_position(q_data, target_pos='last'):
    invalidate_cache('all_questions')
    all_qs = get_all_questions()
    q_id = q_data.get('id')
    
    remaining = [q for q in all_qs if str(getattr(q, 'id', '')) != str(q_id) and getattr(q, 'id', None) is not None]
    total_q = len(remaining)
    
    pos_str = str(target_pos).strip().lower()
    if pos_str == 'first' or pos_str == '1':
        idx = 0
    elif pos_str == 'last':
        idx = total_q
    else:
        try:
            pos_num = int(pos_str)
            if pos_num <= 1:
                idx = 0
            elif pos_num > total_q:
                idx = total_q
            else:
                idx = pos_num - 1
        except Exception:
            idx = total_q
            
    q_data['display_order'] = idx + 1

    saved_q = save_question(q_data)
    if not saved_q:
        return None
        
    saved_id = getattr(saved_q, 'id', None) or q_data.get('id')
    remaining = [q for q in remaining if str(getattr(q, 'id', '')) != str(saved_id)]
    remaining.insert(idx, saved_q)
    
    if pos_str != 'last':
        ordered_ids = [str(getattr(q, 'id', '')) for q in remaining if getattr(q, 'id', None) is not None]
        update_question_order_list(ordered_ids)
    
    invalidate_cache('all_questions')
    return saved_q

def get_question_by_id(question_id):
    if not supabase_initialized or not supabase_client or not question_id:
        return None
    try:
        target_id = int(question_id) if str(question_id).isdigit() else question_id
        res = supabase_client.table('questions').select('*').eq('id', target_id).limit(1).execute()
        if res.data and len(res.data) > 0:
            d = res.data[0]
            asker = get_user_by_id(d.get('user_id'))
            if asker:
                d['asker'] = {'username': asker.username, 'id': asker.id}
            return SupabaseDoc(d)
    except Exception as e:
        logger.error(f"Error fetching question {question_id}: {e}")
    return None

def _sanitize_for_json(val):
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if hasattr(val, 'isoformat') and callable(getattr(val, 'isoformat')):
        try:
            return val.isoformat()
        except Exception:
            pass
    if hasattr(val, 'val'):
        if hasattr(val.val, 'isoformat') and callable(getattr(val.val, 'isoformat')):
            try:
                return val.val.isoformat()
            except Exception:
                pass
        return str(val.val)
    if hasattr(val, 'to_dict') and callable(getattr(val, 'to_dict')):
        try:
            return _sanitize_for_json(val.to_dict())
        except Exception:
            pass
    if isinstance(val, dict):
        return {str(k): _sanitize_for_json(v) for k, v in val.items()}
    if isinstance(val, (list, tuple, set)):
        return [_sanitize_for_json(x) for x in val]
    return val

def save_question(q_dict):
    if not q_dict:
        return None
    try:
        clean_dict = {}
        for k, v in q_dict.items():
            if k in ('asker', 'replies'):
                continue
            clean_dict[k] = _sanitize_for_json(v)

        if 'id' not in clean_dict or not clean_dict['id']:
            clean_dict['id'] = get_next_id('questions')

        if 'created_at' not in clean_dict or not clean_dict['created_at']:
            clean_dict['created_at'] = datetime.utcnow().isoformat()
        clean_dict['updated_at'] = datetime.utcnow().isoformat()

        # Save to Firebase Firestore if active
        if firebase_initialized and db_firestore:
            saved_fs = save_firestore_doc('questions', clean_dict)
            if not saved_fs:
                raise RuntimeError("Firebase Firestore failed to save question document.")

        # Save to Supabase if active
        res = None
        if supabase_initialized and supabase_client:
            if 'id' in clean_dict and clean_dict['id']:
                raw_id = clean_dict['id']
                target_id = int(raw_id) if str(raw_id).isdigit() else raw_id
                update_payload = {k: v for k, v in clean_dict.items() if k != 'id'}
                try:
                    res = supabase_client.table('questions').update(update_payload).eq('id', target_id).select().execute()
                    if not res.data:
                        res = supabase_client.table('questions').update(update_payload).eq('id', str(raw_id)).select().execute()
                except Exception as ex1:
                    standard_cols = (
                        'text', 'user_id', 'type', 'marks', 'display_order',
                        'image', 'image_data', 'image_filename',
                        'video', 'video_data', 'video_filename',
                        'audio', 'audio_data', 'audio_filename',
                        'answer_text', 'has_answer', 'is_answered',
                        'answer_image_data', 'answer_video_data', 'answer_audio_data',
                        'created_at', 'updated_at'
                    )
                    pruned_payload = {k: v for k, v in update_payload.items() if k in standard_cols}
                    try:
                        res = supabase_client.table('questions').update(pruned_payload).eq('id', target_id).select().execute()
                        if not res.data:
                            res = supabase_client.table('questions').update(pruned_payload).eq('id', str(raw_id)).select().execute()
                    except Exception:
                        pass
            else:
                try:
                    res = supabase_client.table('questions').insert(clean_dict).select().execute()
                except Exception:
                    pass

        invalidate_cache('all_questions')
        if res and hasattr(res, 'data') and res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
        return SupabaseDoc(clean_dict)
    except Exception as e:
        logger.error(f"Error saving question: {e}")
        return SupabaseDoc(q_dict)

def delete_question_doc(question_id):
    if not supabase_initialized or not supabase_client or not question_id:
        return False
    try:
        q = get_question_by_id(question_id)
        if q:
            for m_attr in ('image_data', 'video_data', 'audio_data', 'answer_image_data', 'answer_video_data', 'answer_audio_data'):
                url = q.get(m_attr)
                if url:
                    delete_file_from_supabase(url)
            
            replies = get_replies_for_question(question_id)
            for r in replies:
                delete_reply_doc(r.id)

            target_id = int(question_id) if str(question_id).isdigit() else question_id
            supabase_client.table('questions').delete().eq('id', target_id).execute()
            invalidate_cache('all_questions')
            logger.info(f"⚡ Question {question_id} deleted from Supabase PostgreSQL")
            return True
    except Exception as e:
        logger.error(f"Error deleting question {question_id}: {e}")
    return False

def get_replies_for_question(question_id):
    if not supabase_initialized or not supabase_client or not question_id:
        return []
    try:
        users_map = {str(u.id): u for u in get_all_users()}
        target_id = int(question_id) if str(question_id).isdigit() else question_id
        res = supabase_client.table('replies').select('*').eq('question_id', target_id).order('created_at').execute()
        replies = []
        for d in (res.data or []):
            u_id = str(d.get('user_id'))
            replier = users_map.get(u_id)
            if replier:
                d['replier'] = {'username': replier.username, 'id': replier.id}
            replies.append(SupabaseDoc(d))
        return replies
    except Exception as e:
        logger.error(f"Error fetching replies for question {question_id}: {e}")
        return []

def get_all_replies():
    def _fetch():
        replies_raw = []
        if firebase_initialized and db_firestore:
            try:
                replies_raw = fetch_firestore_collection('replies')
            except Exception as e:
                logger.warning(f"Firebase get_all_replies warning: {e}")

        if not replies_raw and supabase_initialized and supabase_client:
            try:
                def _exec():
                    res = supabase_client.table('replies').select('*').order('created_at', desc=True).execute()
                    return res.data or []
                replies_raw = safe_supabase_query(_exec)
            except Exception as e:
                logger.warning(f"Supabase get_all_replies warning: {e}")

        users_map = {str(u.id): u for u in get_all_users()}
        questions_raw = get_all_questions()
        q_map = {str(getattr(qd, 'id', '')): qd._data if hasattr(qd, '_data') else qd for qd in questions_raw}

        replies = []
        for d in replies_raw:
            u_id = str(d.get('user_id'))
            replier = users_map.get(u_id)
            if replier:
                d['replier'] = {'username': replier.username, 'id': replier.id}
            
            q_id = str(d.get('question_id'))
            if q_id in q_map:
                d['question'] = q_map[q_id]
            replies.append(SupabaseDoc(d))
        return replies
    return get_cached('all_replies', _fetch, ttl=5)

def save_reply(r_dict):
    if not r_dict:
        return None
    try:
        clean_dict = {k: _sanitize_for_json(v) for k, v in r_dict.items() if k not in ('replier', 'question')}
        if 'id' not in clean_dict or not clean_dict['id']:
            clean_dict['id'] = get_next_id('replies')

        if 'created_at' not in clean_dict or not clean_dict['created_at']:
            clean_dict['created_at'] = datetime.utcnow().isoformat()
        clean_dict['updated_at'] = datetime.utcnow().isoformat()

        # Save to Firebase Firestore if active
        if firebase_initialized and db_firestore:
            saved_fs = save_firestore_doc('replies', clean_dict)
            if not saved_fs:
                raise RuntimeError("Firebase Firestore failed to save reply document.")

        # Save to Supabase if active
        res = None
        if supabase_initialized and supabase_client:
            try:
                res = supabase_client.table('replies').upsert(clean_dict).select().execute()
            except Exception:
                pass

        invalidate_cache('all_replies')
        if res and hasattr(res, 'data') and res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
        return SupabaseDoc(clean_dict)
    except Exception as e:
        logger.error(f"Error saving reply: {e}")
        return SupabaseDoc(r_dict)

def delete_reply_doc(reply_id):
    if not reply_id:
        return False
    try:
        if firebase_initialized and db_firestore:
            try:
                db_firestore.collection('replies').document(str(reply_id)).delete()
            except Exception as e:
                logger.warning(f"Error deleting reply from Firebase: {e}")

        if supabase_initialized and supabase_client:
            try:
                target_id = int(reply_id) if str(reply_id).isdigit() else reply_id
                supabase_client.table('replies').delete().eq('id', target_id).execute()
            except Exception:
                pass

        invalidate_cache('all_replies')
        return True
    except Exception as e:
        logger.error(f"Error deleting reply {reply_id}: {e}")
        return False

def get_all_typing_texts():
    if firebase_initialized and db_firestore:
        try:
            fs_t = fetch_firestore_collection('typing_text')
            if fs_t:
                return [SupabaseDoc(d) for d in fs_t]
        except Exception:
            pass

    if supabase_initialized and supabase_client:
        try:
            res = supabase_client.table('typing_texts').select('*').order('created_at', desc=True).execute()
            return [SupabaseDoc(d) for d in (res.data or [])]
        except Exception:
            pass
    return []

def get_active_typing_text():
    if firebase_initialized and db_firestore:
        try:
            fs_t = fetch_firestore_collection('typing_text')
            for t in fs_t:
                if t.get('is_active'):
                    return SupabaseDoc(t)
        except Exception:
            pass

    if supabase_initialized and supabase_client:
        try:
            res = supabase_client.table('typing_texts').select('*').eq('is_active', True).limit(1).execute()
            if res.data and len(res.data) > 0:
                return SupabaseDoc(res.data[0])
        except Exception:
            pass
    return None

def save_typing_text(text_str, is_active=True):
    t_id = get_next_id('typing_text')
    t_dict = {
        'id': t_id,
        'text': text_str,
        'is_active': is_active,
        'created_at': datetime.utcnow().isoformat(),
        'updated_at': datetime.utcnow().isoformat()
    }
    
    if is_active and firebase_initialized and db_firestore:
        try:
            all_t = fetch_firestore_collection('typing_text')
            for item in all_t:
                db_firestore.collection('typing_text').document(str(item['id'])).update({'is_active': False})
        except Exception:
            pass

    if firebase_initialized and db_firestore:
        save_firestore_doc('typing_text', t_dict)

    if supabase_initialized and supabase_client:
        try:
            if is_active:
                supabase_client.table('typing_texts').update({'is_active': False}).neq('id', 0).execute()
            supabase_client.table('typing_texts').insert(t_dict).execute()
        except Exception:
            pass

    return SupabaseDoc(t_dict)

def update_typing_text(text_id, text_str):
    if not text_id:
        return None
    up_dict = {'text': text_str, 'updated_at': datetime.utcnow().isoformat()}
    
    if firebase_initialized and db_firestore:
        try:
            db_firestore.collection('typing_text').document(str(text_id)).update(up_dict)
        except Exception as e:
            logger.warning(f"Note updating typing text in Firebase: {e}")

    if supabase_initialized and supabase_client:
        try:
            target_id = int(text_id) if str(text_id).isdigit() else text_id
            supabase_client.table('typing_texts').update(up_dict).eq('id', target_id).execute()
        except Exception:
            pass
    return SupabaseDoc(up_dict)

def activate_typing_text(text_id):
    if not text_id:
        return False
    if firebase_initialized and db_firestore:
        try:
            all_t = fetch_firestore_collection('typing_text')
            for item in all_t:
                db_firestore.collection('typing_text').document(str(item['id'])).update({'is_active': False})
            db_firestore.collection('typing_text').document(str(text_id)).update({'is_active': True})
        except Exception as e:
            logger.warning(f"Error activating typing text in Firebase: {e}")

    if supabase_initialized and supabase_client:
        try:
            target_id = int(text_id) if str(text_id).isdigit() else text_id
            supabase_client.table('typing_texts').update({'is_active': False}).neq('id', 0).execute()
            supabase_client.table('typing_texts').update({'is_active': True}).eq('id', target_id).execute()
        except Exception:
            pass
    return True

def delete_typing_text_doc(text_id):
    if not text_id:
        return False
    if firebase_initialized and db_firestore:
        try:
            db_firestore.collection('typing_text').document(str(text_id)).delete()
        except Exception as e:
            logger.warning(f"Error deleting typing text in Firebase: {e}")

    if supabase_initialized and supabase_client:
        try:
            target_id = int(text_id) if str(text_id).isdigit() else text_id
            supabase_client.table('typing_texts').delete().eq('id', target_id).execute()
        except Exception:
            pass
    return True

def save_friend_snapshot(user_id, image_data_raw):
    if not supabase_initialized or not supabase_client or not user_id or not image_data_raw:
        return None
    try:
        image_url = None
        filename = f"snapshot_{user_id}_{int(datetime.utcnow().timestamp())}.jpg"
        
        if image_data_raw.startswith('data:image'):
            try:
                header, encoded = image_data_raw.split(',', 1)
                file_bytes = base64.b64decode(encoded)
                uploaded_url = upload_file_to_supabase(file_bytes, filename, content_type='image/jpeg')
                if uploaded_url:
                    image_url = uploaded_url
            except Exception as ex_up:
                logger.warning(f"Note uploading snapshot to Supabase Storage: {ex_up}")
        elif image_data_raw.startswith('http'):
            image_url = image_data_raw
            
        if not image_url:
            image_url = image_data_raw
            
        snap_dict = {
            'user_id': int(user_id) if str(user_id).isdigit() else user_id,
            'image_data': image_url,
            'image_filename': filename,
            'created_at': datetime.utcnow().isoformat()
        }
        
        try:
            res = supabase_client.table('friend_snapshots').insert(snap_dict).execute()
            if res.data and len(res.data) > 0:
                return SupabaseDoc(res.data[0])
            return SupabaseDoc(snap_dict)
        except Exception as ex_snap:
            logger.warning(f"Note inserting into friend_snapshots table: {ex_snap}. Trying fallback to questions table...")
            q_dict = {
                'user_id': int(user_id) if str(user_id).isdigit() else user_id,
                'text': '[FRIEND SNAPSHOT]',
                'image_data': image_url,
                'image_filename': filename,
                'has_answer': False,
                'is_answered': False,
                'created_at': datetime.utcnow().isoformat()
            }
            return save_question(q_dict)
    except Exception as e:
        logger.error(f"Error saving friend snapshot: {e}")
        return None

def get_all_friend_snapshots():
    if not supabase_initialized or not supabase_client:
        return []
    snapshots = []
    try:
        res = supabase_client.table('friend_snapshots').select('*').order('created_at', desc=True).execute()
        for d in (res.data or []):
            u = get_user_by_id(d.get('user_id'))
            if u:
                d['user'] = {'username': u.username, 'id': u.id}
            snapshots.append(SupabaseDoc(d))
    except Exception as e:
        logger.warning(f"Note fetching friend_snapshots table: {e}")
        
    try:
        res_qs = supabase_client.table('questions').select('*').eq('text', '[FRIEND SNAPSHOT]').execute()
        for d in (res_qs.data or []):
            u = get_user_by_id(d.get('user_id'))
            if u:
                d['user'] = {'username': u.username, 'id': u.id}
            if not any(str(s.get('id')) == str(d.get('id')) or s.get('image_data') == d.get('image_data') for s in snapshots):
                snapshots.append(SupabaseDoc(d))
    except Exception as e2:
        logger.warning(f"Note checking fallback snapshots: {e2}")

    return snapshots

def delete_friend_snapshot_doc(snapshot_id):
    if not supabase_initialized or not supabase_client or not snapshot_id:
        return False
    try:
        target_id = int(snapshot_id) if str(snapshot_id).isdigit() else snapshot_id
        
        # 1. Delete from friend_snapshots table if present
        try:
            res = supabase_client.table('friend_snapshots').select('*').eq('id', target_id).limit(1).execute()
            if res.data and len(res.data) > 0:
                url = res.data[0].get('image_data')
                if url and str(url).startswith('http'):
                    delete_file_from_supabase(url)
                supabase_client.table('friend_snapshots').delete().eq('id', target_id).execute()
        except Exception as e1:
            logger.warning(f"Note deleting from friend_snapshots: {e1}")

        # 2. Delete from questions table if stored as fallback snapshot
        try:
            if supabase_initialized and supabase_client:
                res_q = supabase_client.table('questions').select('*').eq('id', target_id).limit(1).execute()
                if res_q.data and len(res_q.data) > 0:
                    q_doc = res_q.data[0]
                    if q_doc.get('text') == '[FRIEND SNAPSHOT]':
                        url = q_doc.get('image_data')
                        if url and str(url).startswith('http'):
                            delete_file_from_supabase(url)
                        supabase_client.table('questions').delete().eq('id', target_id).execute()
                        invalidate_cache('all_questions')
        except Exception as e2:
            logger.warning(f"Note deleting fallback question snapshot: {e2}")

        return True
    except Exception as e:
        logger.error(f"Error deleting friend snapshot {snapshot_id}: {e}")
        return False

def get_all_feedback_questions():
    questions_raw = []
    if firebase_initialized and db_firestore:
        try:
            questions_raw = fetch_firestore_collection('feedback_questions')
        except Exception as e:
            logger.warning(f"Firebase feedback_questions warning: {e}")

    if not questions_raw and supabase_initialized and supabase_client:
        try:
            res = supabase_client.table('feedback_questions').select('*').order('created_at', desc=True).execute()
            questions_raw = res.data or []
        except Exception as e:
            logger.warning(f"Supabase feedback_questions warning: {e}")

    all_responses = []
    if firebase_initialized and db_firestore:
        try:
            all_responses = fetch_firestore_collection('feedback_responses')
        except Exception:
            pass

    questions = []
    for d in questions_raw:
        q_id_str = str(d.get('id', ''))
        responses = []
        for rd in all_responses:
            if str(rd.get('question_id', '')) == q_id_str:
                u = get_user_by_id(rd.get('user_id'))
                if u:
                    rd['user'] = {'username': u.username, 'id': u.id}
                responses.append(SupabaseDoc(rd))
        d['responses'] = responses
        questions.append(SupabaseDoc(d))
    return questions

DEFAULT_FEEDBACK_QUESTIONS = [
    '⭐ How would you rate your overall experience with your friend?',
    '💬 How helpful and responsive is your friend?',
    '🌟 How much do you enjoy communicating here?'
]

def get_active_feedback_questions():
    all_q = get_all_feedback_questions()
    active = [q for q in all_q if getattr(q, 'is_active', True)]
    if not active:
        for q_text in DEFAULT_FEEDBACK_QUESTIONS:
            try:
                save_feedback_question(q_text)
            except Exception:
                pass
        all_q = get_all_feedback_questions()
        active = [q for q in all_q if getattr(q, 'is_active', True)]
    return active

def save_feedback_question(question_str):
    fq_id = get_next_id('feedback_questions')
    q_dict = {
        'id': fq_id,
        'question': question_str,
        'is_active': True,
        'created_at': datetime.utcnow().isoformat()
    }
    
    if firebase_initialized and db_firestore:
        save_firestore_doc('feedback_questions', q_dict)

    if supabase_initialized and supabase_client:
        try:
            supabase_client.table('feedback_questions').insert(q_dict).execute()
        except Exception:
            pass

    return SupabaseDoc(q_dict)

def toggle_feedback_question(q_id):
    if not q_id:
        return False
    target_id_str = str(q_id)
    if firebase_initialized and db_firestore:
        try:
            doc_ref = db_firestore.collection('feedback_questions').document(target_id_str)
            doc = doc_ref.get()
            if doc.exists:
                curr = doc.to_dict().get('is_active', True)
                new_val = not curr
                doc_ref.update({'is_active': new_val})
                return new_val
        except Exception as e:
            logger.warning(f"Error toggling feedback in Firebase: {e}")

    if supabase_initialized and supabase_client:
        try:
            target_id = int(q_id) if str(q_id).isdigit() else q_id
            res = supabase_client.table('feedback_questions').select('is_active').eq('id', target_id).limit(1).execute()
            if res.data and len(res.data) > 0:
                curr = res.data[0].get('is_active', True)
                new_val = not curr
                supabase_client.table('feedback_questions').update({'is_active': new_val}).eq('id', target_id).execute()
                return new_val
        except Exception:
            pass
    return False

def delete_feedback_question_doc(q_id):
    if not q_id:
        return False
    target_id_str = str(q_id)
    if firebase_initialized and db_firestore:
        try:
            db_firestore.collection('feedback_questions').document(target_id_str).delete()
        except Exception as e:
            logger.warning(f"Error deleting feedback question from Firebase: {e}")

    if supabase_initialized and supabase_client:
        try:
            target_id = int(q_id) if str(q_id).isdigit() else q_id
            supabase_client.table('feedback_questions').delete().eq('id', target_id).execute()
        except Exception:
            pass
    return True

def save_feedback_response(user_id, question_id, rating, comment=None):
    r_id = get_next_id('feedback_responses')
    r_dict = {
        'id': r_id,
        'user_id': user_id,
        'question_id': question_id,
        'rating': rating,
        'comment': comment,
        'created_at': datetime.utcnow().isoformat()
    }
    if firebase_initialized and db_firestore:
        save_firestore_doc('feedback_responses', r_dict)

    if supabase_initialized and supabase_client:
        try:
            supabase_client.table('feedback_responses').insert(r_dict).execute()
        except Exception:
            pass

    return SupabaseDoc(r_dict)

def get_all_feedback_responses():
    if not supabase_initialized or not supabase_client:
        return []
    try:
        res = supabase_client.table('feedback_responses').select('*').order('created_at', desc=True).execute()
        responses = []
        for d in (res.data or []):
            u = get_user_by_id(d.get('user_id'))
            if u:
                d['user'] = {'username': u.username, 'id': u.id}
            q_res = supabase_client.table('feedback_questions').select('*').eq('id', d.get('question_id')).limit(1).execute()
            if q_res.data and len(q_res.data) > 0:
                d['question'] = q_res.data[0]
            responses.append(SupabaseDoc(d))
        return responses
    except Exception as e:
        logger.error(f"Error fetching feedback responses: {e}")
        return []

_site_settings_cache = None
_site_settings_cache_time = None
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'site_settings.json')

def load_local_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'auto_snapshot_enabled': True, 'intro_video_url': '', 'intro_video_enabled': True}

def save_local_settings(data):
    try:
        curr = load_local_settings()
        curr.update(data)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(curr, f)
    except Exception:
        pass

def get_site_settings():
    global _site_settings_cache, _site_settings_cache_time
    now = datetime.utcnow()
    if _site_settings_cache and _site_settings_cache_time and (now - _site_settings_cache_time).total_seconds() < 10:
        return _site_settings_cache

    default_settings = SupabaseDoc({
        'id': 1,
        'site_title': 'YASH WORLD',
        'site_tagline': 'Private Messaging Platform',
        'welcome_message': '',
        'auto_snapshot_enabled': True,
        'intro_video_url': '',
        'intro_video_enabled': True
    })

    if firebase_initialized and db_firestore:
        try:
            fs_s = fetch_firestore_collection('site_settings')
            if fs_s:
                _site_settings_cache = SupabaseDoc(fs_s[0])
                _site_settings_cache_time = now
                return _site_settings_cache
        except Exception:
            pass

    if supabase_initialized and supabase_client:
        try:
            res = supabase_client.table('site_settings').select('*').eq('id', 1).limit(1).execute()
            if res.data and len(res.data) > 0:
                _site_settings_cache = SupabaseDoc(res.data[0])
                _site_settings_cache_time = now
                return _site_settings_cache
        except Exception:
            pass

    _site_settings_cache = default_settings
    _site_settings_cache_time = now
    return default_settings

def save_site_settings(title, tagline, welcome, auto_snapshot_enabled=True, intro_video_url='', intro_video_enabled=True):
    global _site_settings_cache, _site_settings_cache_time
    _site_settings_cache = None
    _site_settings_cache_time = None

    auto_snap_bool = bool(auto_snapshot_enabled)
    intro_vid_bool = bool(intro_video_enabled)

    s_dict = {
        'id': 1,
        'site_title': title,
        'site_tagline': tagline,
        'welcome_message': welcome,
        'auto_snapshot_enabled': auto_snap_bool,
        'intro_video_url': intro_video_url or '',
        'intro_video_enabled': intro_vid_bool,
        'updated_at': datetime.utcnow().isoformat()
    }

    if firebase_initialized and db_firestore:
        save_firestore_doc('site_settings', s_dict)

    if supabase_initialized and supabase_client:
        try:
            supabase_client.table('site_settings').upsert(s_dict).execute()
        except Exception as ex:
            logger.warning(f"Note saving site settings to Supabase: {ex}")

    _site_settings_cache = SupabaseDoc(s_dict)
    _site_settings_cache_time = datetime.utcnow()
    return _site_settings_cache

# ============================================================
# PERMANENT STORAGE UPLOAD & DELETE HELPERS (DUAL-LAYER CLOUD + LOCAL DISK FALLBACK)
# ============================================================

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')

def ensure_upload_dirs():
    for sub in ['images', 'videos', 'audios']:
        p = os.path.join(UPLOAD_FOLDER, sub)
        os.makedirs(p, exist_ok=True)

ensure_upload_dirs()

def upload_file_to_supabase(file, media_type='image'):
    if not file or not file.filename:
        return None, None

    orig_name = file.filename or 'media'
    ext = ''
    if '.' in orig_name:
        ext = '.' + orig_name.rsplit('.', 1)[-1].lower()
    if not ext:
        if media_type == 'video':
            ext = '.mp4'
        elif media_type == 'audio':
            ext = '.mp3'
        else:
            ext = '.jpg'

    base_name = secure_filename(orig_name.rsplit('.', 1)[0] if '.' in orig_name else orig_name)
    if not base_name:
        base_name = f"{media_type}_{uuid.uuid4().hex[:6]}"
    filename = f"{base_name}{ext}"

    file.seek(0)
    file_bytes = file.read()
    if not file_bytes:
        logger.warning(f"Uploaded file '{filename}' payload is empty (0 bytes).")
        return None, None

    unique_name = f"{media_type}s/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}_{filename}"

    # Determine exact MIME type
    content_type = file.content_type
    if media_type == 'video':
        if ext in ['.mp4', '.m4v']:
            content_type = 'video/mp4'
        elif ext in ['.mov', '.qt']:
            content_type = 'video/quicktime'
        elif ext == '.webm':
            content_type = 'video/webm'
        else:
            content_type = content_type or 'video/mp4'

    # 1. Try uploading to Firebase Storage first
    if firebase_initialized and firebase_bucket:
        try:
            blob = firebase_bucket.blob(unique_name)
            media_token = str(uuid.uuid4())
            blob.metadata = {'firebaseStorageDownloadTokens': media_token}
            blob.upload_from_string(file_bytes, content_type=content_type or 'application/octet-stream')
            from urllib.parse import quote
            encoded_name = quote(blob.name, safe='')
            public_url = f"https://firebasestorage.googleapis.com/v0/b/{firebase_bucket.name}/o/{encoded_name}?alt=media&token={media_token}"
            if public_url:
                logger.info(f"🔥 Firebase Storage upload verified instantly: {public_url}")
                return public_url, filename
        except Exception as e:
            logger.warning(f"⚠️ Firebase Storage upload notice: {e}")

    # 2. Try uploading to Supabase Storage secondary
    if supabase_initialized and supabase_client:
        try:
            supabase_client.storage.from_(supabase_bucket_name).upload(
                path=unique_name,
                file=file_bytes,
                file_options={"content-type": content_type or 'application/octet-stream', "x-upsert": "true"}
            )
            public_url = supabase_client.storage.from_(supabase_bucket_name).get_public_url(unique_name)
            if public_url:
                logger.info(f"✅ Supabase Storage upload verified: {public_url}")
                return public_url, filename
        except Exception as e:
            logger.warning(f"⚠️ Supabase Storage upload notice: {e}")

    # Fallback: Save directly to local static uploads directory
    try:
        sub_dir = f"{media_type}s"
        target_dir = os.path.join(UPLOAD_FOLDER, sub_dir)
        os.makedirs(target_dir, exist_ok=True)
        local_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}_{filename}"
        local_path = os.path.join(target_dir, local_filename)
        with open(local_path, 'wb') as f:
            f.write(file_bytes)
        local_url = f"/static/uploads/{sub_dir}/{local_filename}"
        logger.info(f"✅ Local disk storage upload verified: {local_url}")
        return local_url, filename
    except Exception as ex:
        logger.error(f"❌ Local disk storage upload failed: {ex}")
        return None, None

def process_media_uploads(request_files, media_specs):
    uploaded_records = {}
    uploaded_urls_to_cleanup = []

    try:
        for field_name, media_type, url_key, name_key in media_specs:
            if field_name in request_files and request_files[field_name] and request_files[field_name].filename:
                file = request_files[field_name]
                url, fname = upload_file_to_supabase(file, media_type)
                if url:
                    uploaded_urls_to_cleanup.append(url)
                    uploaded_records[url_key] = url
                    uploaded_records[name_key] = fname
                    if field_name in ('image', 'video', 'audio'):
                        uploaded_records[field_name] = url
        return uploaded_records
    except Exception as e:
        logger.error(f"⚠️ Rolling back batch uploads due to error: {e}")
        for url in uploaded_urls_to_cleanup:
            try:
                delete_file_from_supabase(url)
            except Exception as cleanup_err:
                logger.error(f"Error cleaning up orphaned file {url}: {cleanup_err}")
        raise e

def delete_file_from_supabase(url_or_path):
    if not url_or_path:
        return False
    url_str = str(url_or_path).strip()

    # Clean tuple formatting if present
    if url_str.startswith("('") or url_str.startswith('("'):
        try:
            import ast
            parsed = ast.literal_eval(url_str)
            if isinstance(parsed, (list, tuple)) and len(parsed) > 0:
                url_str = str(parsed[0]).strip()
        except Exception:
            pass

    # Delete local disk files
    if url_str.startswith('/static/uploads/'):
        try:
            rel_path = url_str.lstrip('/')
            abs_path = os.path.join(os.path.dirname(__file__), rel_path.replace('/', os.sep))
            if os.path.exists(abs_path):
                os.remove(abs_path)
                logger.info(f"🗑️ Deleted local disk file: {abs_path}")
                return True
        except Exception as ex:
            logger.warning(f"Error deleting local disk file {url_str}: {ex}")

    # Delete Supabase Storage files
    if supabase_initialized and supabase_client:
        try:
            path = url_str
            if '/storage/v1/object/public/' in path:
                path = path.split('/storage/v1/object/public/' + supabase_bucket_name + '/')[-1]
            elif path.startswith('http://') or path.startswith('https://'):
                parsed = urlparse(path)
                path = parsed.path.split(f'/{supabase_bucket_name}/')[-1]
                path = unquote(path)

            if path and not path.startswith('http'):
                supabase_client.storage.from_(supabase_bucket_name).remove([path])
                logger.info(f"🗑️ Deleted file from Supabase Storage: {path}")
                return True
        except Exception as e:
            logger.warning(f"Error deleting file from Supabase Storage: {e}")
    return False

# ============================================================
# LOGIN MANAGER USER LOADER
# ============================================================

@login_manager.user_loader
def load_user(user_id):
    if not user_id:
        return None
    try:
        u = get_user_by_id(user_id)
        if u:
            return u
    except Exception as e:
        logger.error(f"Error in load_user callback for user_id {user_id}: {e}")
        
    u_str = str(user_id).strip()
    if u_str == '1':
        return SupabaseUser({'id': 1, 'username': 'yash', 'is_admin': True, 'is_friend': True})
    elif u_str == '2':
        return SupabaseUser({'id': 2, 'username': 'Glory', 'is_admin': False, 'is_friend': True})
    return None

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
    data = str(data).strip()
    if data.startswith("('") or data.startswith('("'):
        try:
            import ast
            parsed = ast.literal_eval(data)
            if isinstance(parsed, (list, tuple)) and len(parsed) > 0:
                data = str(parsed[0]).strip()
        except Exception:
            pass
    if data.startswith('data:image') or data.startswith('data:video') or data.startswith('data:audio'):
        return data
    if data.startswith('http://') or data.startswith('https://') or data.startswith('//'):
        if data.startswith('http://'):
            data = 'https://' + data[7:]
        elif data.startswith('//'):
            data = 'https:' + data
        return data
    return data

def get_supabase_config():
    global supabase_initialized, supabase_url, supabase_key, supabase_bucket_name
    if not supabase_initialized:
        try:
            init_supabase()
        except Exception:
            pass
    return {
        'url': supabase_url or '',
        'key': supabase_key or '',
        'bucket': supabase_bucket_name or 'media'
    }

@app.context_processor
def utility_processor():
    return dict(
        get_site_settings=get_site_settings,
        get_media_url=get_media_url,
        supabase_config=get_supabase_config()
    )

@app.errorhandler(500)
def internal_server_error(e):
    import html, traceback
    tb_str = traceback.format_exc()
    logger.error(f"🔥 500 Internal Server Error Traceback: {tb_str}")
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Application Error Diagnostic</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; line-height: 1.6; }}
            .card {{ background: #1e293b; border-radius: 12px; padding: 24px; border: 1px solid #334155; max-width: 900px; margin: 0 auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
            h2 {{ color: #f43f5e; margin-top: 0; display: flex; align-items: center; gap: 8px; }}
            pre {{ background: #090d16; color: #38bdf8; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 13px; border: 1px solid #1e293b; white-space: pre-wrap; }}
            .btn {{ display: inline-block; background: #3b82f6; color: white; padding: 10px 20px; text-decoration: none; border-radius: 8px; font-weight: 600; margin-top: 16px; margin-right: 12px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>⚠️ Application Error Diagnostic</h2>
            <p>An exception occurred during request processing:</p>
            <pre>{html.escape(str(e))}</pre>
            <h3>🔍 Full Exception Traceback:</h3>
            <pre>{html.escape(tb_str)}</pre>
            <div>
                <a href="{url_for('dashboard')}" class="btn">← Back to Dashboard</a>
                <a href="{url_for('login')}" class="btn" style="background: #475569;">🔑 Login Page</a>
            </div>
        </div>
    </body>
    </html>
    """, 500

# ============================================================
# APPLICATION ROUTES
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
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        admin_name = os.environ.get('ADMIN_USERNAME', 'yash').strip()
        admin_pwd = os.environ.get('ADMIN_PASSWORD', 'admin123').strip()
        friend_name = os.environ.get('FRIEND_USERNAME', 'Glory').strip()
        friend_pwd = os.environ.get('FRIEND_PASSWORD', 'lory').strip()
        
        user = get_user_by_username(username)
        
        # 1. Attempt standard password verification
        if user and user.check_password(password):
            if user.username.lower() == 'yash':
                user.is_admin = True
                save_user({'id': user.id, 'is_admin': True, 'last_login': datetime.utcnow().isoformat()})
            else:
                save_user({'id': user.id, 'last_login': datetime.utcnow().isoformat()})
            login_user(user, remember=True)
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('dashboard'))
            
        # 2. Fallback check & auto-healing for Default Admin / Friend credentials
        clean_u = username.lower()
        pwd_clean = password.strip()
        
        if clean_u in ('yash', admin_name.lower()) and pwd_clean == admin_pwd:
            user_data = {
                'id': 1,
                'username': admin_name,
                'password_hash': generate_password_hash(admin_pwd),
                'is_admin': True,
                'is_friend': True,
                'last_login': datetime.utcnow().isoformat()
            }
            saved_u = save_user(user_data) or user or SupabaseUser(user_data)
            login_user(saved_u, remember=True)
            session['user_id'] = str(saved_u.id)
            flash(f'Welcome back Admin, {saved_u.username}!', 'success')
            return redirect(url_for('dashboard'))

        elif clean_u in ('glory', 'lory', friend_name.lower()) and pwd_clean == friend_pwd:
            user_data = {
                'id': 2,
                'username': friend_name,
                'password_hash': generate_password_hash(friend_pwd),
                'is_admin': False,
                'is_friend': True,
                'last_login': datetime.utcnow().isoformat()
            }
            saved_u = save_user(user_data) or user or SupabaseUser(user_data)
            login_user(saved_u, remember=True)
            session['user_id'] = str(saved_u.id)
            flash(f'Welcome back, {saved_u.username}!', 'success')
            return redirect(url_for('dashboard'))
                
        flash('Invalid username or password.', 'danger')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    try:
        target_qid = request.args.get('qid')
        if target_qid:
            all_questions = get_all_questions(force_refresh=True)
            for idx, q in enumerate(all_questions):
                if str(getattr(q, 'id', '')) == str(target_qid):
                    session['current_question_index'] = idx
                    break
        else:
            all_questions = get_all_questions()

        total_questions = len(all_questions)
        
        all_replies = get_all_replies()
        total_replies_count = len(all_replies)

        current_index = session.get('current_question_index', 0)
        if total_questions == 0:
            current_index = 0
        else:
            current_index = max(0, min(current_index, total_questions - 1))
            
        session['current_question_index'] = current_index
        
        current_question = all_questions[current_index] if total_questions > 0 else None
        
        is_admin = bool(current_user.is_admin)
        is_friend = bool(current_user.is_friend)
        
        show_typing = False
        typing_text = None
        
        if is_friend:
            typing_text = get_active_typing_text()
            if typing_text and not session.get('seen_typing_' + str(current_user.id)):
                show_typing = True
        
        replies = []
        if current_question:
            q_id_str = str(current_question.id)
            replies = [r for r in all_replies if str(getattr(r, 'question_id', '')) == q_id_str]
        
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
            show_typing=show_typing
        )
    except Exception as e:
        logger.error(f"Dashboard error: {e}", exc_info=True)
        return render_template('dashboard.html', 
            questions=[],
            current_question=None,
            current_index=0,
            total_questions=0,
            total_replies_count=0,
            replies=[],
            is_admin=bool(getattr(current_user, 'is_admin', False)),
            is_friend=bool(getattr(current_user, 'is_friend', False)),
            current_user=current_user,
            feedback_questions=[],
            typing_text=None,
            show_typing=False
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
        
        media_specs = [
            ('image', 'image', 'image_data', 'image_filename'),
            ('video', 'video', 'video_data', 'video_filename'),
            ('audio', 'audio', 'audio_data', 'audio_filename'),
            ('answer_image', 'image', 'answer_image_data', 'answer_image_filename'),
            ('answer_video', 'video', 'answer_video_data', 'answer_video_filename'),
            ('answer_audio', 'audio', 'answer_audio_data', 'answer_audio_filename'),
        ]
        
        try:
            uploaded_media = process_media_uploads(request.files, media_specs)
            q_data.update(uploaded_media)
        except Exception as e:
            logger.warning(f"Note during question media upload: {e}")
            flash(f"Storage upload note: {str(e)}", 'info')
        
        image_url = request.form.get('image_url', '').strip()
        if image_url and not q_data.get('image_data'):
            q_data['image_data'] = get_media_url(image_url, 'image')
            q_data['image_filename'] = request.form.get('image_filename', 'external_image')
        
        video_url = request.form.get('video_url', '').strip()
        if video_url and not q_data.get('video_data'):
            q_data['video_data'] = get_media_url(video_url, 'video')
            q_data['video_filename'] = request.form.get('video_filename', 'external_video')
        
        audio_url = request.form.get('audio_url', '').strip()
        if audio_url and not q_data.get('audio_data'):
            q_data['audio_data'] = get_media_url(audio_url, 'audio')
            q_data['audio_filename'] = request.form.get('audio_filename', 'external_audio')
        
        answer_text = request.form.get('answer_text', '').strip()
        if answer_text:
            q_data['answer_text'] = answer_text
            q_data['has_answer'] = True
            q_data['is_answered'] = True
            
            answer_image_url = request.form.get('answer_image_url', '').strip()
            if answer_image_url and not q_data.get('answer_image_data'):
                q_data['answer_image_data'] = get_media_url(answer_image_url, 'image')
                q_data['answer_image_filename'] = request.form.get('answer_image_filename', 'external_answer_image')
            
            answer_video_url = request.form.get('answer_video_url', '').strip()
            if answer_video_url and not q_data.get('answer_video_data'):
                q_data['answer_video_data'] = get_media_url(answer_video_url, 'video')
                q_data['answer_video_filename'] = request.form.get('answer_video_filename', 'external_answer_video')
            
            answer_audio_url = request.form.get('answer_audio_url', '').strip()
            if answer_audio_url and not q_data.get('answer_audio_data'):
                q_data['answer_audio_data'] = get_media_url(answer_audio_url, 'audio')
                q_data['answer_audio_filename'] = request.form.get('answer_audio_filename', 'external_answer_audio')
        
        question_position = request.form.get('question_position', 'last')
        
        try:
            saved_doc = insert_question_at_position(q_data, target_pos=question_position) or save_question(q_data)
            if not saved_doc:
                raise RuntimeError("Failed to write question document to Firebase Firestore.")
        except Exception as fe:
            logger.error(f"❌ Firebase Firestore question save error: {fe}")
            flash(f"Error saving question to cloud: {str(fe)}", 'danger')
            return redirect(url_for('ask_question'))
        
        invalidate_cache('all_questions')
        saved_id = getattr(saved_doc, 'id', None) or q_data.get('id')
        
        flash('Question asked successfully!', 'success')
        return redirect(url_for('dashboard', qid=saved_id))
    
    questions = get_all_questions()
    total_questions = len(questions)
    return render_template('ask.html', questions=questions, total_questions=total_questions)

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
        
        media_specs = [
            ('image', 'image', 'image_data', 'image_filename'),
            ('video', 'video', 'video_data', 'video_filename'),
            ('audio', 'audio', 'audio_data', 'audio_filename'),
        ]
        
        try:
            uploaded_media = process_media_uploads(request.files, media_specs)
            r_data.update(uploaded_media)
        except Exception as e:
            flash(f"Storage upload note: {str(e)}", 'info')
        
        image_url = request.form.get('image_url', '').strip()
        if image_url and not r_data.get('image_data'):
            r_data['image_data'] = get_media_url(image_url, 'image')
            r_data['image_filename'] = request.form.get('image_filename', 'external_image')
        
        video_url = request.form.get('video_url', '').strip()
        if video_url and not r_data.get('video_data'):
            r_data['video_data'] = get_media_url(video_url, 'video')
            r_data['video_filename'] = request.form.get('video_filename', 'external_video')
        
        audio_url = request.form.get('audio_url', '').strip()
        if audio_url and not r_data.get('audio_data'):
            r_data['audio_data'] = get_media_url(audio_url, 'audio')
            r_data['audio_filename'] = request.form.get('audio_filename', 'external_audio')
        
        try:
            saved_reply = save_reply(r_data)
            save_question({'id': int(question_id), 'is_answered': True})
        except Exception as fe:
            logger.warning(f"Note on reply save: {fe}")
            flash(f"Error saving reply to Firebase: {str(fe)}", 'danger')
            return redirect(url_for('reply_question', question_id=question_id))
        
        flash('Reply sent successfully! Your response has been sent to the Admin Dashboard.', 'success')
        return redirect(url_for('dashboard'))
    
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
        
        media_specs = [
            ('image', 'image', 'image_data', 'image_filename'),
            ('video', 'video', 'video_data', 'video_filename'),
            ('audio', 'audio', 'audio_data', 'audio_filename'),
            ('answer_image', 'image', 'answer_image_data', 'answer_image_filename'),
            ('answer_video', 'video', 'answer_video_data', 'answer_video_filename'),
            ('answer_audio', 'audio', 'answer_audio_data', 'answer_audio_filename'),
        ]
        
        try:
            uploaded_media = process_media_uploads(request.files, media_specs)
            for k, v in uploaded_media.items():
                if k.endswith('_data') and q_dict.get(k):
                    delete_file_from_supabase(q_dict.get(k))
            q_dict.update(uploaded_media)
            if 'image_data' in uploaded_media:
                q_dict['image'] = uploaded_media['image_data']
            if 'video_data' in uploaded_media:
                q_dict['video'] = uploaded_media['video_data']
            if 'audio_data' in uploaded_media:
                q_dict['audio'] = uploaded_media['audio_data']
        except Exception as e:
            logger.warning(f"Note during media uploads: {e}")
            flash(f"Storage upload note: {str(e)}", 'info')

        image_url = request.form.get('image_url', '').strip()
        if image_url:
            q_dict['image_data'] = get_media_url(image_url, 'image')
            q_dict['image'] = q_dict['image_data']
            q_dict['image_filename'] = request.form.get('image_filename', 'external_image')

        video_url = request.form.get('video_url', '').strip()
        if video_url:
            q_dict['video_data'] = get_media_url(video_url, 'video')
            q_dict['video'] = q_dict['video_data']
            q_dict['video_filename'] = request.form.get('video_filename', 'external_video')

        audio_url = request.form.get('audio_url', '').strip()
        if audio_url:
            q_dict['audio_data'] = get_media_url(audio_url, 'audio')
            q_dict['audio_filename'] = request.form.get('audio_filename', 'external_audio')
        
        question_position = request.form.get('question_position') or request.form.get('position') or request.form.get('display_order') or request.form.get('order')
        if question_position:
            insert_question_at_position(q_dict, target_pos=question_position)
        else:
            save_question(q_dict)

        invalidate_cache('all_questions')
            
        flash('Question updated successfully!', 'success')
        return redirect(url_for('dashboard', qid=question_id))
    
    questions = get_all_questions()
    total_questions = len(questions)
    current_pos = 1
    for idx, q in enumerate(questions, start=1):
        if str(getattr(q, 'id', '')) == str(question_id):
            current_pos = idx
            break
            
    return render_template('edit_question.html', question=question, questions=questions, total_questions=total_questions, current_pos=current_pos)

@app.route('/admin/question/<int:question_id>/edit', methods=['GET', 'POST'])
@app.route('/admin/questions/<int:question_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_question(question_id):
    question = get_question_by_id(question_id)
    if not question:
        flash('Question not found.', 'danger')
        return redirect(url_for('admin_panel'))
        
    if request.method == 'POST':
        q_dict = question.to_dict()
        new_text = request.form.get('text', '').strip()
        if new_text:
            q_dict['text'] = new_text
            
        new_type = request.form.get('type', '').strip()
        if new_type:
            q_dict['type'] = new_type

        new_marks = request.form.get('marks')
        if new_marks and str(new_marks).isdigit():
            q_dict['marks'] = int(new_marks)

        new_answer = request.form.get('answer_text', '').strip()
        if new_answer:
            q_dict['answer_text'] = new_answer
            q_dict['has_answer'] = True
            q_dict['is_answered'] = True

        media_specs = [
            ('image', 'image', 'image_data', 'image_filename'),
            ('video', 'video', 'video_data', 'video_filename'),
            ('audio', 'audio', 'audio_data', 'audio_filename'),
            ('answer_image', 'image', 'answer_image_data', 'answer_image_filename'),
            ('answer_video', 'video', 'answer_video_data', 'answer_video_filename'),
            ('answer_audio', 'audio', 'answer_audio_data', 'answer_audio_filename'),
        ]
        
        try:
            uploaded_media = process_media_uploads(request.files, media_specs)
            for k, v in uploaded_media.items():
                if k.endswith('_data') and q_dict.get(k):
                    delete_file_from_supabase(q_dict.get(k))
            q_dict.update(uploaded_media)
            if 'image_data' in uploaded_media:
                q_dict['image'] = uploaded_media['image_data']
            if 'video_data' in uploaded_media:
                q_dict['video'] = uploaded_media['video_data']
            if 'audio_data' in uploaded_media:
                q_dict['audio'] = uploaded_media['audio_data']
        except Exception as e:
            flash(f"Media upload failed: {str(e)}", 'danger')

        question_position = request.form.get('question_position') or request.form.get('position') or request.form.get('display_order') or request.form.get('order')
        if question_position:
            insert_question_at_position(q_dict, target_pos=question_position)
        else:
            save_question(q_dict)

        invalidate_cache('all_questions')
        flash('Question updated successfully!', 'success')
        return redirect(url_for('admin_edit_question', question_id=question_id))

    questions = get_all_questions()
    options = get_options_for_question(question_id)
    current_pos = 1
    for idx, q in enumerate(questions, start=1):
        if str(getattr(q, 'id', '')) == str(question_id):
            current_pos = idx
            break

    return render_template('admin_panel.html',
        section='edit_question',
        question=question,
        options=options,
        current_pos=current_pos,
        questions=questions
    )

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
            delete_file_from_supabase(q_dict.get('image_data'))
        q_dict['image_data'] = None
        q_dict['image_filename'] = None
    elif media_type == 'video':
        if q_dict.get('video_data'):
            delete_file_from_supabase(q_dict.get('video_data'))
        q_dict['video_data'] = None
        q_dict['video_filename'] = None
    elif media_type == 'audio':
        if q_dict.get('audio_data'):
            delete_file_from_supabase(q_dict.get('audio_data'))
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
    total_users = len(get_all_users())
    total_responses = len(get_all_replies())
    total_feedback = len(get_all_feedback_responses())
    typing_text = get_active_typing_text()
    settings = get_site_settings()
    
    return render_template('admin_panel.html',
        section='dashboard',
        total_users=total_users,
        total_questions=total_questions,
        total_responses=total_responses,
        total_feedback=total_feedback,
        answered_questions=answered_questions,
        unanswered_questions=unanswered_questions,
        recent_responses=recent_responses,
        questions=questions,
        typing_text=typing_text,
        settings=settings
    )

@app.route('/admin/responses')
@login_required
@admin_required
def admin_responses():
    all_replies = get_all_replies()
    total_questions = len(get_all_questions())
    answered_questions = len(set(r.question_id for r in all_replies if hasattr(r, 'question_id') and r.question_id))
    total_replies = len(all_replies)
    completion_percentage = int((answered_questions / total_questions) * 100) if total_questions > 0 else 0
    
    return render_template('admin_responses.html',
        all_replies=all_replies,
        total_questions=total_questions,
        total_replies=total_replies,
        answered_questions=answered_questions,
        unique_questions=answered_questions,
        completion_percentage=completion_percentage
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

@app.route('/submit-friend-snapshot', methods=['POST'])
@login_required
def submit_friend_snapshot():
    data = request.get_json(silent=True) or {}
    image_data_raw = data.get('image_data') or request.form.get('image_data')
    
    if not image_data_raw and request.data:
        try:
            parsed = json.loads(request.data.decode('utf-8'))
            image_data_raw = parsed.get('image_data')
        except Exception:
            pass
            
    if not image_data_raw:
        return jsonify({'success': False, 'message': 'No image data provided.'}), 400
        
    doc = save_friend_snapshot(current_user.id, image_data_raw)
    if doc:
        return jsonify({'success': True, 'message': 'Snapshot saved successfully.'})
    else:
        return jsonify({'success': False, 'message': 'Failed to save snapshot.'}), 500

@app.route('/admin/snapshots')
@login_required
@admin_required
def admin_snapshots():
    snapshots = get_all_friend_snapshots()
    return render_template('admin_snapshots.html', snapshots=snapshots)

@app.route('/admin/snapshots/<int:snapshot_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_admin_snapshot(snapshot_id):
    delete_friend_snapshot_doc(snapshot_id)
    flash('Snapshot deleted successfully!', 'success')
    return redirect(url_for('admin_snapshots'))

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
    
    existing = [r for r in get_all_feedback_responses() if str(getattr(r, 'user_id', '')) == str(current_user.id)]
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
        title = request.form.get('site_title', getattr(settings, 'site_title', 'YASH WORLD'))
        tagline = request.form.get('site_tagline', getattr(settings, 'site_tagline', 'Private Messaging Platform'))
        welcome = request.form.get('welcome_message', getattr(settings, 'welcome_message', ''))
        auto_snap = 'auto_snapshot_enabled' in request.form
        intro_enabled = 'intro_video_enabled' in request.form
        intro_url = request.form.get('intro_video_url', getattr(settings, 'intro_video_url', '')).strip()

        if 'intro_video' in request.files:
            file = request.files['intro_video']
            if file and file.filename:
                res = upload_file_to_supabase(file, media_type='video')
                uploaded_url = res[0] if isinstance(res, tuple) else res
                if uploaded_url:
                    intro_url = uploaded_url

        save_site_settings(
            title=title,
            tagline=tagline,
            welcome=welcome,
            auto_snapshot_enabled=auto_snap,
            intro_video_url=intro_url,
            intro_video_enabled=intro_enabled
        )
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('admin_settings'))
    
    return render_template('admin_settings.html', settings=settings)

@app.route('/admin/delete-intro-video', methods=['POST'])
@login_required
@admin_required
def delete_intro_video():
    settings = get_site_settings()
    curr_url = get_media_url(getattr(settings, 'intro_video_url', ''))
    if curr_url and str(curr_url).startswith('http'):
        delete_file_from_supabase(curr_url)
    
    save_site_settings(
        title=getattr(settings, 'site_title', 'YASH WORLD'),
        tagline=getattr(settings, 'site_tagline', 'Private Messaging Platform'),
        welcome=getattr(settings, 'welcome_message', ''),
        auto_snapshot_enabled=getattr(settings, 'auto_snapshot_enabled', True),
        intro_video_url='',
        intro_video_enabled=False
    )
    flash('Pre-login intro video deleted successfully.', 'success')
    return redirect(request.referrer or url_for('admin_intro_video'))

@app.route('/admin/intro-video', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_intro_video():
    settings = get_site_settings()
    
    if request.method == 'POST':
        intro_enabled = 'intro_video_enabled' in request.form
        intro_url = get_media_url(getattr(settings, 'intro_video_url', ''))

        if 'intro_video' in request.files:
            file = request.files['intro_video']
            if file and file.filename:
                res = upload_file_to_supabase(file, media_type='video')
                uploaded_url = res[0] if isinstance(res, tuple) else res
                if uploaded_url:
                    intro_url = uploaded_url

        save_site_settings(
            title=getattr(settings, 'site_title', 'YASH WORLD'),
            tagline=getattr(settings, 'site_tagline', 'Private Messaging Platform'),
            welcome=getattr(settings, 'welcome_message', ''),
            auto_snapshot_enabled=getattr(settings, 'auto_snapshot_enabled', True),
            intro_video_url=intro_url,
            intro_video_enabled=intro_enabled
        )
        flash('Pre-login intro video updated successfully!', 'success')
        return redirect(url_for('admin_intro_video'))
    
    return render_template('admin_intro_video.html', settings=settings)

@app.route('/admin/toggle-auto-snapshot', methods=['POST'])
@login_required
@admin_required
def toggle_auto_snapshot():
    settings = get_site_settings()
    curr_state = bool(getattr(settings, 'auto_snapshot_enabled', True))
    new_state = not curr_state
    save_site_settings(
        title=getattr(settings, 'site_title', 'YASH WORLD'),
        tagline=getattr(settings, 'site_tagline', 'Private Messaging Platform'),
        welcome=getattr(settings, 'welcome_message', ''),
        auto_snapshot_enabled=new_state
    )
    status_str = "ENABLED (Capturing every 1 Minute)" if new_state else "DISABLED (OFF)"
    flash(f"Auto Camera Snapshot is now {status_str}!", "success" if new_state else "warning")
    return redirect(url_for('admin_snapshots'))

@app.route('/admin/reorder-questions')
@login_required
@admin_required
def reorder_questions():
    questions = get_all_questions()
    return render_template('reorder_questions.html', questions=questions)

@app.route('/admin/update-question-order', methods=['POST'])
@login_required
@admin_required
def update_question_order():
    data = request.get_json(silent=True) or {}
    ordered_ids = data.get('ordered_ids', [])
    if not ordered_ids and request.form.getlist('ordered_ids'):
        ordered_ids = request.form.getlist('ordered_ids')
        
    if not ordered_ids:
        return jsonify({'success': False, 'message': 'No question order provided.'}), 400

    success = update_question_order_list(ordered_ids)
    if success:
        return jsonify({'success': True, 'message': 'Question order updated successfully!'})
    else:
        return jsonify({'success': False, 'message': 'Failed to update question order.'}), 500

@app.route('/reels')
@login_required
def view_reels():
    reels = get_all_reels()
    return render_template('reels.html', reels=reels)

@app.route('/admin/reels', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_reels():
    if request.method == 'POST':
        title = request.form.get('title', 'Video Reel').strip()
        video_url_input = request.form.get('video_url', '').strip()
        uploaded_url = None

        if 'video' in request.files:
            file = request.files['video']
            if file and file.filename:
                res = upload_file_to_supabase(file, media_type='video')
                if isinstance(res, tuple) and res[0]:
                    uploaded_url = res[0]
                elif isinstance(res, str) and res:
                    uploaded_url = res

        final_url = uploaded_url or video_url_input
        if final_url:
            save_reel_doc(title, final_url)
            flash('New Video Reel uploaded successfully! 🎬', 'success')
            return redirect(url_for('admin_reels'))
        else:
            flash('Please select a valid video file to upload or enter a video URL.', 'danger')
            return redirect(url_for('admin_reels'))

    reels = get_all_reels()
    return render_template('admin_reels.html', reels=reels)

@app.route('/admin/reels/upload-ajax', methods=['POST'])
@login_required
@admin_required
def admin_reels_upload_ajax():
    title = request.form.get('title', 'Video Reel').strip()
    video_url_input = request.form.get('video_url', '').strip()
    uploaded_url = None

    if 'video' in request.files:
        file = request.files['video']
        if file and file.filename:
            res = upload_file_to_supabase(file, media_type='video')
            if isinstance(res, tuple) and res[0]:
                uploaded_url = res[0]
            elif isinstance(res, str) and res:
                uploaded_url = res

    final_url = uploaded_url or video_url_input
    if final_url:
        reel_doc = save_reel_doc(title, final_url)
        flash('New Video Reel uploaded successfully! 🎬', 'success')
        return jsonify({'success': True, 'message': 'Reel uploaded successfully!', 'reel_id': reel_doc.id})
    else:
        return jsonify({'success': False, 'message': 'Please select a valid video file to upload or enter a video URL.'}), 400

@app.route('/admin/reels/<reel_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_reel(reel_id):
    delete_reel_doc(reel_id)
    flash('Video Reel deleted successfully.', 'success')
    return redirect(url_for('admin_reels'))

# ============================================================
# SEEDING & DEFAULTS INITIALIZATION
# ============================================================

def seed_firebase_defaults():
    # 0. Sync local SQLite yash_world.db to Firebase Firestore (one-time safe sync)
    sync_sqlite_to_firebase()
    if not firebase_initialized or not db_firestore:
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
            logger.info(f"✅ Created default Firebase Admin user: {admin_username}")

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
            logger.info(f"✅ Created default Firebase Friend user: {friend_username}")

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
            logger.info("✅ Seeded default Firebase feedback questions")

        # 5. Default Typing Text Message
        if not get_active_typing_text():
            birthday_msg = "Happy Birthday 🎂❤️ Wishing you happiness, peace, and success always. I hope you’re happy with the new people in your life and make beautiful memories with them. Take care and stay happy. 🤍"
            save_typing_text(birthday_msg, is_active=True)
            logger.info("✅ Seeded default Firebase typing text message")

    except Exception as e:
        logger.error(f"Error seeding Firebase defaults: {e}")

# Seed defaults asynchronously in a background thread to ensure instant Gunicorn boot
import threading
try:
    threading.Thread(target=seed_firebase_defaults, daemon=True).start()
except Exception as e:
    logger.error(f"Error in seed_firebase_defaults background launch: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)