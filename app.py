import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")
# ============================================
# Imports
# ============================================
# ============================================
# Imports
# ============================================
import os
import json
import time
import base64
from datetime import datetime
from io import BytesIO
import tempfile
import PyPDF2
import pdfplumber
from PIL import Image
import pytesseract
import openai
from flask import Flask, render_template, session, redirect, url_for, request, flash, jsonify, send_file, send_from_directory
from flask_socketio import SocketIO, join_room, emit, leave_room
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text
from hashlib import sha256
from functools import wraps
import uuid
import requests
from dotenv import load_dotenv
import random
from difflib import get_close_matches
from bs4 import BeautifulSoup

# Load environment variables
load_dotenv()

# ============================================
# Flask App Configuration
# ============================================
app = Flask(__name__)

# Configurations
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SECRET_KEY'] = os.getenv('MY_SECRET', 'fallback_secret_for_dev')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# ============================================
# Upload Configuration - ADDED SECTION
# ============================================

# Configure upload folder
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)  # Create folder if it doesn't exist
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ============================================
# Session Cleanup Functions - ADDED SECTION
# ============================================
def cleanup_old_files():
    """Remove old uploaded files from session and disk"""
    try:
        current_time = time.time()
        # Clean files older than 1 hour (3600 seconds)
        if session.get('last_upload_time') and (current_time - session.get('last_upload_time', 0)) > 3600:
            # Remove file from disk
            if session.get('last_file_path'):
                try:
                    if os.path.exists(session['last_file_path']):
                        os.remove(session['last_file_path'])
                        debug_print(f"🗑️ Cleaned up old file: {session['last_file_path']}")
                except Exception as e:
                    debug_print(f"⚠️ Could not delete file: {e}")
            
            # Clear session references
            session.pop('last_file_id', None)
            session.pop('last_file_type', None)
            session.pop('last_file_name', None)
            session.pop('last_file_path', None)
            session.pop('last_image_base64', None)
            session.pop('last_file_preview', None)
            session.pop('last_upload_time', None)
            
    except Exception as e:
        debug_print(f"⚠️ Cleanup error: {e}")

def cleanup_stale_files():
    """Remove files older than 24 hours on startup"""
    try:
        current_time = time.time()
        for filename in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(file_path):
                file_age = current_time - os.path.getmtime(file_path)
                if file_age > 24 * 3600:  # 24 hours
                    os.remove(file_path)
                    debug_print(f"🗑️ Removed stale file: {filename}")
    except Exception as e:
        debug_print(f"⚠️ Stale file cleanup error: {e}")

# Clean up stale files on startup
cleanup_stale_files()

# Database configuration
DATABASE_URL = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:///tellavista.db'
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 300,
    'pool_pre_ping': True,
    'connect_args': {
        'connect_timeout': 10,
        'application_name': 'tellavista_app'
    }
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Debug mode - set to False in production
DEBUG_MODE = True

