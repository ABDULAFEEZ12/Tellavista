from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, flash
)
import os
import json
from dotenv import load_dotenv
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import requests
from bs4 import BeautifulSoup
import openai
from functools import wraps
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configurations
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SECRET_KEY'] = os.getenv('MY_SECRET', 'fallback_secret_for_dev')

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

from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy(app)

# --- Models ---
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

# --- Helper Functions ---
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

# --- Initialize database ---
print("🚀 Initializing database...")
if init_database():
    print("✅ Database initialized successfully")
else:
    print("❌ Database initialization failed, but continuing...")

# --- Routes ---
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

@app.route('/talk-to-tellavista')
@login_required
def talk_to_tellavista():
    return render_template('talk_to_tellavista.html')

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

        print(f"🤖 Processing question from {username}: {user_question}")

        # Simple response for now
        answer = f"Hello {username}! I'm Tellavista, your AI tutor. You asked: '{user_question}'. I'm here to help you learn!"

        # Save Q&A to database
        try:
            new_q = UserQuestions(username=username, question=user_question, answer=answer)
            db.session.add(new_q)
            db.session.commit()
        except Exception as e:
            print(f"❌ Error saving question to database: {e}")
            db.session.rollback()

        return jsonify({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": answer
                }
            }]
        })

    except Exception as e:
        print(f"Error in /ask route: {str(e)}")
        return jsonify({
            'error': 'Failed to process your question. Please try again.'
        }), 500

@app.route('/about')
def about():
    return render_template('about.html')

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

# Create default user after initialization
create_default_user()

if __name__ == '__main__':
    print("🚀 Tellavista starting...")
    print(f"📊 Database URL: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print("🔑 Default user: test / test123")
    print("🌐 Server running on http://localhost:5000")
    app.run(debug=True, port=5000)
