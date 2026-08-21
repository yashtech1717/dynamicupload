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
from datetime import datetime
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
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'yash-world-secret-key-2024')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max payload limit

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ============================================================
# SUPABASE DATABASE & STORAGE CONFIGURATION
# ============================================================

supabase_initialized = False
supabase_client = None
supabase_url = None
supabase_key = None
supabase_bucket_name = 'media'

def init_supabase():
    global supabase_initialized, supabase_client, supabase_url, supabase_key, supabase_bucket_name
    if supabase_initialized:
        return

    try:
        from supabase import create_client, Client

        supabase_url = os.environ.get('SUPABASE_URL', '').strip()
        supabase_key = os.environ.get('SUPABASE_KEY', '').strip()
        supabase_bucket_name = os.environ.get('SUPABASE_BUCKET', 'media').strip()

        if not supabase_url or not supabase_key:
            # Fallback check for local development credentials
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
        else:
            logger.error("❌ CRITICAL: No Supabase credentials found. Please set SUPABASE_URL and SUPABASE_KEY environment variables!")
            raise RuntimeError("CRITICAL: No Supabase credentials found. Please set SUPABASE_URL and SUPABASE_KEY environment variables.")
    except Exception as e:
        logger.error(f"❌ Supabase initialization error: {e}")
        raise RuntimeError(f"Supabase initialization failed: {e}")

# Call Supabase init
init_supabase()

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
        self.id = str(self._data.get('id', ''))
        self.username = self._data.get('username', '')
        self.password_hash = self._data.get('password_hash', '')
        self.is_admin = bool(self._data.get('is_admin', False))
        self.is_friend = bool(self._data.get('is_friend', False))
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

def get_user_by_id(user_id):
    if not user_id:
        return None
    if not supabase_initialized or not supabase_client:
        raise RuntimeError("Supabase PostgreSQL is unavailable. Database connection failed.")
    try:
        target_id = int(user_id) if str(user_id).isdigit() else user_id
        res = supabase_client.table('users').select('*').eq('id', target_id).limit(1).execute()
        if res.data and len(res.data) > 0:
            return SupabaseUser(res.data[0])
        return None
    except Exception as e:
        logger.error(f"Error fetching user by id {user_id}: {e}")
        raise RuntimeError(f"Supabase read error fetching user {user_id}: {e}")

def get_user_by_username(username):
    if not username:
        return None
    if not supabase_initialized or not supabase_client:
        raise RuntimeError("Supabase PostgreSQL is unavailable. Database connection failed.")
    try:
        clean_user = username.strip()
        # 1. Exact username match
        res = supabase_client.table('users').select('*').eq('username', clean_user).limit(1).execute()
        if res.data and len(res.data) > 0:
            return SupabaseUser(res.data[0])
            
        # 2. Case-insensitive ilike match
        res_ilike = supabase_client.table('users').select('*').ilike('username', clean_user).limit(1).execute()
        if res_ilike.data and len(res_ilike.data) > 0:
            return SupabaseUser(res_ilike.data[0])
            
        # 3. Fallback: all users case-insensitive search
        all_u = get_all_users()
        for u in all_u:
            if u.username.lower() == clean_user.lower():
                return u
        return None
    except Exception as e:
        logger.error(f"Error fetching user by username {username}: {e}")
        raise RuntimeError(f"Supabase read error fetching user {username}: {e}")

def get_all_users():
    if not supabase_initialized or not supabase_client:
        raise RuntimeError("Supabase PostgreSQL is unavailable. Database connection failed.")
    try:
        res = supabase_client.table('users').select('*').order('id').execute()
        users = [SupabaseUser(d) for d in (res.data or [])]
        return users
    except Exception as e:
        logger.error(f"Error fetching all users: {e}")
        raise RuntimeError(f"Supabase read error fetching all users: {e}")

def save_user(user_dict):
    if not supabase_initialized or not supabase_client or not user_dict:
        return None
    try:
        res = supabase_client.table('users').upsert(user_dict).execute()
        if res.data and len(res.data) > 0:
            return SupabaseUser(res.data[0])
        return get_user_by_id(user_dict.get('id'))
    except Exception as e:
        logger.error(f"Error saving user: {e}")
        return None

