import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")

# ============================================
# IMPORTS
# ============================================

import os
import sys
import json
import re
import time
import uuid
import base64
import shutil
import traceback
from datetime import datetime
from functools import wraps

# Third-party imports
import requests
from bs4 import BeautifulSoup
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Flask and extensions
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============================================
# Configuration
# ============================================
DEBUG_MODE = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'

def debug_print(*args, **kwargs):
    if DEBUG_MODE:
        print(*args, **kwargs)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ============================================
# Flask App Initialization
# ============================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///tellavista.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['IMAGE_FOLDER'] = os.path.join(os.getcwd(), 'extracted_images')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB limit for video uploads

# Create upload folders if they don't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['IMAGE_FOLDER'], exist_ok=True)

# Video upload folder inside static
VIDEO_UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
os.makedirs(VIDEO_UPLOAD_FOLDER, exist_ok=True)

ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv'}

def allowed_video_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS

# ============================================
# Extensions
# ============================================
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# ============================================
# Missing stub functions (to be implemented)
# ============================================
def extract_text_from_pdf(file):
    """Stub: extract text from PDF file."""
    try:
        # In a real implementation, use PyPDF2, pdfplumber, etc.
        return "PDF text extraction not implemented."
    except Exception:
        return ""

def extract_text_from_pdf_turbo(file):
    """Stub: enhanced text extraction."""
    return extract_text_from_pdf(file)

def extract_images_from_pdf(file, session_id):
    """Stub: extract images from PDF."""
    return []

def extract_tables_from_pdf(file):
    """Stub: extract tables from PDF."""
    return []

def analyze_document_structure(text):
    """Stub: analyze document structure."""
    return {
        'document_title': '',
        'main_topics': [],
        'definitions': []
    }

def is_diagram_or_visual(text):
    """Stub: determine if image is diagram."""
    return False

def extract_text_from_image(file):
    """Stub: OCR on image."""
    return "DIAGRAM_OR_VISUAL_CONTENT"

def cleanup_stale_files():
    """Stub: clean up old uploaded files."""
    pass

# ============================================
# Database Models
# ============================================
class User(db.Model):
    """User model for authentication and user management."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    level = db.Column(db.Integer, default=1)
    joined_on = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        """Hash and set the user's password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verify the user's password against the stored hash."""
        return check_password_hash(self.password_hash, password)

class UserQuestions(db.Model):
    """Model for storing user questions and AI responses (memory system)."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    memory_layer = db.Column(db.String(50))

class UserProfile(db.Model):
    """Model for storing user preferences and learning styles."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    level = db.Column(db.String(50))
    department = db.Column(db.String(100))
    traits = db.Column(db.Text)  # JSON string of learning preferences
    explanation_style = db.Column(db.String(50))
    focus_areas = db.Column(db.Text)  # JSON string of focus areas
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        """Convert profile object to dictionary."""
        return {
            "username": self.username,
            "level": self.level,
            "department": self.department,
            "traits": json.loads(self.traits) if self.traits else [],
            "explanation_style": self.explanation_style,
            "focus_areas": json.loads(self.focus_areas) if self.focus_areas else []
        }

class Room(db.Model):
    """Model for live meeting rooms."""
    id = db.Column(db.String(32), primary_key=True)
    teacher_id = db.Column(db.String(120))
    teacher_name = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Video(db.Model):
    __tablename__ = 'videos'
    id = db.Column(db.Integer, primary_key=True)
    creator_name = db.Column(db.String(150), nullable=False)
    department = db.Column(db.String(100), nullable=False)
    course = db.Column(db.String(200), nullable=False)
    level = db.Column(db.String(50), nullable=False)      # increased to 50
    semester = db.Column(db.String(20), nullable=False)   # increased to 20
    caption = db.Column(db.Text)
    video_url = db.Column(db.String(500), nullable=False)
    bank_name = db.Column(db.String(100))
    account_number = db.Column(db.String(20))
    views = db.Column(db.Integer, default=0)
    likes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_approved = db.Column(db.Boolean, default=False)
    username = db.Column(db.String(150))

    def to_dict(self):
        return {
            'id': self.id,
            'creator_name': self.creator_name,
            'department': self.department,
            'course': self.course,
            'level': self.level,
            'semester': self.semester,
            'caption': self.caption,
            'video_url': self.video_url,
            'views': self.views,
            'likes': self.likes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_approved': self.is_approved
        }

# ============================================
# SESSION MEMORY FUNCTIONS (temporary, last 5 messages)
# ============================================
def get_session_memory():
    """Retrieve the current session's chat memory."""
    if 'chat_memory' not in session:
        session['chat_memory'] = []
    return session['chat_memory']

def add_to_session_memory(role, content, max_messages=5):
    """Add a message to session memory, keeping only the last `max_messages` exchanges."""
    memory = get_session_memory()
    memory.append({
        "role": role,
        "content": content
    })
    # Keep only last N messages (each exchange = user + assistant → 2 messages per exchange)
    if len(memory) > max_messages * 2:
        memory = memory[-max_messages * 2:]
    session['chat_memory'] = memory

# ============================================
# Helper Functions
# ============================================
def login_required(f):
    """Decorator to require login for protected routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def create_database_if_not_exists():
    """Create database if it doesn't exist."""
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
    """Initialize database with error handling and upgrade video columns."""
    try:
        # Try to create database first
        create_database_if_not_exists()

        with app.app_context():
            db.create_all()
            print("✅ Database tables created/verified")

            # ---- Upgrade video table columns if needed (PostgreSQL) ----
            try:
                from sqlalchemy import text
                with db.engine.connect() as conn:
                    # Increase semester and level column sizes
                    conn.execute(text("ALTER TABLE videos ALTER COLUMN semester TYPE VARCHAR(20)"))
                    conn.execute(text("ALTER TABLE videos ALTER COLUMN level TYPE VARCHAR(50)"))
                    conn.commit()
                    print("✅ Video table columns upgraded (semester -> 20, level -> 50)")
            except Exception as e:
                # Columns may already be upgraded or not exist; ignore
                debug_print(f"Note: Could not upgrade video columns (might already be fine): {e}")

            # Test the connection
            from sqlalchemy import text
            db.session.execute(text('SELECT 1'))
            print("✅ Database connection successful")
            # Log the database URI (mask password)
            masked_uri = re.sub(r':[^@]*@', ':****@', app.config['SQLALCHEMY_DATABASE_URI'])
            print(f"🗄️ Connected to database: {masked_uri}")
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
    """Create default user with error handling."""
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

# ============================================
# Session Cleanup Functions
# ============================================
def cleanup_old_files():
    """Remove old uploaded files from session and disk."""
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

# ============================================
# File Processing Helper Functions
# ============================================
def encode_image_to_base64(file):
    """Encode image file to base64 string."""
    try:
        file.seek(0)
        image_bytes = file.read()
        return base64.b64encode(image_bytes).decode('utf-8')
    except Exception as e:
        debug_print(f"❌ Error encoding image to base64: {e}")
        return None

def is_academic_book(title, topic, department):
    """Determine if a book title appears to be academic based on keywords."""
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
    """Get existing room or create new one."""
    if room_id not in rooms:
        rooms[room_id] = {
            'participants': {},      # socket_id -> {'username', 'role', 'joined_at'}
            'teacher_sid': None,
            'created_at': datetime.utcnow().isoformat()
        }
    return rooms[room_id]

