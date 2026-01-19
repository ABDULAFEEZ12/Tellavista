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
from memory.pdf_handler import PDFMemory
from functools import wraps
import uuid
import requests
from dotenv import load_dotenv
import random
from difflib import get_close_matches
from bs4 import BeautifulSoup
import traceback

# Memory system imports
from memory.pdf_handler import PDFMemory
from memory.chat_context import ChatContext
from memory.memory_router import MemoryRouter
from memory.layers import MemoryLayer as MemoryLayers

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
# Memory System Initialization
# ============================================

# Create separate directory for PDF memory storage
PDF_MEMORY_DIR = 'pdf_memory'
os.makedirs(PDF_MEMORY_DIR, exist_ok=True)

# Create GLOBAL PDF memory instance
pdf_memory = PDFMemory(PDF_MEMORY_DIR)

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
# Memory System Helper Functions
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
    Build Nelavista system prompt based on memory context.
    Enforces Nelavista analytical expert behavior.
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
    )

    if memory_layer == MemoryLayers.PDF:
        return (
            f"{base_prompt}"
            "DOCUMENT CONTEXT:\n"
            f"{context[:1500]}\n\n"
            "DOCUMENT-SPECIFIC INSTRUCTIONS:\n"
            "- Rely primarily on the document content.\n"
            "- Reference specific sections or pages when possible.\n"
            "- State clearly if the document lacks the answer.\n"
            "- Do not hallucinate or invent content.\n"
            "- Quote directly when appropriate.\n\n"
            "ENDING:\n"
            "- End naturally after the explanation. Do not add summaries beyond the TL;DR."
        )

    if memory_layer == MemoryLayers.HISTORY:
        return (
            f"{base_prompt}"
            "CONVERSATION HISTORY:\n"
            f"{context}\n\n"
            "HISTORY-SPECIFIC INSTRUCTIONS:\n"
            "- Build logically on prior explanations.\n"
            "- Maintain consistency with earlier answers.\n"
            "- Correct any previous errors when necessary.\n"
            "- Reference relevant earlier points when useful.\n\n"
            "ENDING:\n"
            "- End naturally after the explanation. Do not add summaries beyond the TL;DR."
        )

    if memory_layer == MemoryLayers.PROFILE:
        return (
            f"{base_prompt}"
            "STUDENT PROFILE:\n"
            f"{context}\n\n"
            "PROFILE-SPECIFIC INSTRUCTIONS:\n"
            "- Adapt explanation depth to the student’s academic level.\n"
            "- Use examples relevant to the student’s field or department.\n"
            "- Keep explanations academically grounded and precise.\n\n"
            "ENDING:\n"
            "- End naturally after the explanation. Do not add summaries beyond the TL;DR."
        )

    # GENERAL (no special context)
    return (
        f"{base_prompt}"
        "GENERAL INSTRUCTIONS:\n"
        "- Provide accurate, structured academic explanations.\n"
        "- Use relevant examples only when they add clarity.\n"
        "- Maintain a logical flow from premises to conclusions.\n\n"
        "ENDING:\n"
        "- End naturally after the explanation. Do not add summaries beyond the TL;DR."
    )

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
    """
    # Fallback message to use in case of any failure
    GRACEFUL_FALLBACK = "I'm having a little trouble answering right now, but please try again."
    
    try:
        # Get user info
        username = session['user']['username']
        user_id = session.get('user_id')
        
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
        
        # Check if we should answer from PDF context or general knowledge
        answer_from_pdf = False
        pdf_context = ""
        
        if has_pdfs and file_texts:
            # Extract and clean PDF context
            pdf_context = "\n\n".join(file_texts)
            
            # Simple keyword check - could be enhanced with embeddings
            # For now, we'll assume PDF contains relevant info if it has content
            if pdf_context and len(pdf_context) > 50:
                answer_from_pdf = True
            else:
                answer_from_pdf = False
        
        # Prepare context for memory system
        memory_context = ""
        history_context = ""
        profile_context = ""
        memory_layer = MemoryLayers.GENERAL
        
        try:
            # Get profile context if available
            with app.app_context():
                profile = UserProfile.query.filter_by(username=username).first()
                if profile:
                    profile_context = ChatContext.get_profile_context(profile.to_dict())
                
                # Route to appropriate memory layer
                router = MemoryRouter(pdf_context, history_context, profile_context)
                context, memory_layer = router.route()
                memory_context = context
        except Exception:
            # Memory system failed, use basic context
            memory_context = pdf_context if answer_from_pdf else ""
            memory_layer = MemoryLayers.GENERAL
        
        # Build system prompt with memory context
        system_prompt = build_prompt_with_context(memory_context, memory_layer)
        
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
                    memory_layer=memory_layer
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
                        'last_login': user.last_login.strftime('%Y-%m-%d %H:%M:%S')
                    }
                    
                    # ✅ CRITICAL: Store user_id in session for memory system
                    session['user_id'] = user.id
                    
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
    # Clear PDF memory for this user
    username = session.get('user', {}).get('username')
    user_id = session.get('user_id')
    if username:
        pdf_memory.clear_user_cache(username, user_id)
    
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
    """Handle file upload with memory system integration."""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected"}), 400
    
    username = session['user']['username']
    user_id = session.get('user_id')
    
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
            
            # Store in PDF memory
            pdf_memory.extract_and_store(file_data, username, file_id, user_id)
            
            # Store references in session
            session['last_file_id'] = file_id
            session['last_file_type'] = 'pdf'
            session['last_file_name'] = filename
            session['last_upload_time'] = time.time()
            
            # Get preview from memory
            user_key = pdf_memory._get_user_key(username, user_id)
            chunks = pdf_memory._load_chunks(user_key, file_id)
            preview = "\n".join(chunks)[:300] + "..." if chunks else "PDF processed successfully"
            
            debug_print(f"📄 PDF stored in memory: {filename}, Size: {file_size} bytes")
            
            return jsonify({
                "success": True,
                "message": "PDF uploaded and processed",
                "preview": preview,
                "type": "pdf",
                "filename": file.filename,
                "size_kb": round(file_size / 1024, 1),
                "has_text": bool(chunks),
                "file_id": file_id,
                "cache_stats": pdf_memory.get_cache_stats()
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
    """Legacy endpoint that redirects to /ask_with_files for compatibility."""
    try:
        # Get data from request
        data = request.get_json() or {}
        message = data.get('message', '').strip()
        
        if not message:
            return jsonify({'error': 'No question provided'}), 400
        
        # Create form data for /ask_with_files
        from werkzeug.datastructures import ImmutableMultiDict
        
        # Prepare request for /ask_with_files
        with app.test_request_context('/ask_with_files', method='POST'):
            # Create form data
            form_data = ImmutableMultiDict([
                ('message', message),
                ('history', json.dumps([]))  # Empty history for compatibility
            ])
            
            # Call the main handler
            return ask_with_files()
            
    except Exception as e:
        debug_print(f"❌ Error in /ask: {e}")
        return jsonify({
            "success": True,
            "answer": "Sorry, an error occurred. Please use the /ask_with_files endpoint."
        })

# ============================================
# Memory Management Endpoints
# ============================================
@app.route('/memory/stats', methods=['GET'])
@login_required
def get_memory_stats():
    """Get memory system statistics."""
    try:
        username = session['user']['username']
        user_id = session.get('user_id')
        user_key = pdf_memory._get_user_key(username, user_id)
        
        user_files = []
        if user_key in pdf_memory.cache:
            for file_id in pdf_memory.cache[user_key]:
                chunks = pdf_memory.cache[user_key][file_id]
                user_files.append({
                    'file_id': file_id,
                    'chunk_count': len(chunks),
                    'total_chars': sum(len(chunk) for chunk in chunks)
                })
        
        return jsonify({
            "success": True,
            "user_stats": {
                "username": username,
                "user_id": user_id,
                "file_count": len(user_files),
                "files": user_files
            },
            "system_stats": pdf_memory.get_cache_stats(),
            "health": pdf_memory.health_check(),
            "memory_layers": MemoryLayers.get_all_metadata()
        })
    except Exception as e:
        debug_print(f"❌ Error getting memory stats: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/memory/clear', methods=['POST'])
@login_required
def clear_memory():
    """Clear user's memory cache."""
    try:
        username = session['user']['username']
        user_id = session.get('user_id')
        
        # Clear specific file or all files
        file_id = request.json.get('file_id') if request.is_json else None
        
        if file_id:
            pdf_memory.clear_file_cache(username, file_id, user_id)
            message = f"Cleared file cache: {file_id}"
        else:
            pdf_memory.clear_user_cache(username, user_id)
            message = "Cleared all memory cache"
        
        # Clear session references
        session.pop('last_file_id', None)
        session.pop('last_file_type', None)
        session.pop('last_upload_time', None)
        session.pop('last_image_base64', None)
        
        return jsonify({
            "success": True,
            "message": message,
            "cache_stats": pdf_memory.get_cache_stats()
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
        username = session['user']['username']
        user_id = session.get('user_id')
        
        # Clear from memory system if it's a PDF
        file_id = session.get('last_file_id')
        if file_id and session.get('last_file_type') == 'pdf':
            pdf_memory.clear_file_cache(username, file_id, user_id)
        
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
        {"course": "Accountancy", "caption": "Introduction to Accounting", "video_url": "https://youtu.be/Gua2Bo_G-J0?si=FNnNZBbmBh0yqvrk"},
        {"course": "Zoology", "caption": "Animal Classification", "video_url": "https://example.com/videos/zoology1.mp4"},
        {"course": "Accountancy", "caption": "Financial Statements Basics", "video_url": "https://youtu.be/fb7YCVR5fIU?si=XWozkxGoBV2HP2HW"},
        {"course": "Accountancy", "caption": "Management Accounting Overview", "video_url": "https://youtu.be/qISkyoiGHcI?si=BKRnkFfl-fqKXgLG"},
        {"course": "Accountancy", "caption": "Auditing Principles", "video_url": "https://youtu.be/27gabbJQZqc?si=rsOLmkD2QXOoxSoi"},
        {"course": "Accountancy", "caption": "Taxation Fundamentals", "video_url": "https://youtu.be/Cox8rLXYAGQ?si=CvKUaPuPJOxPb6cr"},
        {"course": "Accountancy", "caption": "Learn Accounting in under 5 hours", "video_url": "https://youtu.be/gPBhGkBN30s?si=bUYfaccZPlBni3aZ"},
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
# Memory System Debug Route
# ============================================
@app.route('/debug/memory')
@login_required
def debug_memory():
    """Debug endpoint for memory system."""
    username = session['user']['username']
    user_id = session.get('user_id')
    user_key = pdf_memory._get_user_key(username, user_id)
    
    debug_info = {
        'user': {
            'username': username,
            'user_id': user_id,
            'user_key': user_key
        },
        'session': {
            'last_file_id': session.get('last_file_id'),
            'last_file_type': session.get('last_file_type'),
            'last_upload_time': session.get('last_upload_time')
        },
        'pdf_memory': {
            'cache_stats': pdf_memory.get_cache_stats(),
            'health_check': pdf_memory.health_check(),
            'user_in_cache': user_key in pdf_memory.cache,
            'user_file_count': len(pdf_memory.cache.get(user_key, {})),
            'user_files': list(pdf_memory.cache.get(user_key, {}).keys()) if user_key in pdf_memory.cache else []
        },
        'memory_layers': MemoryLayers.get_all_metadata(),
        'chat_context': ChatContext.get_context_stats(),
        'memory_router': MemoryRouter.get_default_config()
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
    print("🌟 Complete Educational Platform with Memory System")
    print("🧠 INTEGRATED MEMORY SYSTEM:")
    print("   - PDF Memory: Document extraction and storage")
    print("   - Chat Context: History relevance scoring")
    print("   - Memory Router: Intelligent context selection")
    print("   - Profile Context: Personalized responses")
    print(f"{'='*60}")
    print("✅ Educational Platform Features:")
    print("   - User Authentication with Memory Integration")
    print("   - AI Tutor (Nelavista) with Multi-Context Memory")
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
    print("🧠 Memory debug: http://localhost:5000/debug/memory")
    print("👨‍🏫 Teacher test: http://localhost:5000/live_meeting/teacher")
    print("👨‍🎓 Student test: http://localhost:5000/live_meeting")
    print("🎓 Platform login: http://localhost:5000/login (test/test123)")
    print(f"{'='*60}")
    print("\n⚠️  IMPORTANT: Install required packages:")
    print("   pip install PyPDF2 pdfplumber Pillow pytesseract openai")
    print(f"{'='*60}\n")
    
    # Print memory system status
    print("🧠 Memory System Initialization:")
    print(f"   PDF Memory Directory: {PDF_MEMORY_DIR}")
    print(f"   Upload Directory: {UPLOAD_FOLDER}")
    print(f"   Memory Layers: {', '.join(MemoryLayers.get_primary_layers())}")
    print(f"   Cache Stats: {pdf_memory.get_cache_stats()}")
    print(f"{'='*60}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=DEBUG_MODE)