def debug_print(*args, **kwargs):
    if DEBUG_MODE:
        print(*args, **kwargs)

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ============================================
# Database Models
# ============================================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    level = db.Column(db.Integer, default=1)
    joined_on = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class UserQuestions(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Room(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    teacher_id = db.Column(db.String(120))
    teacher_name = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ============================================
# File Processing Helper Functions
# ============================================
def encode_image_to_base64(file):
    """Encode image file to base64 string"""
    try:
        file.seek(0)
        image_bytes = file.read()
        return base64.b64encode(image_bytes).decode('utf-8')
    except Exception as e:
        debug_print(f"❌ Error encoding image to base64: {e}")
        return None

def extract_text_from_pdf(file):
    """Extract text from PDF file - SMART LIMITED"""
    text = ""
    
    try:
        # First 3 pages only for demo stability
        file.seek(0)
        with pdfplumber.open(BytesIO(file.read())) as pdf:
            total_pages = min(3, len(pdf.pages))
            for page in pdf.pages[:total_pages]:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
                    # Stop if we already have enough text
                    if len(text) > 2000:
                        text = text[:2000] + "\n...[Content truncated for demo]"
                        break
        
        # Fallback to PyPDF2 if pdfplumber fails
        if not text.strip() or len(text.strip()) < 10:
            file.seek(0)
            pdf_reader = PyPDF2.PdfReader(BytesIO(file.read()))
            total_pages = min(3, len(pdf_reader.pages))
            for page_num in range(total_pages):
                page = pdf_reader.pages[page_num]
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
                    if len(text) > 2000:
                        text = text[:2000] + "\n...[Content truncated for demo]"
                        break
                
    except Exception as e:
        text = f"[Note: PDF extraction encountered issues. Some content may not be available.]"
    
    return text.strip()

def extract_text_from_image(file):
    """Extract text from image using OCR with smart detection"""
    try:
        # Save to temp file for OCR
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
            file.save(tmp.name)
            img = Image.open(tmp.name)
            
            # Try OCR
            text = pytesseract.image_to_string(img)
            
            # Clean up
            os.unlink(tmp.name)
            
            # Smart detection: is this a text-based image or diagram?
            cleaned_text = text.strip()
            if len(cleaned_text) < 30:  # Very little text
                return "DIAGRAM_OR_VISUAL_CONTENT"
            elif "http://" in cleaned_text or "www." in cleaned_text:
                # Might be a screenshot with URLs
                return cleaned_text
            else:
                return cleaned_text
            
    except Exception as e:
        return f"[Note: Image processing incomplete. Treat this as visual content.]"

def is_diagram_or_visual(text_content):
    """Detect if content is primarily visual/diagram"""
    if not text_content:
        return True
    if text_content == "DIAGRAM_OR_VISUAL_CONTENT":
        return True
    if len(text_content.strip()) < 50:
        return True
    return False

# ============================================
# Helper Functions
# ============================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def create_database_if_not_exists():
    """Create database if it doesn't exist"""
    try:
        # Parse the DATABASE_URL to get connection details without database name
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            print("ℹ️  No DATABASE_URL, using SQLite")
            return True
            
        # Extract parts from the connection string
        if 'postgresql://' in db_url:
            # Remove the database name from the URL to connect to default database
            parts = db_url.split('/')
            base_url = '/'.join(parts[:-1])  # Everything before the last /
            db_name = parts[-1]  # The database name
            
            # Connect to default postgres database to create our database
            conn = psycopg2.connect(base_url + '/postgres')
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cursor = conn.cursor()
            
            # Check if database exists
            cursor.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (db_name,))
            exists = cursor.fetchone()
            
            if not exists:
                print(f"🔄 Creating database: {db_name}")
                cursor.execute(f'CREATE DATABASE "{db_name}"')
                print(f"✅ Database {db_name} created")
            else:
                print(f"✅ Database {db_name} already exists")
                
            cursor.close()
            conn.close()
            return True
            
    except Exception as e:
        print(f"⚠️  Could not create database (might already exist): {e}")
        return False

def init_database():
    """Initialize database with error handling"""
    try:
        # Try to create database first
        create_database_if_not_exists()
        
        with app.app_context():
            db.create_all()
            print("✅ Database tables created/verified")
            # Test the connection
            db.session.execute('SELECT 1')
            print("✅ Database connection successful")
            return True
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        print("🚨 Falling back to SQLite database")
        # Fallback to SQLite
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tellavista.db'
        try:
            with app.app_context():
                db.create_all()
                print("✅ SQLite database created as fallback")
                return True
        except Exception as e2:
            print(f"❌ SQLite fallback also failed: {e2}")
            return False

def create_default_user():
    """Create default user with error handling"""
    with app.app_context():
        try:
            user = User.query.filter_by(username='test').first()
            if not user:
                user = User(username='test', email='test@example.com')
                user.set_password('test123')
                db.session.add(user)
                db.session.commit()
                print("✅ Created default user: test / test123")
            else:
                print("✅ Default user already exists: test / test123")
        except Exception as e:
            print(f"❌ Error creating default user: {e}")

def is_academic_book(title, topic, department):
    if not title:
        return False
    title_lower = title.lower()
    topic_lower = topic.lower()
    department_lower = department.lower()

    academic_keywords = [
        "principles", "fundamentals", "introduction", "basics", "theory",
        "textbook", "manual", "engineering", "mathematics", "analysis",
        "guide", "mechanics", "accounting", "algebra", "economics", "physics",
        "statistics", topic_lower, department_lower
    ]

    fiction_keywords = [
        "novel", "jedi", "star wars", "story", "episode", "adventure", "magic",
        "wizard", "putting", "love", "mystery", "thriller", "detective",
        "vampire", "romance", "oz", "dragon", "ghost", "horror"
    ]

    if any(bad in title_lower for bad in fiction_keywords):
        return False
    if any(good in title_lower for good in academic_keywords):
        return True
    return False

# ============================================
# In-Memory Storage for Live Meetings
# ============================================
rooms = {}           # room_id -> room data
participants = {}    # socket_id -> participant info
room_authority = {}  # room_id -> authority state

# ============================================
# Live Meeting Helper Functions
# ============================================
def get_or_create_room(room_id):
    """Get existing room or create new one"""
    if room_id not in rooms:
        rooms[room_id] = {
            'participants': {},      # socket_id -> {'username', 'role', 'joined_at'}
            'teacher_sid': None,
            'created_at': datetime.utcnow().isoformat()
        }
    return rooms[room_id]

def get_room_authority(room_id):
    """Get or create authority state for a room"""
    if room_id not in room_authority:
        room_authority[room_id] = {
            'muted_all': False,
            'cameras_disabled': False,
            'mic_requests': {},
            'questions_enabled': True,
            'question_visibility': 'public'
        }
    return room_authority[room_id]

def get_participants_list(room_id, exclude_sid=None):
    """Get list of all participants in room except exclude_sid"""
    if room_id not in rooms:
        return []
    
    room = rooms[room_id]
    result = []
    
    for sid, info in room['participants'].items():
        if sid != exclude_sid:
            result.append({
                'sid': sid,
                'username': info['username'],
                'role': info['role']
            })
    
    return result

def cleanup_room(room_id):
    """Remove empty rooms"""
    if room_id in rooms:
        room = rooms[room_id]
        if not room['participants']:
            del rooms[room_id]
            if room_id in room_authority:
                del room_authority[room_id]
            with app.app_context():
                Room.query.filter_by(id=room_id).delete()
                db.session.commit()

# ============================================
# Socket.IO Event Handlers - Live Meetings
# ============================================
@socketio.on('connect')
def handle_connect():
    sid = request.sid
    # CRITICAL FIX: Join client to their private SID room for direct messaging
    join_room(sid)
    participants[sid] = {'room_id': None, 'username': None, 'role': None}
    debug_print(f"✅ Client connected: {sid} (joined private room: {sid})")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    
    # Find which room this participant is in
    participant = participants.get(sid)
    if not participant:
        return
    
    room_id = participant['room_id']
    
    if room_id in rooms:
        room = rooms[room_id]
        
        # Notify all other participants
        if sid in room['participants']:
            participant_info = room['participants'][sid]
            
            # Remove from room
            del room['participants'][sid]
            
            # Update teacher_sid if teacher left
            if sid == room['teacher_sid']:
                room['teacher_sid'] = None
                # Notify students that teacher left
                for participant_sid in room['participants']:
                    if room['participants'][participant_sid]['role'] == 'student':
                        emit('teacher-disconnected', room=participant_sid)
            
            # Notify others
            emit('participant-left', {
                'sid': sid,
                'username': participant_info['username'],
                'role': participant_info['role']
            }, room=room_id, skip_sid=sid)
            
            debug_print(f"❌ {participant_info['username']} left room {room_id}")
        
        # Clean up empty room
        cleanup_room(room_id)
    
    # Remove from participants
    if sid in participants:
        del participants[sid]

@socketio.on('join-room')
def handle_join_room(data):
    """Join room and get all existing participants - FIXED"""
    try:
        sid = request.sid
        room_id = data.get('room')
        role = data.get('role', 'student')
        username = data.get('username', 'Teacher' if role == 'teacher' else f'Student_{sid[:6]}')
        
        if not room_id:
            emit('error', {'message': 'Room ID required'})
            return
        
        debug_print(f"👤 {username} ({role}) joining room: {room_id}")
        
        room = get_or_create_room(room_id)
        authority_state = get_room_authority(room_id)
        
        # Check if teacher already exists
        if role == 'teacher' and room['teacher_sid']:
            emit('error', {'message': 'Room already has a teacher'})
            return
        
        # Add to room
        room['participants'][sid] = {
            'username': username,
            'role': role,
            'joined_at': datetime.utcnow().isoformat()
        }
        
        # Update teacher reference
        if role == 'teacher':
            room['teacher_sid'] = sid
            authority_state['teacher_sid'] = sid
            
            with app.app_context():
                existing_room = Room.query.get(room_id)
                if not existing_room:
                    room_db = Room(
                        id=room_id,
                        teacher_id=sid,
                        teacher_name=username,
                        is_active=True
                    )
                    db.session.add(room_db)
                else:
                    existing_room.teacher_id = sid
                    existing_room.teacher_name = username
                db.session.commit()
            
            # Notify all students that teacher joined
            for participant_sid in room['participants']:
                if room['participants'][participant_sid]['role'] == 'student':
                    emit('teacher-joined', {
                        'teacher_sid': sid,
                        'teacher_name': username
                    }, room=participant_sid)
        
        # Update participant info
        participants[sid] = {
            'room_id': room_id,
            'username': username,
            'role': role
        }
        
        # Join the socket room
        join_room(room_id)
        
        # Get all existing participants (excluding self)
        existing_participants = get_participants_list(room_id, exclude_sid=sid)
        
        # Send room joined confirmation
        emit('room-joined', {
            'room': room_id,
            'sid': sid,
            'username': username,
            'role': role,
            'existing_participants': existing_participants,
            'teacher_sid': room['teacher_sid'],
            'is_waiting': (role == 'student' and not room['teacher_sid'])  # Inform student they're waiting
        })
        
        # Notify all other participants about new joiner
        emit('new-participant', {
            'sid': sid,
            'username': username,
            'role': role
        }, room=room_id, skip_sid=sid)
        
        # Send authority state if student and teacher exists
        if role == 'student' and room['teacher_sid']:
            emit('room-state', {
                'muted_all': authority_state['muted_all'],
                'cameras_disabled': authority_state['cameras_disabled'],
                'questions_enabled': authority_state['questions_enabled'],
                'question_visibility': authority_state['question_visibility']
            })
        
        # Log room status
        debug_print(f"✅ {username} joined room {room_id}. Total participants: {len(room['participants'])}")
        
    except Exception as e:
        debug_print(f"❌ Error in join-room: {e}")
        emit('error', {'message': str(e)})

# ============================================
# WebRTC Signaling - Full Mesh Support - FIXED
# ============================================
@socketio.on('webrtc-offer')
def handle_webrtc_offer(data):
    """Relay WebRTC offer to specific participant - FIXED"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        offer = data.get('offer')
        
        if not all([room_id, target_sid, offer]):
            return
        
        # Verify both are in the same room
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        debug_print(f"📨 {request.sid[:8]} → offer → {target_sid[:8]}")
        
        # FIX: Use target_sid as room (requires client to join their SID room on connect)
        emit('webrtc-offer', {
            'from_sid': request.sid,
            'offer': offer,
            'room': room_id
        }, room=target_sid)  # This now works because we joined SID room in connect
        
    except Exception as e:
        debug_print(f"❌ Error relaying offer: {e}")

@socketio.on('webrtc-answer')
def handle_webrtc_answer(data):
    """Relay WebRTC answer to specific participant - FIXED"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        answer = data.get('answer')
        
        if not all([room_id, target_sid, answer]):
            return
        
        # Verify both are in the same room
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        debug_print(f"📨 {request.sid[:8]} → answer → {target_sid[:8]}")
        
        # FIX: Use target_sid as room
        emit('webrtc-answer', {
            'from_sid': request.sid,
            'answer': answer,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying answer: {e}")

@socketio.on('webrtc-ice-candidate')
def handle_webrtc_ice_candidate(data):
    """Relay ICE candidate to specific participant - FIXED"""
    try:
        room_id = data.get('room')
        target_sid = data.get('target_sid')
        candidate = data.get('candidate')
        
        if not all([room_id, target_sid, candidate]):
            return
        
        # Verify both are in the same room
        sender = participants.get(request.sid)
        target = participants.get(target_sid)
        
        if not sender or not target:
            return
        
        if sender['room_id'] != room_id or target['room_id'] != room_id:
            return
        
        debug_print(f"📨 {request.sid[:8]} → ICE → {target_sid[:8]}")
        
        # FIX: Use target_sid as room
        emit('webrtc-ice-candidate', {
            'from_sid': request.sid,
            'candidate': candidate,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying ICE candidate: {e}")

# ============================================
# NEW: Full Mesh Initiation System
# ============================================
@socketio.on('request-full-mesh')
def handle_request_full_mesh(data):
    """Initiate full mesh connections between all participants"""
    try:
        room_id = data.get('room')
        sid = request.sid
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        
        # Verify participant is in room
        if sid not in room['participants']:
            return
        
        # Get all other participants in room
        other_participants = []
        for other_sid, info in room['participants'].items():
            if other_sid != sid:
                other_participants.append({
                    'sid': other_sid,
                    'username': info['username'],
                    'role': info['role']
                })
        
        # Send list of peers to connect to
        emit('initiate-mesh-connections', {
            'peers': other_participants,
            'room': room_id
        }, room=sid)
        
        debug_print(f"🔗 Initiating full mesh for {sid[:8]} with {len(other_participants)} peers")
        
    except Exception as e:
        debug_print(f"❌ Error in request-full-mesh: {e}")

# ============================================
# Teacher Authority System
# ============================================
@socketio.on('teacher-mute-all')
def handle_teacher_mute_all(data):
    """Teacher mutes all students"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        # Verify this is the teacher
        if teacher_sid != room['teacher_sid']:
            return
        
        authority = get_room_authority(room_id)
        authority['muted_all'] = True
        
        # Notify all students
        for sid in room['participants']:
            if room['participants'][sid]['role'] == 'student':
                emit('room-muted', {'muted': True}, room=sid)
        
        debug_print(f"🔇 Teacher muted all in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-mute-all: {e}")

@socketio.on('teacher-unmute-all')
def handle_teacher_unmute_all(data):
    """Teacher unmutes all students"""
    try:
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            return
        
        authority = get_room_authority(room_id)
        authority['muted_all'] = False
        
        for sid in room['participants']:
            if room['participants'][sid]['role'] == 'student':
                emit('room-muted', {'muted': False}, room=sid)
        
        debug_print(f"🔊 Teacher unmuted all in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in teacher-unmute-all: {e}")

# ============================================
# Control Events - FIXED
# ============================================
@socketio.on('start-broadcast')
def handle_start_broadcast(data):
    """Teacher starts broadcasting to all students - FIXED"""
    try:
        room_id = data.get('room')
        
        if room_id not in rooms:
            emit('error', {'message': 'Room not found'})
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        if teacher_sid != room['teacher_sid']:
            emit('error', {'message': 'Only teacher can start broadcast'})
            return
        
        debug_print(f"📢 Teacher starting broadcast in room: {room_id}")
        
        # Get all student SIDs
        student_sids = []
        student_info = []
        for sid, info in room['participants'].items():
            if info['role'] == 'student':
                student_sids.append(sid)
                student_info.append({
                    'sid': sid,
                    'username': info['username']
                })
        
        # Notify teacher
        emit('broadcast-ready', {
            'student_sids': student_sids,
            'student_info': student_info,
            'student_count': len(student_sids),
            'room': room_id
        }, room=teacher_sid)
        
        # FIX: Initiate WebRTC connections for each student
        for student_sid in student_sids:
            # Send list of all peers to connect to (full mesh)
            peers_to_connect = []
            for other_sid in room['participants']:
                if other_sid != student_sid:  # Don't connect to self
                    peers_to_connect.append({
                        'sid': other_sid,
                        'username': room['participants'][other_sid]['username'],
                        'role': room['participants'][other_sid]['role']
                    })
            
            emit('initiate-full-mesh', {
                'peers': peers_to_connect,
                'teacher_sid': teacher_sid,
                'room': room_id
            }, room=student_sid)
        
    except Exception as e:
        debug_print(f"❌ Error in start-broadcast: {e}")
        emit('error', {'message': str(e)})

@socketio.on('ping')
def handle_ping(data):
    """Keep-alive ping"""
    emit('pong', {'timestamp': datetime.utcnow().isoformat()})

# ============================================
# Flask Routes - Educational Platform
# ============================================
@app.route('/')
def index():
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
    return render_template('index.html', user=user)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        print(f"📝 Signup attempt - Username: '{username}', Email: '{email}'")

        if not username or not email or not password:
            flash('Please fill out all fields.')
            return redirect(url_for('signup'))

        try:
            # Check if user exists
            existing_user = User.query.filter(
                (User.username == username) | (User.email == email)
            ).first()
            
            if existing_user:
                if existing_user.username == username:
                    flash('Username already exists.')
                else:
                    flash('Email already registered.')
                return redirect(url_for('signup'))

            # Create new user
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            print(f"✅ Successfully created user: {username}")

            session['user'] = {
                'username': username,
                'email': email,
                'joined_on': user.joined_on.strftime('%Y-%m-%d'),
                'last_login': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            }
            flash('Account created successfully!')
            return redirect(url_for('index'))
            
        except Exception as e:
            print(f"❌ Error during signup: {e}")
            db.session.rollback()
            flash('Error creating account. Please try again.')
            return redirect(url_for('signup'))
    
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        print("🔄 Login POST received")
        
        # Get the specific field names from your form
        login_input = request.form.get('username_or_email', '').strip()
        password = request.form.get('password', '').strip()
        
        print(f"🔐 Login attempt - Input: '{login_input}', Password: {'*' * len(password)}")

        if not login_input:
            flash('Please enter username or email.')
            return redirect(url_for('login'))
            
        if not password:
            flash('Please enter password.')
            return redirect(url_for('login'))

        print(f"🔍 Looking for user by username or email: '{login_input}'")
        
        try:
            # Try to find user by username OR email
            user = User.query.filter(
                (User.username == login_input) | (User.email == login_input)
            ).first()
            
            if user:
                print(f"✅ User found: {user.username} (email: {user.email})")
                print(f"🔑 Checking password...")
                if user.check_password(password):
                    user.last_login = datetime.utcnow()
                    db.session.commit()
                    
                    session['user'] = {
                        'username': user.username,
                        'email': user.email,
                        'joined_on': user.joined_on.strftime('%Y-%m-%d'),
                        'last_login': user.last_login.strftime('%Y-%m-%d %H:%M:%S')
                    }
                    
                    print(f"🎉 Login successful for user: {user.username}")
                    flash('Logged in successfully!')
                    return redirect(url_for('index'))
                else:
                    print(f"❌ Password incorrect for user: {login_input}")
                    flash('Invalid password.')
            else:
                print(f"❌ User not found: {login_input}")
                # Debug: Show all users in database
                try:
                    all_users = User.query.all()
                    print(f"📊 All users in database: {[u.username for u in all_users]}")
                except Exception as e:
                    print(f"📊 Could not fetch users: {e}")
                flash('User not found.')
                
        except Exception as e:
            print(f"❌ Database error during login: {e}")
            flash('Database error. Please try again.')

        return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.')
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile():
    user = session.get('user', {})
    return render_template('profile.html', user=user)

@app.route('/talk-to-nelavista')
@login_required
def talk_to_nelavista():
    return render_template('talk-to-nelavista.html')

# ============================================
# UPDATED /ask_with_files ENDPOINT - CHATGPT-STYLE ATOMIC BEHAVIOR
# ============================================
# ============================================
# FIXED: /ask_with_files Endpoint (Atomic Processing)
# ============================================
@app.route('/ask_with_files', methods=['POST'])
@login_required
def ask_with_files():
    """Handle atomic file+text requests like ChatGPT"""
    try:
        username = session['user']['username']
        
        # Get the text message (REQUIRED for context)
        message = request.form.get('message', '').strip()
        
        print(f"🚀 /ask_with_files called by {username}")
        print(f"📝 Message: '{message}'")
        print(f"📁 Files received: {list(request.files.keys())}")
        
        # Process ALL files from the request
        file_contents = []
        image_base64_list = []
        file_count = 0
        
        # Handle multiple files
        if 'files' in request.files:
            files = request.files.getlist('files')  # Get ALL files with key 'files'
            print(f"📦 Processing {len(files)} file(s)")
            
            for file in files:
                if file and file.filename and file.filename.strip():
                    file_count += 1
                    filename = file.filename.lower()
                    
                    print(f"  📄 File {file_count}: {filename}")
                    
                    if filename.endswith('.pdf'):
                        # Extract text from PDF
                        text = extract_text_from_pdf(file)
                        if text and text.strip():
                            file_contents.append(f"[PDF CONTENT: {file.filename}]\n{text[:2000]}")
                            print(f"    ✅ Extracted {len(text)} chars from PDF")
                        else:
                            file_contents.append(f"[PDF: {file.filename} - Could not extract text]")
                            print(f"    ⚠️ Could not extract text from PDF")
                    
                    elif filename.endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        # Encode image for vision
                        file.seek(0)
                        image_bytes = file.read()
                        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                        
                        # Determine MIME type
                        mime_type = file.content_type
                        if not mime_type:
                            if filename.endswith('.png'):
                                mime_type = 'image/png'
                            elif filename.endswith('.jpg') or filename.endswith('.jpeg'):
                                mime_type = 'image/jpeg'
                            elif filename.endswith('.gif'):
                                mime_type = 'image/gif'
                            else:
                                mime_type = 'image/jpeg'
                        
                        image_base64_list.append({
                            'base64': image_base64,
                            'filename': file.filename,
                            'mime_type': mime_type,
                            'type': 'image'
                        })
                        print(f"    ✅ Prepared image for vision: {mime_type}")
        
        print(f"📊 Summary: {file_count} files, {len(image_base64_list)} images")
        
        # CRITICAL: If files exist, they ARE the context
        if file_count > 0:
            print("🔧 Mode: SOLUTION (files attached)")
            
            # Build context from files
            file_context = ""
            if file_contents:
                file_context = "\n\n".join(file_contents)
            
            # SYSTEM PROMPT when files are attached
            system_prompt = (
                "You are Nelavista, an advanced AI tutor for Nigerian university students.\n\n"
                
                "IMPORTANT: The user has attached file(s) with their message. "
                "The file(s) contain the primary content to analyze.\n\n"
                
                "USER'S FILES:\n"
            )
            
            if file_context:
                system_prompt += f"Document content:\n{file_context}\n\n"
            
            if image_base64_list:
                system_prompt += f"Images attached: {len(image_base64_list)} image(s)\n\n"
            
            system_prompt += (
                "USER'S INSTRUCTION:\n"
                f"\"{message}\"\n\n"
                
                "RESPONSE REQUIREMENTS:\n"
                "1. Acknowledge you're analyzing the attached file(s)\n"
                "2. Respond based SOLELY on the file content\n"
                "3. If summarizing, summarize the file content\n"
                "4. If analyzing, analyze what's in the files\n"
                "5. Use clean HTML formatting\n"
                "6. Do NOT ask for the text - you already have it\n"
            )
            
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add user message if there's no text from files
            if not file_context and not image_base64_list:
                messages.append({"role": "user", "content": message})
            
            # Use vision model for images
            if image_base64_list:
                content_parts = [{"type": "text", "text": message}]
                
                for image_data in image_base64_list:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_data['mime_type']};base64,{image_data['base64']}"
                        }
                    })
                
                messages.append({
                    "role": "user",
                    "content": content_parts
                })
                model = "openai/gpt-4o"
                print(f"👁️ Using vision model: {model}")
            else:
                model = "openai/gpt-4o-mini"
                print(f"💬 Using text model: {model}")
        
        else:
            # NO FILES: Normal chat mode
            print("🔧 Mode: CHAT (no files)")
            
            chatty_keywords = ["hi", "hello", "hey", "how are you", "good morning", "good evening"]
            is_chatty = any(k in message.lower() for k in chatty_keywords)
            
            if is_chatty:
                system_prompt = (
                    "You are Nelavista, an AI tutor for Nigerian university students. "
                    "For casual conversation:\n"
                    "- Respond in clean HTML using <p> only.\n"
                    "- Be natural, brief, and human.\n"
                    "- Emojis are allowed.\n"
                    "- No structuring, no steps, no analysis tone."
                )
                model = "openai/gpt-4o-mini"
            else:
                system_prompt = (
                    "You are Nelavista, an advanced AI tutor for Nigerian university students.\n\n"
                    "Provide helpful, accurate academic assistance.\n"
                    "Use clean HTML formatting.\n"
                    "Be concise and informative."
                )
                model = "openai/gpt-4o-mini"
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ]
        
        print(f"🚀 Calling API with {len(messages)} messages")
        
        # --- Call OpenRouter API ---
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nelavista.com",
            "X-Title": "Nelavista AI Tutor"
        }
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": 1500
        }
        
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        
        if response.status_code != 200:
            print(f"❌ API Error: {response.status_code} - {response.text}")
            return jsonify({'error': f'API error: {response.status_code}'}), 500
        
        answer = response.json()["choices"][0]["message"]["content"]
        print(f"✅ Response: {len(answer)} chars")
        
        # Save to database
        new_q = UserQuestions(
            username=username,
            question=message + (" [with file(s)]" if file_count > 0 else ""),
            answer=answer
        )
        db.session.add(new_q)
        db.session.commit()
        
        return jsonify({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": answer
                }
            }],
            "file_count": file_count,
            "has_files": file_count > 0
        })
        
    except Exception as e:
        print(f"❌ Error in /ask_with_files: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Server error processing your request'}), 500

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    """Handle file upload - Store files on disk, not in session"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected"}), 400
    
    # Delete the previous uploaded file if it exists
    old_file_path = session.get('last_file_path')
    if old_file_path and os.path.exists(old_file_path):
        try:
            os.remove(old_file_path)
            debug_print(f"🗑️ Deleted old file: {old_file_path}")
        except Exception as e:
            debug_print(f"⚠️ Error deleting old file: {e}")
    
    # Clear old file session data
    session.pop('last_file_path', None)
    session.pop('last_file_type', None)
    session.pop('last_file_content', None)
    session.pop('last_file_preview', None)
    session.pop('last_file_id', None)
    session.pop('last_upload_time', None)
    session.pop('last_image_base64', None)
    
    try:
        filename = file.filename.lower()
        
        if not allowed_file(filename):
            return jsonify({"success": False, "error": "Unsupported file type. Use PDF or images."}), 400
        
        file_size = 0
        
        # Read file content once for size calculation
        file_data = file.read()
        file_size = len(file_data)
        file.seek(0)  # Reset file pointer for later use
        
        if filename.endswith('.pdf'):
            # Generate unique filename for PDF
            file_id = str(uuid.uuid4())
            pdf_filename = f"{file_id}.pdf"
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename)
            
            # Save PDF to disk
            with open(pdf_path, 'wb') as f:
                f.write(file_data)
            
            # Extract text from saved file
            with open(pdf_path, 'rb') as pdf_file:
                text = extract_text_from_pdf(BytesIO(pdf_file.read()))
            
            # Store only references in session
            session['last_file_id'] = file_id
            session['last_file_type'] = 'pdf'
            session['last_file_name'] = pdf_filename
            session['last_file_path'] = pdf_path
            session['last_upload_time'] = time.time()
            
            if text:
                # Store only first 2000 chars in session for preview
                session['last_file_content'] = text[:2000]
            else:
                session['last_file_content'] = "PDF loaded but text extraction failed"
            
            debug_print(f"📄 PDF saved: {pdf_filename}, Size: {file_size} bytes")
            
            return jsonify({
                "success": True,
                "message": "PDF uploaded successfully",
                "preview": text[:300] + "..." if len(text) > 300 else text,
                "type": "pdf",
                "filename": file.filename,
                "size_kb": round(file_size / 1024, 1),
                "has_text": len(text.strip()) > 50,
                "file_id": file_id
            })
            
        elif filename.endswith(('.png', '.jpg', '.jpeg', '.gif')):
            # Generate unique filename for image
            file_id = str(uuid.uuid4())
            ext = filename.rsplit('.', 1)[1].lower()
            image_filename = f"{file_id}.{ext}"
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
            
            # Save image to disk
            with open(image_path, 'wb') as f:
                f.write(file_data)
            
            # Encode image to base64 for vision
            image_base64 = base64.b64encode(file_data).decode('utf-8')
            
            # Store only references in session
            session['last_file_id'] = file_id
            session['last_file_type'] = 'image'
            session['last_file_name'] = image_filename
            session['last_file_path'] = image_path
            session['last_image_base64'] = image_base64  # Keep base64 in session temporarily
            session['last_upload_time'] = time.time()
            
            # Extract text via OCR for fallback
            file.seek(0)  # Reset file pointer
            text = extract_text_from_image(file)
            session['last_file_content'] = text[:500] if text else ""
            
            # Determine if it's text or diagram
            is_diagram = is_diagram_or_visual(text)
            
            debug_print(f"🖼️ Image saved: {image_filename}, Size: {file_size} bytes")
            
            return jsonify({
                "success": True,
                "message": "Image uploaded - ready for vision analysis",
                "type": "image",
                "filename": file.filename,
                "size_kb": round(file_size / 1024, 1),
                "is_diagram": is_diagram,
                "has_text": text != "DIAGRAM_OR_VISUAL_CONTENT" and len(text.strip()) > 10,
                "vision_ready": True,
                "file_id": file_id
            })
            
        else:
            return jsonify({"success": False, "error": "Unsupported file type. Use PDF or images."}), 400
            
    except Exception as e:
        debug_print(f"❌ Upload error: {e}")
        return jsonify({"success": False, "error": f"Processing failed: {str(e)[:100]}"}), 500

@app.route('/cleanup_attachments', methods=['POST'])
@login_required
def cleanup_attachments():
    """Clean up uploaded files from session"""
    try:
        # Clear all file-related session data
        keys_to_remove = [
            'last_file_id', 'last_file_type', 'last_file_name',
            'last_file_path', 'last_upload_time', 'last_image_base64',
            'last_file_content', 'last_file_preview'
        ]
        
        for key in keys_to_remove:
            session.pop(key, None)
        
        return jsonify({
            "success": True,
            "message": "Attachment context cleared"
        })
    except Exception as e:
        debug_print(f"❌ Cleanup error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
        
# ============================================
# UPDATED /ask ENDPOINT - VISION INTEGRATION
# ============================================
@app.route('/ask', methods=['POST'])
@login_required
def ask():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        username = session['user']['username']
        history = data.get('history', [])
        user_question = data.get('message', '')

        # If no direct message, fall back to last user message in history
        if not user_question:
            for message in reversed(history):
                if message.get('role') == 'user':
                    user_question = message.get('content', '')
                    break

        if not user_question:
            return jsonify({'error': 'No question provided'}), 400

        debug_print(f"🤖 Processing question from {username}: {user_question[:100]}...")

        # --- File context handling ---
        file_content = ""
        file_type = ""
        last_upload_time = session.get('last_upload_time', 0)

        if time.time() - last_upload_time < 300:
            file_content = session.get('last_file_content', '')
            file_type = session.get('last_file_type', '')
            image_base64 = session.get('last_image_base64', '')
        else:
            session.pop('last_file_content', None)
            session.pop('last_file_type', None)
            session.pop('last_upload_time', None)
            session.pop('last_image_base64', None)
            session.pop('last_file_path', None)  # Also remove the file path
            image_base64 = ''

        # --- Vision detection ---
        use_vision = False
        vision_image_base64 = None

        if session.get('last_file_type') == 'image' and session.get('last_image_base64'):
            vision_keywords = [
                'image', 'picture', 'diagram', 'photo', 'screenshot',
                'explain this', 'what is this', 'describe', 'analyze',
                'what does this show', 'tell me about this', 'summarize this',
                'can you see', 'look at this', 'what is in this', 'explain the image'
            ]
            user_q_lower = user_question.lower()
            use_vision = any(k in user_q_lower for k in vision_keywords)
            
            if use_vision:
                vision_image_base64 = session.get('last_image_base64')
                debug_print("👁️ Vision enabled for image analysis")

        # --- Mode detection ---
        chatty_keywords = ["hi", "hello", "hey", "how are you", "good morning", "good evening"]
        is_chatty = any(k in user_question.lower() for k in chatty_keywords)

        if (file_content or vision_image_base64) and not is_chatty:
            mode = "solution"
        else:
            mode = "chatty" if is_chatty else "solution"

        # --- SYSTEM PROMPTS ---
        if mode == "chatty":
            system_prompt = (
                "You are Nelavista, an AI tutor created by Afeez Adewale Tella for Nigerian university students (100–400 level). "
                "For casual conversation:\n"
                "- Respond in clean HTML using <p> only.\n"
                "- Be natural, brief, and human.\n"
                "- Emojis are allowed.\n"
                "- No structuring, no steps, no analysis tone."
            )
        else:
            vision_context = ""
            if vision_image_base64:
                vision_context = (
                    "You are analyzing an image. Base your explanation strictly on what is visible."
                )

            system_prompt = (
                "You are Nelavista, an advanced AI tutor created by Afeez Adewale Tella for Nigerian university students (100–400 level).\n\n"

                "ROLE:\n"
                "You are a specialized analytical tutor and researcher.\n\n"

                "STYLE: Analytical Expert\n"
                "Follow these rules strictly.\n\n"

                "GLOBAL RULES:\n"
                "- Always respond in clean HTML using <p>, <ul>, <li>, <strong>, <h2>, <h3>, and <table> when appropriate.\n"
                "- No greetings.\n"
                "- Do NOT use labels like Step 1, Step 2, Intro, or Final Answer.\n"
                "- No emojis in academic explanations.\n"
                "- Use neutral, precise language. Avoid hype, emotion, or filler.\n\n"

                "STRUCTURE REQUIREMENTS:\n"
                "- Begin with a concise <strong>TL;DR</strong> section summarizing the core conclusion.\n"
                "- Use clear headers and bullet points to organize information.\n"
                "- Break complex ideas into logical components and explain the reasoning behind them.\n"
                "- Use short paragraphs (1–2 sentences).\n\n"

                "REASONING:\n"
                "- Show the reasoning path clearly but naturally.\n"
                "- Explain why conclusions follow from facts or data.\n\n"

                "DATA & ACCURACY:\n"
                "- Include formulas, definitions, metrics, or comparisons when relevant.\n"
                "- Do not speculate or invent facts.\n\n"

                f"{vision_context}\n\n"

                "ENDING:\n"
                "- End naturally after the explanation. Do not add summaries beyond the TL;DR."
            )

        # --- Build messages ---
        messages = [{"role": "system", "content": system_prompt}]

        for msg in history:
            if msg.get('role') in ['user', 'assistant']:
                messages.append({"role": msg['role'], "content": msg['content']})

        if file_content and file_type and not vision_image_base64:
            context = f"[DOCUMENT CONTEXT]\n{file_content[:1500]}"
            messages.append({"role": "user", "content": context})

        if vision_image_base64:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": user_question},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{vision_image_base64}"
                        }
                    }
                ]
            })
            model = "openai/gpt-4o"
        else:
            messages.append({"role": "user", "content": user_question})
            model = "openai/gpt-4o-mini"

        # --- API call ---
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nelavista.com",
            "X-Title": "Nelavista AI Tutor"
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": 1500
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            data=json.dumps(payload),
            timeout=60
        )

        if response.status_code != 200:
            raise Exception(response.text)

        answer = response.json()["choices"][0]["message"]["content"]

        # --- Save solution responses ---
        if mode == "solution":
            new_q = UserQuestions(
                username=username,
                question=user_question,
                answer=answer
            )
            db.session.add(new_q)
            db.session.commit()

        # Clean up old files
        cleanup_old_files()

        return jsonify({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": answer
                }
            }],
            "file_used": bool(file_content or vision_image_base64),
            "vision_used": bool(vision_image_base64)
        })

    except Exception as e:
        debug_print(f"❌ Error in /ask route: {str(e)}")
        return jsonify({'error': 'Failed to process your question.'}), 500

