"""
One-time migration script to convert Base64 videos to Cloudinary
Run this once to migrate all existing videos
"""

import os
import sys
import base64
import tempfile
from app import app, db, Question, Reply
import cloudinary
import cloudinary.uploader

# Configure Cloudinary (hardcode your cloud name)
CLOUDINARY_CLOUD_NAME = "your_cloud_name_here"  # ← CHANGE THIS

cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key="",  # Not needed for public uploads
    api_secret="",  # Not needed for public uploads
    secure=True
)

def migrate_videos():
    with app.app_context():
        print("🚀 Starting video migration to Cloudinary...")
        print(f"☁️ Cloud Name: {CLOUDINARY_CLOUD_NAME}")
        print("-" * 50)
        
        # ============================================
        # MIGRATE QUESTION VIDEOS
        # ============================================
        questions = Question.query.filter(
            Question.video_data.isnot(None),
            Question.video_data != ''
        ).all()
        
        print(f"📹 Found {len(questions)} question videos to migrate")
        
        for q in questions:
            # Skip if already a Cloudinary URL
            if q.video_data and q.video_data.startswith('http'):
                print(f"⏭️  Question {q.id} already has Cloudinary URL: {q.video_data[:50]}...")
                continue
            
            try:
                # Check if it's Base64
                if q.video_data and not q.video_data.startswith('http'):
                    # Decode Base64
                    try:
                        # Remove data:video/mp4;base64, prefix if present
                        if 'base64,' in q.video_data:
                            base64_str = q.video_data.split('base64,')[1]
                        else:
                            base64_str = q.video_data
                        
                        video_bytes = base64.b64decode(base64_str)
                    except Exception as e:
                        print(f"⚠️  Question {q.id} - Failed to decode Base64: {e}")
                        continue
                    
                    # Create temp file
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_file:
                        tmp_file.write(video_bytes)
                        tmp_file.flush()
                        temp_path = tmp_file.name
                    
                    try:
                        # Upload to Cloudinary
                        upload_result = cloudinary.uploader.upload(
                            temp_path,
                            resource_type='video',
                            folder='yash_world/questions',
                            public_id=f"q_{q.id}_video",
                            use_filename=True,
                            unique_filename=False
                        )
                        
                        # Update question
                        q.video_data = upload_result.get('secure_url')
                        db.session.commit()
                        
                        print(f"✅ Question {q.id} migrated: {upload_result.get('secure_url')}")
                        
                    except Exception as e:
                        print(f"❌ Question {q.id} upload failed: {e}")
                    finally:
                        # Clean up temp file
                        try:
                            os.unlink(temp_path)
                        except:
                            pass
                else:
                    print(f"⏭️  Question {q.id} - No video data found")
                    
            except Exception as e:
                print(f"❌ Question {q.id} migration error: {e}")
        
        # ============================================
        # MIGRATE ANSWER VIDEOS
        # ============================================
        questions_with_answer_video = Question.query.filter(
            Question.answer_video_data.isnot(None),
            Question.answer_video_data != ''
        ).all()
        
        print(f"\n📹 Found {len(questions_with_answer_video)} answer videos to migrate")
        
        for q in questions_with_answer_video:
            if q.answer_video_data and q.answer_video_data.startswith('http'):
                print(f"⏭️  Question {q.id} answer already has Cloudinary URL")
                continue
            
            try:
                if q.answer_video_data and not q.answer_video_data.startswith('http'):
                    try:
                        if 'base64,' in q.answer_video_data:
                            base64_str = q.answer_video_data.split('base64,')[1]
                        else:
                            base64_str = q.answer_video_data
                        
                        video_bytes = base64.b64decode(base64_str)
                    except Exception as e:
                        print(f"⚠️  Question {q.id} answer - Failed to decode Base64: {e}")
                        continue
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_file:
                        tmp_file.write(video_bytes)
                        tmp_file.flush()
                        temp_path = tmp_file.name
                    
                    try:
                        upload_result = cloudinary.uploader.upload(
                            temp_path,
                            resource_type='video',
                            folder='yash_world/answers',
                            public_id=f"a_{q.id}_video",
                            use_filename=True,
                            unique_filename=False
                        )
                        
                        q.answer_video_data = upload_result.get('secure_url')
                        db.session.commit()
                        
                        print(f"✅ Question {q.id} answer migrated: {upload_result.get('secure_url')}")
                        
                    except Exception as e:
                        print(f"❌ Question {q.id} answer upload failed: {e}")
                    finally:
                        try:
                            os.unlink(temp_path)
                        except:
                            pass
                    
            except Exception as e:
                print(f"❌ Question {q.id} answer migration error: {e}")
        
        # ============================================
        # MIGRATE REPLY VIDEOS
        # ============================================
        replies = Reply.query.filter(
            Reply.video_data.isnot(None),
            Reply.video_data != ''
        ).all()
        
        print(f"\n📹 Found {len(replies)} reply videos to migrate")
        
        for r in replies:
            if r.video_data and r.video_data.startswith('http'):
                print(f"⏭️  Reply {r.id} already has Cloudinary URL")
                continue
            
            try:
                if r.video_data and not r.video_data.startswith('http'):
                    try:
                        if 'base64,' in r.video_data:
                            base64_str = r.video_data.split('base64,')[1]
                        else:
                            base64_str = r.video_data
                        
                        video_bytes = base64.b64decode(base64_str)
                    except Exception as e:
                        print(f"⚠️  Reply {r.id} - Failed to decode Base64: {e}")
                        continue
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_file:
                        tmp_file.write(video_bytes)
                        tmp_file.flush()
                        temp_path = tmp_file.name
                    
                    try:
                        upload_result = cloudinary.uploader.upload(
                            temp_path,
                            resource_type='video',
                            folder='yash_world/replies',
                            public_id=f"r_{r.id}_video",
                            use_filename=True,
                            unique_filename=False
                        )
                        
                        r.video_data = upload_result.get('secure_url')
                        db.session.commit()
                        
                        print(f"✅ Reply {r.id} migrated: {upload_result.get('secure_url')}")
                        
                    except Exception as e:
                        print(f"❌ Reply {r.id} upload failed: {e}")
                    finally:
                        try:
                            os.unlink(temp_path)
                        except:
                            pass
                    
            except Exception as e:
                print(f"❌ Reply {r.id} migration error: {e}")
        
        print("\n" + "="*50)
        print("🎉 Migration Complete!")
        print("="*50)

if __name__ == '__main__':
    migrate_videos()