def get_next_id(table_name):
    if not supabase_initialized or not supabase_client:
        raise RuntimeError(f"Supabase is not initialized. Cannot generate atomic ID for {table_name}.")
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

def get_all_questions():
    def _fetch():
        if not supabase_initialized or not supabase_client:
            raise RuntimeError("Supabase PostgreSQL is unavailable. Database connection failed.")
        try:
            users_map = {str(u.id): u for u in get_all_users()}
            res = supabase_client.table('questions').select('*').execute()
            questions = []
            for d in (res.data or []):
                u_id = str(d.get('user_id'))
                asker = users_map.get(u_id)
                if asker:
                    d['asker'] = {'username': asker.username, 'id': asker.id}
                questions.append(SupabaseDoc(d))
            
            # Sort by display_order ascending if present; fallback to created_at ascending
            def sort_key(q):
                d_order = getattr(q, 'display_order', None)
                if d_order is not None and str(d_order).isdigit() and int(d_order) > 0:
                    return (0, int(d_order))
                created = getattr(q, 'created_at', None)
                dt_str = str(created.val) if hasattr(created, 'val') and created.val else str(created or '')
                return (1, dt_str)

            questions.sort(key=sort_key)
            return questions
        except Exception as e:
            logger.error(f"Error fetching all questions: {e}")
            raise RuntimeError(f"Supabase read error fetching all questions: {e}")
    return get_cached('all_questions', _fetch, ttl=5)

def insert_question_at_position(q_data, target_pos='last'):
    all_qs = get_all_questions()
    q_id = q_data.get('id')
    
    remaining = [q for q in all_qs if str(getattr(q, 'id', '')) != str(q_id) and getattr(q, 'id', None)]
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
            
    saved_q = save_question(q_data)
    if not saved_q:
        return None
        
    saved_id = getattr(saved_q, 'id', None) or q_data.get('id')
    remaining = [q for q in remaining if str(getattr(q, 'id', '')) != str(saved_id)]
    remaining.insert(idx, saved_q)
    
    base_dt = datetime(2026, 1, 1, 0, 0, 0)
    
    for order_idx, q in enumerate(remaining, start=1):
        curr_id = getattr(q, 'id', None)
        if curr_id and supabase_client:
            try:
                t_id = int(curr_id) if str(curr_id).isdigit() else curr_id
                synthetic_created = (base_dt + timedelta(seconds=order_idx)).isoformat() + 'Z'
                
                update_payload = {
                    'display_order': order_idx,
                    'created_at': synthetic_created
                }
                
                try:
                    supabase_client.table('questions').update(update_payload).eq('id', t_id).execute()
                except Exception as ex1:
                    logger.info(f"Fallback updating created_at only for Q {t_id}: {ex1}")
                    supabase_client.table('questions').update({'created_at': synthetic_created}).eq('id', t_id).execute()
            except Exception as e:
                logger.warning(f"Error re-indexing Q {curr_id}: {e}")
                
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

def save_question(q_dict):
    if not supabase_initialized or not supabase_client or not q_dict:
        return None
    try:
        clean_dict = {k: v for k, v in q_dict.items() if k not in ('asker', 'replies')}
        if 'created_at' not in clean_dict or not clean_dict['created_at']:
            clean_dict['created_at'] = datetime.utcnow().isoformat()
        clean_dict['updated_at'] = datetime.utcnow().isoformat()

        res = supabase_client.table('questions').upsert(clean_dict).execute()
        invalidate_cache('all_questions')
        logger.info("⚡ Question saved to Supabase PostgreSQL")
        if res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
        return get_question_by_id(q_dict.get('id'))
    except Exception as e:
        logger.error(f"Error saving question: {e}")
        return None

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
        if not supabase_initialized or not supabase_client:
            raise RuntimeError("Supabase PostgreSQL is unavailable. Database connection failed.")
        try:
            users_map = {str(u.id): u for u in get_all_users()}
            res = supabase_client.table('replies').select('*').order('created_at', desc=True).execute()
            
            q_res = supabase_client.table('questions').select('*').execute()
            q_map = {str(qd.get('id')): qd for qd in (q_res.data or [])}

            replies = []
            for d in (res.data or []):
                u_id = str(d.get('user_id'))
                replier = users_map.get(u_id)
                if replier:
                    d['replier'] = {'username': replier.username, 'id': replier.id}
                
                q_id = str(d.get('question_id'))
                if q_id in q_map:
                    d['question'] = q_map[q_id]
                replies.append(SupabaseDoc(d))
            return replies
        except Exception as e:
            logger.error(f"Error fetching all replies: {e}")
            raise RuntimeError(f"Supabase read error fetching all replies: {e}")
    return get_cached('all_replies', _fetch, ttl=10)