# ============================================
# NEW: Clear Context Endpoint
# ============================================
@app.route('/clear_context', methods=['POST'])
@login_required
def clear_context():
    """Clear uploaded file context from session and delete the file from disk"""
    try:
        # Delete the file from disk if it exists
        file_path = session.get('last_file_path')
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            debug_print(f"🗑️ Deleted file: {file_path}")
        
        # Clear session variables
        session.pop('last_file_content', None)
        session.pop('last_file_type', None)
        session.pop('last_upload_time', None)
        session.pop('last_image_base64', None)
        session.pop('last_file_path', None)
        session.pop('last_file_id', None)
        session.pop('last_file_name', None)
        
        return jsonify({
            "success": True,
            "message": "File context cleared successfully"
        })
    except Exception as e:
        debug_print(f"❌ Error clearing context: {e}")
        return jsonify({
            "success": False,
            "error": "Failed to clear context"
        }), 500

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy-policy.html')

@app.route('/settings')
@login_required
def settings():
    memory = {
        "traits": session.get('traits', []),
        "more_info": session.get('more_info', ''),
        "enable_memory": session.get('enable_memory', False)
    }
    return render_template('settings.html', memory=memory, theme=session.get('theme'), language=session.get('language'))

@app.route('/memory', methods=['POST'])
@login_required
def save_memory():
    session['theme'] = request.form.get('theme')
    session['language'] = request.form.get('language')
    session['notifications'] = 'notifications' in request.form
    flash('Settings saved!')
    return redirect('/settings')

