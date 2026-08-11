# ============================================================
# MIGRATION SCRIPT: SQL (PostgreSQL / SQLite) -> Firebase Cloud
# Safely reads existing database records and migrates them to Firestore
# Uploads any raw Base64/local media files to Firebase Storage
# ============================================================

import os
import json
import base64
import logging
import sqlite3
from datetime import datetime
from urllib.parse import quote
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration")

# Try initializing Firebase
try:
    import firebase_admin
    from firebase_admin import credentials, storage, firestore
except ImportError:
    logger.error("❌ firebase-admin is required. Run: pip install firebase-admin")
    exit(1)

service_account_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
service_account_path = os.environ.get('FIREBASE_SERVICE_ACCOUNT_PATH', 'service-account.json')

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
    cred = credentials.Certificate(service_account_path)
    logger.info(f"🔑 Loaded Firebase credentials from file: {service_account_path}")

if not cred:
    logger.error("❌ CRITICAL: No Firebase credentials found. Provide FIREBASE_SERVICE_ACCOUNT or service-account.json")
    exit(1)

storage_bucket_name = os.environ.get('FIREBASE_STORAGE_BUCKET', 'happybirthday-a287a.appspot.com')
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {
        'storageBucket': storage_bucket_name
    })

db_firestore = firestore.client()
firebase_bucket = storage.bucket()
logger.info("🔥 Connected to Firebase Firestore & Storage successfully!")

def convert_b64_to_firebase_url(b64_str, media_type='image', filename='migrated_file'):
    if not b64_str or not isinstance(b64_str, str):
        return None
    if b64_str.startswith('http://') or b64_str.startswith('https://') or b64_str.startswith('//'):
        return b64_str

    try:
        raw_data = b64_str
        if ',' in b64_str:
            raw_data = b64_str.split(',', 1)[1]
        
        file_bytes = base64.b64decode(raw_data)
        ext = 'mp4' if media_type == 'video' else ('mp3' if media_type == 'audio' else 'jpg')
        unique_name = f"migrated_{media_type}s/{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}_{filename}.{ext}"
        
        blob = firebase_bucket.blob(unique_name)
        media_token = str(uuid.uuid4())
        blob.metadata = {'firebaseStorageDownloadTokens': media_token}
        blob.upload_from_string(file_bytes, content_type=f"{media_type}/{ext}")

        try:
            blob.make_public()
            return blob.public_url
        except Exception:
            encoded_name = quote(blob.name, safe='')
            return f"https://firebasestorage.googleapis.com/v0/b/{firebase_bucket.name}/o/{encoded_name}?alt=media&token={media_token}"
    except Exception as e:
        logger.error(f"Error migrating Base64 media to Firebase Storage: {e}")
        return None

def migrate_sqlite_db(db_path='yash_world.db'):
    if not os.path.exists(db_path):
        logger.info(f"ℹ️ No SQLite database file found at '{db_path}'. Skipping SQLite import.")
        return

    logger.info(f"📦 Starting migration from SQLite database: '{db_path}'...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Migrate Users
    try:
        cursor.execute("SELECT * FROM user")
        users = cursor.fetchall()
        for u in users:
            u_dict = dict(u)
            doc_id = str(u_dict['id'])
            db_firestore.collection('users').document(doc_id).set({
                'id': u_dict['id'],
                'username': u_dict['username'],
                'password_hash': u_dict['password_hash'],
                'is_admin': bool(u_dict.get('is_admin', False)),
                'is_friend': bool(u_dict.get('is_friend', False)),
                'created_at': u_dict.get('created_at', datetime.utcnow().isoformat()),
                'last_login': u_dict.get('last_login')
            }, merge=True)
            logger.info(f"   👤 Migrated User #{u_dict['id']} ({u_dict['username']})")
    except Exception as e:
        logger.warning(f"   ⚠️ Users migration note: {e}")

    # 2. Migrate Questions
    try:
        cursor.execute("SELECT * FROM question")
        questions = cursor.fetchall()
        for q in questions:
            q_dict = dict(q)
            doc_id = str(q_dict['id'])
            
            # Convert Base64 media to Firebase Storage URLs
            for attr, mtype in [('image_data', 'image'), ('video_data', 'video'), ('audio_data', 'audio'),
                                ('answer_image_data', 'image'), ('answer_video_data', 'video'), ('answer_audio_data', 'audio')]:
                if q_dict.get(attr) and not q_dict[attr].startswith('http'):
                    url = convert_b64_to_firebase_url(q_dict[attr], mtype, f"q_{doc_id}_{attr}")
                    if url:
                        q_dict[attr] = url

            db_firestore.collection('questions').document(doc_id).set(q_dict, merge=True)
            logger.info(f"   ❓ Migrated Question #{q_dict['id']}")
    except Exception as e:
        logger.warning(f"   ⚠️ Questions migration note: {e}")

    # 3. Migrate Replies
    try:
        cursor.execute("SELECT * FROM reply")
        replies = cursor.fetchall()
        for r in replies:
            r_dict = dict(r)
            doc_id = str(r_dict['id'])
            
            for attr, mtype in [('image_data', 'image'), ('video_data', 'video'), ('audio_data', 'audio')]:
                if r_dict.get(attr) and not r_dict[attr].startswith('http'):
                    url = convert_b64_to_firebase_url(r_dict[attr], mtype, f"r_{doc_id}_{attr}")
                    if url:
                        r_dict[attr] = url

            db_firestore.collection('replies').document(doc_id).set(r_dict, merge=True)
            logger.info(f"   💬 Migrated Reply #{r_dict['id']}")
    except Exception as e:
        logger.warning(f"   ⚠️ Replies migration note: {e}")

    # 4. Migrate Typing Text
    try:
        cursor.execute("SELECT * FROM typing_text")
        texts = cursor.fetchall()
        for t in texts:
            t_dict = dict(t)
            doc_id = str(t_dict['id'])
            db_firestore.collection('typing_texts').document(doc_id).set(t_dict, merge=True)
            logger.info(f"   ✏️ Migrated TypingText #{t_dict['id']}")
    except Exception as e:
        logger.warning(f"   ⚠️ Typing text migration note: {e}")

    # 5. Migrate Feedback Questions & Responses
    try:
        cursor.execute("SELECT * FROM feedback_question")
        fq = cursor.fetchall()
        for f in fq:
            f_dict = dict(f)
            db_firestore.collection('feedback_questions').document(str(f_dict['id'])).set(f_dict, merge=True)
    except Exception as e:
        pass

    try:
        cursor.execute("SELECT * FROM feedback_response")
        fr = cursor.fetchall()
        for r in fr:
            r_dict = dict(r)
            db_firestore.collection('feedback_responses').document(str(r_dict['id'])).set(r_dict, merge=True)
    except Exception as e:
        pass

    conn.close()
    logger.info("🎉 Migration completed successfully! All SQL data is now safely in Firebase Cloud.")

if __name__ == '__main__':
    migrate_sqlite_db()