def save_reply(r_dict):
    if not supabase_initialized or not supabase_client or not r_dict:
        return None
    try:
        clean_dict = {k: v for k, v in r_dict.items() if k not in ('replier', 'question')}
        if 'created_at' not in clean_dict or not clean_dict['created_at']:
            clean_dict['created_at'] = datetime.utcnow().isoformat()
        clean_dict['updated_at'] = datetime.utcnow().isoformat()

        res = supabase_client.table('replies').upsert(clean_dict).execute()
        invalidate_cache('all_replies')
        logger.info("⚡ Reply saved to Supabase PostgreSQL")
        if res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
        return SupabaseDoc(clean_dict)
    except Exception as e:
        logger.error(f"Error saving reply: {e}")
        return None

def delete_reply_doc(reply_id):
    if not supabase_initialized or not supabase_client or not reply_id:
        return False
    try:
        target_id = int(reply_id) if str(reply_id).isdigit() else reply_id
        res = supabase_client.table('replies').select('*').eq('id', target_id).limit(1).execute()
        if res.data and len(res.data) > 0:
            d = res.data[0]
            for m_attr in ('image_data', 'video_data', 'audio_data'):
                url = d.get(m_attr)
                if url:
                    delete_file_from_supabase(url)
            supabase_client.table('replies').delete().eq('id', target_id).execute()
            invalidate_cache('all_replies')
            logger.info(f"⚡ Reply {reply_id} deleted from Supabase PostgreSQL")
            return True
    except Exception as e:
        logger.error(f"Error deleting reply {reply_id}: {e}")
    return False

def get_all_typing_texts():
    if not supabase_initialized or not supabase_client:
        return []
    try:
        res = supabase_client.table('typing_texts').select('*').order('created_at', desc=True).execute()
        texts = [SupabaseDoc(d) for d in (res.data or [])]
        return texts
    except Exception as e:
        logger.error(f"Error fetching typing texts: {e}")
        return []

def get_active_typing_text():
    if not supabase_initialized or not supabase_client:
        return None
    try:
        res = supabase_client.table('typing_texts').select('*').eq('is_active', True).limit(1).execute()
        if res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
    except Exception as e:
        logger.error(f"Error fetching active typing text: {e}")
    return None

