import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")
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
import traceback

# MEMORY SYSTEM REMOVED: All memory imports commented out
# from memory.pdf_handler import PDFMemory
# from memory.chat_context import ChatContext
# from memory.memory_router import MemoryRouter
# from memory.layers import MemoryLayer as MemoryLayers

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
# MEMORY SYSTEM NEUTRALIZED: PDF Memory removed
# ============================================

# Create GLOBAL PDF memory instance - REPLACED WITH DUMMY
# pdf_memory = PDFMemory(PDF_MEMORY_DIR)

class DummyPDFMemory:
    """Dummy class to replace PDFMemory functionality"""
    def extract_and_store(self, *args, **kwargs):
        return True
    
    def clear_user_cache(self, *args, **kwargs):
        return True
    
    def clear_file_cache(self, *args, **kwargs):
        return True
    
    def get_cache_stats(self):
        return {"files": 0, "chunks": 0, "users": 0}
    
    def health_check(self):
        return True

# Initialize dummy memory
pdf_memory = DummyPDFMemory()

# Configure upload folder
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)  # Create folder if it doesn't exist
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    """Check whether the file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

def cleanup_stale_files():
    """Remove files older than 24 hours on startup."""
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
    """Print debug information when DEBUG_MODE is True."""
    if DEBUG_MODE:
        print(*args, **kwargs)

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

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
    """Model for storing user questions and AI responses."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    memory_layer = db.Column(db.String(50))  # Store which memory layer was used

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
            import psycopg2
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
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
    """Initialize database with error handling."""
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

def extract_text_from_pdf(file):
    """Extract text from PDF file with smart limitations."""
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
    """Extract text from image using OCR with smart detection."""
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
    """Detect if content is primarily visual/diagram."""
    if not text_content:
        return True
    if text_content == "DIAGRAM_OR_VISUAL_CONTENT":
        return True
    if len(text_content.strip()) < 50:
        return True
    return False

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
# MEMORY SYSTEM HELPER FUNCTIONS NEUTRALIZED
# ============================================

def validate_chat_history(history_data):
    """
    Validate and clean chat history from frontend.
    Returns cleaned history with only user and assistant roles.
    """
    if not history_data:
        return []

    try:
        if isinstance(history_data, str):
            history = json.loads(history_data)
        else:
            history = history_data

        cleaned_history = []
        for msg in history:
            if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                role = msg['role'].lower()
                content = str(msg['content']).strip()

                if role in ['user', 'assistant'] and content:
                    cleaned_history.append({
                        'role': role,
                        'content': content
                    })

        return cleaned_history
    except Exception as e:
        debug_print(f"❌ Error validating chat history: {e}")
        return []

def build_prompt_with_context(context: str, memory_layer: str) -> str:
    """
    Build Nelavista system prompt WITHOUT memory system dependencies.
    Simplified for deployment stability.
    """
    base_prompt = (
        "You are Nelavista, an advanced AI tutor created by Afeez Adewale Tella "
        "for Nigerian university students (100–400 level).\n\n"

        "ROLE:\n"
        "You are a specialized analytical tutor and researcher.\n\n"

        "STYLE: Analytical Expert\n"
        "Follow these rules strictly.\n\n"

        "GLOBAL RULES:\n"
        "- Always respond in clean HTML using <p>, <ul>, <li>, <strong>, "
        "<h2>, <h3>, and <table> when appropriate.\n"
        "- Respond naturally to greetings when the user greets you, but do not add greetings unnecessarily.\n"
        "- Do not use labels like Step 1, Step 2, Intro, or Final Answer.\n"
        "- No emojis in academic explanations.\n"
        "- Use clear, calm, and friendly academic language.\n"
        "- Explain ideas patiently, like a lecturer guiding students through the topic.\n"
        "- Avoid hype, exaggeration, or unnecessary filler.\n\n"

        "STRUCTURE REQUIREMENTS:\n"
        "- Begin with a brief <strong>Key Idea</strong> statement introducing the core conclusion.\n"
        "- Use clear headers and bullet points to organize information.\n"
        "- Break complex ideas into logical components and explain the reasoning behind them.\n"
        "- Use short paragraphs (1–2 sentences).\n\n"

        "REASONING:\n"
        "- Show the reasoning path clearly but naturally.\n"
        "- Explain why conclusions follow from facts or data.\n\n"

        "DATA & ACCURACY:\n"
        "- Include formulas, definitions, metrics, or comparisons when relevant.\n"
        "- Do not speculate or invent facts.\n\n"

        "GENERAL INSTRUCTIONS:\n"
        "- Provide accurate, structured academic explanations.\n"
        "- Use relevant examples only when they add clarity.\n"
        "- Maintain a logical flow from premises to conclusions.\n\n"
        "ENDING:\n"
        "- End naturally after the explanation. Do not add summaries beyond the TL;DR."
    )

    # MEMORY SYSTEM REMOVED: No special context handling, just return base prompt
    return base_prompt

def optimize_history_fetch(question: str, has_pdf_context: bool) -> bool:
    """
    Determine whether to fetch history based on question type and available context.
    Returns True if history should be fetched.
    """
    question_lower = question.lower().strip()
    
    # Always fetch for follow-up questions
    follow_up_phrases = [
        "explain that", "tell me more", "go on", "continue",
        "what about", "how about", "and then", "next",
        "clarify", "elaborate", "expand", "detail"
    ]
    
    if any(phrase in question_lower for phrase in follow_up_phrases):
        return True
    
    # Don't fetch for greetings
    greeting_phrases = ["hi", "hello", "hey", "how are you", "good morning", "good evening"]
    if any(phrase in question_lower for phrase in greeting_phrases):
        return False
    
    # If PDF context is available, only fetch history for complex or multi-part questions
    if has_pdf_context:
        words = question_lower.split()
        return len(words) >= 8  # Longer questions might need additional context
    
    # No PDF context - use history more liberally
    return True

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
# SINGLE ENTRY POINT: /ask_with_files
# ============================================
@app.route('/ask_with_files', methods=['POST'])
@login_required
def ask_with_files():
    """
    SINGLE ENTRY POINT for AI requests.
    ALWAYS returns JSON with non-empty "answer" field.
    Follows strict fallback requirements.
    MEMORY SYSTEM COMPLETELY REMOVED - Simplified for deployment.
    """
    # Fallback message to use in case of any failure
    GRACEFUL_FALLBACK = "I'm having a little trouble answering right now, but please try again."
    
    try:
        # Get user info
        username = session['user']['username']
        
        # Get message (always present)
        message = request.form.get('message', '').strip()
        if not message:
            return jsonify({
                "success": True,
                "answer": GRACEFUL_FALLBACK
            })
        
        # Get and validate chat history
        history_json = request.form.get('history', '[]')
        chat_history = validate_chat_history(history_json)
        
        # Process files (0 or more)
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
                        # Extract text from PDF
                        file.seek(0)
                        text = extract_text_from_pdf(file)
                        if text:
                            file_texts.append(f"[PDF: {file.filename}]\n{text}")
                    
                    elif filename.endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        # Prepare for vision model
                        file.seek(0)
                        image_bytes = file.read()
                        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                        
                        # Determine MIME type
                        if filename.endswith('.png'):
                            mime_type = 'image/png'
                        elif filename.endswith('.jpg') or filename.endswith('.jpeg'):
                            mime_type = 'image/jpeg'
                        elif filename.endswith('.gif'):
                            mime_type = 'image/gif'
                        else:
                            mime_type = 'image/jpeg'
                        
                        vision_images.append({
                            'base64': image_base64,
                            'mime_type': mime_type,
                            'filename': file.filename
                        })
        
        # STRICT REQUIREMENT: Build user content with clear context separation
        user_content_parts = []
        
        # Add message text if present
        if message:
            user_content_parts.append(message)
        
        # Add file texts if present
        if file_texts:
            file_context = "\n\n".join(file_texts)
            user_content_parts.append(f"DOCUMENT CONTENT:\n{file_context}")
        
        # Create final user content
        if not user_content_parts and not vision_images:
            return jsonify({
                "success": True,
                "answer": "Please provide a message or upload files for analysis."
            })
        
        user_content = "\n\n".join(user_content_parts) if user_content_parts else "Please analyze the uploaded image(s)."
        
        # MEMORY SYSTEM REMOVED: No PDF context routing, just basic text extraction
        pdf_context = ""
        if has_pdfs and file_texts:
            pdf_context = "\n\n".join(file_texts)
        
        # Build system prompt WITHOUT memory layer dependencies
        system_prompt = build_prompt_with_context(pdf_context, "GENERAL")
        
        # Prepare messages array
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add chat history (validated)
        for msg in chat_history:
            messages.append({"role": msg['role'], "content": msg['content']})
        
        # STRICT REQUIREMENT: Handle different input types
        openrouter_model = "openai/gpt-4o-mini"
        
        if vision_images:
            # Use vision model
            content_parts = [{"type": "text", "text": user_content}]
            
            for image_data in vision_images:
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
            openrouter_model = "openai/gpt-4o"
        else:
            # Use text-only model
            messages.append({"role": "user", "content": user_content})
        
        # STRICT REQUIREMENT: Call OpenRouter with timeout and error handling
        try:
            headers = {
                "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://nelavista.com",
                "X-Title": "Nelavista AI Tutor"
            }
            
            payload = {
                "model": openrouter_model,
                "messages": messages,
                "temperature": 0.6,
                "max_tokens": 1500
            }
            
            # Make API call with timeout
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30  # 30 second timeout
            )
            
            if response.status_code != 200:
                # OpenRouter failed, return fallback
                return jsonify({
                    "success": True,
                    "answer": GRACEFUL_FALLBACK
                })
            
            # Extract response
            response_json = response.json()
            ai_response = response_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # STRICT REQUIREMENT: Ensure response is non-empty
            if not ai_response or not ai_response.strip():
                ai_response = GRACEFUL_FALLBACK
            
        except requests.exceptions.Timeout:
            # Timeout occurred
            return jsonify({
                "success": True,
                "answer": GRACEFUL_FALLBACK
            })
        except Exception as e:
            # Any other API error
            debug_print(f"❌ OpenRouter API error: {e}")
            return jsonify({
                "success": True,
                "answer": GRACEFUL_FALLBACK
            })
        
        # Save to database (optional, don't fail if this doesn't work)
        try:
            with app.app_context():
                new_q = UserQuestions(
                    username=username,
                    question=message[:500] if message else "[File analysis]",
                    answer=ai_response[:1000],
                    memory_layer="GENERAL"  # MEMORY SYSTEM REMOVED: Hardcoded value
                )
                db.session.add(new_q)
                db.session.commit()
        except Exception as db_error:
            debug_print(f"⚠️ Database save error: {db_error}")
            # Don't fail the response
        
        # Clean up old files
        cleanup_old_files()
        
        # STRICT REQUIREMENT: Return in MANDATORY format
        return jsonify({
            "success": True,
            "answer": ai_response
        })
        
    except Exception as e:
        # STRICT REQUIREMENT: Catch ALL exceptions and return fallback
        debug_print(f"❌ Unhandled error in /ask_with_files: {e}")
        traceback.print_exc()
        
        return jsonify({
            "success": True,
            "answer": GRACEFUL_FALLBACK
        })