@app.route('/telavista/memory', methods=['POST'])
@login_required
def telavista_save_memory():
    print("Saving Telavista memory!")
    flash('Memory saved!')
    return redirect('/settings')

@app.route('/materials')
@login_required
def materials():
    all_courses = ["Python", "Data Science", "AI Basics", "Math", "Physics"]
    selected_course = request.args.get("course")
    materials = []

    if selected_course:
        materials = [
            {
                "title": f"{selected_course} Introduction",
                "description": f"Basics of {selected_course}",
                "link": "https://youtube.com"
            },
            {
                "title": f"{selected_course} Tutorial",
                "description": f"Complete guide on {selected_course}",
                "link": "https://youtube.com"
            }
        ]

    return render_template(
        "materials.html",
        courses=all_courses,
        selected_course=selected_course,
        materials=materials
    )

@app.route('/api/materials')
def get_study_materials():
    query = request.args.get("q", "python")

    pdfs = []
    try:
        pdf_html = requests.get(
            f"https://www.pdfdrive.com/search?q={query}", 
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        ).text
        soup = BeautifulSoup(pdf_html, 'html.parser')
        for book in soup.select('.file-left')[:5]:
            title = book.select_one('img')['alt']
            link = "https://www.pdfdrive.com" + book.parent['href']
            pdfs.append({'title': title, 'link': link})
    except Exception as e:
        pdfs = [{"error": str(e)}]

    books = []
    try:
        ol_data = requests.get(
            f"https://openlibrary.org/search.json?q={query}",
            timeout=10
        ).json()
        for doc in ol_data.get("docs", [])[:5]:
            books.append({
                "title": doc.get("title"),
                "author": ', '.join(doc.get("author_name", [])) if doc.get("author_name") else "Unknown",
                "link": f"https://openlibrary.org{doc.get('key')}"
            })
    except Exception as e:
        books = [{"error": str(e)}]

    return jsonify({
        "query": query,
        "pdfs": pdfs,
        "books": books
    })

@app.route('/ai/materials')
def ai_materials():
    topic = request.args.get("topic")
    level = request.args.get("level")
    department = request.args.get("department")
    goal = request.args.get("goal", "general")
    
    if not topic or not level or not department:
        return jsonify({"error": "Missing one or more parameters: topic, level, department"}), 400

    # AI Explanation
    prompt = f"""
    You're an educational AI helping a {level} student in the {department} department.
    They want to learn: '{goal}' in the topic of {topic}.
    Provide a short and clear explanation to help them get started.
    End with: '📚 Here are materials to study further:'
    """

    explanation = ""
    try:
        if os.getenv('OPENAI_API_KEY'):
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You're a helpful and knowledgeable tutor."},
                    {"role": "user", "content": prompt}
                ]
            )
            explanation = response.choices[0].message.content
        else:
            explanation = f"As an AI tutor, I'd recommend starting with the basics of {topic}. Focus on understanding the fundamental concepts first, then gradually move to more advanced topics. 📚 Here are materials to study further:"
    except Exception as e:
        explanation = f"Let me help you learn {topic}. Start with the basic concepts and build from there. 📚 Here are materials to study further:"

    # Search PDFDrive
    pdfs = []
    try:
        pdf_html = requests.get(
            f"https://www.pdfdrive.com/search?q={topic}", 
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        ).text
        soup = BeautifulSoup(pdf_html, 'html.parser')
        for book in soup.select('.file-left')[:10]:
            title = book.select_one('img')['alt']
            if is_academic_book(title, topic, department):
                link = "https://www.pdfdrive.com" + book.parent['href']
                pdfs.append({'title': title, 'link': link})
    except Exception as e:
        pdfs = [{"error": str(e)}]

    # Search OpenLibrary
    books = []
    try:
        ol_data = requests.get(
            f"https://openlibrary.org/search.json?q={topic}",
            timeout=10
        ).json()
        for doc in ol_data.get("docs", [])[:10]:
            title = doc.get("title", "")
            if is_academic_book(title, topic, department):
                books.append({
                    "title": doc.get("title"),
                    "author": ', '.join(doc.get("author_name", [])) if doc.get("author_name") else "Unknown",
                    "link": f"https://openlibrary.org{doc.get('key')}"
                })
    except Exception as e:
        books = [{"error": str(e)}]

    if not pdfs and not books:
        return jsonify({
            "query": topic,
            "ai_explanation": explanation,
            "pdfs": [],
            "books": [],
            "message": "❌ No academic study materials found for this topic."
        })

    return jsonify({
        "query": topic,
        "ai_explanation": explanation,
        "pdfs": pdfs,
        "books": books
    })

@app.route('/reels', methods=['GET'])
@login_required
def reels():
    categories = ["Tech", "Motivation", "Islamic", "AI"]
    selected_category = request.args.get("category")
    videos = []

    if selected_category:
        videos = [
            {"title": f"{selected_category} Reel 1", "video_id": "abc123"},
            {"title": f"{selected_category} Reel 2", "video_id": "def456"}
        ]

    return render_template("reels.html",
                           user=session.get("user"),
                           categories=categories,
                           selected_category=selected_category,
                           videos=videos)