def get_room_authority(room_id):
    """Get or create authority state for a room."""
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
    """Get list of all participants in room except exclude_sid."""
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
    """Remove empty rooms."""
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
    """Handle client connection to Socket.IO."""
    sid = request.sid
    # CRITICAL FIX: Join client to their private SID room for direct messaging
    join_room(sid)
    participants[sid] = {'room_id': None, 'username': None, 'role': None}
    debug_print(f"✅ Client connected: {sid} (joined private room: {sid})")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection from Socket.IO."""
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
    """Join room and get all existing participants."""
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
# WebRTC Signaling - Full Mesh Support
# ============================================
@socketio.on('webrtc-offer')
def handle_webrtc_offer(data):
    """Relay WebRTC offer to specific participant."""
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
    """Relay WebRTC answer to specific participant."""
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
    """Relay ICE candidate to specific participant."""
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
# Full Mesh Initiation System
# ============================================
@socketio.on('request-full-mesh')
def handle_request_full_mesh(data):
    """Initiate full mesh connections between all participants."""
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
    """Teacher mutes all students."""
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
    """Teacher unmutes all students."""
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
# Control Events
# ============================================
@socketio.on('start-broadcast')
def handle_start_broadcast(data):
    """Teacher starts broadcasting to all students."""
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

        # Initiate WebRTC connections for each student
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
    """Keep-alive ping."""
    emit('pong', {'timestamp': datetime.utcnow().isoformat()})

# ============================================
# FLASK ROUTES - CORE PLATFORM
# ============================================
@app.route('/')
def landing():
    """Render landing page if not logged in, otherwise redirect to dashboard."""
    user = session.get('user')
    if user:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/dashboard')
@login_required
def dashboard():
    """Render main dashboard (index.html) for logged-in users."""
    user = session.get('user')
    return render_template('index.html', user=user)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """Handle user registration."""
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
            return redirect(url_for('dashboard'))

        except Exception as e:
            print(f"❌ Error during signup: {e}")
            db.session.rollback()
            flash('Error creating account. Please try again.')
            return redirect(url_for('signup'))

    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user authentication."""
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
                    return redirect(url_for('dashboard'))
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
    """Handle user logout and clear session."""
    session.clear()
    flash('You have been logged out.')
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile():
    """Render user profile page."""
    user = session.get('user', {})
    return render_template('profile.html', user=user)

# ============================================
# TURBO AI-STYLE PDF ANALYZER ROUTES
# ============================================
@app.route('/analyze')
@login_required
def analyze_page():
    """Render the Turbo AI-Style PDF Analyzer page."""
    user = session.get('user')
    return render_template('analyze.html', user=user)

@app.route('/analyze', methods=['POST'])
@login_required
def analyze_pdf():
    """Handle PDF upload and extraction."""

    try:
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file uploaded"}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "error": "No file selected"}), 400

        if not allowed_file(file.filename):
            return jsonify({"success": False, "error": "Only PDF files are supported"}), 400

        # Generate session ID
        session_id = str(uuid.uuid4())

        # Read file content
        file_content = file.read()
        if len(file_content) == 0:
            return jsonify({"success": False, "error": "Uploaded file is empty"}), 400

        # Create multiple streams for extraction
        from io import BytesIO
        file_streams = [BytesIO(file_content) for _ in range(3)]

        # Extract content
        debug_print("📄 Starting comprehensive extraction...")
        text = extract_text_from_pdf_turbo(file_streams[0])

        if not text or len(text.strip()) < 100:
            return jsonify({"success": False, "error": "PDF is unreadable or contains too little text"}), 400

        images = extract_images_from_pdf(file_streams[1], session_id)
        tables = extract_tables_from_pdf(file_streams[2])

        # Analyze document structure
        document_analysis = analyze_document_structure(text)

        # Store in session
        analyzer_content = {
            "type": "pdf",
            "text": text,
            "images": images,
            "tables": tables,
            "document_analysis": document_analysis,
            "filename": file.filename,
            "session_id": session_id,
            "timestamp": datetime.utcnow().isoformat(),
            "text_length": len(text),
            "image_count": len(images),
            "table_count": len(tables)
        }

        session['analyzer_content'] = analyzer_content

        debug_print(f"✅ PDF analysis complete:")
        debug_print(f"   - Text: {len(text)} characters")
        debug_print(f"   - Images: {len(images)} extracted")
        debug_print(f"   - Tables: {len(tables)} extracted")
        debug_print(f"   - Main topics: {len(document_analysis.get('main_topics', []))}")

        return jsonify({
            "success": True,
            "filename": file.filename,
            "text_length": len(text),
            "image_count": len(images),
            "table_count": len(tables),
            "preview": text[:500] + "..." if len(text) > 500 else text,
            "session_id": session_id,
            "main_topics": document_analysis.get('main_topics', [])[:3]
        })

    except Exception as e:
        debug_print(f"❌ Analyze error: {str(e)}")
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Processing failed: {str(e)}"}), 500

# ============================================
# Turbo AI-Style Notes Generation Function
# ============================================
def generate_turbo_style_notes(text, tables, images, filename, document_analysis):
    """Generate comprehensive lecture-style notes using AI."""
    try:
        # Build summaries
        tables_summary = f"Found {len(tables)} table(s)."
        if tables:
            for i, table in enumerate(tables[:3], 1):
                tables_summary += f"\nTable {i} (page {table.get('page', '?')}): {table.get('text', '')[:200]}"

        images_summary = f"Found {len(images)} image(s)."

        PDF_ANALYSIS_PROMPT = """
You are an expert academic tutor and textbook author. Your task is to transform raw extracted content from a PDF into ULTIMATE LECTURE-STYLE NOTES that are clear, comprehensive, and exam-focused.

Your output must:
1. Turn all bullet points into flowing textbook explanations.
2. Create comprehensive comparison tables from any comparable data.
3. Explain EVERY concept in student-friendly language.
4. Add structure with clear headings and logical flow.
5. Include practical examples and real-world applications.
6. Prepare for exams with must-know facts and common questions.

Remember: Your output should serve both slow learners (clear explanations) and fast learners (advanced insights).
"""

        enhanced_prompt = f"""
EXTRACTED TABLES SUMMARY:
{tables_summary}

EXTRACTED IMAGES: {images_summary}

YOUR TASK:
Transform this raw material into ULTIMATE LECTURE-STYLE NOTES that:
1. Turn all bullet points into flowing textbook explanations
2. Create comprehensive comparison tables from any comparable data
3. Explain EVERY concept in student-friendly language
4. Add structure with clear headings and logical flow
5. Include practical examples and real-world applications
6. Prepare for exams with must-know facts and common questions

REMEMBER: Your output should serve both slow learners (clear explanations) and fast learners (advanced insights).
"""

        # Call AI API
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nelavista.com",
            "X-Title": "Nellavista Turbo-Style Notes Generator"
        }

        payload = {
            "model": "openai/gpt-4-turbo",
            "messages": [
                {"role": "system", "content": PDF_ANALYSIS_PROMPT},
                {"role": "user", "content": enhanced_prompt}
            ],
            "temperature": 0.2,
            "max_tokens": 7000
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=180
        )

        if response.status_code == 200:
            data = response.json()
            notes = data["choices"][0]["message"]["content"]

            # Enhance with extracted content
            enhanced_notes = enhance_notes_with_extractions(notes, tables, images)

            return enhanced_notes
        else:
            debug_print(f"❌ AI API error: {response.status_code}")
            raise Exception(f"AI service error: {response.status_code}")

    except Exception as e:
        debug_print(f"❌ AI note generation failed: {e}")
        return generate_structured_fallback(text, tables, images, filename, document_analysis)

def enhance_notes_with_extractions(notes, tables, images):
    """Enhance AI notes with actual extracted content."""

    enhanced = notes

    # Add extracted tables section if we have tables
    if tables and len(tables) > 0:
        table_section = "\n\n---\n\n## 📊 EXTRACTED DATA TABLES FROM DOCUMENT\n\n"
        table_section += "*Below are the actual tables extracted from the original document:*\n\n"

        for i, table in enumerate(tables[:5], 1):
            table_section += f"### 📋 Table {i} (Page {table.get('page', '?')})\n\n"
            table_section += table.get("markdown", "Table format not available") + "\n\n"
            if i < min(5, len(tables)):
                table_section += "---\n\n"

        enhanced += table_section

    # Add extracted images section
    if images and len(images) > 0:
        image_section = "\n\n---\n\n## 🖼️ EXTRACTED DIAGRAMS & ILLUSTRATIONS\n\n"
        image_section += f"*The original document contains {len(images)} image(s) that supplement the text:*\n\n"

        for i, img in enumerate(images[:3], 1):
            image_section += f"### Image {i} (Page {img.get('page', '?')})\n\n"
            image_section += f"![{img.get('alt', 'Diagram')}]({img.get('url', '')})\n"
            image_section += f"*{img.get('alt', 'Document diagram')}*\n\n"

        enhanced += image_section

    # Add footer
    enhanced += "\n\n---\n"
    enhanced += "*Generated by Nellavista Academic Document Analyzer | "
    enhanced += f"{datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"

    return enhanced