# ============================================
# Flask Routes - Educational Platform
# ============================================
@app.route('/')
def index():
    """Render main index page."""
    user = session.get('user')
    if not user:
        return redirect(url_for('login'))
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
            return redirect(url_for('index'))
            
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
                        'last_login': user.last_login.strftime('%Y-%m-d %H:%M:%S')
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
    """Handle user logout and clear session."""
    # MEMORY SYSTEM REMOVED: No PDF memory cleanup needed
    session.clear()
    flash('You have been logged out.')
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile():
    """Render user profile page."""
    user = session.get('user', {})
    return render_template('profile.html', user=user)

@app.route('/talk-to-nelavista')
@login_required
def talk_to_nelavista():
    """Render AI chat interface."""
    return render_template('talk-to-nelavista.html')

# ============================================
# File Upload Endpoint (for standalone uploads)
# ============================================
@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    """Handle file upload WITHOUT memory system integration."""
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
            
            # MEMORY SYSTEM REMOVED: No PDF memory storage
            
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
# Legacy /ask endpoint (redirects to single entry point)
# ============================================
@app.route('/ask', methods=['POST'])
@login_required
def ask():
    """Handle AI requests without uploaded files."""
    GRACEFUL_FALLBACK = "I'm having a little trouble answering right now, but please try again."
    
    try:
        # Get data from request
        data = request.get_json() or {}
        message = data.get('message', '').strip()
        
        if not message:
            return jsonify({'error': 'No question provided'}), 400
        
        # Get user info for database saving
        username = session['user']['username']
        
        # Build system prompt (same as ask_with_files but without PDF context)
        system_prompt = build_prompt_with_context("", "GENERAL")
        
        # Prepare messages array
        messages = [{"role": "system", "content": system_prompt}]
        messages.append({"role": "user", "content": message})
        
        # Call OpenRouter API
        headers = {
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nelavista.com",
            "X-Title": "Nelavista AI Tutor"
        }
        
        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": messages,
            "temperature": 0.6,
            "max_tokens": 1500
        }
        
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code != 200:
            debug_print(f"❌ OpenRouter API error: {response.status_code} - {response.text}")
            return jsonify({
                "success": True,
                "answer": GRACEFUL_FALLBACK
            })
        
        # Extract response
        response_json = response.json()
        ai_response = response_json.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # 🔹 CLEANUP: remove HTML tags and normalize spacing
        if ai_response:
            import re
            ai_response = re.sub(r'<[^>]+>', '', ai_response)
            ai_response = ai_response.replace('\n', ' ').strip()
        
        if not ai_response:
            ai_response = GRACEFUL_FALLBACK
        
        # Save to database (optional)
        try:
            with app.app_context():
                new_q = UserQuestions(
                    username=username,
                    question=message[:500],
                    answer=ai_response[:1000],
                    memory_layer="GENERAL"
                )
                db.session.add(new_q)
                db.session.commit()
        except Exception as db_error:
            debug_print(f"⚠️ Database save error: {db_error}")
        
        # Return response in the format frontend expects
        return jsonify({
            "success": True,
            "answer": ai_response
        })
        
    except Exception as e:
        debug_print(f"❌ Unhandled error in /ask: {e}")
        traceback.print_exc()
        return jsonify({
            "success": True,
            "answer": GRACEFUL_FALLBACK
        })