@app.route("/api/reels")
def get_reels():
    course = request.args.get("course")

    all_reels = [
        {"course": "Accountancy", "caption": "Introduction to Accounting", "video_url": "https://youtu.be/Gua2Bo_G-J0?si=FNnNZBbmBh0yqvrk"},
        {"course": "Zoology", "caption": "Animal Classification", "video_url": "https://example.com/videos/zoology1.mp4"},
        {"course": "Accountancy", "caption": "Financial Statements Basics", "video_url": "https://youtu.be/fb7YCVR5fIU?si=XWozkxGoBV2HP2HW"},
        {"course": "Accountancy", "caption": "Management Accounting Overview", "video_url": "https://youtu.be/qISkyoiGHcI?si=BKRnkFfl-fqKXgLG"},
        {"course": "Accountancy", "caption": "Auditing Principles", "video_url": "https://youtu.be/27gabbJQZqc?si=rsOLmkD2QXOoxSoi"},
        {"course": "Accountancy", "caption": "Taxation Fundamentals", "video_url": "https://youtu.be/Cox8rLXYAGQ?si=CvKUaPuPJOxPb6cr"},
        {"course": "Accountancy", "caption": "Learn Accounting in under 5 hours", "video_url": "https://youtu.be/gPBhGkBN30s?si=bUYfaccZPlBni3aZ"},

        # Accounting
        {"course": "Accounting", "caption": "Basics of Double Entry", "video_url": "https://youtu.be/cjO8qHM5Wjg?si=P0hcqm9x-wjmXpN3"},
        {"course": "Accounting", "caption": "Trail Balance Explained", "video_url": "https://youtu.be/3_PfoTzSCQE?si=SGRI7KVJ6ZC3iJe7"},
        {"course": "Accounting", "caption": "Financial Analysis Techniques", "video_url": "https://youtu.be/g2wEFJ7upNs?si=ht44vAply2f7b-P0"},
        {"course": "Accounting", "caption": "Cost Accounting Overview", "video_url": "https://youtu.be/a5D3Iopi0-4?si=vXOVFcV1NqPGt6Tk"},
        {"course": "Accounting", "caption": "Budgeting and Forecasting", "video_url": "https://youtu.be/GjxhDo9luh8?si=BXn4Z5J-RdKJdBoP"},

        # Agriculture
        {"course": "Agriculture", "caption": "Introduction to Agriculture", "video_url": "https://youtu.be/1FLcijYWHZQ?si=B6iWcOVNXYCDWsKR"},
        {"course": "Agriculture", "caption": "Crop Production Techniques", "video_url": "https://youtu.be/j4-0rNhxoKs?si=XaUcN8zOq1EtkVbX"},
        {"course": "Agriculture", "caption": "Soil Fertility Management", "video_url": "https://youtu.be/TjbxOEEOCh0?si=grkiA5OewbgtFF78"},
        {"course": "Agriculture", "caption": "Livestock Management", "video_url": "https://youtu.be/TjbxOEEOCh0?si=Jr_UpYvei_oieZxz"},
        {"course": "Agriculture", "caption": "Agricultural Economics", "video_url": "https://youtu.be/fbOiwV3gBLg?si=f8HcQW1xdOEfQXEy"},

        # Arabic Studies
        {"course": "Arabic Studies", "caption": "Arabic Language Basics", "video_url": "https://youtu.be/X1mC1XY65Kc?si=gIIUVXBrseXau1Tj"},
        {"course": "Arabic Studies", "caption": "Arabic Grammar Essentials", "video_url": "https://youtu.be/CKD1O4tKZUA?si=JwH8Hb090aZTAI7n"},
        {"course": "Arabic Studies", "caption": "Conversational Arabic", "video_url": "https://youtu.be/dinQIb4ZFXY?si=eGF1Vhsdwm8imJ3Y"},
        {"course": "Arabic Studies", "caption": "Arabic Poetry Introduction", "video_url": "https://youtu.be/ZmjK5cu81RA?si=XnRGefNNXTCws278"},
        {"course": "Arabic Studies", "caption": "Arabic Writing Skills", "video_url": "https://youtu.be/b_WdZCrKr3k?si=pYe0F4bLx8FiT8HT"},

        # Banking and Finance
        {"course": "Banking and Finance", "caption": "Banking Systems Overview", "video_url": "https://youtu.be/fTTGALaRZoc?si=ThB2kkYTd_iIhFX1"},
        {"course": "Banking and Finance", "caption": "Financial Markets Basics", "video_url": "https://youtu.be/UOwi7MBSfhk?si=XSyvxPRp4mEQx2OH"},
        {"course": "Banking and Finance", "caption": "Loan and Credit Management", "video_url": "https://youtu.be/f3VgVOgAUoE?si=JwfSWSogIZeIMpY8"},
        {"course": "Banking and Finance", "caption": "Investment Banking Intro", "video_url": "https://youtu.be/-PkN15TtFnc?si=xgcAoZBAdge-PBjb"},
        {"course": "Banking and Finance", "caption": "Risk Management in Banking", "video_url": "https://youtu.be/BLAEuVSAlVM?si=hubXYQaexc2Iizjd"},

        # Biochemistry
        {"course": "Biochemistry", "caption": "Introduction to Biochemistry", "video_url": "https://youtu.be/CHJsaq2lNjU?si=owCTFJffO4MyBtPB"},
        {"course": "Biochemistry", "caption": "Enzymes and their Functions", "video_url": "https://youtu.be/ozdO1mLXBQE?si=Xj6z5vY8rAgRMdA_"},
        {"course": "Biochemistry", "caption": "Metabolism Basics", "video_url": "https://youtu.be/onDQ9KgDSVw?si=4IKHj5VVJoahw51B"},
        {"course": "Biochemistry", "caption": "Protein Synthesis", "video_url": "https://youtu.be/8wAwLwJAGHs?si=vDuhcZbjQ0nNyhoL"},
        {"course": "Biochemistry", "caption": "Biochemical Techniques", "video_url": "https://youtu.be/lDWL_EEhReo?si=F4nhulNv3l0nxRcA"},

        # Botany
        {"course": "Botany", "caption": "Plant Classification", "video_url": "https://youtu.be/SAM5mcHkSxU?si=P1cX_dbg0pGJFDBB"},
        {"course": "Botany", "caption": "Photosynthesis Process", "video_url": "https://youtu.be/-ZRsLhaukn8?si=CfNMcb-tVWwq6-be"},
        {"course": "Botany", "caption": "Plant Anatomy", "video_url": "https://youtu.be/pvVvCt6Kdp8?si=3ubgF8WibXgzFLcI"},
        {"course": "Botany", "caption": "Plant Reproduction", "video_url": "https://youtu.be/h077JEQ8w6g?si=O5vHjcnuPetMLbCo"},
        {"course": "Botany", "caption": "Ecology and Environment", "video_url": "https://youtu.be/fxVGiq1kggg?si=ESD-BALtjmX0Qxcb"},

        # Business Administration
        {"course": "Business Administration", "caption": "Principles of Management", "video_url": "https://example.com/videos/management1.mp4"},
        {"course": "Business Administration", "caption": "Marketing Strategies", "video_url": "https://example.com/videos/marketing.mp4"},
        {"course": "Business Administration", "caption": "Human Resources Management", "video_url": "https://example.com/videos/hr.mp4"},
        {"course": "Business Administration", "caption": "Financial Planning", "video_url": "https://example.com/videos/financial_planning.mp4"},
        {"course": "Business Administration", "caption": "Business Ethics", "video_url": "https://example.com/videos/ethics.mp4"},

        # Chemical and Polymer Engineering
        {"course": "Chemical and Polymer Engineering", "caption": "Introduction to Chemical Engineering", "video_url": "https://example.com/videos/chemical1.mp4"},
        {"course": "Chemical and Polymer Engineering", "caption": "Polymer Chemistry", "video_url": "https://example.com/videos/polymer_chemistry.mp4"},
        {"course": "Chemical and Polymer Engineering", "caption": "Process Control", "video_url": "https://example.com/videos/process_control.mp4"},
        {"course": "Chemical and Polymer Engineering", "caption": "Chemical Reactor Design", "video_url": "https://example.com/videos/reactor_design.mp4"},
        {"course": "Chemical and Polymer Engineering", "caption": "Environmental Impact of Chemical Processes", "video_url": "https://example.com/videos/environment.mp4"},

        # Chemistry
        {"course": "Chemistry", "caption": "Atomic Structure", "video_url": "https://example.com/videos/atomic_structure.mp4"},
        {"course": "Chemistry", "caption": "Chemical Bonding", "video_url": "https://example.com/videos/chemical_bonding.mp4"},
        {"course": "Chemistry", "caption": "Periodic Table Trends", "video_url": "https://example.com/videos/periodic_trends.mp4"},
        {"course": "Chemistry", "caption": "Acids and Bases", "video_url": "https://example.com/videos/acids_bases.mp4"},
        {"course": "Chemistry", "caption": "Organic Chemistry Basics", "video_url": "https://example.com/videos/organic_chemistry.mp4"},

        # Christian Religious Studies
        {"course": "Christian Religious Studies", "caption": "Introduction to Christianity", "video_url": "https://example.com/videos/crs1.mp4"},
        {"course": "Christian Religious Studies", "caption": "Bible Overview", "video_url": "https://example.com/videos/bible_overview.mp4"},
        {"course": "Christian Religious Studies", "caption": "Christian Doctrine", "video_url": "https://example.com/videos/doctrine.mp4"},
        {"course": "Christian Religious Studies", "caption": "Christian Worship", "video_url": "https://example.com/videos/worship.mp4"},
        {"course": "Christian Religious Studies", "caption": "Ethics in Christianity", "video_url": "https://example.com/videos/ethics.mp4"},

        # Computer Science
        {"course": "Computer Science", "caption": "Intro to Python 🔥", "video_url": "https://youtu.be/XKHEtdqhLK8?si=6wwK4EPdr9_A6L9w"},
        {"course": "Computer Science", "caption": "Understanding APIs", "video_url": "/static/reels/api_basics.mp4"},
        {"course": "Computer Science", "caption": "Data Structures Overview", "video_url": "https://example.com/videos/data_structures.mp4"},
        {"course": "Computer Science", "caption": "Algorithms Explained", "video_url": "https://example.com/videos/algorithms.mp4"},
        {"course": "Computer Science", "caption": "Database Management Systems", "video_url": "https://example.com/videos/dbms.mp4"},

        # Dentistry and Dental Surgery
        {"course": "Dentistry and Dental Surgery", "caption": "Basics of Oral Anatomy", "video_url": "https://example.com/videos/dentistry1.mp4"},
        {"course": "Dentistry and Dental Surgery", "caption": "Dental Hygiene Tips", "video_url": "https://example.com/videos/dentistry2.mp4"},
        {"course": "Dentistry and Dental Surgery", "caption": "Common Dental Procedures", "video_url": "https://example.com/videos/dentistry3.mp4"},
        {"course": "Dentistry and Dental Surgery", "caption": "Dental Radiography", "video_url": "https://example.com/videos/dentistry4.mp4"},
        {"course": "Dentistry and Dental Surgery", "caption": "Oral Disease Prevention", "video_url": "https://example.com/videos/dentistry5.mp4"},

        # Drama / Dramatic / Performing Arts
        {"course": "Drama / Dramatic / Performing Arts", "caption": "Introduction to Acting", "video_url": "https://example.com/videos/drama1.mp4"},
        {"course": "Drama / Dramatic / Performing Arts", "caption": "Stage Management Basics", "video_url": "https://example.com/videos/drama2.mp4"},
        {"course": "Drama / Dramatic / Performing Arts", "caption": "Theatrical Lighting Techniques", "video_url": "https://example.com/videos/drama3.mp4"},
        {"course": "Drama / Dramatic / Performing Arts", "caption": "Voice Modulation", "video_url": "https://example.com/videos/drama4.mp4"},
        {"course": "Drama / Dramatic / Performing Arts", "caption": "Costume Design", "video_url": "https://example.com/videos/drama5.mp4"},

        # Early Childhood Education
        {"course": "Early Childhood Education", "caption": "Child Development Stages", "video_url": "https://example.com/videos/early_childhood1.mp4"},
        {"course": "Early Childhood Education", "caption": "Play-Based Learning", "video_url": "https://example.com/videos/early_childhood2.mp4"},
        {"course": "Early Childhood Education", "caption": "Classroom Management", "video_url": "https://example.com/videos/early_childhood3.mp4"},
        {"course": "Early Childhood Education", "caption": "Curriculum Planning", "video_url": "https://example.com/videos/early_childhood4.mp4"},
        {"course": "Early Childhood Education", "caption": "Assessment Techniques", "video_url": "https://example.com/videos/early_childhood5.mp4"},

        # Education and Accounting
        {"course": "Education and Accounting", "caption": "Educational Accounting Principles", "video_url": "https://example.com/videos/educ_acc1.mp4"},
        {"course": "Education and Accounting", "caption": "Budgeting in Education", "video_url": "https://example.com/videos/educ_acc2.mp4"},
        {"course": "Education and Accounting", "caption": "Financial Planning for Schools", "video_url": "https://example.com/videos/educ_acc3.mp4"},
        {"course": "Education and Accounting", "caption": "Accounting Records Management", "video_url": "https://example.com/videos/educ_acc4.mp4"},
        {"course": "Education and Accounting", "caption": "Auditing in Educational Institutions", "video_url": "https://example.com/videos/educ_acc5.mp4"},

        # Education and Biology
        {"course": "Education and Biology", "caption": "Biology Curriculum Development", "video_url": "https://example.com/videos/educ_bio1.mp4"},
        {"course": "Education and Biology", "caption": "Lab Techniques for Teachers", "video_url": "https://example.com/videos/educ_bio2.mp4"},
        {"course": "Education and Biology", "caption": "Teaching Genetics", "video_url": "https://example.com/videos/educ_bio3.mp4"},
        {"course": "Education and Biology", "caption": "Ecology in Education", "video_url": "https://example.com/videos/educ_bio4.mp4"},
        {"course": "Education and Biology", "caption": "Biology Teaching Aids", "video_url": "https://example.com/videos/educ_bio5.mp4"},

        # Education and Chemistry
        {"course": "Education and Chemistry", "caption": "Chemistry Lab Safety", "video_url": "https://example.com/videos/educ_chem1.mp4"},
        {"course": "Education and Chemistry", "caption": "Teaching Organic Chemistry", "video_url": "https://example.com/videos/educ_chem2.mp4"},
        {"course": "Education and Chemistry", "caption": "Chemistry Demonstrations", "video_url": "https://example.com/videos/educ_chem3.mp4"},
        {"course": "Education and Chemistry", "caption": "Curriculum for Chemistry Teachers", "video_url": "https://example.com/videos/educ_chem4.mp4"},
        {"course": "Education and Chemistry", "caption": "Assessment Methods in Chemistry", "video_url": "https://example.com/videos/educ_chem5.mp4"},

        # Education and Christian Religious Studies
        {"course": "Education and Christian Religious Studies", "caption": "Teaching Religious Studies", "video_url": "https://example.com/videos/educ_crs1.mp4"},
        {"course": "Education and Christian Religious Studies", "caption": "Curriculum Design for CRS", "video_url": "https://example.com/videos/educ_crs2.mp4"},
        {"course": "Education and Christian Religious Studies", "caption": "Teaching Biblical Stories", "video_url": "https://example.com/videos/educ_crs3.mp4"},
        {"course": "Education and Christian Religious Studies", "caption": "Classroom Discussions on Faith", "video_url": "https://example.com/videos/educ_crs4.mp4"},
        {"course": "Education and Christian Religious Studies", "caption": "Assessment in Religious Education", "video_url": "https://example.com/videos/educ_crs5.mp4"},

        # Education and Computer Science
        {"course": "Education and Computer Science", "caption": "Integrating Tech in Education", "video_url": "https://example.com/videos/educ_comp1.mp4"},
        {"course": "Education and Computer Science", "caption": "Basic Programming for Teachers", "video_url": "https://example.com/videos/educ_comp2.mp4"},
        {"course": "Education and Computer Science", "caption": "E-learning Tools", "video_url": "https://example.com/videos/educ_comp3.mp4"},
        {"course": "Education and Computer Science", "caption": "Computer Lab Management", "video_url": "https://example.com/videos/educ_comp4.mp4"},
        {"course": "Education and Computer Science", "caption": "Curriculum Development in CS", "video_url": "https://example.com/videos/educ_comp5.mp4"},

        # Education and Economics
        {"course": "Education and Economics", "caption": "Teaching Economics Concepts", "video_url": "https://example.com/videos/educ_econ1.mp4"},
        {"course": "Education and Economics", "caption": "Economic Models in Classroom", "video_url": "https://example.com/videos/educ_econ2.mp4"},
        {"course": "Education and Economics", "caption": "Curriculum for Economics Education", "video_url": "https://example.com/videos/educ_econ3.mp4"},
        {"course": "Education and Economics", "caption": "Assessing Economics Learning", "video_url": "https://example.com/videos/educ_econ4.mp4"},
        {"course": "Education and Economics", "caption": "Using Data in Economics Teaching", "video_url": "https://example.com/videos/educ_econ5.mp4"},

        # Education and English Language
        {"course": "Education and English Language", "caption": "Teaching Grammar Effectively", "video_url": "https://example.com/videos/educ_eng1.mp4"},
        {"course": "Education and English Language", "caption": "Literature in Education", "video_url": "https://example.com/videos/educ_eng2.mp4"},
        {"course": "Education and English Language", "caption": "Language Teaching Strategies", "video_url": "https://example.com/videos/educ_eng3.mp4"},
        {"course": "Education and English Language", "caption": "Assessing Language Skills", "video_url": "https://example.com/videos/educ_eng4.mp4"},
        {"course": "Education and English Language", "caption": "Creating Engaging Lessons", "video_url": "https://example.com/videos/educ_eng5.mp4"},

        # Education and French
        {"course": "Education and French", "caption": "French Grammar for Teachers", "video_url": "https://example.com/videos/educ_french1.mp4"},
        {"course": "Education and French", "caption": "French Vocabulary Building", "video_url": "https://example.com/videos/educ_french2.mp4"},
        {"course": "Education and French", "caption": "Teaching French Conversation", "video_url": "https://example.com/videos/educ_french3.mp4"},
        {"course": "Education and French", "caption": "French Language Curriculum", "video_url": "https://example.com/videos/educ_french4.mp4"},
        {"course": "Education and French", "caption": "Assessment in French", "video_url": "https://example.com/videos/educ_french5.mp4"},

        # Education and Geography
        {"course": "Education and Geography", "caption": "Teaching Map Skills", "video_url": "https://example.com/videos/educ_geo1.mp4"},
        {"course": "Education and Geography", "caption": "Curriculum in Geography", "video_url": "https://example.com/videos/educ_geo2.mp4"},
        {"course": "Education and Geography", "caption": "Fieldwork and Practicals", "video_url": "https://example.com/videos/educ_geo3.mp4"},
        {"course": "Education and Geography", "caption": "Using GIS in Education", "video_url": "https://example.com/videos/educ_geo4.mp4"},
        {"course": "Education and Geography", "caption": "Assessment Techniques", "video_url": "https://example.com/videos/educ_geo5.mp4"},

        # Education and History
        {"course": "Education and History", "caption": "Teaching Historical Skills", "video_url": "https://example.com/videos/educ_hist1.mp4"},
        {"course": "Education and History", "caption": "History Curriculum Design", "video_url": "https://example.com/videos/educ_hist2.mp4"},
        {"course": "Education and History", "caption": "Integrating Primary Sources", "video_url": "https://example.com/videos/educ_hist3.mp4"},
        {"course": "Education and History", "caption": "Assessment in History", "video_url": "https://example.com/videos/educ_hist4.mp4"},
        {"course": "Education and History", "caption": "Project-Based History Learning", "video_url": "https://example.com/videos/educ_hist5.mp4"},

        # Education and Islamic Studies
        {"course": "Education and Islamic Studies", "caption": "Teaching Islamic History", "video_url": "https://example.com/videos/educ_islam1.mp4"},
        {"course": "Education and Islamic Studies", "caption": "Islamic Religious Practices", "video_url": "https://example.com/videos/educ_islam2.mp4"},
        {"course": "Education and Islamic Studies", "caption": "Curriculum for Islamic Studies", "video_url": "https://example.com/videos/educ_islam3.mp4"},
        {"course": "Education and Islamic Studies", "caption": "Islamic Ethical Teachings", "video_url": "https://example.com/videos/educ_islam4.mp4"},
        {"course": "Education and Islamic Studies", "caption": "Assessment Methods", "video_url": "https://example.com/videos/educ_islam5.mp4"},

        # Education and Mathematics
        {"course": "Education and Mathematics", "caption": "Teaching Mathematical Concepts", "video_url": "https://example.com/videos/educ_math1.mp4"},
        {"course": "Education and Mathematics", "caption": "Developing Problem-Solving Skills", "video_url": "https://example.com/videos/educ_math2.mp4"},
        {"course": "Education and Mathematics", "caption": "Mathematics Curriculum Design", "video_url": "https://example.com/videos/educ_math3.mp4"},
        {"course": "Education and Mathematics", "caption": "Use of Technology in Math Teaching", "video_url": "https://example.com/videos/educ_math4.mp4"},
        {"course": "Education and Mathematics", "caption": "Assessment in Mathematics", "video_url": "https://example.com/videos/educ_math5.mp4"},

        # Education and Physics
        {"course": "Education and Physics", "caption": "Teaching Physics Fundamentals", "video_url": "https://example.com/videos/educ_phys1.mp4"},
        {"course": "Education and Physics", "caption": "Lab Techniques in Physics", "video_url": "https://example.com/videos/educ_phys2.mp4"},
        {"course": "Education and Physics", "caption": "Curriculum Development for Physics", "video_url": "https://example.com/videos/educ_phys3.mp4"},
        {"course": "Education and Physics", "caption": "Using Simulations in Physics", "video_url": "https://example.com/videos/educ_phys4.mp4"},
        {"course": "Education and Physics", "caption": "Assessment Strategies in Physics", "video_url": "https://example.com/videos/educ_phys5.mp4"},

        # Education and Political Science
        {"course": "Education and Political Science", "caption": "Teaching Political Concepts", "video_url": "https://example.com/videos/educ_pol1.mp4"},
        {"course": "Education and Political Science", "caption": "Curriculum Design for Political Science", "video_url": "https://example.com/videos/educ_pol2.mp4"},
        {"course": "Education and Political Science", "caption": "Role of Discussion in Teaching Politics", "video_url": "https://example.com/videos/educ_pol3.mp4"},
        {"course": "Education and Political Science", "caption": "Assessment Methods in Political Science", "video_url": "https://example.com/videos/educ_pol4.mp4"},
        {"course": "Education and Political Science", "caption": "Using Case Studies in Teaching", "video_url": "https://example.com/videos/educ_pol5.mp4"},

        # Education and Yoruba
        {"course": "Education and Yoruba", "caption": "Teaching Yoruba Language", "video_url": "https://example.com/videos/educ_yoruba1.mp4"},
        {"course": "Education and Yoruba", "caption": "Yoruba Literature in Education", "video_url": "https://example.com/videos/educ_yoruba2.mp4"},
        {"course": "Education and Yoruba", "caption": "Curriculum Development for Yoruba", "video_url": "https://example.com/videos/educ_yoruba3.mp4"},
        {"course": "Education and Yoruba", "caption": "Assessment Strategies", "video_url": "https://example.com/videos/educ_yoruba4.mp4"},
        {"course": "Education and Yoruba", "caption": "Language Preservation Techniques", "video_url": "https://example.com/videos/educ_yoruba5.mp4"},

        # Educational Management
        {"course": "Educational Management", "caption": "School Administration Basics", "video_url": "https://example.com/videos/ed_mgmt1.mp4"},
        {"course": "Educational Management", "caption": "Leadership in Education", "video_url": "https://example.com/videos/ed_mgmt2.mp4"},
        {"course": "Educational Management", "caption": "Curriculum Planning", "video_url": "https://example.com/videos/ed_mgmt3.mp4"},
        {"course": "Educational Management", "caption": "Resource Management", "video_url": "https://example.com/videos/ed_mgmt4.mp4"},
        {"course": "Educational Management", "caption": "Policy Development in Schools", "video_url": "https://example.com/videos/ed_mgmt5.mp4"},

        # Electronics and Computer Engineering
        {"course": "Electronics and Computer Engineering", "caption": "Basics of Electronics", "video_url": "https://example.com/videos/ece1.mp4"},
        {"course": "Electronics and Computer Engineering", "caption": "Digital Circuits", "video_url": "https://example.com/videos/ece2.mp4"},
        {"course": "Electronics and Computer Engineering", "caption": "Microcontrollers and Applications", "video_url": "https://example.com/videos/ece3.mp4"},
        {"course": "Electronics and Computer Engineering", "caption": "Signal Processing", "video_url": "https://example.com/videos/ece4.mp4"},
        {"course": "Electronics and Computer Engineering", "caption": "Embedded Systems", "video_url": "https://example.com/videos/ece5.mp4"},

        # English Language
        {"course": "English Language", "caption": "Grammar and Sentence Structure", "video_url": "https://example.com/videos/eng1.mp4"},
        {"course": "English Language", "caption": "Creative Writing Techniques", "video_url": "https://example.com/videos/eng2.mp4"},
        {"course": "English Language", "caption": "Effective Communication Skills", "video_url": "https://example.com/videos/eng3.mp4"},
        {"course": "English Language", "caption": "Literature Analysis", "video_url": "https://example.com/videos/eng4.mp4"},
        {"course": "English Language", "caption": "Language Teaching Strategies", "video_url": "https://example.com/videos/eng5.mp4"},

        # Fine and Applied Arts
        {"course": "Fine and Applied Arts", "caption": "Introduction to Fine Arts", "video_url": "https://example.com/videos/finearts1.mp4"},
        {"course": "Fine and Applied Arts", "caption": "Art Techniques and Styles", "video_url": "https://example.com/videos/finearts2.mp4"},
        {"course": "Fine and Applied Arts", "caption": "Sculpture and Ceramics", "video_url": "https://example.com/videos/finearts3.mp4"},
        {"course": "Fine and Applied Arts", "caption": "Design Principles", "video_url": "https://example.com/videos/finearts4.mp4"},
        {"course": "Fine and Applied Arts", "caption": "Art History Overview", "video_url": "https://example.com/videos/finearts5.mp4"},

        # Fisheries
        {"course": "Fisheries", "caption": "Aquaculture Techniques", "video_url": "https://example.com/videos/fisheries1.mp4"},
        {"course": "Fisheries", "caption": "Fish Species Identification", "video_url": "https://example.com/videos/fisheries2.mp4"},
        {"course": "Fisheries", "caption": "Water Quality Management", "video_url": "https://example.com/videos/fisheries3.mp4"},
        {"course": "Fisheries", "caption": "Fisheries Economics", "video_url": "https://example.com/videos/fisheries4.mp4"},
        {"course": "Fisheries", "caption": "Sustainable Fishing Practices", "video_url": "https://example.com/videos/fisheries5.mp4"},

        # French
        {"course": "French", "caption": "French Grammar Basics", "video_url": "https://example.com/videos/french1.mp4"},
        {"course": "French", "caption": "French Vocabulary Building", "video_url": "https://example.com/videos/french2.mp4"},
        {"course": "French", "caption": "French Conversation Practice", "video_url": "https://example.com/videos/french3.mp4"},
        {"course": "French", "caption": "French Culture and Traditions", "video_url": "https://example.com/videos/french4.mp4"},
        {"course": "French", "caption": "Writing in French", "video_url": "https://example.com/videos/french5.mp4"},

        # Geography and Planning
        {"course": "Geography and Planning", "caption": "Urban Planning Basics", "video_url": "https://example.com/videos/geop1.mp4"},
        {"course": "Geography and Planning", "caption": "GIS Applications", "video_url": "https://example.com/videos/geop2.mp4"},
        {"course": "Geography and Planning", "caption": "Environmental Management", "video_url": "https://example.com/videos/geop3.mp4"},
        {"course": "Geography and Planning", "caption": "Land Use Planning", "video_url": "https://example.com/videos/geop4.mp4"},
        {"course": "Geography and Planning", "caption": "Sustainable Development", "video_url": "https://example.com/videos/geop5.mp4"},

        # Guidance and Counseling
        {"course": "Guidance and Counseling", "caption": "Guidance Techniques", "video_url": "https://example.com/videos/guidance1.mp4"},
        {"course": "Guidance and Counseling", "caption": "Counseling Skills", "video_url": "https://example.com/videos/guidance2.mp4"},
        {"course": "Guidance and Counseling", "caption": "Career Counseling", "video_url": "https://example.com/videos/guidance3.mp4"},
        {"course": "Guidance and Counseling", "caption": "Student Welfare Programs", "video_url": "https://example.com/videos/guidance4.mp4"},
        {"course": "Guidance and Counseling", "caption": "Psychological Support", "video_url": "https://example.com/videos/guidance5.mp4"},

        # Health Education
        {"course": "Health Education", "caption": "Health Promotion Strategies", "video_url": "https://example.com/videos/health1.mp4"},
        {"course": "Health Education", "caption": "Disease Prevention", "video_url": "https://example.com/videos/health2.mp4"},
        {"course": "Health Education", "caption": "Nutrition Education", "video_url": "https://example.com/videos/health3.mp4"},
        {"course": "Health Education", "caption": "Mental Health Awareness", "video_url": "https://example.com/videos/health4.mp4"},
        {"course": "Health Education", "caption": "First Aid Training", "video_url": "https://example.com/videos/health5.mp4"},

        # History and International Studies
        {"course": "History and International Studies", "caption": "World History Overview", "video_url": "https://example.com/videos/history1.mp4"},
        {"course": "History and International Studies", "caption": "International Relations", "video_url": "https://example.com/videos/history2.mp4"},
        {"course": "History and International Studies", "caption": "Colonial and Post-Colonial History", "video_url": "https://example.com/videos/history3.mp4"},
        {"course": "History and International Studies", "caption": "Historical Research Methods", "video_url": "https://example.com/videos/history4.mp4"},
        {"course": "History and International Studies", "caption": "Cultural Heritage Preservation", "video_url": "https://example.com/videos/history5.mp4"},

        # Industrial Relations and Personnel Management
        {"course": "Industrial Relations and Personnel Management", "caption": "Labor Laws Overview", "video_url": "https://example.com/videos/irp1.mp4"},
        {"course": "Industrial Relations and Personnel Management", "caption": "Conflict Resolution", "video_url": "https://example.com/videos/irp2.mp4"},
        {"course": "Industrial Relations and Personnel Management", "caption": "Staff Recruitment and Selection", "video_url": "https://example.com/videos/irp3.mp4"},
        {"course": "Industrial Relations and Personnel Management", "caption": "Performance Appraisal", "video_url": "https://example.com/videos/irp4.mp4"},
        {"course": "Industrial Relations and Personnel Management", "caption": "Workplace Motivation", "video_url": "https://example.com/videos/irp5.mp4"},

        # Insurance
        {"course": "Insurance", "caption": "Types of Insurance", "video_url": "https://example.com/videos/insurance1.mp4"},
        {"course": "Insurance", "caption": "Risk Assessment in Insurance", "video_url": "https://example.com/videos/insurance2.mp4"},
        {"course": "Insurance", "caption": "Claims Processing", "video_url": "https://example.com/videos/insurance3.mp4"},
        {"course": "Insurance", "caption": "Insurance Policies", "video_url": "https://example.com/videos/insurance4.mp4"},
        {"course": "Insurance", "caption": "Insurance Regulations", "video_url": "https://example.com/videos/insurance5.mp4"},

        # Islamic Studies
        {"course": "Islamic Studies", "caption": "Introduction to Islam", "video_url": "https://example.com/videos/islamic1.mp4"},
        {"course": "Islamic Studies", "caption": "Quranic Studies", "video_url": "https://example.com/videos/islamic2.mp4"},
        {"course": "Islamic Studies", "caption": "Islamic Jurisprudence", "video_url": "https://example.com/videos/islamic3.mp4"},
        {"course": "Islamic Studies", "caption": "Islamic History", "video_url": "https://example.com/videos/islamic4.mp4"},
        {"course": "Islamic Studies", "caption": "Islamic Ethics", "video_url": "https://example.com/videos/islamic5.mp4"},

        # Law
        {"course": "Law", "caption": "Introduction to Nigerian Law", "video_url": "https://example.com/videos/law1.mp4"},
        {"course": "Law", "caption": "Legal Systems and Processes", "video_url": "https://example.com/videos/law2.mp4"},
        {"course": "Law", "caption": "Constitutional Law", "video_url": "https://example.com/videos/law3.mp4"},
        {"course": "Law", "caption": "Criminal Law Basics", "video_url": "https://example.com/videos/law4.mp4"},
        {"course": "Law", "caption": "Legal Ethics and Practice", "video_url": "https://example.com/videos/law5.mp4"},

        # Library and Information Science
        {"course": "Library and Information Science", "caption": "Library Management", "video_url": "https://example.com/videos/library1.mp4"},
        {"course": "Library and Information Science", "caption": "Information Retrieval", "video_url": "https://example.com/videos/library2.mp4"},
        {"course": "Library and Information Science", "caption": "Cataloging and Classification", "video_url": "https://example.com/videos/library3.mp4"},
        {"course": "Library and Information Science", "caption": "Digital Libraries", "video_url": "https://example.com/videos/library4.mp4"},
        {"course": "Library and Information Science", "caption": "Reference Services", "video_url": "https://example.com/videos/library5.mp4"},

        # Local Government and Development Studies
        {"course": "Local Government and Development Studies", "caption": "Local Governance Structures", "video_url": "https://example.com/videos/lgds1.mp4"},
        {"course": "Local Government and Development Studies", "caption": "Community Development", "video_url": "https://example.com/videos/lgds2.mp4"},
        {"course": "Local Government and Development Studies", "caption": "Decentralization Policies", "video_url": "https://example.com/videos/lgds3.mp4"},
        {"course": "Local Government and Development Studies", "caption": "Public Administration in Local Govt", "video_url": "https://example.com/videos/lgds4.mp4"},
        {"course": "Local Government and Development Studies", "caption": "Development Planning", "video_url": "https://example.com/videos/lgds5.mp4"},

        # Marketing
        {"course": "Marketing", "caption": "Marketing Principles", "video_url": "https://example.com/videos/marketing1.mp4"},
        {"course": "Marketing", "caption": "Market Research Techniques", "video_url": "https://example.com/videos/marketing2.mp4"},
        {"course": "Marketing", "caption": "Advertising Strategies", "video_url": "https://example.com/videos/marketing3.mp4"},
        {"course": "Marketing", "caption": "Digital Marketing", "video_url": "https://example.com/videos/marketing4.mp4"},
        {"course": "Marketing", "caption": "Consumer Behavior", "video_url": "https://example.com/videos/marketing5.mp4"},

        # Mass Communication
        {"course": "Mass Communication", "caption": "Media and Society", "video_url": "https://example.com/videos/masscom1.mp4"},
        {"course": "Mass Communication", "caption": "Broadcasting Techniques", "video_url": "https://example.com/videos/masscom2.mp4"},
        {"course": "Mass Communication", "caption": "Public Relations", "video_url": "https://example.com/videos/masscom3.mp4"},
        {"course": "Mass Communication", "caption": "Journalism Fundamentals", "video_url": "https://example.com/videos/masscom4.mp4"},
        {"course": "Mass Communication", "caption": "Media Ethics", "video_url": "https://example.com/videos/masscom5.mp4"},

        # Mathematics
        {"course": "Mathematics", "caption": "Algebraic Expressions", "video_url": "https://example.com/videos/math1.mp4"},
        {"course": "Mathematics", "caption": "Calculus Basics", "video_url": "https://example.com/videos/math2.mp4"},
        {"course": "Mathematics", "caption": "Statistics Fundamentals", "video_url": "https://example.com/videos/math3.mp4"},
        {"course": "Mathematics", "caption": "Geometry in Focus", "video_url": "https://example.com/videos/math4.mp4"},
        {"course": "Mathematics", "caption": "Mathematical Problem Solving", "video_url": "https://example.com/videos/math5.mp4"},

        # Mechanical Engineering
        {"course": "Mechanical Engineering", "caption": "Thermodynamics Principles", "video_url": "https://example.com/videos/mech1.mp4"},
        {"course": "Mechanical Engineering", "caption": "Fluid Mechanics", "video_url": "https://example.com/videos/mech2.mp4"},
        {"course": "Mechanical Engineering", "caption": "Machine Design", "video_url": "https://example.com/videos/mech3.mp4"},
        {"course": "Mechanical Engineering", "caption": "Manufacturing Processes", "video_url": "https://example.com/videos/mech4.mp4"},
        {"course": "Mechanical Engineering", "caption": "Automation and Robotics", "video_url": "https://example.com/videos/mech5.mp4"},

        # Medicine and Surgery
        {"course": "Medicine and Surgery", "caption": "Introduction to Human Anatomy", "video_url": "https://example.com/videos/medsurg1.mp4"},
        {"course": "Medicine and Surgery", "caption": "Basics of Pathology", "video_url": "https://example.com/videos/medsurg2.mp4"},
        {"course": "Medicine and Surgery", "caption": "Pharmacology Overview", "video_url": "https://example.com/videos/medsurg3.mp4"},
        {"course": "Medicine and Surgery", "caption": "Surgical Procedures", "video_url": "https://example.com/videos/medsurg4.mp4"},
        {"course": "Medicine and Surgery", "caption": "Patient Care and Ethics", "video_url": "https://example.com/videos/medsurg5.mp4"},

        # Microbiology
        {"course": "Microbiology", "caption": "Microbial Classification", "video_url": "https://example.com/videos/micro1.mp4"},
        {"course": "Microbiology", "caption": "Bacterial Pathogens", "video_url": "https://example.com/videos/micro2.mp4"},
        {"course": "Microbiology", "caption": "Antibiotic Resistance", "video_url": "https://example.com/videos/micro3.mp4"},
        {"course": "Microbiology", "caption": "Lab Techniques", "video_url": "https://example.com/videos/micro4.mp4"},
        {"course": "Microbiology", "caption": "Infection Control", "video_url": "https://example.com/videos/micro5.mp4"},

        # Music
        {"course": "Music", "caption": "Music Theory Basics", "video_url": "https://example.com/videos/music1.mp4"},
        {"course": "Music", "caption": "Instrumental Techniques", "video_url": "https://example.com/videos/music2.mp4"},
        {"course": "Music", "caption": "Music Composition", "video_url": "https://example.com/videos/music3.mp4"},
        {"course": "Music", "caption": "Music Performance Tips", "video_url": "https://example.com/videos/music4.mp4"},
        {"course": "Music", "caption": "Music History", "video_url": "https://example.com/videos/music5.mp4"},

        # Nursing / Nursing Science
        {"course": "Nursing / Nursing Science", "caption": "Basics of Patient Care", "video_url": "https://example.com/videos/nursing1.mp4"},
        {"course": "Nursing / Nursing Science", "caption": "Infection Control", "video_url": "https://example.com/videos/nursing2.mp4"},
        {"course": "Nursing / Nursing Science", "caption": "Pharmacology for Nurses", "video_url": "https://example.com/videos/nursing3.mp4"},
        {"course": "Nursing / Nursing Science", "caption": "Emergency Response", "video_url": "https://example.com/videos/nursing4.mp4"},
        {"course": "Nursing / Nursing Science", "caption": "Patient Assessment", "video_url": "https://example.com/videos/nursing5.mp4"},

        # Pharmacology
        {"course": "Pharmacology", "caption": "Drug Classifications", "video_url": "https://example.com/videos/pharm1.mp4"},
        {"course": "Pharmacology", "caption": "Pharmacokinetics", "video_url": "https://example.com/videos/pharm2.mp4"},
        {"course": "Pharmacology", "caption": "Adverse Drug Reactions", "video_url": "https://example.com/videos/pharm3.mp4"},
        {"course": "Pharmacology", "caption": "Drug Interactions", "video_url": "https://example.com/videos/pharm4.mp4"},
        {"course": "Pharmacology", "caption": "Therapeutic Uses", "video_url": "https://example.com/videos/pharm5.mp4"},

        # Philosophy
        {"course": "Philosophy", "caption": "Introduction to Philosophy", "video_url": "https://example.com/videos/phil1.mp4"},
        {"course": "Philosophy", "caption": "Ethical Theories", "video_url": "https://example.com/videos/phil2.mp4"},
        {"course": "Philosophy", "caption": "Logic and Reasoning", "video_url": "https://example.com/videos/phil3.mp4"},
        {"course": "Philosophy", "caption": "Philosophy of Mind", "video_url": "https://example.com/videos/phil4.mp4"},
        {"course": "Philosophy", "caption": "Existentialism", "video_url": "https://example.com/videos/phil5.mp4"},

        # Physical and Health Education
        {"course": "Physical and Health Education", "caption": "Fitness Training Basics", "video_url": "https://example.com/videos/physh1.mp4"},
        {"course": "Physical and Health Education", "caption": "Health and Wellness", "video_url": "https://example.com/videos/physh2.mp4"},
        {"course": "Physical and Health Education", "caption": "Sports Management", "video_url": "https://example.com/videos/physh3.mp4"},
        {"course": "Physical and Health Education", "caption": "Nutrition and Diet", "video_url": "https://example.com/videos/physh4.mp4"},
        {"course": "Physical and Health Education", "caption": "First Aid and Emergency Procedures", "video_url": "https://example.com/videos/physh5.mp4"},

        # Physics
        {"course": "Physics", "caption": "Newton's Laws of Motion", "video_url": "https://example.com/videos/phys1.mp4"},
        {"course": "Physics", "caption": "Electromagnetism Basics", "video_url": "https://example.com/videos/phys2.mp4"},
        {"course": "Physics", "caption": "Wave Properties", "video_url": "https://example.com/videos/phys3.mp4"},
        {"course": "Physics", "caption": "Optics Fundamentals", "video_url": "https://example.com/videos/phys4.mp4"},
        {"course": "Physics", "caption": "Thermodynamics Principles", "video_url": "https://example.com/videos/phys5.mp4"},

        # Physiology
        {"course": "Physiology", "caption": "Human Body Systems", "video_url": "https://example.com/videos/physio1.mp4"},
        {"course": "Physiology", "caption": "Circulatory System", "video_url": "https://example.com/videos/physio2.mp4"},
        {"course": "Physiology", "caption": "Nervous System", "video_url": "https://example.com/videos/physio3.mp4"},
        {"course": "Physiology", "caption": "Respiratory System", "video_url": "https://example.com/videos/physio4.mp4"},
        {"course": "Physiology", "caption": "Digestive System", "video_url": "https://example.com/videos/physio5.mp4"},

        # Political Science
        {"course": "Political Science", "caption": "Introduction to Political Science", "video_url": "https://example.com/videos/pol1.mp4"},
        {"course": "Political Science", "caption": "Government Systems", "video_url": "https://example.com/videos/pol2.mp4"},
        {"course": "Political Science", "caption": "International Relations", "video_url": "https://example.com/videos/pol3.mp4"},
        {"course": "Political Science", "caption": "Political Theories", "video_url": "https://example.com/videos/pol4.mp4"},
        {"course": "Political Science", "caption": "Electoral Processes", "video_url": "https://example.com/videos/pol5.mp4"},

        # Portuguese / English
        {"course": "Portuguese / English", "caption": "Language Basics", "video_url": "https://example.com/videos/port_eng1.mp4"},
        {"course": "Portuguese / English", "caption": "Vocabulary Building", "video_url": "https://example.com/videos/port_eng2.mp4"},
        {"course": "Portuguese / English", "caption": "Conversational Skills", "video_url": "https://example.com/videos/port_eng3.mp4"},
        {"course": "Portuguese / English", "caption": "Writing Skills", "video_url": "https://example.com/videos/port_eng4.mp4"},
        {"course": "Portuguese / English", "caption": "Cultural Contexts", "video_url": "https://example.com/videos/port_eng5.mp4"},

        # Psychology
        {"course": "Psychology", "caption": "Introduction to Psychology", "video_url": "https://example.com/videos/psych1.mp4"},
        {"course": "Psychology", "caption": "Developmental Psychology", "video_url": "https://example.com/videos/psych2.mp4"},
        {"course": "Psychology", "caption": "Behavioral Theories", "video_url": "https://example.com/videos/psych3.mp4"},
        {"course": "Psychology", "caption": "Counseling Techniques", "video_url": "https://example.com/videos/psych4.mp4"},
        {"course": "Psychology", "caption": "Research Methods", "video_url": "https://example.com/videos/psych5.mp4"},

        # Public Administration
        {"course": "Public Administration", "caption": "Public Policy Making", "video_url": "https://example.com/videos/pubadmin1.mp4"},
        {"course": "Public Administration", "caption": "Bureaucracy and Management", "video_url": "https://example.com/videos/pubadmin2.mp4"},
        {"course": "Public Administration", "caption": "Ethics in Public Service", "video_url": "https://example.com/videos/pubadmin3.mp4"},
        {"course": "Public Administration", "caption": "Budgeting and Finance", "video_url": "https://example.com/videos/pubadmin4.mp4"},
        {"course": "Public Administration", "caption": "Governance and Development", "video_url": "https://example.com/videos/pubadmin5.mp4"},

        # Sociology
        {"course": "Sociology", "caption": "Basic Sociological Concepts", "video_url": "https://example.com/videos/sociology1.mp4"},
        {"course": "Sociology", "caption": "Social Stratification", "video_url": "https://example.com/videos/sociology2.mp4"},
        {"course": "Sociology", "caption": "Family and Society", "video_url": "https://example.com/videos/sociology3.mp4"},
        {"course": "Sociology", "caption": "Social Change", "video_url": "https://example.com/videos/sociology4.mp4"},
        {"course": "Sociology", "caption": "Research Methods in Sociology", "video_url": "https://example.com/videos/sociology5.mp4"},

        # Teacher Education Science
        {"course": "Teacher Education Science", "caption": "Curriculum Development", "video_url": "https://example.com/videos/ted_science1.mp4"},
        {"course": "Teacher Education Science", "caption": "Teaching Methodologies", "video_url": "https://example.com/videos/ted_science2.mp4"},
        {"course": "Teacher Education Science", "caption": "Practical Teaching Skills", "video_url": "https://example.com/videos/ted_science3.mp4"},
        {"course": "Teacher Education Science", "caption": "Assessment and Evaluation", "video_url": "https://example.com/videos/ted_science4.mp4"},
        {"course": "Teacher Education Science", "caption": "Classroom Management", "video_url": "https://example.com/videos/ted_science5.mp4"},

        # Transport Management Technology
        {"course": "Transport Management Technology", "caption": "Logistics and Supply Chain", "video_url": "https://example.com/videos/transport1.mp4"},
        {"course": "Transport Management Technology", "caption": "Transport Planning", "video_url": "https://example.com/videos/transport2.mp4"},
        {"course": "Transport Management Technology", "caption": "Fleet Management", "video_url": "https://example.com/videos/transport3.mp4"},
        {"course": "Transport Management Technology", "caption": "Traffic Management", "video_url": "https://example.com/videos/transport4.mp4"},
        {"course": "Transport Management Technology", "caption": "Safety Regulations", "video_url": "https://example.com/videos/transport5.mp4"},

        # Yoruba
        {"course": "Yoruba", "caption": "Yoruba Language Basics", "video_url": "https://example.com/videos/yoruba1.mp4"},
        {"course": "Yoruba", "caption": "Yoruba Proverbs and Sayings", "video_url": "https://example.com/videos/yoruba2.mp4"},
        {"course": "Yoruba", "caption": "Yoruba Cultural Practices", "video_url": "https://example.com/videos/yoruba3.mp4"},
        {"course": "Yoruba", "caption": "Yoruba Traditional Attire", "video_url": "https://example.com/videos/yoruba4.mp4"},
        {"course": "Yoruba", "caption": "Yoruba Folklore", "video_url": "https://example.com/videos/yoruba5.mp4"},

    ]

    matching = [r for r in all_reels if r["course"] == course] if course else all_reels
    return jsonify({"reels": matching})