def generate_structured_fallback(text, tables, images, filename, document_analysis):
    """Generate structured notes as fallback."""

    notes = f"# 📚 {filename} - Comprehensive Study Guide\n\n"
    notes += f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n"

    # Document overview
    notes += "## 📖 Document Overview\n\n"
    if document_analysis.get('document_title'):
        notes += f"**Main Topic**: {document_analysis['document_title']}\n\n"

    # Key concepts
    if document_analysis.get('main_topics'):
        notes += "## 🎯 Key Concepts\n\n"
        for i, topic in enumerate(document_analysis['main_topics'][:10], 1):
            notes += f"{i}. **{topic}**\n"
        notes += "\n"

    # Definitions
    if document_analysis.get('definitions'):
        notes += "## 🔍 Key Definitions\n\n"
        for i, definition in enumerate(document_analysis['definitions'][:8], 1):
            notes += f"**Definition {i}**: {definition}\n\n"

    # Tables
    if tables:
        notes += f"## 📊 Extracted Tables ({len(tables)} found)\n\n"
        for i, table in enumerate(tables[:3], 1):
            notes += f"### Table {i} (Page {table.get('page', '?')})\n\n"
            notes += table.get("markdown", "Table not available in markdown") + "\n\n"

    # Images
    if images:
        notes += f"## 🖼️ Extracted Images ({len(images)} found)\n\n"
        for img in images[:2]:
            notes += f"![{img.get('alt', 'Diagram')}]({img.get('url', '')})\n"
            notes += f"*{img.get('alt', 'Document image')}*\n\n"

    # Content preview
    notes += "## 📝 Document Content Preview\n\n"
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    for i, para in enumerate(paragraphs[:6]):
        notes += f"{para}\n\n"
        if i == 2:  # Add a separator after first few paragraphs
            notes += "---\n\n"

    notes += "\n---\n"
    notes += "*Note: AI-powered comprehensive analysis was unavailable. Showing structured extraction.*\n"

    return notes

@app.route('/understand', methods=['POST'])
@login_required
def understand_content():
    """Generate Turbo AI-style comprehensive notes."""

    try:
        if 'analyzer_content' not in session:
            return jsonify({
                "success": False,
                "error": "No PDF uploaded. Please upload a PDF first."
            }), 400

        content = session.get('analyzer_content')

        if not content:
            return jsonify({
                "success": False,
                "error": "Session expired. Please upload the PDF again."
            }), 400

        # Get extracted content
        text = content.get("text", "")
        images = content.get("images", [])
        tables = content.get("tables", [])
        document_analysis = content.get("document_analysis", {})
        filename = content.get("filename", "Study Material")

        if not text or len(text.strip()) < 100:
            return jsonify({
                "success": False,
                "error": "Uploaded PDF content is insufficient for analysis."
            }), 400

        debug_print(f"🧠 Generating Turbo AI-style notes for: {filename}")
        debug_print(f"   - Text available: {len(text)} chars")
        debug_print(f"   - Tables to incorporate: {len(tables)}")
        debug_print(f"   - Images to reference: {len(images)}")

        # Generate comprehensive notes
        notes = generate_turbo_style_notes(text, tables, images, filename, document_analysis)

        # Update session
        content["generated_notes"] = notes
        content["notes_timestamp"] = datetime.utcnow().isoformat()
        content["markdown"] = notes
        session['analyzer_content'] = content

        # Prepare data for frontend
        image_urls = []
        for img in images[:5]:
            if os.path.exists(img.get("path", "")):
                image_urls.append({
                    "url": img.get("url", ""),
                    "alt": img.get("alt", "Diagram"),
                    "page": img.get("page", 1)
                })

        table_data = []
        for table in tables[:5]:
            table_data.append({
                "markdown": table.get("markdown", ""),
                "page": table.get("page", 1),
                "preview": table.get("text", "")[:150] + "..."
            })

        debug_print(f"✅ Generated {len(notes.split())} words of comprehensive notes")

        return jsonify({
            "success": True,
            "mode": "turbo_comprehensive",
            "markdown": notes,
            "filename": filename,
            "images": image_urls,
            "tables": table_data,
            "note_type": "lecture_textbook_style",
            "word_count": len(notes.split()),
            "has_tables": len(tables) > 0,
            "has_images": len(images) > 0
        })

    except Exception as e:
        debug_print(f"[UNDERSTAND] Error: {e}")
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": f"Failed to generate comprehensive notes: {str(e)}"
        }), 500

@app.route('/analyzer/clear', methods=['POST'])
@login_required
def clear_analyzer_content():
    """Clear uploaded content."""
    try:
        content = session.get('analyzer_content', {})
        session_id = content.get('session_id')

        # Remove session images folder
        if session_id:
            session_folder = os.path.join(app.config['IMAGE_FOLDER'], session_id)
            if os.path.exists(session_folder):
                shutil.rmtree(session_folder)
                debug_print(f"Cleared image folder: {session_folder}")

        if 'analyzer_content' in session:
            session.pop('analyzer_content')

        debug_print("✅ Analyzer content cleared")

        return jsonify({
            "success": True,
            "message": "Content cleared successfully"
        })

    except Exception as e:
        debug_print(f"❌ Error clearing content: {e}")
        return jsonify({
            "success": False,
            "error": f"Error clearing content: {str(e)}"
        }), 500

@app.route('/analyzer/status', methods=['GET'])
@login_required
def get_analyzer_status():
    """Get processing status."""
    try:
        content = session.get('analyzer_content')

        if content and content.get('type') == 'pdf':
            has_notes = 'generated_notes' in content
            return jsonify({
                "success": True,
                "has_content": True,
                "has_notes": has_notes,
                "content_type": 'pdf',
                "filename": content.get('filename'),
                "image_count": len(content.get('images', [])),
                "table_count": len(content.get('tables', [])),
                "text_length": len(content.get('text', '')),
                "notes_length": len(content.get('generated_notes', '')) if has_notes else 0,
                "session_id": content.get('session_id', 'unknown')
            })
        else:
            return jsonify({
                "success": True,
                "has_content": False,
                "has_notes": False,
                "message": "No PDF content uploaded"
            })

    except Exception as e:
        debug_print(f"❌ Error getting status: {e}")
        return jsonify({
            "success": False,
            "error": f"Error getting status: {str(e)}"
        }), 500

@app.route('/static/extracted_images/<path:filename>')
def serve_extracted_image(filename):
    """Serve extracted images."""
    try:
        return send_from_directory(app.config['IMAGE_FOLDER'], filename)
    except Exception as e:
        debug_print(f"Error serving image {filename}: {e}")
        return "Image not found", 404