# ============================================
# MEMORY MANAGEMENT ENDPOINTS NEUTRALIZED
# ============================================
@app.route('/memory/stats', methods=['GET'])
@login_required
def get_memory_stats():
    """Get memory system statistics - DUMMY VERSION."""
    try:
        username = session['user']['username']
        
        return jsonify({
            "success": True,
            "user_stats": {
                "username": username,
                "file_count": 0,
                "files": []
            },
            "system_stats": {"files": 0, "chunks": 0, "users": 0},
            "health": "Memory system disabled for deployment",
            "memory_layers": ["GENERAL"]
        })
    except Exception as e:
        debug_print(f"❌ Error getting memory stats: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/memory/clear', methods=['POST'])
@login_required
def clear_memory():
    """Clear user's memory cache - DUMMY VERSION."""
    try:
        username = session['user']['username']
        
        # Clear session references
        session.pop('last_file_id', None)
        session.pop('last_file_type', None)
        session.pop('last_upload_time', None)
        session.pop('last_image_base64', None)
        
        return jsonify({
            "success": True,
            "message": "Memory system disabled",
            "cache_stats": {"files": 0, "chunks": 0, "users": 0}
        })
    except Exception as e:
        debug_print(f"❌ Error clearing memory: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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
# Other Routes
# ============================================
@app.route('/about')
def about():
    """Render about page."""
    return render_template('about.html')

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

@app.route('/telavista/memory', methods=['POST'])
@login_required
def telavista_save_memory():
    """Save Telavista memory settings."""
    print("Saving Telavista memory!")
    flash('Memory saved!')
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
    {"course": "Accountancy", "caption": "Introduction to Accounting", "video_url": "https://youtu.be/Gua2Bo_G-J0?si=lDEAsxKN8lh8gf5Y"},
    {"course": "Accountancy", "caption": "Financial Statements Basics", "video_url": "https://youtu.be/eorpdJUWfTA?si=SoT4Zin87uu1I9hU"},
    {"course": "Accountancy", "caption": "Management Accounting Overview", "video_url": "https://youtu.be/MTXDi0nDeI0?si=DAHvD6mtvXm8MnoI"},
    {"course": "Accountancy", "caption": "Auditing Principles", "video_url": "https://youtu.be/27gabbJQZqc?si=NGxkpo8_ruskoHSG"},
    {"course": "Accountancy", "caption": "Taxation Fundamentals", "video_url": "https://youtu.be/Cox8rLXYAGQ?si=uZ7IwRKgKGN2Tc45"},
    {"course": "Zoology", "caption": "Animal Classification", "video_url": "https://youtu.be/L6anmd7DnYw?si=i91pAwjsCSjfRJhO"},
    {"course": "Zoology", "caption": "Introduction to Animal Behavior", "video_url": "https://youtu.be/CqJkz9SfGk4?si=_0jyvcj8JZG4BAFu"},
    {"course": "Zoology", "caption": "Vertebrate Diversity and Evolution", "video_url": "https://youtu.be/DkHYDp-iCO0?si=H4B9v7oxNjoWGTHc"},
    {"course": "Zoology", "caption": "Marine Biology and Ocean Life", "video_url": "https://youtu.be/2YquYkNcF7I?si=kmwTZETjscJ4sAHp"},
    {"course": "Zoology", "caption": "The Science of Entomology (Insects)", "video_url": "https://youtu.be/cgiTOpdck9w?si=xDyLEhUlT3yuZuU0"},
    {"course": "Aeronautic & Astronautic Engineering", "caption": "How do Airplanes Fly?", "video_url": "https://youtu.be/Gg0TXNXgz-w?si=j_xaLB0aFzKvN8Vb"},
    {"course": "Aeronautic & Astronautic Engineering", "caption": "Introduction to Aerospace Engineering", "video_url": "https://youtu.be/v7jbCROl7o8?si=unxpAu384BFfCTUD"},
    {"course": "Aeronautic & Astronautic Engineering", "caption": "Rocket Science and Propulsion", "video_url": "https://youtu.be/SNpGBVoKOmg?si=r-iXFeYvcOMC6oAW"},
    {"course": "Aeronautic & Astronautic Engineering", "caption": "Aerodynamics and Fluid Dynamics", "video_url": "https://youtu.be/GMmNKUlXXDs?si=ciymEjw6aBlrYpOP"},
    {"course": "Aeronautic & Astronautic Engineering", "caption": "Materials Science in Aerospace", "video_url": "https://youtu.be/c0EXAuWkMqA?si=43vJMms0oYcCO1kG"},
    {"course": "Agriculture", "caption": "Introduction to Modern Agriculture", "video_url": "https://youtu.be/OTbn_gXfb2A?si=qat3w2Wo_PFV22PV"},
    {"course": "Agriculture", "caption": "Soil Science Fundamentals", "video_url": "https://youtu.be/tz8yhfKLdaQ?si=sQOLqrDRXM9SegG4"},
    {"course": "Agriculture", "caption": "Crop Production and Management", "video_url": "https://youtu.be/8ulpy_GFLDk?si=NFFDfeLot3nEFtKn"},
    {"course": "Agriculture", "caption": "Animal Husbandry Basics", "video_url": "https://youtu.be/Bc1UJZTcHkc?si=JGvMg1YfiK8B5bNA"},
    {"course": "Agriculture", "caption": "Agricultural Economics", "video_url": "https://youtu.be/fbOiwV3gBLg?si=JWNp-NTTcwbAlOxs"},
    {"course": "Arabic Studies", "caption": "Learn Arabic Alphabet in 30 Minutes", "video_url": "https://youtu.be/zkDccT4Obe4?si=Z5JsxnkdyfH-S1SH"},
    {"course": "Arabic Studies", "caption": "Arabic Grammar for Beginners", "video_url": "https://youtu.be/482yzdMyjZQ?si=mLijsQPVAg3LpAah"},
    {"course": "Arabic Studies", "caption": "Introduction to Arabic Literature", "video_url": "https://youtu.be/_qTH9vXPRjg?si=kNGQDpZTJmXPJUgN"},
    {"course": "Arabic Studies", "caption": "Modern Standard Arabic Conversation", "video_url": "https://youtu.be/X1mC1XY65Kc?si=fODp6DWZoou_J2vg"},
    {"course": "Arabic Studies", "caption": "The History of the Arabic Language", "video_url": "https://youtu.be/nDg3yPSzsEg?si=T5tmecHzT0-7KxU7"},
    {"course": "Banking & Finance", "caption": "Introduction to Banking", "video_url": "https://youtu.be/lF_Pk00CoK8?si=MlbEjby9vmxFgiwp"},
    {"course": "Banking & Finance", "caption": "Financial Markets and Instruments", "video_url": "https://youtu.be/UOwi7MBSfhk?si=J6BF0g47hmOLIPBE"},
    {"course": "Banking & Finance", "caption": "Corporate Finance Fundamentals", "video_url": "https://youtu.be/lm2ym3J4FMw?si=0upwxDFfmcXR2ayd"},
    {"course": "Banking & Finance", "caption": "Risk Management in Banks", "video_url": "https://youtu.be/dIsxj7HZmqM?si=zw3-Yq1_NZg4igPs"},
    {"course": "Banking & Finance", "caption": "International Finance and Forex", "video_url": "https://youtu.be/5iEHsRja8u0?si=xtrtP1Fk86NGUgnj"},
    {"course": "Biochemistry", "caption": "Introduction to Biochemistry", "video_url": "https://youtu.be/BjzH4Hr_wGg?si=fi_WBoA7augN0t0h"},
    {"course": "Biochemistry", "caption": "Protein Structure and Function", "video_url": "https://youtu.be/Bsk9hvXDJp8?si=E7xFs96qGT2eEujI"},
    {"course": "Biochemistry", "caption": "Enzyme Kinetics and Mechanisms", "video_url": "https://youtu.be/kmyR1cYxRL4?si=jph0vQBJR_Ym6Dv2"},
    {"course": "Biochemistry", "caption": "DNA Replication and Repair", "video_url": "https://youtu.be/Qqe4thU-os8?si=RYK_QwAzX2C3g3oc"},
    {"course": "Biochemistry", "caption": "Metabolic Pathways Overview", "video_url": "https://youtu.be/Lf4irlyN1eE?si=cxxEU4OCsQBwuLVc"},
    {"course": "Botany", "caption": "Introduction to Botany", "video_url": "https://youtu.be/8-G7D_sy7qE?si=1xHKCNazZmNirAYC"},
    {"course": "Botany", "caption": "Plant Anatomy and Physiology", "video_url": "https://youtu.be/pvVvCt6Kdp8?si=Qb2FuHI1odDq4xZX"},
    {"course": "Botany", "caption": "Photosynthesis Deep Dive", "video_url": "https://youtu.be/D2Y_eEaxrYo?si=mwwGa9IYelZpb6kJ"},
    {"course": "Botany", "caption": "Plant Taxonomy and Classification", "video_url": "https://youtu.be/2mXkTLTQ5Zk?si=Dg2AcFE8zEoXCKRK"},
    {"course": "Botany", "caption": "Ethnobotany: Plants and People", "video_url": "https://youtu.be/JinM3F4kXtw?si=ZFXE7pdB4wCKhPkG"},
    {"course": "Business Administration", "caption": "What is Business Administration?", "video_url": "https://youtu.be/UtbfATNSePk?si=9b2JLLAgiTzHUioe"},
    {"course": "Business Administration", "caption": "Principles of Management", "video_url": "https://youtu.be/lj7ZnyskZuA?si=ZQ-OTOD6X1A87X0b"},
    {"course": "Business Administration", "caption": "Strategic Planning Process", "video_url": "https://youtu.be/HQ6348u6o08?si=6hlJvl0aFL4tkYjG"},
    {"course": "Business Administration", "caption": "Organizational Behavior Basics", "video_url": "https://youtu.be/zF8PY8pyrEQ?si=5yKP0_PBuHLoJ_L9"},
    {"course": "Business Administration", "caption": "Fundamentals of Marketing", "video_url": "https://youtu.be/8Sj2tbh-ozE?si=xlcP3HFYfUTjMsDr"},
    {"course": "Business Education", "caption": "Teaching Business Education", "video_url": "https://youtu.be/69dLyztc-As?si=WBkN-aiTKb3XqzOd"},
    {"course": "Business Education", "caption": "Curriculum Development for Business", "video_url": "https://youtu.be/zzQIANlz4lI?si=jmyaq8xBGvb8p8bj"},
    {"course": "Business Education", "caption": "Teaching Entrepreneurship", "video_url": "https://youtu.be/eHJnEHyyN1Y?si=5QZIyvp4OxEEPhAl"},
    {"course": "Business Education", "caption": "Using Technology in Business Class", "video_url": "https://youtu.be/-Zpu85fWglA?si=LrStwu3iLlKyZti8"},
    {"course": "Business Education", "caption": "Assessment in Business Education", "video_url": "https://youtu.be/69dLyztc-As?si=Wt3gdmAaDbr13_Jw"},
    {"course": "Chemical & Polymer Engineering", "caption": "What is Chemical Engineering?", "video_url": "https://youtu.be/RJeWKvQD90Y?si=OSs2XNS43kYGIjPI"},
    {"course": "Chemical & Polymer Engineering", "caption": "Introduction to Polymer Science", "video_url": "https://youtu.be/p-yXd7Tks3c?si=VNCMy5OTzxaUO3gI"},
    {"course": "Chemical & Polymer Engineering", "caption": "Chemical Reaction Engineering", "video_url": "https://youtu.be/H-myZaCmtdg?si=j8U9M_0ASmieXmHy"},
    {"course": "Chemical & Polymer Engineering", "caption": "Process Control and Dynamics", "video_url": "https://youtu.be/PqNL_DW-Tx4?si=dyIkFFqET_6TIMsO"},
    {"course": "Chemical & Polymer Engineering", "caption": "Plant Design and Economics", "video_url": "https://www.youtube.com/live/ozRIY65WIq0?si=-4pTcSSl7K0nL_Ta"},
    {"course": "Chemical & Polymer Engineering", "caption": "Plant Design and Economics 2", "video_url": "https://www.youtube.com/live/63dZ5j8WAeg?si=eba7YNlRGAR1eOJ4"},
        {"course": "Chemical & Polymer Engineering", "caption": "Plant Design and Economics 3", "video_url": "https://www.youtube.com/live/63dZ5j8WAeg?si=eba7YNlRGAR1eOJ4"},
    {"course": "Chemistry", "caption": "Chemistry Basics", "video_url": "https://youtu.be/LiAvDpl5aJA?si=9DU7nZ-Q_SB4dmPc"},
    {"course": "Chemistry", "caption": "Organic Chemistry Fundamentals", "video_url": "https://youtu.be/bSMx0NS0XfY?si=54ln8rQaTXjcmIYL"},
    {"course": "Chemistry", "caption": "Inorganic Chemistry Introduction", "video_url": "https://youtu.be/2B7X7Be4Vus?si=HX4BHE3o5YfT-lQP"},
    {"course": "Chemistry", "caption": "Physical Chemistry Key Concepts", "video_url": "https://youtu.be/qttWxnLNZQ8?si=rOVH6LvphJKwS96j"},
    {"course": "Chemistry", "caption": "Analytical Chemistry Techniques", "video_url": "https://youtu.be/HUqOpoXQYic?si=2YI-dnGk5opmIRal"},
    {"course": "Christian Religious Studies", "caption": "Introduction to Christianity", "video_url": "https://youtu.be/9Lc7wTdM-0U?si=w7gK3yXly7OfXK0I"},
    {"course": "Christian Religious Studies", "caption": "The Old Testament Survey", "video_url": "https://youtu.be/a2pG8ckXmdo?si=O3hGSNqxKvE7T7E-"},
    {"course": "Christian Religious Studies", "caption": "The New Testament Survey", "video_url": "https://youtu.be/40C_ah2p_CE?si=H_CutkM4yadqbh1O"},
    {"course": "Christian Religious Studies", "caption": "Christian Ethics and Morality", "video_url": "https://youtu.be/SQnc3rDn_EM?si=k5Ld2flijR4s_c0r"},
    {"course": "Christian Religious Studies", "caption": "History of the Church", "video_url": "https://youtu.be/FmY3exO5JYs?si=9RbbD-8JN13O33lR"},
    {"course": "Common and Islamic Law", "caption": "Introduction to Islamic Law", "video_url": "https://youtu.be/D_ZMzZ1sBvM?si=tpJXXfZ9l7UYahLy"},
    {"course": "Common and Islamic Law", "caption": "Sources of Islamic Jurisprudence", "video_url": "https://youtu.be/2jTRAazlXpc?si=PkXCFykqvP8sNroY"},
    {"course": "Common and Islamic Law", "caption": "Contract Law in Islamic Finance", "video_url": "https://youtu.be/Vv9ZvD8Jzyw?si=yzq-uwc3mifJ-LrP"},
    {"course": "Common and Islamic Law", "caption": "Family Law in Islam", "video_url": "https://youtu.be/jxP1U9-VVwQ?si=YQYlJwQjqJp_Nu0k"},
    {"course": "Common and Islamic Law", "caption": "Comparative Common Law Systems", "video_url": "https://youtu.be/A5uJXzjHKlo?si=JZayCk2lNxG1S-xw"},
    {"course": "Computer Science", "caption": "Introduction to Programming", "video_url": "https://youtu.be/zOjov-2OZ0E?si=Jc0sjcUY3UJcNnRG"},
    {"course": "Computer Science", "caption": "Data Structures and Algorithms", "video_url": "https://youtu.be/RBSGKlAvoiM?si=6_V3q6wN_nD9TP50"},
    {"course": "Computer Science", "caption": "Introduction to Databases", "video_url": "https://youtu.be/wR0jg0eQsZA?si=ZTO20FMLETU2Z8kf"},
    {"course": "Computer Science", "caption": "Computer Networks Basics", "video_url": "https://youtu.be/3QhU9jd03a0?si=BR6rQhDDXvV45P4S"},
    {"course": "Computer Science", "caption": "Cybersecurity Fundamentals", "video_url": "https://youtu.be/inWWhr5tnEA?si=W8PPOcKhMgPMb3Dy"},
    {"course": "Curriculum Studies", "caption": "Curriculum Design", "video_url": "https://youtu.be/6h5KqLvOqBk?si=JQbYl9y2T5VqxydJ"},
    {"course": "Curriculum Studies", "caption": "Theories of Curriculum Development", "video_url": "https://youtu.be/Y7_OePR_zMk?si=YcOmPJ5evScuZ6nA"},
    {"course": "Curriculum Studies", "caption": "Implementing a Curriculum", "video_url": "https://youtu.be/1eQ0-7f-yzE?si=o4oyzxOxn0CJ10fd"},
    {"course": "Curriculum Studies", "caption": "Curriculum Evaluation Methods", "video_url": "https://youtu.be/SnchMoE1hJ8?si=61o2s5D9kfDe6Wf9"},
    {"course": "Curriculum Studies", "caption": "Trends in Modern Curriculum", "video_url": "https://youtu.be/RuG_X-bLh1o?si=oQ9KjWAz4WOs6zJ9"},
    {"course": "Dentistry & Dental Surgery", "caption": "A Day in Dental School", "video_url": "https://youtu.be/dTnHMd5knpQ?si=GGIPKYyDgGgTg--N"},
    {"course": "Dentistry & Dental Surgery", "caption": "Introduction to Oral Anatomy", "video_url": "https://youtu.be/DD6hJq8Nx0k?si=D2LfIFagNx9hUzmL"},
    {"course": "Dentistry & Dental Surgery", "caption": "Dental Materials Science", "video_url": "https://youtu.be/tSgPpE4oDSU?si=9fxFtfOKd-0sz_P6"},
    {"course": "Dentistry & Dental Surgery", "caption": "Periodontology Basics", "video_url": "https://youtu.be/wuHpvm3qzlI?si=5CxMLluzGFO-JhbW"},
    {"course": "Dentistry & Dental Surgery", "caption": "Pediatric Dentistry Introduction", "video_url": "https://youtu.be/9j8ZLDGXjXo?si=J8i_Wb9en9JYdm4A"},
    {"course": "Drama/Dramatic/Performing Arts", "caption": "Introduction to Theatre", "video_url": "https://youtu.be/saTRCNSjhv8?si=WOmhJjqf8pyvx0MQ"},
    {"course": "Drama/Dramatic/Performing Arts", "caption": "Acting Techniques and Methods", "video_url": "https://youtu.be/LRLqcRz5Lh4?si=4a1C-dqTr5QGVBUK"},
    {"course": "Drama/Dramatic/Performing Arts", "caption": "Stage Design and Production", "video_url": "https://youtu.be/s2l1LXJdGcw?si=7_9J6yRw4p-W8K-5"},
    {"course": "Drama/Dramatic/Performing Arts", "caption": "The History of World Drama", "video_url": "https://youtu.be/YewD9Kk-K9M?si=kCq4SnKbMABV_J7f"},
    {"course": "Drama/Dramatic/Performing Arts", "caption": "Playwriting Fundamentals", "video_url": "https://youtu.be/6lvGQx0QFx4?si=CzwMEFaNpqWpm-bP"},
    {"course": "Early Childhood & Primary Education", "caption": "Early Childhood Education", "video_url": "https://youtu.be/LKXqk8v0DNA?si=LLKl3O1oITW3Kzxq"},
    {"course": "Early Childhood & Primary Education", "caption": "Child Development Stages", "video_url": "https://youtu.be/y5i5E4zRqlM?si=wOGW0e8r1XkPhI5U"},
    {"course": "Early Childhood & Primary Education", "caption": "Play-Based Learning Strategies", "video_url": "https://youtu.be/8_b-VnDL-6I?si=kyM8XYxez9f27w2X"},
    {"course": "Early Childhood & Primary Education", "caption": "Creating Inclusive Classrooms", "video_url": "https://youtu.be/b8H0qyfQA8Y?si=QN_XQ6bW7PjCexIh"},
    {"course": "Early Childhood & Primary Education", "caption": "Literacy in Early Years", "video_url": "https://youtu.be/4M7GRo4fscs?si=r29SLpZRaLj3J1qX"},
    {"course": "Economics", "caption": "Intro to Economics", "video_url": "https://youtu.be/2YULdjmg3o0?si=8rZHEgAvSDiALZ1I"},
    {"course": "Economics", "caption": "Microeconomics vs Macroeconomics", "video_url": "https://youtu.be/ZtS7kF-Wgvs?si=Y8Q8KfAEMB3LbpNy"},
    {"course": "Economics", "caption": "Supply, Demand, and Market Equilibrium", "video_url": "https://youtu.be/LwLh6ax0zTE?si=imjM2gMJ3DHv2y0Z"},
    {"course": "Economics", "caption": "Introduction to Econometrics", "video_url": "https://youtu.be/0LqyFk5vK7w?si=kKzK3hx5hPFC9JX_"},
    {"course": "Economics", "caption": "International Trade Theories", "video_url": "https://youtu.be/10h1Kk1kq-w?si=hU8vG20ezv7zyvCS"},
    {"course": "Education & Accounting", "caption": "Teaching Accounting Concepts", "video_url": "https://youtu.be/x6R4qEf4Ngg?si=_ntHnY3EmFqOBzCQ"},
    {"course": "Education & Accounting", "caption": "Pedagogy for Business Subjects", "video_url": "https://youtu.be/_RkSg_QHc1c?si=qmPR4RTMhz_5eKrU"},
    {"course": "Education & Accounting", "caption": "Classroom Activities for Accounting", "video_url": "https://youtu.be/5SESVVK7Evg?si=O75bnvSpwWvFd55P"},
    {"course": "Education & Accounting", "caption": "Assessing Accounting Students", "video_url": "https://youtu.be/ak78y2AnMVI?si=9NqCv-q4K8dZayyR"},
    {"course": "Education & Accounting", "caption": "Integrating Technology in Accounting Ed", "video_url": "https://youtu.be/RlCHG6I5e6E?si=07W_CKzZbhGl7NpW"},
    {"course": "Education & Arabic", "caption": "Methods of Teaching Arabic", "video_url": "https://youtu.be/WfJXG-CPox4?si=Z8b_5oU9B5qsvsqW"},
    {"course": "Education & Arabic", "caption": "Developing Arabic Language Curriculum", "video_url": "https://youtu.be/TW5rNesX8oY?si=MO8uB51q3BcFj1Ww"},
    {"course": "Education & Arabic", "caption": "Cultural Context in Arabic Teaching", "video_url": "https://youtu.be/Z6jXkT2dRWI?si=BfFd8LS7x0bMnajv"},
    {"course": "Education & Arabic", "caption": "Resources for Arabic Teachers", "video_url": "https://youtu.be/4qqfDl5VcoY?si=gKkSQpz9ukAnGBiV"},
    {"course": "Education & Arabic", "caption": "Assessment in Arabic Language Learning", "video_url": "https://youtu.be/XgNqvO_jiiE?si=ivs1dD_qnLh7CgOa"},
    {"course": "Education & Biology", "caption": "Teaching Biology Effectively", "video_url": "https://youtu.be/-1Rc_97jC-A?si=oib7KpszXkxChqCr"},
    {"course": "Education & Biology", "caption": "Hands-on Biology Experiments for Class", "video_url": "https://youtu.be/YdKzRcIJMEA?si=3ruwrdB_hLh9vztU"},
    {"course": "Education & Biology", "caption": "Biology Curriculum Planning", "video_url": "https://youtu.be/X5Cq_OsXq8k?si=91abgczf2dkh9uCv"},
    {"course": "Education & Biology", "caption": "Addressing Misconceptions in Biology", "video_url": "https://youtu.be/ZLJehZ6mQN8?si=9lOOrnRue6OrN-eF"},
    {"course": "Education & Biology", "caption": "Using Models in Biology Education", "video_url": "https://youtu.be/6zVcR1Bd1Gs?si=PTbZv6Z5nwqwSnl-"},
    {"course": "Education & Chemistry", "caption": "Strategies for Teaching Chemistry", "video_url": "https://youtu.be/tWYlV_mnc7M?si=OJnGMeob7qnpQlY_"},
    {"course": "Education & Chemistry", "caption": "Safe Lab Demonstrations for Schools", "video_url": "https://youtu.be/7Ye8PIG7s_I?si=Qh1p84VRMyMcjJqD"},
    {"course": "Education & Chemistry", "caption": "Making Chemistry Relatable", "video_url": "https://youtu.be/5j0Ev_cX-ds?si=8yEjkGfUbgqlfrD8"},
    {"course": "Education & Chemistry", "caption": "Concept Mapping in Chemistry", "video_url": "https://youtu.be/3RGvH1Gc6Z0?si=xSNjLePV0H3rVbKv"},
    {"course": "Education & Chemistry", "caption": "Digital Tools for Chemistry Teachers", "video_url": "https://youtu.be/hT9GGxgaufI?si=HdsxNx-EqChvhKXp"},
    {"course": "Education & Christian Religious Studies", "caption": "Pedagogy for Religious Education", "video_url": "https://youtu.be/8NJrV5b-CWo?si=U0sLTHaZh-eC7FmS"},
    {"course": "Education & Christian Religious Studies", "caption": "Teaching Biblical Stories", "video_url": "https://youtu.be/p4iPqVZ1fLI?si=K8G0s8PjxWJVYVDy"},
    {"course": "Education & Christian Religious Studies", "caption": "Ethics Education in Schools", "video_url": "https://youtu.be/R3yJsBTPjcs?si=DpVy7hR4atqdcu8L"},
    {"course": "Education & Christian Religious Studies", "caption": "Interfaith Dialogue in Classroom", "video_url": "https://youtu.be/qKGEhVgIgTg?si=Ckww6wF3Yt2bV_kk"},
    {"course": "Education & Christian Religious Studies", "caption": "Creating Inclusive RE Lessons", "video_url": "https://youtu.be/0jqYrZ9QK_o?si=LJkqO_52Wv5gMSLS"},
    {"course": "Education & Computer Science", "caption": "Teaching Computational Thinking", "video_url": "https://youtu.be/VFcUgSYyRPg?si=CNz0aepLCXaweG9f"},
    {"course": "Education & Computer Science", "caption": "Introducing Coding to Beginners", "video_url": "https://youtu.be/N7ZmPYaXoic?si=3rU1tZqFUTeAlY_Q"},
    {"course": "Education & Computer Science", "caption": "Project-Based Learning in CS", "video_url": "https://youtu.be/5Z5dOXl_1AU?si=GvqHnP7QiyB_Q-Ag"},
    {"course": "Education & Computer Science", "caption": "Assessing Programming Skills", "video_url": "https://youtu.be/YpKdC0-OVd0?si=gxZCNRO2ETQvZg5Z"},
    {"course": "Education & Computer Science", "caption": "CS Education for All Students", "video_url": "https://youtu.be/FpMNs7H24X0?si=YV3Ef14DDrqAS0qD"},
    {"course": "Education & Economics", "caption": "How to Teach Economics Concepts", "video_url": "https://youtu.be/Jh8Hj-qbZf8?si=IR9zCvKgC0KZIVeZ"},
    {"course": "Education & Economics", "caption": "Using Real-World Data in Econ Class", "video_url": "https://youtu.be/mcPGDPKZvdw?si=02hSTJpWJz-VIBlv"},
    {"course": "Education & Economics", "caption": "Simulations and Games for Economics", "video_url": "https://youtu.be/XeBQ0p6vF88?si=q9RII_rXQxZ0P-mO"},
    {"course": "Education & Economics", "caption": "Current Events in Economics Teaching", "video_url": "https://youtu.be/1EzLjgCJlgc?si=zOCbH5lINo2t8eHm"},
    {"course": "Education & Economics", "caption": "Differentiating Economics Instruction", "video_url": "https://youtu.be/ME5XpP72LDA?si=9-k36nJktgvkyDm7"},
    {"course": "Education & English Language", "caption": "Teaching English as a Second Language", "video_url": "https://youtu.be/cjIvc-8DAlc?si=WJkhlttzYfAxTepf"},
    {"course": "Education & English Language", "caption": "Grammar Instruction Techniques", "video_url": "https://youtu.be/ev_0ZKFMo2o?si=19_UmLekYtNCR6q0"},
    {"course": "Education & English Language", "caption": "Developing Reading and Writing Skills", "video_url": "https://youtu.be/l6a8I7e2A88?si=YWCTtbygG4bIxtq9"},
    {"course": "Education & English Language", "caption": "Literature in the Language Classroom", "video_url": "https://youtu.be/zG7e5Mjpc_g?si=3bIf8VpYo5dJ-sVn"},
    {"course": "Education & English Language", "caption": "Classroom Communication Strategies", "video_url": "https://youtu.be/ZrNGM7tZqlg?si=lUfIETAHK7B7dsnx"},
    {"course": "Education & French", "caption": "Methodologies for Teaching French", "video_url": "https://youtu.be/ZgBqLQclZPs?si=wMBzZpsbVk2FWEX4"},
    {"course": "Education & French", "caption": "French Pronunciation for Teachers", "video_url": "https://youtu.be/YdeG_SsHIII?si=1yhMp4_0e0Py4yB8"},
    {"course": "Education & French", "caption": "Creating Immersive French Lessons", "video_url": "https://youtu.be/Pm8uF3w2AEE?si=Y26BfIGuY9rqy_6w"},
    {"course": "Education & French", "caption": "Teaching French Grammar Creatively", "video_url": "https://youtu.be/AfFp4nqqp3A?si=_bEeXXytsdcH8lMj"},
    {"course": "Education & French", "caption": "Cultural Aspects of French Teaching", "video_url": "https://youtu.be/QCgJdJgC0_w?si=oLj8VWjAlv2gFvI2"},
    {"course": "Education & Geography", "caption": "Innovative Geography Teaching Methods", "video_url": "https://youtu.be/cAgkvhwP5Mw?si=Yw_8kG8BgsEnQxRX"},
    {"course": "Education & Geography", "caption": "Using GIS in the Classroom", "video_url": "https://youtu.be/qpJ0U5gqHlY?si=mog0Q_Ci6GJN9hx2"},
    {"course": "Education & Geography", "caption": "Fieldwork and Outdoor Learning", "video_url": "https://youtu.be/qaO7Oni-km8?si=TgIOjJxNPP0U04H9"},
    {"course": "Education & Geography", "caption": "Teaching Human and Physical Geography", "video_url": "https://youtu.be/8WjtStJi_Ns?si=ww7mfnGAmC0u3F_C"},
    {"course": "Education & Geography", "caption": "Geography Curriculum Development", "video_url": "https://youtu.be/p5T7-3zW9iU?si=8CgFpXf8B7vN8wGl"},
    {"course": "Education & History", "caption": "Teaching Historical Thinking", "video_url": "https://youtu.be/wvsE8jm1gTs?si=ixC8-2j9PMLExYSo"},
    {"course": "Education & History", "caption": "Using Primary Sources in Class", "video_url": "https://youtu.be/qKbLxgLf3vo?si=z1KSM1V1zmLv-yQq"},
    {"course": "Education & History", "caption": "Making History Engaging", "video_url": "https://youtu.be/j15Q1O9ITAM?si=KGs0GpmSVRKfLhBv"},
    {"course": "Education & History", "caption": "Debates and Discussions in History Ed", "video_url": "https://youtu.be/DsH-WqZc0Ac?si=_DfDfqqm-S0aJdW3"},
    {"course": "Education & History", "caption": "Assessing Historical Understanding", "video_url": "https://youtu.be/EdJxZOvrWw4?si=XX-43p7HdQk52lg_"},
    {"course": "Education & Islamic Studies", "caption": "Pedagogical Approaches to Islamic Studies", "video_url": "https://youtu.be/GZ06bP44iUM?si=DTiYjMXB9jFcSgaE"},
    {"course": "Education & Islamic Studies", "caption": "Teaching Quranic Studies", "video_url": "https://youtu.be/WgQHafyV3uw?si=wFZk6R6d8Y_w_QJh"},
    {"course": "Education & Islamic Studies", "caption": "Islamic History in the Curriculum", "video_url": "https://youtu.be/XSgakFVSLmQ?si=W_Fs1E4Ua8zZbr-c"},
    {"course": "Education & Islamic Studies", "caption": "Character Education through Islamic Teachings", "video_url": "https://youtu.be/AtR44mZ_QOY?si=UgbHtU02K8RxHfUb"},
    {"course": "Education & Islamic Studies", "caption": "Resources for Islamic Studies Teachers", "video_url": "https://youtu.be/HNUr8PwH_HM?si=78rcnnNK8TzSRb6Z"},
    {"course": "Education & Mathematics", "caption": "Teaching Math for Understanding", "video_url": "https://youtu.be/3icoSeGqQtY?si=Ad4i-vrmimBQXe4v"},
    {"course": "Education & Mathematics", "caption": "Overcoming Math Anxiety in Students", "video_url": "https://youtu.be/7snnraJJEqI?si=gTCz0lEedtHHxrhp"},
    {"course": "Education & Mathematics", "caption": "Hands-on Math Activities", "video_url": "https://youtu.be/9rQwpcPH0ME?si=4wxAp1itX2u6a_Pe"},
    {"course": "Education & Mathematics", "caption": "Differentiated Math Instruction", "video_url": "https://youtu.be/6tqQYlxtB_0?si=cgEVT-lcPs4T4HmW"},
    {"course": "Education & Mathematics", "caption": "Technology in Mathematics Education", "video_url": "https://youtu.be/8yYu9wOk_48?si=Ct8dr0IGGQgDgWcH"},
    {"course": "Education & Physics", "caption": "Effective Physics Teaching Strategies", "video_url": "https://youtu.be/fhwa7KtK-4k?si=H4DtvFd6J9oRgV2l"},
    {"course": "Education & Physics", "caption": "Demonstrating Physics Principles", "video_url": "https://youtu.be/pxWd3c6hVKM?si=0L2q7hhXxcfPJpL5"},
    {"course": "Education & Physics", "caption": "Problem-Solving in Physics Class", "video_url": "https://youtu.be/B-cm27Ow5j4?si=H5XxmmrDAJT3Fyph"},
    {"course": "Education & Physics", "caption": "Connecting Physics to Everyday Life", "video_url": "https://youtu.be/Pm9dU7a5YbM?si=6z8V3jfqJvHxxdW9"},
    {"course": "Education & Physics", "caption": "Laboratory Work in Physics Education", "video_url": "https://youtu.be/KUa9cC-eW9c?si=oM5de0QoTwClwQsl"},
    {"course": "Education & Political Science", "caption": "Teaching Civics and Government", "video_url": "https://youtu.be/Lv_Y7qY4ks0?si=4Xh5Bf0ntt4M5yB0"},
    {"course": "Education & Political Science", "caption": "Simulating Political Processes", "video_url": "https://youtu.be/JZQjW_WsM7g?si=aJ6TTTPIk9eZnsk_"},
    {"course": "Education & Political Science", "caption": "Critical Thinking in Poli Sci", "video_url": "https://youtu.be/mMjeHx0Xlj0?si=bbBwFR3rHZ8D_ZaV"},
    {"course": "Education & Political Science", "caption": "Discussing Current Political Issues", "video_url": "https://youtu.be/l8hUcYBuGDY?si=Kb4_h5v6TkyIGP1I"},
    {"course": "Education & Political Science", "caption": "Curriculum for Political Literacy", "video_url": "https://youtu.be/FSGschcTVYQ?si=WKv6MZ1rHlBfXvCK"},
    {"course": "Education & Yoruba", "caption": "Teaching Yoruba Language and Culture", "video_url": "https://youtu.be/qvqhmk4wjCM?si=HXZ3wAp1kDFg9eVV"},
    {"course": "Education & Yoruba", "caption": "Yoruba Grammar for Educators", "video_url": "https://youtu.be/7B-6gPNMkIw?si=m7f6M40pGmGpDpFW"},
    {"course": "Education & Yoruba", "caption": "Developing Yoruba Literacy", "video_url": "https://youtu.be/lwW-ZUvDZok?si=kS17_tsJq0d0CTYs"},
    {"course": "Education & Yoruba", "caption": "Oral Traditions in Teaching", "video_url": "https://youtu.be/tqo8xVlkru8?si=3rT_vQ78ezWeUu3u"},
    {"course": "Education & Yoruba", "caption": "Creating Yoruba Teaching Materials", "video_url": "https://youtu.be/9bfh1CS-vGc?si=G8r7a2rMBJ7L9ng2"},
    {"course": "Educational Foundations", "caption": "Philosophical Foundations of Education", "video_url": "https://youtu.be/5W5KM7Zc0hU?si=l8SEZ1gZ7PRazQnI"},
    {"course": "Educational Foundations", "caption": "Sociological Perspectives on Education", "video_url": "https://youtu.be/s7XGzNtV9HI?si=whQsvB-sWvY8g0sL"},
    {"course": "Educational Foundations", "caption": "Historical Foundations of Education", "video_url": "https://youtu.be/kqBQ6sn0hQk?si=sPrCkrEyp0oR3dRB"},
    {"course": "Educational Foundations", "caption": "Psychological Theories of Learning", "video_url": "https://youtu.be/sgjB_I1zB_w?si=SCgvzBPjykpjb3r_"},
    {"course": "Educational Foundations", "caption": "Comparative Education", "video_url": "https://youtu.be/fW37P7T3C4Q?si=jv8LZqmXZf3X7Fps"},
    {"course": "Educational Management", "caption": "Educational Leadership", "video_url": "https://youtu.be/E5Gr_AzYybs?si=mLb3rVW_tEw5dQZI"},
    {"course": "Educational Management", "caption": "School Administration and Organization", "video_url": "https://youtu.be/ryW13at-Jfc?si=1R2Lrr13bVJymVVB"},
    {"course": "Educational Management", "caption": "Financial Management in Education", "video_url": "https://youtu.be/4qJkL8n6dDk?si=B2PN0x8h9ffICebh"},
    {"course": "Educational Management", "caption": "Human Resource Management in Schools", "video_url": "https://youtu.be/tkDY8l7hSUg?si=akqEmrqLz_3_xCQe"},
    {"course": "Educational Management", "caption": "Educational Policy and Planning", "video_url": "https://youtu.be/efb4j7c_TvI?si=mlwC1VVq-fV0ln2U"},
    {"course": "Electronics & Computer Engineering", "caption": "Introduction to Digital Electronics", "video_url": "https://youtu.be/LsGpHLzZBPU?si=4fqxST6o_13xUPBf"},
    {"course": "Electronics & Computer Engineering", "caption": "Microprocessors and Microcontrollers", "video_url": "https://youtu.be/7F-WiTjQ0wM?si=G00VqHv8nF5_4AD_"},
    {"course": "Electronics & Computer Engineering", "caption": "Embedded Systems Design", "video_url": "https://youtu.be/5XcQrUvJzVE?si=AVCrz9dKexX8D8xT"},
    {"course": "Electronics & Computer Engineering", "caption": "Signal Processing Basics", "video_url": "https://youtu.be/8qKySSbNvq0?si=QRYWzK9toYt9lA0R"},
    {"course": "Electronics & Computer Engineering", "caption": "Computer Architecture", "video_url": "https://youtu.be/4TzMyNtU5Ok?si=mInz14LSyLJy0yVe"},
    {"course": "English Language", "caption": "History of the English Language", "video_url": "https://youtu.be/H3r9bOkYW9s?si=e6kMl3pKlgUdm-uE"},
    {"course": "English Language", "caption": "English Grammar Masterclass", "video_url": "https://youtu.be/T9R5-6eHQNs?si=nr9AUfQW-1L6zxjC"},
    {"course": "English Language", "caption": "Phonetics and Phonology", "video_url": "https://youtu.be/3QyH-z8SXVs?si=rVcKWWrNf9vXG0q_"},
    {"course": "English Language", "caption": "Sociolinguistics: Language in Society", "video_url": "https://youtu.be/1fQ_AXrK8P8?si=8mQnw06L8GTYs4t5"},
    {"course": "English Language", "caption": "Introduction to Stylistics", "video_url": "https://youtu.be/HfIET2E2bVE?si=SoQMBpCM8-yE71sm"},
    {"course": "Fine/Applied Arts", "caption": "Introduction to Fine Art", "video_url": "https://youtu.be/ZWdD_ypRqS4?si=v6JwIlW5kkJ8lGCl"},
    {"course": "Fine/Applied Arts", "caption": "Drawing Fundamentals", "video_url": "https://youtu.be/CT-ydhQ9wXg?si=tcLjmKNjq-QcnwRw"},
    {"course": "Fine/Applied Arts", "caption": "Principles of Design", "video_url": "https://youtu.be/ZK86XQ1iFVs?si=2uEFM-ngOa1R3NDq"},
    {"course": "Fine/Applied Arts", "caption": "Art History Movements", "video_url": "https://youtu.be/t2dou9yZ7-w?si=dhkRjmS20OMj_3HZ"},
    {"course": "Fine/Applied Arts", "caption": "Sculpture and 3D Art", "video_url": "https://youtu.be/c9zPw8E3bMU?si=_DwkJJEL4Qgnb8TK"},
    {"course": "Fisheries", "caption": "Introduction to Fisheries Science", "video_url": "https://youtu.be/wkO_QlDcLho?si=ChsZ1c_P8vNvzFAn"},
    {"course": "Fisheries", "caption": "Aquaculture Techniques", "video_url": "https://youtu.be/NI3ZqFqHrKQ?si=rc6w6O2lN4c1m9xP"},
    {"course": "Fisheries", "caption": "Fish Biology and Physiology", "video_url": "https://youtu.be/wESLch3rWzU?si=bV-_sAXl_sU7uQLa"},
    {"course": "Fisheries", "caption": "Fisheries Management and Conservation", "video_url": "https://youtu.be/9kRgV7r6_34?si=7a4r8fE1A3f3mpvY"},
    {"course": "Fisheries", "caption": "Post-Harvest Fish Technology", "video_url": "https://youtu.be/lIvyq4D6fR8?si=JpOsV2K5Gg0P9Dd_"},
    {"course": "French", "caption": "Learn French from Scratch", "video_url": "https://youtu.be/5fD5RqgNRgk?si=IWYIqW1lR2kq1h0f"},
    {"course": "French", "caption": "French Grammar Essentials", "video_url": "https://youtu.be/ZA9M1Q7rZag?si=YkUQ75g4mIu4hEFh"},
    {"course": "French", "caption": "French Conversation Practice", "video_url": "https://youtu.be/4v-gI6oIy6o?si=7A9INouW7vA3vP-I"},
    {"course": "French", "caption": "French Literature Introduction", "video_url": "https://youtu.be/fJ3s3DKHjyg?si=YExzB0VawL6Ih6hF"},
    {"course": "French", "caption": "French Culture and Civilization", "video_url": "https://youtu.be/yG0VxbhcIWU?si=Uqkjq1yykppQ-nBM"},
    {"course": "Geography & Planning", "caption": "What is Human Geography?", "video_url": "https://youtu.be/5WjD2RQ-gAE?si=5sl81eBISkK7mBVF"},
    {"course": "Geography & Planning", "caption": "Urban and Regional Planning", "video_url": "https://youtu.be/T1-m3pPcUiE?si=vwctMc5zP53xHeWy"},
    {"course": "Geography & Planning", "caption": "Geographic Information Systems (GIS)", "video_url": "https://youtu.be/6jGDW-TSe8M?si=oOBe5vDcE_iI5wDl"},
    {"course": "Geography & Planning", "caption": "Environmental Geography", "video_url": "https://youtu.be/6lU4P6yn9q4?si=EFu6zHQlUdr2Yad-"},
    {"course": "Geography & Planning", "caption": "Remote Sensing Applications", "video_url": "https://youtu.be/cVW9LgHQWJE?si=OKp3GG0BN3KqTNSn"},
    {"course": "Guidance & Counselling", "caption": "Introduction to Counselling", "video_url": "https://youtu.be/Vp8mCHQqISE?si=OlLS3kHEE6zzPtkI"},
    {"course": "Guidance & Counselling", "caption": "Theories of Counselling", "video_url": "https://youtu.be/q2aJp2sOQr4?si=7Xp2Q4SUaR_bZzzd"},
    {"course": "Guidance & Counselling", "caption": "Career Guidance and Development", "video_url": "https://youtu.be/NyE5Oc_6Gvs?si=tcUPG9DkQs2_SujG"},
    {"course": "Guidance & Counselling", "caption": "Counselling Skills and Techniques", "video_url": "https://youtu.be/MgZemV9Z-Qc?si=UxbF1stKOrfxprjY"},
    {"course": "Guidance & Counselling", "caption": "Ethics in Counselling Practice", "video_url": "https://youtu.be/9z_88Y71Mzw?si=OPA3m_jINHLCi13M"},
    {"course": "Health Education", "caption": "Principles of Health Education", "video_url": "https://youtu.be/80K6ckCXQh0?si=0hPYlU_hj9ZFRukM"},
    {"course": "Health Education", "caption": "Health Promotion Strategies", "video_url": "https://youtu.be/GZZhH6ZkAm8?si=gM3Tp4qOa8j0G4cX"},
    {"course": "Health Education", "caption": "School Health Programs", "video_url": "https://youtu.be/1pVq3gC1QU8?si=VQPKU1gVbms-iRxT"},
    {"course": "Health Education", "caption": "Nutrition Education", "video_url": "https://youtu.be/Gmh_xMMJ1Pw?si=GjwTXs2N68qlFQkX"},
    {"course": "Health Education", "caption": "Mental Health Awareness", "video_url": "https://youtu.be/oxx564hMBUI?si=Qq7IqPwe7-J8Gx7l"},
    {"course": "History & International Studies", "caption": "Introduction to International Relations", "video_url": "https://youtu.be/3si3_sWzB2E?si=lhG8FJwE_pfgYzBt"},
    {"course": "History & International Studies", "caption": "Diplomacy and Foreign Policy", "video_url": "https://youtu.be/7B_W8kCqSZw?si=Qb--nsBrt8aFCDi1"},
    {"course": "History & International Studies", "caption": "World History Overview", "video_url": "https://youtu.be/Ek9dF5zdPxQ?si=2zAhUqZ16VulY-TI"},
    {"course": "History & International Studies", "caption": "African History and Politics", "video_url": "https://youtu.be/yTUJ6FLhyaQ?si=xIQU08R_aJXz-yPI"},
    {"course": "History & International Studies", "caption": "International Organizations", "video_url": "https://youtu.be/1opLxYPMqLk?si=4rQO9r1nJ8hajekm"},
    {"course": "Industrial Relations & Personnel Management", "caption": "Introduction to Industrial Relations", "video_url": "https://youtu.be/HQM5CjP01jA?si=w49nY3eD9b86CYoc"},
    {"course": "Industrial Relations & Personnel Management", "caption": "Labor Laws and Regulations", "video_url": "https://youtu.be/dqRZqP08gT4?si=dbFrzvzM1sZTTxEK"},
    {"course": "Industrial Relations & Personnel Management", "caption": "Collective Bargaining", "video_url": "https://youtu.be/-QmbljTP9sE?si=3_c-g6rglC0iIYQE"},
    {"course": "Industrial Relations & Personnel Management", "caption": "Human Resource Management Functions", "video_url": "https://youtu.be/A-6Jw8zY9-w?si=uMCeHLUa-1xX9B9s"},
    {"course": "Industrial Relations & Personnel Management", "caption": "Conflict Resolution at Work", "video_url": "https://youtu.be/KRAqr5pQZRs?si=G31H8XglVU2_gyOS"},
    {"course": "Insurance", "caption": "How Insurance Works", "video_url": "https://youtu.be/GkSxdnO7GvA?si=R7Qn0PLcDvWmK9kU"},
    {"course": "Insurance", "caption": "Principles of Insurance", "video_url": "https://youtu.be/WXqBoo4lmvI?si=V7G2-8iBjqC7OoSV"},
    {"course": "Insurance", "caption": "Life and General Insurance", "video_url": "https://youtu.be/tqThT7u37BE?si=nskw6WXJpC1p_Vl9"},
    {"course": "Insurance", "caption": "Risk Assessment and Underwriting", "video_url": "https://youtu.be/qV4N91PlxyU?si=cu8B0sFk9xP9Z24U"},
    {"course": "Insurance", "caption": "Claims Management Process", "video_url": "https://youtu.be/juxC1hL7gK0?si=rDTz4yl1t7bxtImT"},
    {"course": "Islamic Studies", "caption": "Introduction to Islam", "video_url": "https://youtu.be/PDxKxnVZtgo?si=KbC-A-O51-eV1d_A"},
    {"course": "Islamic Studies", "caption": "The Five Pillars of Islam", "video_url": "https://youtu.be/49dC-0guBf4?si=U9-OCMdUVExnXh8n"},
    {"course": "Islamic Studies", "caption": "Quranic Studies and Tafsir", "video_url": "https://youtu.be/0h0TpFgBTnE?si=H9kD_tE0CBO1nNbK"},
    {"course": "Islamic Studies", "caption": "Islamic History and Civilization", "video_url": "https://youtu.be/XSgakFVSLmQ?si=ltMq9LQ38d3dAGYV"},
    {"course": "Islamic Studies", "caption": "Islamic Ethics and Philosophy", "video_url": "https://youtu.be/HA-2b5JKyRo?si=g53R48Y75KFCr2Ef"},
    {"course": "Law", "caption": "Introduction to Law School", "video_url": "https://youtu.be/1Y4X2TqU9Rc?si=6d0fM7bm9R7f9gJL"},
    {"course": "Law", "caption": "Constitutional Law Basics", "video_url": "https://youtu.be/4wMisE6C9i8?si=He8xwPihWCCVbwMZ"},
    {"course": "Law", "caption": "Criminal Law Introduction", "video_url": "https://youtu.be/eGM-eVP2Enc?si=WynYGYENenOOSfnc"},
    {"course": "Law", "caption": "Law of Contract", "video_url": "https://youtu.be/n6O7I6VYAQw?si=ZXy68EKWuOOs-ty8"},
    {"course": "Law", "caption": "Legal Research and Writing", "video_url": "https://youtu.be/4lMPQh3L4_k?si=_s-mTnVXJEFMh5nZ"},
    {"course": "Local Government & Development Studies", "caption": "What is Local Government?", "video_url": "https://youtu.be/4EZwU2hPZcA?si=WgLX8iT57vFy7_Ww"},
    {"course": "Local Government & Development Studies", "caption": "Development Theories", "video_url": "https://youtu.be/cqMsrKcLXrE?si=DdZX4OQkOQVes_8x"},
    {"course": "Local Government & Development Studies", "caption": "Community Development Practices", "video_url": "https://youtu.be/yFu0GZgQXkA?si=KNFnGVg9l5O-NdrY"},
    {"course": "Local Government & Development Studies", "caption": "Public Policy at Local Level", "video_url": "https://youtu.be/Zk7xXQ8QR5g?si=bfaUtsz3UyMBiVhC"},
    {"course": "Local Government & Development Studies", "caption": "Grassroots Governance", "video_url": "https://youtu.be/hBS8h0Crhw0?si=SgN-SyOhMqwB62g_"},
    {"course": "Marketing", "caption": "Marketing Basics", "video_url": "https://youtu.be/5sP_B7P1QE4?si=AQm1GfUBN6SCo89j"},
    {"course": "Marketing", "caption": "Digital Marketing Fundamentals", "video_url": "https://youtu.be/bixR-KIJKYM?si=ys1NT_4vHPWNTajm"},
    {"course": "Marketing", "caption": "Consumer Behavior", "video_url": "https://youtu.be/xY_a8nNvn-4?si=tkv_1szVMkSQK3Yj"},
    {"course": "Marketing", "caption": "Brand Management", "video_url": "https://youtu.be/0qd_Hk-42wU?si=FxPBHRlzssrvRghY"},
    {"course": "Marketing", "caption": "Marketing Research", "video_url": "https://youtu.be/KRk8D2zJ-ow?si=0FjCzXKAzK4Lt-mQ"},
    {"course": "Mass Communication", "caption": "What is Mass Communication?", "video_url": "https://youtu.be/J8Mv-bU_euM?si=KZ6WF-vQk-H8_l-D"},
    {"course": "Mass Communication", "caption": "Journalism Principles", "video_url": "https://youtu.be/2GAQyq4KDJU?si=9cujZ3Z2F01gZx1U"},
    {"course": "Mass Communication", "caption": "Broadcast Media Production", "video_url": "https://youtu.be/wH_0V0a8s4E?si=sA-PbwKlg51yd5b6"},
    {"course": "Mass Communication", "caption": "Public Relations Strategies", "video_url": "https://youtu.be/9LtUuk46H2g?si=DvDbfd7yCWJYlZgJ"},
    {"course": "Mass Communication", "caption": "Media Laws and Ethics", "video_url": "https://youtu.be/zWHLSG6_0qU?si=ZnFw8Kf7lg1bRr-g"},
    {"course": "Mathematics", "caption": "The Map of Mathematics", "video_url": "https://youtu.be/OmJ-4B-mS-Y?si=Se9-ZojVU7bLkYlW"},
    {"course": "Mathematics", "caption": "Calculus for Beginners", "video_url": "https://youtu.be/5qVYZ4pz8a4?si=S6Bjq4p7qEo1Np2O"},
    {"course": "Mathematics", "caption": "Linear Algebra Introduction", "video_url": "https://youtu.be/fNk_zzaMoSs?si=5PEdMwyYtptkQ2we"},
    {"course": "Mathematics", "caption": "Discrete Mathematics", "video_url": "https://youtu.be/2ZcH5pKqjJs?si=L3ny8u25U8-wkYAQ"},
    {"course": "Mathematics", "caption": "Statistics and Probability", "video_url": "https://youtu.be/MdHtK7CWpCQ?si=obt0owklXzHn38I6"},
    {"course": "Mechanical Engineering", "caption": "What is Mechanical Engineering?", "video_url": "https://youtu.be/G0N-7zTQK-Y?si=ypN2XZlgjJQbGLao"},
    {"course": "Mechanical Engineering", "caption": "Thermodynamics Fundamentals", "video_url": "https://youtu.be/Wk_a0GjFfBU?si=uacKPD2JQ9N1FAla"},
    {"course": "Mechanical Engineering", "caption": "Mechanics of Materials", "video_url": "https://youtu.be/QhYl-t3d1TI?si=mkOg87P8L1fnl9lG"},
    {"course": "Mechanical Engineering", "caption": "Fluid Mechanics Basics", "video_url": "https://youtu.be/T7wW3sFj6p8?si=Z7iQj0hHcjmZ86bj"},
    {"course": "Mechanical Engineering", "caption": "Machine Design Principles", "video_url": "https://youtu.be/gSLv8cG9mR8?si=albn6XH-VPHmhsfp"},
    {"course": "Medicine & Surgery", "caption": "A Day in the Life of a Med Student", "video_url": "https://youtu.be/bn1P_o4crk8?si=ssT6epC_mO4VlhWA"},
    {"course": "Medicine & Surgery", "caption": "Human Anatomy Overview", "video_url": "https://youtu.be/NB6idAXbXAQ?si=Er6Y_Lr5uzXm_31E"},
    {"course": "Medicine & Surgery", "caption": "Introduction to Physiology", "video_url": "https://youtu.be/0gZISs0Yy14?si=OepS9ZcyW0P8zVnT"},
    {"course": "Medicine & Surgery", "caption": "Pathology Fundamentals", "video_url": "https://youtu.be/hNar-QrjwQE?si=fAPLQH_h3PK_RQQs"},
    {"course": "Medicine & Surgery", "caption": "Clinical Skills for Beginners", "video_url": "https://youtu.be/cX_e2Yd4QfQ?si=9fTAN9THNW5gI89G"},
    {"course": "Microbiology", "caption": "Introduction to Microbiology", "video_url": "https://youtu.be/XIKq1_gxKqg?si=JgqJPD02S5rq-p8m"},
    {"course": "Microbiology", "caption": "Bacteriology: Study of Bacteria", "video_url": "https://youtu.be/nvJ0jSPdGc8?si=dLq-yMxDP4C7D1Ae"},
    {"course": "Microbiology", "caption": "Virology Basics", "video_url": "https://youtu.be/6oWLGxZqCwM?si=FdIqWXaDB3fK9dxW"},
    {"course": "Microbiology", "caption": "Immunology Fundamentals", "video_url": "https://youtu.be/CEOV-SCvMug?si=F93nIs-Pt36m3l8I"},
    {"course": "Microbiology", "caption": "Industrial Microbiology", "video_url": "https://youtu.be/SxHIl1P3VkU?si=WmdgrIyhM2qyFO_N"},
    {"course": "Music", "caption": "Music Theory in 16 Minutes", "video_url": "https://youtu.be/gvZ8h1xYbGE?si=GS2M7wKNQ_54kmui"},
    {"course": "Music", "caption": "Introduction to Music History", "video_url": "https://youtu.be/ZWKO_wy4fkc?si=c2tbRlD4nABHKKCr"},
    {"course": "Music", "caption": "Ear Training Basics", "video_url": "https://youtu.be/8jzD1iFGhKw?si=ArOQ97KqMYWhI1s2"},
    {"course": "Music", "caption": "Composition for Beginners", "video_url": "https://youtu.be/4lVq2Q8Fako?si=DnFP0nGqC-TNsvyr"},
    {"course": "Music", "caption": "World Music Overview", "video_url": "https://youtu.be/bE8d1SLR2QA?si=ZP7JjIwg-x5uhqwU"},
    {"course": "Nursing/Nursing Science", "caption": "Fundamentals of Nursing", "video_url": "https://youtu.be/x0TjV_0uhgI?si=k5GjzNlhScngs9VJ"},
    {"course": "Nursing/Nursing Science", "caption": "Anatomy & Physiology for Nurses", "video_url": "https://youtu.be/uBGl2BujkPQ?si=L_IAJ9ctFhqy5gUI"},
    {"course": "Nursing/Nursing Science", "caption": "Nursing Ethics and Law", "video_url": "https://youtu.be/P_YHXvMqYp0?si=RksFh1lQb5sDz3uQ"},
    {"course": "Nursing/Nursing Science", "caption": "Clinical Nursing Skills", "video_url": "https://youtu.be/qYt-1IxDP-w?si=5k-sfnxmd4Bqu-Ep"},
    {"course": "Nursing/Nursing Science", "caption": "Public Health Nursing", "video_url": "https://youtu.be/FY6PpX4GPgw?si=u_dLcgJ_HOMqVc5J"},
    {"course": "Peace Studies", "caption": "What is Peace Studies?", "video_url": "https://youtu.be/Qq6-Qqk8G8o?si=U5m8wWIDlM6D2INx"},
    {"course": "Peace Studies", "caption": "Conflict Analysis", "video_url": "https://youtu.be/dY5Aivc9As0?si=J-RC6yQ4hU3W_31N"},
    {"course": "Peace Studies", "caption": "Peacebuilding Strategies", "video_url": "https://youtu.be/FG-4Z1a9o_I?si=mAh8wqQi_1OK5wJf"},
    {"course": "Peace Studies", "caption": "Negotiation and Mediation", "video_url": "https://youtu.be/9LtUuk46H2g?si=DvDbfd7yCWJYlZgJ"},
    {"course": "Peace Studies", "caption": "Human Rights and Peace", "video_url": "https://youtu.be/nDgIVseTkuE?si=acflzUeN-XDrmDBz"},
    {"course": "Pharmacology", "caption": "Introduction to Pharmacology", "video_url": "https://youtu.be/8_U_rlKQAbs?si=KPlVTYq9dG9yA9Hn"},
    {"course": "Pharmacology", "caption": "Pharmacokinetics and Pharmacodynamics", "video_url": "https://youtu.be/0e9jLm_qmxY?si=HswS2DvmOnl_JSTx"},
    {"course": "Pharmacology", "caption": "Drug Discovery and Development", "video_url": "https://youtu.be/4wItKQo_UFs?si=wKY2I21K0WuxYKR-"},
    {"course": "Pharmacology", "caption": "Clinical Pharmacology", "video_url": "https://youtu.be/gs6RkF2wJZU?si=AcIqeS6vyJ5uS2vQ"},
    {"course": "Pharmacology", "caption": "Toxicology Basics", "video_url": "https://youtu.be/PrU1nG_t-y4?si=SDLOLtz6oij1rcQQ"},
    {"course": "Physical & Health Education", "caption": "Physical Education for Schools", "video_url": "https://youtu.be/8q1MxJxJZWA?si=7UVTdsR8XGLjMS-Y"},
    {"course": "Physical & Health Education", "caption": "Sports Science Fundamentals", "video_url": "https://youtu.be/Fa6qFpzM1Qo?si=58gsjMXJ9s7S-xkR"},
    {"course": "Physical & Health Education", "caption": "Exercise Physiology", "video_url": "https://youtu.be/7kG3cJhg3nU?si=J4-l8ttCwqCYiL7m"},
    {"course": "Physical & Health Education", "caption": "Health-Related Fitness", "video_url": "https://youtu.be/oZ3_Pb4YgWA?si=81M5UtYrwSddw0oU"},
    {"course": "Physical & Health Education", "caption": "Coaching Principles", "video_url": "https://youtu.be/GEjGFxuyOZ8?si=bl0e7F0QBvqR-hfv"},
    {"course": "Physics", "caption": "Physics for Beginners", "video_url": "https://youtu.be/0TckYjLvXQc?si=vK3y0tJwG75MNYZe"},
    {"course": "Physics", "caption": "Classical Mechanics", "video_url": "https://youtu.be/i7zqAc5oE7I?si=WQW0V2mAkM9FVLUn"},
    {"course": "Physics", "caption": "Electricity and Magnetism", "video_url": "https://youtu.be/Y5YKRq-lA_E?si=QUUph_M6v-CV6P5k"},
    {"course": "Physics", "caption": "Modern Physics: Relativity and Quantum", "video_url": "https://youtu.be/1fQEZGmdFmU?si=gg09a5bth_hYwIGl"},
    {"course": "Physics", "caption": "Thermodynamics and Statistical Physics", "video_url": "https://youtu.be/KW2_aU8Z4F4?si=8z4XrOmdqNJcNcn-"},
    {"course": "Physiology", "caption": "Introduction to Physiology", "video_url": "https://youtu.be/0gZISs0Yy14?si=OepS9ZcyW0P8zVnT"},
    {"course": "Physiology", "caption": "Neurophysiology: How the Brain Works", "video_url": "https://youtu.be/qPix_X-9t7E?si=Etke4kM11cc5gZua"},
    {"course": "Physiology", "caption": "Cardiovascular Physiology", "video_url": "https://youtu.be/DTGpBv4qLzI?si=0vY9_HvQK-5tPyQT"},
    {"course": "Physiology", "caption": "Respiratory Physiology", "video_url": "https://youtu.be/Gf9TQ8QlWBU?si=K56qI3ywM1gY65ym"},
    {"course": "Physiology", "caption": "Renal Physiology", "video_url": "https://youtu.be/l128tW1H5a8?si=YThHe8jJb3AlilPH"},
    {"course": "Political Science", "caption": "Political Ideology", "video_url": "https://youtu.be/j_k_k-bHigM?si=91mX0WkfJ5Dd5fQg"},
    {"course": "Political Science", "caption": "Comparative Politics", "video_url": "https://youtu.be/sEnM3pG6Dwg?si=Kp5nuMVlqFj3f5y9"},
    {"course": "Political Science", "caption": "Political Theory", "video_url": "https://youtu.be/fKbR8e3lJCo?si=RBXjPURvAPnYzxny"},
    {"course": "Political Science", "caption": "Public Administration and Governance", "video_url": "https://youtu.be/CHkQpL_kpJA?si=oBNjIF28kfE3h3CR"},
    {"course": "Political Science", "caption": "International Political Economy", "video_url": "https://youtu.be/0jVq7NM0tFI?si=KWrhKtUTwRVvWqLv"},
    {"course": "Portuguese/English", "caption": "Learn Portuguese Basics", "video_url": "https://youtu.be/Y4CqkHrB6FA?si=U0cetd9h3_glTCqQ"},
    {"course": "Portuguese/English", "caption": "Portuguese Grammar Introduction", "video_url": "https://youtu.be/pOh1v7LfC0I?si=H8D93EDKvQc7WCCY"},
    {"course": "Portuguese/English", "caption": "Portuguese-English Translation", "video_url": "https://youtu.be/oiBfW-JDTgU?si=wK1xWKNQK9mYk9bZ"},
    {"course": "Portuguese/English", "caption": "Lusophone Cultures", "video_url": "https://youtu.be/yRq3NIlZ7K0?si=5sdd97U0U1_2ExM6"},
    {"course": "Portuguese/English", "caption": "Business Portuguese", "video_url": "https://youtu.be/Q5eZgMmZsgA?si=KLYTnO7ub8iCqMCS"},
    {"course": "Psychology", "caption": "Intro to Psychology", "video_url": "https://youtu.be/vo4pMVb0R6M?si=efyLmSq2gy_T4wlb"},
    {"course": "Psychology", "caption": "Cognitive Psychology", "video_url": "https://youtu.be/Q6aY2q_-j_4?si=RYlQwptCPj_ALQoX"},
    {"course": "Psychology", "caption": "Developmental Psychology", "video_url": "https://youtu.be/8nz2dtv--ok?si=ejWNltDrt7G_r1FC"},
    {"course": "Psychology", "caption": "Social Psychology", "video_url": "https://youtu.be/Bf1zwcFGRn8?si=n-7ZGl6iqrzn_fsy"},
    {"course": "Psychology", "caption": "Abnormal Psychology", "video_url": "https://youtu.be/wuhJ-GkRRQc?si=jDb6Kj9cROo4rtk_"},
    {"course": "Public Administration", "caption": "What is Public Administration?", "video_url": "https://youtu.be/WhU3CjZ8Ews?si=j3f7u73r6TShyuG6"},
    {"course": "Public Administration", "caption": "Bureaucracy and Public Policy", "video_url": "https://youtu.be/I6hYp_ZBj6s?si=cCBUqHv7G8NJtTx4"},
    {"course": "Public Administration", "caption": "Public Financial Management", "video_url": "https://youtu.be/_Uw1FpKXk3g?si=fqElbKq6cfDgtFgd"},
    {"course": "Public Administration", "caption": "Ethics in Public Service", "video_url": "https://youtu.be/ZZOmnUVool0?si=IdpAmqbw6Zkf42Pk"},
    {"course": "Public Administration", "caption": "Comparative Public Administration", "video_url": "https://youtu.be/0BwQiCJb1ik?si=JgQ4i2e5GON_7xIB"},
    {"course": "Sociology", "caption": "What is Sociology?", "video_url": "https://youtu.be/YnCJU6PaCio?si=3nBplmJyQj87uqZG"},
    {"course": "Sociology", "caption": "Sociological Theories", "video_url": "https://youtu.be/Dy7h9wGcfE8?si=2zTnsEidzEDQmy5k"},
    {"course": "Sociology", "caption": "Sociology of the Family", "video_url": "https://youtu.be/zvBknw8nQq0?si=27X72rhzB1q-k5AP"},
    {"course": "Sociology", "caption": "Social Stratification and Inequality", "video_url": "https://youtu.be/SlCfxL-Zt5k?si=zSf5CMxKf3aP3eUI"},
    {"course": "Sociology", "caption": "Urban Sociology", "video_url": "https://youtu.be/mb8fLbCCc5U?si=Y9qPz_wXhWr81I8K"},
    {"course": "Teacher Education Science", "caption": "Becoming a Science Teacher", "video_url": "https://youtu.be/u7jN1O4hNsw?si=BcE77Z2W8b7QQZxS"},
    {"course": "Teacher Education Science", "caption": "Science Pedagogy", "video_url": "https://youtu.be/MyzUF7e6_EM?si=gYpxRZcLjVpPv3C1"},
    {"course": "Teacher Education Science", "caption": "Lab Safety for Science Teachers", "video_url": "https://youtu.be/VRWRmIEHr3A?si=Z5fRZfWbjjL6-L_l"},
    {"course": "Teacher Education Science", "caption": "Inquiry-Based Science Teaching", "video_url": "https://youtu.be/lEqlMqVKBSg?si=nnGJnta5sXz_BX_T"},
    {"course": "Teacher Education Science", "caption": "Assessment in Science Education", "video_url": "https://youtu.be/L_3dcpWmM4w?si=FR6MF_sKiz-THt-v"},
    {"course": "Technological Management", "caption": "Technology Management Overview", "video_url": "https://youtu.be/SxHIl1P3VkU?si=WmdgrIyhM2qyFO_N"},
    {"course": "Technological Management", "caption": "Innovation Management", "video_url": "https://youtu.be/4wItKQo_UFs?si=wKY2I21K0WuxYKR-"},
    {"course": "Technological Management", "caption": "Strategic Management of Technology", "video_url": "https://youtu.be/1wK1IxrYmgA?si=7PM2nYFdg2Wyi96g"},
    {"course": "Technological Management", "caption": "Technology Forecasting", "video_url": "https://youtu.be/0BwQiCJb1ik?si=JgQ4i2e5GON_7xIB"},
    {"course": "Technological Management", "caption": "R&D Management", "video_url": "https://youtu.be/KRk8D2zJ-ow?si=0FjCzXKAzK4Lt-mQ"},
    {"course": "Technology & Vocational Education", "caption": "Technical and Vocational Education", "video_url": "https://youtu.be/5Z5dOXl_1AU?si=GvqHnP7QiyB_Q-Ag"},
    {"course": "Technology & Vocational Education", "caption": "Curriculum for TVET", "video_url": "https://youtu.be/5SESVVK7Evg?si=O75bnvSpwWvFd55P"},
    {"course": "Technology & Vocational Education", "caption": "Skills Training and Development", "video_url": "https://youtu.be/9kRgV7r6_34?si=7a4r8fE1A3f3mpvY"},
    {"course": "Technology & Vocational Education", "caption": "Entrepreneurship in TVET", "video_url": "https://youtu.be/4OTm0k_bfq4?si=6V_mJgY9cMEWem1A"},
    {"course": "Technology & Vocational Education", "caption": "Assessment in Vocational Education", "video_url": "https://youtu.be/_6-GXvO3zsw?si=quq8a8zX0Hvy05mD"},
    {"course": "Transport Management Technology", "caption": "Introduction to Transport Management", "video_url": "https://youtu.be/9kRgV7r6_34?si=7a4r8fE1A3f3mpvY"},
    {"course": "Transport Management Technology", "caption": "Logistics and Supply Chain Management", "video_url": "https://youtu.be/1EzLjgCJlgc?si=zOCbH5lINo2t8eHm"},
    {"course": "Transport Management Technology", "caption": "Transportation Economics", "video_url": "https://youtu.be/GD7C88TgRqc?si=-QdQyA4pehx1MPSm"},
    {"course": "Transport Management Technology", "caption": "Fleet Management", "video_url": "https://youtu.be/HQM5CjP01jA?si=w49nY3eD9b86CYoc"},
    {"course": "Transport Management Technology", "caption": "Sustainable Transport Systems", "video_url": "https://youtu.be/6lU4P6yn9q4?si=EFu6zHQlUdr2Yad-"},
    {"course": "Yoruba & Communication Arts", "caption": "Learn Yoruba Language", "video_url": "https://youtu.be/20M6Vv_fIqk?si=AM1xxU_3RNO5T7xX"},
    {"course": "Yoruba & Communication Arts", "caption": "Yoruba Grammar and Syntax", "video_url": "https://youtu.be/7B-6gPNMkIw?si=m7f6M40pGmGpDpFW"},
    {"course": "Yoruba & Communication Arts", "caption": "Yoruba Oral Literature", "video_url": "https://youtu.be/tqo8xVlkru8?si=3rT_vQ78ezWeUu3u"},
    {"course": "Yoruba & Communication Arts", "caption": "Yoruba Media and Communication", "video_url": "https://youtu.be/lwW-ZUvDZok?si=kS17_tsJq0d0CTYs"},
    {"course": "Yoruba & Communication Arts", "caption": "Contemporary Yoruba Culture", "video_url": "https://youtu.be/9bfh1CS-vGc?si=G8r7a2rMBJ7L9ng2"},
]


    matching = [r for r in all_reels if r["course"] == course] if course else all_reels
    return jsonify({"reels": matching})

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
# MEMORY SYSTEM DEBUG ROUTE NEUTRALIZED
# ============================================
@app.route('/debug/memory')
@login_required
def debug_memory():
    """Debug endpoint for memory system - DUMMY VERSION."""
    username = session['user']['username']
    
    debug_info = {
        'user': {
            'username': username,
            'user_key': 'MEMORY_SYSTEM_DISABLED'
        },
        'session': {
            'last_file_id': session.get('last_file_id'),
            'last_file_type': session.get('last_file_type'),
            'last_upload_time': session.get('last_upload_time')
        },
        'pdf_memory': {
            'cache_stats': {"files": 0, "chunks": 0, "users": 0},
            'health_check': True,
            'user_in_cache': False,
            'user_file_count': 0,
            'user_files': []
        },
        'memory_layers': ["GENERAL"],
        'chat_context': "Memory system disabled for deployment",
        'memory_router': "Memory system disabled for deployment"
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
    print("🌟 Complete Educational Platform")
    print("⚠️  MEMORY SYSTEM DISABLED for deployment stability")
    print(f"{'='*60}")
    print("✅ Educational Platform Features:")
    print("   - User Authentication")
    print("   - AI Tutor (Nelavista)")
    print("   - PDF & Image Processing (OCR, Vision AI)")
    print("   - Study Materials & PDFs")
    print("   - Course Reels & Videos")
    print("   - CBT Test System")
    print("\n✅ Live Meeting Features:")
    print("   - Full Mesh WebRTC Video Calls")
    print("   - Teacher Authority System")
    print("   - Real-time Collaboration")
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