@app.route('/CBT', methods=['GET'])
@login_required
def CBT():
    topics = ["Python", "Hadith", "AI", "Math"]
    selected_topic = request.args.get("topic")
    questions = []

    if selected_topic:
        questions = [
            {"question": f"What is {selected_topic}?", "options": ["Option A", "Option B", "Option C"], "answer": "Option A"},
            {"question": f"Why is {selected_topic} important?", "options": ["Reason 1", "Reason 2", "Reason 3"], "answer": "Reason 2"}
        ]
    return render_template("CBT.html", 
                         user=session.get("user"), 
                         topics=topics, 
                         selected_topic=selected_topic, 
                         questions=questions)

@app.route('/teach-me-ai')
@login_required
def teach_me_ai():
    return render_template('teach-me-ai.html')

@app.route('/api/ai-teach')
def ai_teach():
    course = request.args.get("course")
    level = request.args.get("level")

    if not course or not level:
        return jsonify({"error": "Missing course or level"}), 400

    prompt = f"You're a tutor. Teach a {level} student the basics of {course} in a friendly and easy-to-understand way."

    try:
        if os.getenv('OPENAI_API_KEY'):
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are an educational AI assistant."},
                    {"role": "user", "content": prompt}
                ]
            )
            return jsonify({"summary": response.choices[0].message.content})
        else:
            return jsonify({"summary": f"Let me teach you the basics of {course}. We'll start with fundamental concepts and build up from there. This is perfect for {level} students!"})
    except Exception as e:
        return jsonify({"error": str(e)})