# ============================================
# SAFE HTML POST-PROCESSING (preserves LaTeX)
# ============================================
def safe_markdown_to_html(text):
    """
    Convert common Markdown patterns to HTML while preserving LaTeX math.
    This is a safety net; the AI is instructed to output HTML, but if it fails,
    we salvage the output.
    """
    if not text:
        return text

    # Temporarily replace LaTeX math blocks with placeholders
    math_placeholders = {}
    # Inline math: \( ... \) and $ ... $
    def replace_inline_math(match):
        placeholder = f"__INLINE_MATH_{len(math_placeholders)}__"
        math_placeholders[placeholder] = match.group(0)
        return placeholder
    # Display math: $$ ... $$ and \[ ... \]
    def replace_display_math(match):
        placeholder = f"__DISPLAY_MATH_{len(math_placeholders)}__"
        math_placeholders[placeholder] = match.group(0)
        return placeholder

    # First protect math
    text = re.sub(r'\\\(.*?\\\)', replace_inline_math, text, flags=re.DOTALL)
    text = re.sub(r'\$[^\$]*?\$', replace_inline_math, text, flags=re.DOTALL)
    text = re.sub(r'\$\$.*?\$\$', replace_display_math, text, flags=re.DOTALL)
    text = re.sub(r'\\\[.*?\\\]', replace_display_math, text, flags=re.DOTALL)

    # Convert Markdown headings
    # Replace ### heading with <h3>heading</h3>
    text = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.*?)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)  # treat # as h2

    # Convert bold and italic
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text, flags=re.DOTALL)
    text = re.sub(r'__(.*?)__', r'<strong>\1</strong>', text, flags=re.DOTALL)
    text = re.sub(r'\*(.*?)\*', r'<em>\1</em>', text, flags=re.DOTALL)
    text = re.sub(r'_(.*?)_', r'<em>\1</em>', text, flags=re.DOTALL)

    # Convert unordered lists: lines starting with - or * or +
    lines = text.split('\n')
    in_list = False
    new_lines = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(('- ', '* ', '+ ')):
            if not in_list:
                new_lines.append('<ul>')
                in_list = True
            # Remove the bullet and wrap in <li>
            item = stripped[2:].strip()
            new_lines.append(f'<li>{item}</li>')
        else:
            if in_list:
                new_lines.append('</ul>')
                in_list = False
            new_lines.append(line)
    if in_list:
        new_lines.append('</ul>')
    text = '\n'.join(new_lines)

    # Convert numbered lists (simple: 1., 2., etc.)
    lines = text.split('\n')
    in_ol = False
    new_lines = []
    for line in lines:
        stripped = line.lstrip()
        match = re.match(r'^(\d+)\.\s+(.*)$', stripped)
        if match:
            if not in_ol:
                new_lines.append('<ol>')
                in_ol = True
            item = match.group(2).strip()
            new_lines.append(f'<li>{item}</li>')
        else:
            if in_ol:
                new_lines.append('</ol>')
                in_ol = False
            new_lines.append(line)
    if in_ol:
        new_lines.append('</ol>')
    text = '\n'.join(new_lines)

    # Convert Markdown tables to HTML tables (simplified)
    # This is basic; for complex tables AI should output proper 美>
    # We'll look for lines with | and --- separators
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        if '|' in lines[i]:
            # potential table header
            header_line = lines[i].strip()
            if i+1 < len(lines) and re.match(r'^[\s\|:-]+$', lines[i+1]):  # separator line
                # extract headers
                headers = [h.strip() for h in header_line.split('|') if h.strip()]
                separator = lines[i+1]
                # collect data rows
                data_rows = []
                j = i+2
                while j < len(lines) and '|' in lines[j] and not re.match(r'^[\s\|:-]+$', lines[j]):
                    row = [c.strip() for c in lines[j].split('|') if c.strip()]
                    data_rows.append(row)
                    j += 1
                # build HTML table
                table_html = ' 表\n<thead>\n<tr>\n'
                for h in headers:
                    table_html += f'<th>{h}</th>\n'
                table_html += '</tr>\n</thead>\n<tbody>\n'
                for row in data_rows:
                    table_html += '<tr>\n'
                    for cell in row:
                        table_html += f'<td>{cell}</td>\n'
                    table_html += '</tr>\n'
                table_html += '</tbody>\n</table>'
                # replace the block
                lines[i:j] = [table_html]
                i = j
                continue
        i += 1
    text = '\n'.join(lines)

    # Restore math placeholders
    for placeholder, math in math_placeholders.items():
        text = text.replace(placeholder, math)

    # Remove any remaining lone Markdown symbols (like ** without closing)
    text = re.sub(r'\*\*', '', text)
    text = re.sub(r'__', '', text)
    text = re.sub(r'\*', '', text)
    text = re.sub(r'\_', '', text)

    return text

# ============================================
# AI TUTOR ROUTES - WITH SESSION MEMORY (last 5 messages) & HTML‑ONLY OUTPUT
# ============================================
@app.route('/talk-to-nelavista')
@login_required
def talk_to_nelavista():
    """Render AI chat interface."""
    return render_template('talk-to-nelavista.html')

@app.route('/ask_with_files', methods=['POST'])
@login_required
def ask_with_files():
    GRACEFUL_FALLBACK = "I'm having a little trouble answering right now, but please try again."

    try:
        username = session['user']['username']

        message = request.form.get('message', '').strip()
        if not message and 'files' not in request.files:
            return jsonify({"success": True, "answer": GRACEFUL_FALLBACK})

        # ---- SESSION MEMORY ----
        session_memory = get_session_memory()

        # Process files
        file_texts = []
        vision_images = []
        has_pdfs = False

        if 'files' in request.files:
            files = request.files.getlist('files')
            for file in files:
                if file and file.filename and file.filename.strip():
                    filename = file.filename.lower()

                    if filename.endswith('.pdf'):
                        has_pdfs = True
                        file.seek(0)
                        text = extract_text_from_pdf(file)
                        if text:
                            file_texts.append(f"[PDF: {file.filename}]\n{text}")

                    elif filename.endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        file.seek(0)
                        image_bytes = file.read()
                        image_base64 = base64.b64encode(image_bytes).decode('utf-8')

                        if filename.endswith('.png'):
                            mime_type = 'image/png'
                        elif filename.endswith('.gif'):
                            mime_type = 'image/gif'
                        else:
                            mime_type = 'image/jpeg'

                        vision_images.append({
                            'base64': image_base64,
                            'mime_type': mime_type,
                            'filename': file.filename
                        })

        user_content_parts = []
        if message:
            user_content_parts.append(message)
        if file_texts:
            user_content_parts.append("DOCUMENT CONTENT:\n" + "\n\n".join(file_texts))

        if not user_content_parts and not vision_images:
            return jsonify({"success": True, "answer": "Please provide a message or upload files for analysis."})

        user_content = "\n\n".join(user_content_parts) if user_content_parts else "Please analyze the uploaded image(s)."

        # Log received message
        print(f"💬 Received message from {username}: {message[:50]}..." if message else "📎 File upload without text")

        system_prompt = """You are Nelavista, an advanced AI tutor created by Afeez Adewale Tella for Nigerian university students (100–400 level).

## YOUR ROLE
You are a professional, friendly university‑level tutor who makes learning enjoyable. Your answers should feel like a conversation with a brilliant, approachable lecturer.

## YOUR GOAL
Teach clearly, patiently, and in a way students love to read and keep using. Every response should be a mini‑lesson that is both informative and inviting.

## TEACHING STYLE
- **Start with a warm, encouraging opening** – e.g., "Great question!", "Let's dive into that together.", "That's an excellent topic to explore."
- **Use headings (`<h2>`, `<h3>`) to structure longer explanations** – for multi‑part answers, use headings to guide the reader. For simple or introductory responses (e.g., "What can you teach me?"), you may start directly with a warm opening paragraph **without** a heading. Avoid headings that merely repeat the user's question.
- **Use short paragraphs** – no more than 3–4 sentences each. Keep each paragraph focused on one idea.
- **Use bullet points** (`<ul>`) for lists of key points, examples, or summaries.
- **Use numbered lists** (`<ol>`) for step‑by‑step processes.
- **Emphasise important terms** with `<strong>` or `<em>`.
- **Explain each step in words** when solving problems, before or after showing the math.
- **Use simple, relatable language**, but never sacrifice accuracy. Include real‑world examples or analogies when helpful.
- **End with a short, encouraging conclusion** or a “next steps” suggestion to keep the student engaged.

## STRUCTURE (HTML)
- `<h2>` for main sections.
- `<h3>` for subsections if needed.
- `<p>` for explanatory text.
- `<ul>` / `<li>` for unordered lists.
- `<ol>` / `<li>` for ordered lists.
- Use `<strong>` for bold, `<em>` for italics.
- Present ideas in a logical order: introduction → explanation → steps (if applicable) → conclusion/summary.

## FORMAT RULES (STRICT)
- **Output pure HTML** – no Markdown syntax whatsoever.
- Do **not** wrap the whole answer in `<html>` or `<body>` tags.
- Do **not** include code blocks.
- Use only valid HTML tags as listed above.
- **Emojis are allowed occasionally** in headings to make them visually inviting (e.g., 📘 **Core Concepts**, 💡 **Tip**, ✅ **Key Takeaway**). Use at most one emoji per section; do not overdo it.
- If you include mathematics, use LaTeX:
- Inline math: `\\( ... \\)`
- Display math: `$$ ... $$`

## LATEX RULES
- Every mathematical expression must be **complete** inside a single `\\( ... \\)` or `$$ ... $$` block.
- **Never split** one formula across multiple lines or tags.
- **Never break** fractions, powers, roots, or equations into pieces.
- Do **not** mix normal text inside math expressions.
- Prefer `$$ ... $$` for important equations or multi‑step derivations.

## TONE
- Warm, supportive, and enthusiastic.
- Avoid being robotic or too formal.
- Use phrases like “Let’s break this down”, “Think of it this way”, “You’ll often see this in…”.
- Sound like a real teacher who genuinely wants the student to understand.

## EXAMPLES
**For a detailed topic (use heading):**
> <h2>📘 Understanding Cellular Respiration</h2>
> <p>That's an excellent question! Cellular respiration is how your cells turn food into energy – think of it as the cell's power plant. Let’s explore it step by step.</p>

**For a simple introductory question (no heading):**
> <p>Great question! I can help you with a wide range of university subjects – from Mathematics and Sciences to Computer Science, Social Sciences, Literature, and more. Just tell me what topic you'd like to explore, and we'll dive right in!</p>

Your final answer should be so clear and pleasant that a student would *want* to read it and come back for more."""
        messages = [{"role": "system", "content": system_prompt}]

        for mem in session_memory:
            messages.append({"role": mem["role"], "content": mem["content"]})

        openrouter_model = "openai/gpt-4o-mini"

        if vision_images:
            content_parts = [{"type": "text", "text": user_content}]
            for image_data in vision_images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image_data['mime_type']};base64,{image_data['base64']}"
                    }
                })
            messages.append({"role": "user", "content": content_parts})
            openrouter_model = "openai/gpt-4o"
        else:
            messages.append({"role": "user", "content": user_content})

        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nelavista.com",
            "X-Title": "Nelavista AI Tutor"
        }

        payload = {
            "model": openrouter_model,
            "messages": messages,
            "temperature": 0.5,
            "max_tokens": 1500
        }

        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)

        if response.status_code != 200:
            return jsonify({"success": True, "answer": GRACEFUL_FALLBACK})

        response_json = response.json()
        ai_response = response_json.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not ai_response or not ai_response.strip():
            ai_response = GRACEFUL_FALLBACK

        final_answer = ai_response

        # ===== SAVE TO DATABASE =====
        try:
            question_record = UserQuestions(
                username=username,
                question=user_content,
                answer=final_answer,
                memory_layer='chat'
            )
            db.session.add(question_record)
            db.session.commit()
            print(f"💾 Saved Q&A to database for user {username} (id: {question_record.id})")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Failed to save message to database: {e}")
            traceback.print_exc()
        # =============================

        add_to_session_memory("user", user_content)
        add_to_session_memory("assistant", final_answer)

        cleanup_old_files()

        return jsonify({"success": True, "answer": final_answer})

    except Exception as e:
        debug_print(f"❌ Unhandled error in /ask_with_files: {e}")
        traceback.print_exc()
        return jsonify({"success": True, "answer": GRACEFUL_FALLBACK})