def save_typing_text(text_str, is_active=True):
    if not supabase_initialized or not supabase_client:
        return None
    try:
        if is_active:
            supabase_client.table('typing_texts').update({'is_active': False}).neq('id', 0).execute()
        
        t_dict = {
            'text': text_str,
            'is_active': is_active,
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        res = supabase_client.table('typing_texts').insert(t_dict).execute()
        if res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
        return SupabaseDoc(t_dict)
    except Exception as e:
        logger.error(f"Error saving typing text: {e}")
        return None

def update_typing_text(text_id, text_str):
    if not supabase_initialized or not supabase_client or not text_id:
        return None
    try:
        target_id = int(text_id) if str(text_id).isdigit() else text_id
        res = supabase_client.table('typing_texts').update({
            'text': text_str,
            'updated_at': datetime.utcnow().isoformat()
        }).eq('id', target_id).execute()
        if res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
        return None
    except Exception as e:
        logger.error(f"Error updating typing text {text_id}: {e}")
        return None

def activate_typing_text(text_id):
    if not supabase_initialized or not supabase_client or not text_id:
        return False
    try:
        target_id = int(text_id) if str(text_id).isdigit() else text_id
        supabase_client.table('typing_texts').update({'is_active': False}).neq('id', 0).execute()
        supabase_client.table('typing_texts').update({'is_active': True}).eq('id', target_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error activating typing text {text_id}: {e}")
        return False

def delete_typing_text_doc(text_id):
    if not supabase_initialized or not supabase_client or not text_id:
        return False
    try:
        target_id = int(text_id) if str(text_id).isdigit() else text_id
        supabase_client.table('typing_texts').delete().eq('id', target_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error deleting typing text {text_id}: {e}")
        return False

def save_friend_snapshot(user_id, image_data_raw):
    if not supabase_initialized or not supabase_client or not user_id or not image_data_raw:
        return None
    try:
        image_url = None
        filename = f"snapshot_{user_id}_{int(datetime.utcnow().timestamp())}.jpg"
        
        if image_data_raw.startswith('data:image'):
            header, encoded = image_data_raw.split(',', 1)
            file_bytes = base64.b64decode(encoded)
            uploaded_url = upload_file_to_supabase(file_bytes, filename, content_type='image/jpeg')
            if uploaded_url:
                image_url = uploaded_url
        elif image_data_raw.startswith('http'):
            image_url = image_data_raw
            
        if not image_url:
            logger.warning("Could not upload snapshot image to Supabase Storage.")
            return None
            
        snap_dict = {
            'user_id': int(user_id) if str(user_id).isdigit() else user_id,
            'image_data': image_url,
            'image_filename': filename,
            'created_at': datetime.utcnow().isoformat()
        }
        res = supabase_client.table('friend_snapshots').insert(snap_dict).execute()
        if res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
        return SupabaseDoc(snap_dict)
    except Exception as e:
        logger.error(f"Error saving friend snapshot: {e}")
        return None

def get_all_friend_snapshots():
    if not supabase_initialized or not supabase_client:
        return []
    try:
        res = supabase_client.table('friend_snapshots').select('*').order('created_at', desc=True).execute()
        snapshots = []
        for d in (res.data or []):
            u = get_user_by_id(d.get('user_id'))
            if u:
                d['user'] = {'username': u.username, 'id': u.id}
            snapshots.append(SupabaseDoc(d))
        return snapshots
    except Exception as e:
        logger.error(f"Error fetching friend snapshots: {e}")
        return []

def delete_friend_snapshot_doc(snapshot_id):
    if not supabase_initialized or not supabase_client or not snapshot_id:
        return False
    try:
        target_id = int(snapshot_id) if str(snapshot_id).isdigit() else snapshot_id
        res = supabase_client.table('friend_snapshots').select('*').eq('id', target_id).limit(1).execute()
        if res.data and len(res.data) > 0:
            url = res.data[0].get('image_data')
            if url:
                delete_file_from_supabase(url)
        supabase_client.table('friend_snapshots').delete().eq('id', target_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error deleting friend snapshot {snapshot_id}: {e}")
        return False

def get_all_feedback_questions():
    if not supabase_initialized or not supabase_client:
        return []
    try:
        res = supabase_client.table('feedback_questions').select('*').order('created_at', desc=True).execute()
        questions = []
        for d in (res.data or []):
            r_res = supabase_client.table('feedback_responses').select('*').eq('question_id', d.get('id')).execute()
            responses = []
            for rd in (r_res.data or []):
                u = get_user_by_id(rd.get('user_id'))
                if u:
                    rd['user'] = {'username': u.username, 'id': u.id}
                responses.append(SupabaseDoc(rd))
            d['responses'] = responses
            questions.append(SupabaseDoc(d))
        return questions
    except Exception as e:
        logger.error(f"Error fetching feedback questions: {e}")
        return []

def get_active_feedback_questions():
    all_q = get_all_feedback_questions()
    return [q for q in all_q if getattr(q, 'is_active', True)]

def save_feedback_question(question_str):
    if not supabase_initialized or not supabase_client:
        return None
    try:
        q_dict = {
            'question': question_str,
            'is_active': True,
            'created_at': datetime.utcnow().isoformat()
        }
        res = supabase_client.table('feedback_questions').insert(q_dict).execute()
        if res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
        return SupabaseDoc(q_dict)
    except Exception as e:
        logger.error(f"Error saving feedback question: {e}")
        return None

def toggle_feedback_question(q_id):
    if not supabase_initialized or not supabase_client or not q_id:
        return False
    try:
        target_id = int(q_id) if str(q_id).isdigit() else q_id
        res = supabase_client.table('feedback_questions').select('is_active').eq('id', target_id).limit(1).execute()
        if res.data and len(res.data) > 0:
            curr = res.data[0].get('is_active', True)
            new_val = not curr
            supabase_client.table('feedback_questions').update({'is_active': new_val}).eq('id', target_id).execute()
            return new_val
    except Exception as e:
        logger.error(f"Error toggling feedback question {q_id}: {e}")
    return False

def delete_feedback_question_doc(q_id):
    if not supabase_initialized or not supabase_client or not q_id:
        return False
    try:
        target_id = int(q_id) if str(q_id).isdigit() else q_id
        supabase_client.table('feedback_questions').delete().eq('id', target_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error deleting feedback question {q_id}: {e}")
    return False

def save_feedback_response(user_id, question_id, rating, comment=None):
    if not supabase_initialized or not supabase_client:
        return None
    try:
        r_dict = {
            'user_id': user_id,
            'question_id': question_id,
            'rating': rating,
            'comment': comment,
            'created_at': datetime.utcnow().isoformat()
        }
        res = supabase_client.table('feedback_responses').insert(r_dict).execute()
        if res.data and len(res.data) > 0:
            return SupabaseDoc(res.data[0])
        return SupabaseDoc(r_dict)
    except Exception as e:
        logger.error(f"Error saving feedback response: {e}")
        return None

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

def get_site_settings():
    global _site_settings_cache, _site_settings_cache_time
    now = datetime.utcnow()
    if _site_settings_cache and _site_settings_cache_time and (now - _site_settings_cache_time).total_seconds() < 60:
        return _site_settings_cache

    default_settings = SupabaseDoc({
        'id': 1,
        'site_title': 'YASH WORLD',
        'site_tagline': 'Private Messaging Platform',
        'welcome_message': ''
    })

    if not supabase_initialized or not supabase_client:
        raise RuntimeError("Supabase PostgreSQL is unavailable. Database connection failed.")

    try:
        res = supabase_client.table('site_settings').select('*').eq('id', 1).limit(1).execute()
        if res.data and len(res.data) > 0:
            _site_settings_cache = SupabaseDoc(res.data[0])
            _site_settings_cache_time = now
            return _site_settings_cache
        else:
            s_dict = {
                'id': 1,
                'site_title': 'YASH WORLD',
                'site_tagline': 'Private Messaging Platform',
                'welcome_message': '',
                'created_at': datetime.utcnow().isoformat()
            }
            supabase_client.table('site_settings').upsert(s_dict).execute()
            _site_settings_cache = SupabaseDoc(s_dict)
            _site_settings_cache_time = now
            return _site_settings_cache
    except Exception as e:
        logger.error(f"Error fetching site settings: {e}")
        return default_settings

def save_site_settings(title, tagline, welcome):
    global _site_settings_cache
    _site_settings_cache = None
    if not supabase_initialized or not supabase_client:
        return None
    try:
        s_dict = {
            'id': 1,
            'site_title': title,
            'site_tagline': tagline,
            'welcome_message': welcome,
            'updated_at': datetime.utcnow().isoformat()
        }
        supabase_client.table('site_settings').upsert(s_dict).execute()
        return get_site_settings()
    except Exception as e:
        logger.error(f"Error saving site settings: {e}")
        return None

# ============================================================
# PERMANENT SUPABASE STORAGE UPLOAD & DELETE HELPERS
# ============================================================

def upload_file_to_supabase(file, media_type='image'):
    if not file or not file.filename:
        return None, None

    if not supabase_initialized or not supabase_client:
        raise RuntimeError("Supabase Storage is not initialized. Please verify SUPABASE_URL and SUPABASE_KEY environment variables.")

    filename = secure_filename(file.filename)
    if not filename:
        filename = f"{media_type}_{uuid.uuid4().hex[:6]}"

    file.seek(0)
    file_bytes = file.read()
    if not file_bytes:
        raise RuntimeError(f"Uploaded file '{filename}' payload is empty (0 bytes).")

    unique_name = f"{media_type}s/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}_{filename}"

    try:
        content_type = file.content_type
        if not content_type:
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            content_type = f"{media_type}/{ext}" if ext else 'application/octet-stream'

        # Upload file directly to Supabase Storage bucket
        supabase_client.storage.from_(supabase_bucket_name).upload(
            path=unique_name,
            file=file_bytes,
            file_options={"content-type": content_type, "x-upsert": "true"}
        )

        # Get Permanent Public URL
        public_url = supabase_client.storage.from_(supabase_bucket_name).get_public_url(unique_name)
        if not public_url:
            public_url = f"{supabase_url.rstrip('/')}/storage/v1/object/public/{supabase_bucket_name}/{unique_name}"

        logger.info(f"✅ Supabase Storage upload verified successfully on bucket '{supabase_bucket_name}': {public_url}")
        return public_url, filename
    except Exception as e:
        logger.error(f"❌ Supabase Storage upload failed for {filename}: {e}")
        raise RuntimeError(f"Supabase Storage upload failed: {str(e)}")

def process_media_uploads(request_files, media_specs):
    """
    Synchronously process multiple file uploads into Supabase Storage.
    media_specs: list of tuples: (field_name, media_type, target_url_key, target_filename_key)
    If ANY upload fails: immediately deletes all already uploaded files in this batch (preventing orphaned files) and raises RuntimeError.
    """
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
        return uploaded_records
    except Exception as e:
        logger.error(f"⚠️ Rolling back batch Supabase Storage uploads due to error: {e}")
        for url in uploaded_urls_to_cleanup:
            try:
                delete_file_from_supabase(url)
            except Exception as cleanup_err:
                logger.error(f"Error cleaning up orphaned file {url}: {cleanup_err}")
        raise e

def delete_file_from_supabase(url_or_path):
    if not supabase_initialized or not supabase_client or not url_or_path:
        return False
    try:
        path = str(url_or_path)
        if '/storage/v1/object/public/' in path:
            path = path.split('/storage/v1/object/public/' + supabase_bucket_name + '/')[-1]
        elif path.startswith('http://') or path.startswith('https://'):
            parsed = urlparse(path)
            path = parsed.path.split(f'/{supabase_bucket_name}/')[-1]
            path = unquote(path)

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
    data = str(data).strip()
    if data.startswith('http://') or data.startswith('https://') or data.startswith('//'):
        if data.startswith('http://'):
            data = 'https://' + data[7:]
        elif data.startswith('//'):
            data = 'https:' + data
        return data
    return ''

@app.context_processor
def utility_processor():
    return dict(get_site_settings=get_site_settings, get_media_url=get_media_url)

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
        
        user = get_user_by_username(username)
        if user and user.check_password(password):
            login_user(user, remember=True)
            save_user({'id': user.id, 'last_login': datetime.utcnow().isoformat()})
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('dashboard'))
        else:
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
        raise RuntimeError(f"Dashboard query error: {e}")

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
            flash(f"Supabase Storage upload failed: {str(e)}", 'danger')
            return redirect(url_for('ask_question'))
        
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
            saved_doc = insert_question_at_position(q_data, target_pos=question_position)
            if not saved_doc:
                raise RuntimeError("Supabase PostgreSQL question document write failed.")
        except Exception as fe:
            for u_key in ('image_data', 'video_data', 'audio_data', 'answer_image_data', 'answer_video_data', 'answer_audio_data'):
                if q_data.get(u_key):
                    delete_file_from_supabase(q_data[u_key])
            flash(f"Supabase PostgreSQL save failed: {str(fe)}", 'danger')
            return redirect(url_for('ask_question'))
        
        all_q = get_all_questions()
        if all_q:
            saved_id = getattr(saved_doc, 'id', None)
            new_idx = 0
            for idx, q in enumerate(all_q):
                if str(getattr(q, 'id', '')) == str(saved_id):
                    new_idx = idx
                    break
            session['current_question_index'] = new_idx
            session.modified = True
        
        flash('Question asked successfully!', 'success')
        return redirect(url_for('dashboard'))
    
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
            flash(f"Supabase Storage upload failed: {str(e)}", 'danger')
            return redirect(url_for('reply_question', question_id=question_id))
        
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
            if not saved_reply:
                raise RuntimeError("Supabase PostgreSQL reply write failed.")
            save_question({'id': int(question_id), 'is_answered': True})
        except Exception as fe:
            for u_key in ('image_data', 'video_data', 'audio_data'):
                if r_data.get(u_key):
                    delete_file_from_supabase(r_data[u_key])
            flash(f"Supabase PostgreSQL reply save failed: {str(fe)}", 'danger')
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
        ]
        
        try:
            uploaded_media = process_media_uploads(request.files, media_specs)
            for k, v in uploaded_media.items():
                if k.endswith('_data') and q_dict.get(k):
                    delete_file_from_supabase(q_dict.get(k))
            q_dict.update(uploaded_media)
        except Exception as e:
            flash(f"Supabase Storage upload failed: {str(e)}", 'danger')
            return redirect(url_for('edit_question', question_id=question_id))

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
        
        question_position = request.form.get('question_position')
        if question_position:
            insert_question_at_position(q_dict, target_pos=question_position)
        else:
            save_question(q_dict)
            
        flash('Question updated successfully!', 'success')
        return redirect(url_for('dashboard'))
    
    questions = get_all_questions()
    total_questions = len(questions)
    current_pos = 1
    for idx, q in enumerate(questions, start=1):
        if str(getattr(q, 'id', '')) == str(question_id):
            current_pos = idx
            break
            
    return render_template('edit_question.html', question=question, questions=questions, total_questions=total_questions, current_pos=current_pos)

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
    if not current_user.is_friend:
        return jsonify({'success': False, 'message': 'Only friends can submit snapshots.'}), 403
    
    data = request.get_json(silent=True) or {}
    image_data_raw = data.get('image_data') or request.form.get('image_data')
    
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

def seed_supabase_defaults():
    if not supabase_initialized or not supabase_client:
        return
    try:
        # 1. Admin User
        admin_username = os.environ.get('ADMIN_USERNAME', 'yash')
        admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
        if not get_user_by_username(admin_username):
            save_user({
                'username': admin_username,
                'password_hash': generate_password_hash(admin_password),
                'is_admin': True,
                'is_friend': True,
                'created_at': datetime.utcnow().isoformat()
            })
            logger.info(f"✅ Created default Supabase Admin user: {admin_username}")

        # 2. Friend User
        friend_username = os.environ.get('FRIEND_USERNAME', 'Glory')
        friend_password = os.environ.get('FRIEND_PASSWORD', 'lory')
        if not get_user_by_username(friend_username):
            save_user({
                'username': friend_username,
                'password_hash': generate_password_hash(friend_password),
                'is_admin': False,
                'is_friend': True,
                'created_at': datetime.utcnow().isoformat()
            })
            logger.info(f"✅ Created default Supabase Friend user: {friend_username}")

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
            logger.info("✅ Seeded default Supabase feedback questions")

        # 5. Default Typing Text Message
        if not get_active_typing_text():
            birthday_msg = "Happy Birthday 🎂❤️ Wishing you happiness, peace, and success always. I hope you’re happy with the new people in your life and make beautiful memories with them. Take care and stay happy. 🤍"
            save_typing_text(birthday_msg, is_active=True)
            logger.info("✅ Seeded default Supabase typing text message")

    except Exception as e:
        logger.error(f"Error seeding Supabase defaults: {e}")

# Seed defaults asynchronously in a background thread to ensure instant Gunicorn boot
import threading
try:
    threading.Thread(target=seed_supabase_defaults, daemon=True).start()
except Exception as e:
    logger.error(f"Error in seed_supabase_defaults background launch: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)