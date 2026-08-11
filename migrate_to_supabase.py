# ============================================================
# SAFE MIGRATION SCRIPT: FIREBASE -> SUPABASE
# Usage: python migrate_to_supabase.py
# Reads existing data from Firebase and writes to Supabase
# ============================================================

import os
import json
import logging
import requests
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration")

def run_migration():
    supabase_url = os.environ.get('SUPABASE_URL', '').strip()
    supabase_key = os.environ.get('SUPABASE_KEY', '').strip()
    
    if not supabase_url or not supabase_key:
        logger.error("❌ SUPABASE_URL and SUPABASE_KEY environment variables are required for migration.")
        return

    from supabase import create_client
    supabase = create_client(supabase_url, supabase_key)
    logger.info("⚡ Supabase client connected.")

    # Check for Firebase credentials file
    service_account_path = os.environ.get('FIREBASE_SERVICE_ACCOUNT_PATH', 'service-account.json')
    if not os.path.exists(service_account_path):
        logger.info("ℹ️ No local Firebase service-account.json found. Skipping Firebase extraction (Supabase is ready).")
        return

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        cred = credentials.Certificate(service_account_path)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        fb_db = firestore.client()
        logger.info("🔥 Firebase client connected for extraction.")

        # 1. Migrate Users
        fb_users = fb_db.collection('users').get()
        for doc in fb_users:
            u_dict = doc.to_dict()
            clean_u = {
                'id': int(u_dict['id']) if str(u_dict.get('id', '')).isdigit() else None,
                'username': u_dict.get('username'),
                'password_hash': u_dict.get('password_hash'),
                'is_admin': u_dict.get('is_admin', False),
                'is_friend': u_dict.get('is_friend', False),
                'created_at': u_dict.get('created_at'),
                'last_login': u_dict.get('last_login')
            }
            clean_u = {k: v for k, v in clean_u.items() if v is not None}
            supabase.table('users').upsert(clean_u).execute()
        logger.info("✅ Users migrated to Supabase PostgreSQL.")

        # 2. Migrate Questions & Media
        fb_questions = fb_db.collection('questions').get()
        for doc in fb_questions:
            q_dict = doc.to_dict()
            clean_q = {
                'id': int(q_dict['id']) if str(q_dict.get('id', '')).isdigit() else None,
                'user_id': int(q_dict['user_id']) if str(q_dict.get('user_id', '')).isdigit() else None,
                'text': q_dict.get('text'),
                'image_data': q_dict.get('image_data'),
                'image_filename': q_dict.get('image_filename'),
                'video_data': q_dict.get('video_data'),
                'video_filename': q_dict.get('video_filename'),
                'audio_data': q_dict.get('audio_data'),
                'audio_filename': q_dict.get('audio_filename'),
                'answer_text': q_dict.get('answer_text'),
                'answer_image_data': q_dict.get('answer_image_data'),
                'answer_image_filename': q_dict.get('answer_image_filename'),
                'answer_video_data': q_dict.get('answer_video_data'),
                'answer_video_filename': q_dict.get('answer_video_filename'),
                'answer_audio_data': q_dict.get('answer_audio_data'),
                'answer_audio_filename': q_dict.get('answer_audio_filename'),
                'has_answer': q_dict.get('has_answer', False),
                'is_answered': q_dict.get('is_answered', False),
                'created_at': q_dict.get('created_at'),
                'updated_at': q_dict.get('updated_at')
            }
            clean_q = {k: v for k, v in clean_q.items() if v is not None}
            supabase.table('questions').upsert(clean_q).execute()
        logger.info("✅ Questions & media references migrated to Supabase PostgreSQL.")

        # 3. Migrate Replies
        fb_replies = fb_db.collection('replies').get()
        for doc in fb_replies:
            r_dict = doc.to_dict()
            clean_r = {
                'id': int(r_dict['id']) if str(r_dict.get('id', '')).isdigit() else None,
                'question_id': int(r_dict['question_id']) if str(r_dict.get('question_id', '')).isdigit() else None,
                'user_id': int(r_dict['user_id']) if str(r_dict.get('user_id', '')).isdigit() else None,
                'text': r_dict.get('text'),
                'image_data': r_dict.get('image_data'),
                'image_filename': r_dict.get('image_filename'),
                'video_data': r_dict.get('video_data'),
                'video_filename': r_dict.get('video_filename'),
                'audio_data': r_dict.get('audio_data'),
                'audio_filename': r_dict.get('audio_filename'),
                'created_at': r_dict.get('created_at'),
                'updated_at': r_dict.get('updated_at')
            }
            clean_r = {k: v for k, v in clean_r.items() if v is not None}
            supabase.table('replies').upsert(clean_r).execute()
        logger.info("✅ Replies & media references migrated to Supabase PostgreSQL.")

        logger.info("🎉 Migration complete!")
    except Exception as e:
        logger.error(f"Migration error: {e}")

if __name__ == '__main__':
    run_migration()