@app.route('/ask', methods=['POST'])
@login_required
def ask():
    GRACEFUL_FALLBACK = "I'm having a little trouble answering right now, but please try again."

    try:
        data = request.get_json() or {}
        message = data.get('message', '').strip()
        if not message:
            return jsonify({'error': 'No question provided'}), 400

        username = session['user']['username']

        # Log received message
        print(f"💬 Received message from {username}: {message[:50]}...")

        session_memory = get_session_memory()

        system_prompt = """You are Nelavista, an advanced AI tutor created by Afeez Adewale Tella for Nigerian university students (100–400 level).

## YOUR ROLE
You are a professional, friendly university‑level tutor who makes learning enjoyable. Your answers should feel like a conversation with a brilliant, approachable lecturer.

## YOUR GOAL
Teach clearly, patiently, and in a way students love to read and keep using. Every response should be a mini‑lesson that is both informative and inviting.

## TEACHING STYLE
- **Start with a warm, encouraging opening** – e.g., "Great question!", "Let's dive into that together.", "That's an excellent topic to explore."
- **Break the explanation into clear sections** with descriptive headings (`<h2>`, `<h3>`). Use headings to guide the reader through the logic.
- **Use short paragraphs** – no more than 3–4 sentences each. Keep each paragraph focused on one idea.
- **Use bullet points** (`<ul>`) for lists of key points, examples, or summaries.
- **Use numbered lists** (`<ol>`) for step‑by‑step processes.
- **Emphasise important terms** with `<strong>` or `<em>`.
- **Explain each step in words** when solving problems, before or after showing the math.
- **Use simple, relatable language**, but never sacrifice accuracy. Include real‑world examples or analogies when helpful.
- **End with a short, encouraging conclusion** or a “next steps” suggestion to keep the student engaged.

## STRUCTURE (HTML)
- `<h2>` for main sections.
- `<h3>` for subsections if needed.
- `<p>` for explanatory text.
- `<ul>` / `<li>` for unordered lists.
- `<ol>` / `<li>` for ordered lists.
- Use `<strong>` for bold, `<em>` for italics.
- Present ideas in a logical order: introduction → explanation → steps (if applicable) → conclusion/summary.

## FORMAT RULES (STRICT)
- **Output pure HTML** – no Markdown syntax whatsoever.
- Do **not** wrap the whole answer in `<html>` or `<body>` tags.
- Do **not** include code blocks.
- Use only valid HTML tags as listed above.
- **Emojis are allowed occasionally** in headings to make them visually inviting (e.g., 📘 **Core Concepts**, 💡 **Tip**, ✅ **Key Takeaway**). Use at most one emoji per section; do not overdo it.
- If you include mathematics, use LaTeX:
- Inline math: `\\( ... \\)`
- Display math: `$$ ... $$`

## LATEX RULES
- Every mathematical expression must be **complete** inside a single `\\( ... \\)` or `$$ ... $$` block.
- **Never split** one formula across multiple lines or tags.
- **Never break** fractions, powers, roots, or equations into pieces.
- Do **not** mix normal text inside math expressions.
- Prefer `$$ ... $$` for important equations or multi‑step derivations.

## TONE
- Warm, supportive, and enthusiastic.
- Avoid being robotic or too formal.
- Use phrases like “Let’s break this down”, “Think of it this way”, “You’ll often see this in…”.
- Sound like a real teacher who genuinely wants the student to understand.

## EXAMPLE OPENING
> **<h2>📘 Understanding Cellular Respiration</h2>**
> <p>That's an excellent question! Cellular respiration is how your cells turn food into energy – think of it as the cell's power plant. Let’s explore it step by step.</p>

Your final answer should be so clear and pleasant that a student would *want* to read it and come back for more."""

        messages = [{"role": "system", "content": system_prompt}]

        for mem in session_memory:
            messages.append({"role": mem["role"], "content": mem["content"]})

        messages.append({"role": "user", "content": message})

        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nelavista.com",
            "X-Title": "Nelavista AI Tutor"
        }

        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": messages,
            "temperature": 0.5,
            "max_tokens": 1500
        }

        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)

        if response.status_code != 200:
            return jsonify({"success": True, "answer": GRACEFUL_FALLBACK})

        response_json = response.json()
        ai_response = response_json.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not ai_response or not ai_response.strip():
            ai_response = GRACEFUL_FALLBACK

        final_answer = ai_response

        # ===== SAVE TO DATABASE =====
        try:
            question_record = UserQuestions(
                username=username,
                question=message,
                answer=final_answer,
                memory_layer='chat'
            )
            db.session.add(question_record)
            db.session.commit()
            print(f"💾 Saved Q&A to database for user {username} (id: {question_record.id})")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Failed to save message to database: {e}")
            traceback.print_exc()
        # =============================

        add_to_session_memory("user", message)
        add_to_session_memory("assistant", final_answer)

        return jsonify({"success": True, "answer": final_answer})

    except Exception as e:
        debug_print(f"❌ Unhandled error in /ask: {e}")
        traceback.print_exc()
        return jsonify({"success": True, "answer": GRACEFUL_FALLBACK})