# ============================================
# Live Meeting Routes
# ============================================
@app.route('/teacher')
def teacher_create():
    room_id = str(uuid.uuid4())[:8]
    return redirect(f'/teacher/{room_id}')

@app.route('/teacher/<room_id>')
def teacher_view(room_id):
    return render_template('teacher.html', room_id=room_id)

@app.route('/student/<room_id>')
def student_view(room_id):
    return render_template('student.html', room_id=room_id)

@app.route('/join', methods=['POST'])
def join_room_post():
    room_id = request.form.get('room_id', '').strip()
    if not room_id:
        flash('Please enter a room ID')
        return redirect('/')
    return redirect(f'/student/{room_id}')

# ============================================
# Live Meeting Routes
# ============================================
@app.route('/live-meeting')
@app.route('/live_meeting')
def live_meeting():
    return render_template('live_meeting.html')

@app.route('/live-meeting/teacher')
@app.route('/live_meeting/teacher')
def live_meeting_teacher_create():
    room_id = str(uuid.uuid4())[:8]
    return redirect(url_for('live_meeting_teacher_view', room_id=room_id))

@app.route('/live-meeting/teacher/<room_id>')
@app.route('/live_meeting/teacher/<room_id>')
def live_meeting_teacher_view(room_id):
    return render_template('teacher_live.html', room_id=room_id)