# ============================================
# FILE UPLOAD ENDPOINT
# ============================================
@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    """Handle file upload."""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected"}), 400

    username = session['user']['username']

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

        # Read file content once
        file_data = file.read()
        file_size = len(file_data)
        file.seek(0)

        if filename.endswith('.pdf'):
            # Generate unique file ID
            file_id = str(uuid.uuid4())

            # Store references in session
            session['last_file_id'] = file_id
            session['last_file_type'] = 'pdf'
            session['last_file_name'] = filename
            session['last_upload_time'] = time.time()

            # Extract text for preview (simplified)
            file.seek(0)
            text = extract_text_from_pdf(file)
            preview = text[:300] + "..." if text else "PDF uploaded successfully"

            debug_print(f"📄 PDF uploaded: {filename}, Size: {file_size} bytes")

            return jsonify({
                "success": True,
                "message": "PDF uploaded",
                "preview": preview,
                "type": "pdf",
                "filename": file.filename,
                "size_kb": round(file_size / 1024, 1),
                "has_text": bool(text),
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

            # Store references in session
            session['last_file_id'] = file_id
            session['last_file_type'] = 'image'
            session['last_file_name'] = image_filename
            session['last_file_path'] = image_path
            session['last_image_base64'] = image_base64
            session['last_upload_time'] = time.time()

            # Extract text via OCR for fallback
            file.seek(0)
            text = extract_text_from_image(file)

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
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Processing failed: {str(e)[:100]}"}), 500

# ============================================
# VIDEO UPLOAD AND REELS ROUTES
# ============================================
@app.route('/video-upload', methods=['GET', 'POST'])
@login_required
def upload_video():
    if request.method == 'GET':
        return render_template('upload.html', user=session.get('user'))

    try:
        creator_name = request.form.get('creator_name', '').strip()
        department = request.form.get('department', '').strip()
        course = request.form.get('course', '').strip()
        level = request.form.get('level', '').strip()
        semester = request.form.get('semester', '').strip()
        caption = request.form.get('caption', '').strip()
        bank_name = request.form.get('bank_name', '').strip()
        account_number = request.form.get('account_number', '').strip()

        # ---- Truncate level and semester to fit database columns ----
        if len(level) > 50:
            level = level[:50]
        if len(semester) > 20:
            semester = semester[:20]

        if not all([creator_name, department, course, level, semester, bank_name, account_number]):
            flash('All fields except caption are required.')
            return redirect(url_for('upload_video'))

        if not re.match(r'^\d{10}$', account_number):
            flash('Account number must be exactly 10 digits.')
            return redirect(url_for('upload_video'))

        if 'video' not in request.files:
            flash('No video file selected.')
            return redirect(url_for('upload_video'))

        file = request.files['video']
        if file.filename == '':
            flash('No video file selected.')
            return redirect(url_for('upload_video'))

        # Validate video file type
        if not allowed_video_file(file.filename):
            flash('Only video files (mp4, mov, avi, mkv, webm, flv) are allowed.')
            return redirect(url_for('upload_video'))

        # Secure the filename and generate unique name
        original_filename = secure_filename(file.filename)
        name_parts = original_filename.rsplit('.', 1)
        base = name_parts[0]
        ext = name_parts[1] if len(name_parts) > 1 else ''
        unique_name = f"{base}_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"
        video_path = os.path.join(VIDEO_UPLOAD_FOLDER, unique_name)

        # Save file
        file.save(video_path)

        # Build public URL
        video_url = url_for('static', filename=f'uploads/{unique_name}')

        video = Video(
            creator_name=creator_name,
            department=department,
            course=course,
            level=level,
            semester=semester,
            caption=caption,
            video_url=video_url,
            bank_name=bank_name,
            account_number=account_number,
            username=session['user']['username'],
            is_approved=True
        )
        db.session.add(video)
        db.session.commit()

        debug_print(f"✅ Video saved: {video_path} | URL: {video_url}")
        flash('Video uploaded successfully!')
        return redirect(url_for('videos_page'))

    except Exception as e:
        debug_print(f"❌ Video upload error: {e}")
        traceback.print_exc()
        flash(f'Upload failed: {str(e)}')
        return redirect(url_for('upload_video'))

@app.route('/videos')
@login_required
def videos_page():
    return render_template('video.html', user=session.get('user'))

import os
import requests
from flask import jsonify, request

YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY')  # Set this in your environment

@app.route('/api/youtube/search', methods=['GET'])
def youtube_search():
    """
    Search YouTube for educational videos.
    Query param: q (search string)
    Returns up to 3 video results.
    """
    if not YOUTUBE_API_KEY:
        return jsonify({'error': 'YouTube API key not configured'}), 500

    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': 'Missing search query'}), 400

    try:
        url = 'https://www.googleapis.com/youtube/v3/search'
        params = {
            'part': 'snippet',
            'maxResults': 3,
            'q': query,
            'type': 'video',
            'key': YOUTUBE_API_KEY,
            'videoCategoryId': '27'  # Education category (optional but relevant)
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        items = []
        for item in data.get('items', []):
            items.append({
                'videoId': item['id']['videoId'],
                'title': item['snippet']['title'],
                'channelTitle': item['snippet']['channelTitle']
            })

        return jsonify({'items': items})

    except requests.exceptions.RequestException as e:
        app.logger.error(f"YouTube search failed: {e}")
        return jsonify({'error': 'External API error'}), 500
    except Exception as e:
        app.logger.error(f"Unexpected error: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/videos')
@login_required
def api_get_videos():
    course = request.args.get('course')
    level = request.args.get('level')
    semester = request.args.get('semester')

    query = Video.query.filter_by(is_approved=True).order_by(Video.created_at.desc())

    if course:
        query = query.filter(Video.course == course)
    if level:
        query = query.filter(Video.level == level)
    if semester:
        query = query.filter(Video.semester == semester)

    videos = query.all()
    return jsonify([v.to_dict() for v in videos])

@app.route('/api/videos/<int:video_id>', methods=['DELETE'])
@login_required
def delete_video(video_id):
    if session['user']['username'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    video = Video.query.get_or_404(video_id)
    db.session.delete(video)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/courses')
@login_required
def api_get_courses():
    courses = db.session.query(Video.course).filter_by(is_approved=True).distinct().all()
    course_list = [c[0] for c in courses if c[0]]
    return jsonify(course_list)

@app.route('/api/videos/<int:video_id>/view', methods=['POST'])
@login_required
def api_increment_view(video_id):
    video = Video.query.get_or_404(video_id)
    video.views += 1
    db.session.commit()
    return jsonify({'success': True, 'views': video.views})

@app.route('/api/videos/<int:video_id>/like', methods=['POST'])
@login_required
def api_increment_like(video_id):
    video = Video.query.get_or_404(video_id)
    video.likes += 1
    db.session.commit()
    return jsonify({'success': True, 'likes': video.likes})

# Optional admin approval route
@app.route('/admin/videos')
@login_required
def admin_videos():
    if session['user']['username'] != 'admin':
        flash('Access denied')
        return redirect(url_for('dashboard'))
    videos = Video.query.filter_by(is_approved=False).order_by(Video.created_at.desc()).all()
    return render_template('admin_videos.html', videos=videos)

@app.route('/admin/videos/<int:video_id>/approve', methods=['POST'])
@login_required
def admin_approve_video(video_id):
    if session['user']['username'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    video = Video.query.get_or_404(video_id)
    video.is_approved = True
    db.session.commit()
    return jsonify({'success': True})

# ============================================
# OTHER ROUTES
# ============================================
@app.route('/about')
def about():
    """Render about page."""
    return render_template('about.html')

@app.route('/images/<path:filename>')
def serve_image(filename):
    return send_from_directory(os.path.join(app.root_path, 'templates', 'images'), filename)

@app.route('/campus-map')
def campus_map():
    """Render LASU campus map page."""
    return render_template('campus-map.html')

@app.route('/privacy-policy')
def privacy_policy():
    """Render privacy policy page."""
    return render_template('privacy-policy.html')

@app.route('/settings')
@login_required
def settings():
    """Render user settings page."""
    memory = {
        "traits": session.get('traits', []),
        "more_info": session.get('more_info', ''),
        "enable_memory": session.get('enable_memory', False)
    }
    return render_template('settings.html', memory=memory, theme=session.get('theme'), language=session.get('language'))

@app.route('/memory', methods=['POST'])
@login_required
def save_memory():
    """Save user memory settings."""
    session['theme'] = request.form.get('theme')
    session['language'] = request.form.get('language')
    session['notifications'] = 'notifications' in request.form
    flash('Settings saved!')
    return redirect('/settings')

@app.route('/materials')
@login_required
def materials():
    """Render study materials page."""
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
    """API endpoint to fetch study materials."""
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
    """API endpoint for AI-generated study materials."""
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
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nelavista.com",
            "X-Title": "Nelavista AI Tutor"
        }

        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You are an educational AI assistant."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 500
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 200:
            explanation = response.json()["choices"][0]["message"]["content"]
        else:
            explanation = f"Let me help you learn {topic}. Start with the basic concepts and build from there. 📚 Here are materials to study further:"
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

@app.route("/mat101")
def math101():
    return render_template("mat101.html")

@app.route('/reels', methods=['GET'])
@login_required
def reels():
    """Render educational reels/videos page."""
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
    """API endpoint to fetch educational reels."""
    course = request.args.get("course")

    all_reels = [
        # ===== ACCOUNTANCY =====
        {"course": "Accountancy", "caption": "Introduction to Accounting", "video_url": "https://youtu.be/Gua2Bo_G-J0?si=FNnNZBbmBh0yqvrk"},
        {"course": "Accountancy", "caption": "Financial Statements Basics", "video_url": "https://youtu.be/fb7YCVR5fIU?si=XWozkxGoBV2HP2HW"},
        {"course": "Accountancy", "caption": "Management Accounting Overview", "video_url": "https://youtu.be/qISkyoiGHcI?si=BKRnkFfl-fqKXgLG"},
        {"course": "Accountancy", "caption": "Auditing Principles", "video_url": "https://youtu.be/27gabbJQZqc?si=rsOLmkD2QXOoxSoi"},
        {"course": "Accountancy", "caption": "Taxation Fundamentals", "video_url": "https://youtu.be/Cox8rLXYAGQ?si=CvKUaPuPJOxPb6cr"},
        {"course": "Accountancy", "caption": "Learn Accounting in under 5 hours", "video_url": "https://youtu.be/gPBhGkBN30s?si=bUYfaccZPlBni3aZ"},
        {"course": "Accountancy", "caption": "The ACCOUNTING BASICS for BEGINNERS (3 core parts)", "video_url": "https://youtu.be/Gua2Bo_G-J0?si=aM8ZPO-OqtV-ZbJ9"},

        # ===== ACCOUNTING (if separate) =====
        {"course": "Accounting", "caption": "Basics of Double Entry", "video_url": "https://youtu.be/cjO8qHM5Wjg?si=P0hcqm9x-wjmXpN3"},
        {"course": "Accounting", "caption": "Trail Balance Explained", "video_url": "https://youtu.be/3_PfoTzSCQE?si=SGRI7KVJ6ZC3iJe7"},
        {"course": "Accounting", "caption": "Financial Analysis Techniques", "video_url": "https://youtu.be/g2wEFJ7upNs?si=ht44vAply2f7b-P0"},
        {"course": "Accounting", "caption": "Cost Accounting Overview", "video_url": "https://youtu.be/a5D3Iopi0-4?si=vXOVFcV1NqPGt6Tk"},
        {"course": "Accounting", "caption": "Budgeting and Forecasting", "video_url": "https://youtu.be/GjxhDo9luh8?si=BXn4Z5J-RdKJdBoP"},

        # ===== AGRICULTURE =====
        {"course": "Agriculture", "caption": "Introduction to Agriculture", "video_url": "https://youtu.be/1FLcijYWHZQ?si=B6iWcOVNXYCDWsKR"},
        {"course": "Agriculture", "caption": "Crop Production Techniques", "video_url": "https://youtu.be/j4-0rNhxoKs?si=XaUcN8zOq1EtkVbX"},
        {"course": "Agriculture", "caption": "Soil Fertility Management", "video_url": "https://youtu.be/TjbxOEEOCh0?si=grkiA5OewbgtFF78"},
        {"course": "Agriculture", "caption": "Livestock Management", "video_url": "https://youtu.be/TjbxOEEOCh0?si=Jr_UpYvei_oieZxz"},
        {"course": "Agriculture", "caption": "Agricultural Economics", "video_url": "https://youtu.be/fbOiwV3gBLg?si=f8HcQW1xdOEfQXEy"},

        # ===== ARABIC STUDIES =====
        {"course": "Arabic Studies", "caption": "Arabic Language Basics", "video_url": "https://youtu.be/X1mC1XY65Kc?si=gIIUVXBrseXau1Tj"},
        {"course": "Arabic Studies", "caption": "Arabic Grammar Essentials", "video_url": "https://youtu.be/CKD1O4tKZUA?si=JwH8Hb090aZTAI7n"},
        {"course": "Arabic Studies", "caption": "Conversational Arabic", "video_url": "https://youtu.be/dinQIb4ZFXY?si=eGF1Vhsdwm8imJ3Y"},
        {"course": "Arabic Studies", "caption": "Arabic Poetry Introduction", "video_url": "https://youtu.be/ZmjK5cu81RA?si=XnRGefNNXTCws278"},
        {"course": "Arabic Studies", "caption": "Arabic Writing Skills", "video_url": "https://youtu.be/b_WdZCrKr3k?si=pYe0F4bLx8FiT8HT"},

        # ===== BANKING AND FINANCE =====
        {"course": "Banking and Finance", "caption": "Banking Systems Overview", "video_url": "https://youtu.be/fTTGALaRZoc?si=ThB2kkYTd_iIhFX1"},
        {"course": "Banking and Finance", "caption": "Financial Markets Basics", "video_url": "https://youtu.be/UOwi7MBSfhk?si=XSyvxPRp4mEQx2OH"},
        {"course": "Banking and Finance", "caption": "Loan and Credit Management", "video_url": "https://youtu.be/f3VgVOgAUoE?si=JwfSWSogIZeIMpY8"},
        {"course": "Banking and Finance", "caption": "Investment Banking Intro", "video_url": "https://youtu.be/-PkN15TtFnc?si=xgcAoZBAdge-PBjb"},
        {"course": "Banking and Finance", "caption": "Risk Management in Banking", "video_url": "https://youtu.be/BLAEuVSAlVM?si=hubXYQaexc2Iizjd"},

        # ===== BIOCHEMISTRY =====
        {"course": "Biochemistry", "caption": "Introduction to Biochemistry", "video_url": "https://youtu.be/CHJsaq2lNjU?si=owCTFJffO4MyBtPB"},
        {"course": "Biochemistry", "caption": "Enzymes and their Functions", "video_url": "https://youtu.be/ozdO1mLXBQE?si=Xj6z5vY8rAgRMdA_"},
        {"course": "Biochemistry", "caption": "Metabolism Basics", "video_url": "https://youtu.be/onDQ9KgDSVw?si=4IKHj5VVJoahw51B"},
        {"course": "Biochemistry", "caption": "Protein Synthesis", "video_url": "https://youtu.be/8wAwLwJAGHs?si=vDuhcZbjQ0nNyhoL"},
        {"course": "Biochemistry", "caption": "Biochemical Techniques", "video_url": "https://youtu.be/lDWL_EEhReo?si=F4nhulNv3l0nxRcA"},

        # ===== BOTANY =====
        {"course": "Botany", "caption": "Plant Classification", "video_url": "https://youtu.be/SAM5mcHkSxU?si=P1cX_dbg0pGJFDBB"},
        {"course": "Botany", "caption": "Photosynthesis Process", "video_url": "https://youtu.be/-ZRsLhaukn8?si=CfNMcb-tVWwq6-be"},
        {"course": "Botany", "caption": "Plant Anatomy", "video_url": "https://youtu.be/pvVvCt6Kdp8?si=3ubgF8WibXgzFLcI"},
        {"course": "Botany", "caption": "Plant Reproduction", "video_url": "https://youtu.be/h077JEQ8w6g?si=O5vHjcnuPetMLbCo"},
        {"course": "Botany", "caption": "Ecology and Environment", "video_url": "https://youtu.be/fxVGiq1kggg?si=ESD-BALtjmX0Qxcb"},

        # For Zoology (example from your list):
        {"course": "Zoology", "caption": "Animal Classification", "video_url": "https://example.com/videos/zoology1.mp4"},
    ]

    if course:
        matching = [r for r in all_reels if r["course"].lower() == course.lower()]
        return jsonify({"reels": matching})
    return jsonify({"reels": all_reels})

@app.route('/CBT', methods=['GET'])
@login_required
def CBT():
    """Render Computer-Based Test page."""
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
    """Render AI teaching interface."""
    return render_template('teach-me-ai.html')

@app.route('/api/ai-teach')
def ai_teach():
    """API endpoint for AI teaching."""
    course = request.args.get("course")
    level = request.args.get("level")

    if not course or not level:
        return jsonify({"error": "Missing course or level"}), 400

    prompt = f"You're a tutor. Teach a {level} student the basics of {course} in a friendly and easy-to-understand way."

    try:
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nelavista.com",
            "X-Title": "Nelavista AI Tutor"
        }

        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You are an educational AI assistant."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 800
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code == 200:
            summary = response.json()["choices"][0]["message"]["content"]
        else:
            summary = f"Let me teach you the basics of {course}. We'll start with fundamental concepts and build up from there. This is perfect for {level} students!"

        return jsonify({"summary": summary})
    except Exception as e:
        return jsonify({"error": str(e)})

# ============================================
# Live Meeting Routes
# ============================================
@app.route('/teacher')
def teacher_create():
    """Create a new teacher room."""
    room_id = str(uuid.uuid4())[:8]
    return redirect(f'/teacher/{room_id}')

@app.route('/teacher/<room_id>')
def teacher_view(room_id):
    """Render teacher view for a specific room."""
    return render_template('teacher.html', room_id=room_id)

@app.route('/student/<room_id>')
def student_view(room_id):
    """Render student view for a specific room."""
    return render_template('student.html', room_id=room_id)

@app.route('/join', methods=['POST'])
def join_room_post():
    """Handle room joining via POST request."""
    room_id = request.form.get('room_id', '').strip()
    if not room_id:
        flash('Please enter a room ID')
        return redirect('/')
    return redirect(f'/student/{room_id}')

@app.route('/live-meeting')
@app.route('/live_meeting')
def live_meeting():
    """Render live meeting landing page."""
    return render_template('live_meeting.html')

@app.route('/live-meeting/teacher')
@app.route('/live_meeting/teacher')
def live_meeting_teacher_create():
    """Create a new live meeting teacher room."""
    room_id = str(uuid.uuid4())[:8]
    return redirect(url_for('live_meeting_teacher_view', room_id=room_id))

@app.route('/live-meeting/teacher/<room_id>')
@app.route('/live_meeting/teacher/<room_id>')
def live_meeting_teacher_view(room_id):
    """Render live meeting teacher view."""
    return render_template('teacher_live.html', room_id=room_id)

@app.route('/live-meeting/student/<room_id>')
@app.route('/live_meeting/student/<room_id>')
def live_meeting_student_view(room_id):
    """Render live meeting student view."""
    return render_template('student_live.html', room_id=room_id)

@app.route('/live-meeting/join', methods=['POST'])
@app.route('/live_meeting/join', methods=['POST'])
def live_meeting_join():
    """Handle live meeting joining."""
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
# Connection Test Route
# ============================================
@app.route('/test-connection')
def test_connection():
    """Simple connection test page."""
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
    """Debug endpoint to view current room states."""
    debug_info = {
        'rooms': rooms,
        'participants': participants,
        'room_authority': room_authority,
        'total_rooms': len(rooms),
        'total_participants': len(participants)
    }
    return json.dumps(debug_info, indent=2, default=str)

# ============================================
# Cleanup Routes
# ============================================
@app.route('/cleanup_attachments', methods=['POST'])
@login_required
def cleanup_attachments():
    """Clean up uploaded files from session."""
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

@app.route('/clear_context', methods=['POST'])
@login_required
def clear_context():
    """Clear uploaded file context from session and delete the file from disk."""
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

# ============================================
# Health Check
# ============================================
@app.route('/health')
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "Nellavista + Tellavista Integrated Platform",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "3.0",
        "features": [
            "User Authentication & Management",
            "Turbo AI-Style PDF Analyzer",
            "Live Meeting System",
            "AI Tutor (Nelavista)",
            "Study Materials Library",
            "Educational Reels",
            "CBT Test System"
        ]
    })

# ============================================
# STARTUP
# ============================================
cleanup_stale_files()

# Initialize database
init_database()
create_default_user()

if __name__ == '__main__':
    print(f"\n{'='*70}")
    print("🚀 NELLAVISTA + TELLAVISTA INTEGRATED PLATFORM")
    print(f"{'='*70}")
    print("🎯 COMPLETE EDUCATIONAL PLATFORM FEATURES:")
    print("   1. 👤 User Authentication & Management")
    print("   2. 🧠 Turbo AI-Style PDF Analyzer")
    print("      • Textbook-quality lecture notes")
    print("      • Comprehensive comparison tables")
    print("      • Step-by-step process explanations")
    print("      • Exam-focused study materials")
    print("   3. 🎥 Live Meeting System")
    print("      • Full Mesh WebRTC Video Calls")
    print("      • Teacher Authority System")
    print("      • Real-time Collaboration")
    print("   4. 🤖 AI Tutor (Nelavista)")
    print("      • File upload support (PDFs, Images)")
    print("      • Vision AI for image analysis")
    print("      • Context-aware responses")
    print("      • **TEMPORARY SESSION MEMORY**: Last 5 messages only – no permanent storage")
    print("      • **LECTURER STYLE**: Enforced with explicit anti‑generic‑tone rules")
    print("      • **HTML‑ONLY OUTPUT**: No Markdown, pure HTML with LaTeX preservation")
    print("      • **NEW: Persistent Database Storage**: Messages are now saved to `user_questions` table")
    print("   5. 📚 Study Materials Library")
    print("   6. 🎬 Educational Reels")
    print("   7. 📝 CBT Test System")
    print("   8. 🎥 Video Upload & Sharing (Local Storage)")
    print(f"{'='*70}")
    print("\n📡 Access at: http://localhost:5000")
    print("👨‍🏫 Teacher test: http://localhost:5000/live_meeting/teacher")
    print("📊 Turbo Analyzer: http://localhost:5000/analyze")
    print("🤖 AI Tutor: http://localhost:5000/talk-to-nelavista")
    print("🎬 Videos: http://localhost:5000/videos")
    print("📤 Upload Video: http://localhost:5000/video-upload")
    print(f"{'='*70}")
    print("\n⚠️  IMPORTANT: Install required packages:")
    print("   pip install PyPDF2 pdfplumber pymupdf Pillow pytesseract")
    print("   pip install flask-socketio flask-session flask-sqlalchemy")
    print("   pip install python-dotenv requests beautifulsoup4")
    print("   pip install cloudinary")
    print(f"{'='*70}\n")

    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=DEBUG_MODE)