@app.route('/live-meeting/student/<room_id>')
@app.route('/live_meeting/student/<room_id>')
def live_meeting_student_view(room_id):
    return render_template('student_live.html', room_id=room_id)

@app.route('/live-meeting/join', methods=['POST'])
@app.route('/live_meeting/join', methods=['POST'])
def live_meeting_join():
    room_id = request.form.get('room_id', '').strip()
    username = request.form.get('username', '').strip()
    
    if not room_id:
        flash('Please enter a meeting ID')
        return redirect('/live_meeting')
    
    if not username:
        username = f"Student_{str(uuid.uuid4())[:4]}"
    
    session['live_username'] = username
    
    return redirect(url_for('live_meeting_student_view', room_id=room_id))

# ============================================
# NEW: Connection Test Route
# ============================================
@app.route('/test-connection')
def test_connection():
    """Simple connection test page"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Connection Test</title>
        <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    </head>
    <body>
        <h1>Socket.IO Connection Test</h1>
        <div id="status">Connecting...</div>
        <div id="events"></div>
        
        <script>
            const socket = io();
            
            socket.on('connect', () => {
                document.getElementById('status').innerHTML = '✅ Connected! SID: ' + socket.id;
                logEvent('Connected to server');
            });
            
            socket.on('disconnect', () => {
                document.getElementById('status').innerHTML = '❌ Disconnected';
                logEvent('Disconnected from server');
            });
            
            socket.on('connect_error', (error) => {
                document.getElementById('status').innerHTML = '❌ Connection Error';
                logEvent('Error: ' + error.message);
            });
            
            function logEvent(msg) {
                const eventsDiv = document.getElementById('events');
                eventsDiv.innerHTML = new Date().toLocaleTimeString() + ': ' + msg + '<br>' + eventsDiv.innerHTML;
            }
        </script>
    </body>
    </html>
    """

# ============================================
# Debug Route
# ============================================
@app.route('/debug/rooms')
def debug_rooms():
    """Debug endpoint to view current room states"""
    debug_info = {
        'rooms': rooms,
        'participants': participants,
        'room_authority': room_authority,
        'total_rooms': len(rooms),
        'total_participants': len(participants)
    }
    return json.dumps(debug_info, indent=2, default=str)

# ============================================
# Create default user
# ============================================
create_default_user()

# ============================================
# Run Server
# ============================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 NELAVISTA LIVE + Tellavista Platform")
    print("🌟 Full Educational Platform with Vision AI")
    print("📚 AI Tutor + Study Materials + Live Classes")
    print(f"{'='*60}")
    print("✅ Vision AI Features:")
    print("   - GPT-4o Vision for image analysis")
    print("   - Automatic image context detection")
    print("   - Fallback to OCR when vision fails")
    print("   - Image base64 storage in session")
    print("\n✅ Educational Platform Features:")
    print("   - User Authentication (Signup/Login)")
    print("   - AI Tutor (Nelavista) with File Upload")
    print("   - PDF & Image Processing (OCR, Text Extraction)")
    print("   - Study Materials & PDFs")
    print("   - Course Reels & Videos")
    print("   - CBT Test System")
    print("\n✅ Live Meeting Features:")
    print("   - Full Mesh WebRTC Video Calls")
    print("   - Teacher Authority System")
    print("   - Real-time Collaboration")
    print("   - Raise Hand & Quick Feedback")
    print(f"{'='*60}")
    print("\n📡 Connection test: http://localhost:5000/test-connection")
    print("👨‍🏫 Teacher test: http://localhost:5000/live_meeting/teacher")
    print("👨‍🎓 Student test: http://localhost:5000/live_meeting")
    print("🎓 Platform login: http://localhost:5000/login (test/test123)")
    print(f"{'='*60}")
    print("\n⚠️  IMPORTANT: Install required packages:")
    print("   pip install PyPDF2 pdfplumber Pillow pytesseract openai")
    print(f"{'='*60}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=DEBUG_MODE)
