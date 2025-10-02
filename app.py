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

# --- Cache Setup ---
CACHE_FILE = "tellavista_cache.json"
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            question_cache = json.load(f)
    except json.JSONDecodeError:
        question_cache = {}
else:
    question_cache = {}

def save_cache():
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(question_cache, f, indent=2, ensure_ascii=False)

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
    return render_template('talk-to-tellavista.html')

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

        # --- Detect Chatty vs Solution Mode ---
        chatty_keywords = ["hi", "hello", "hey", "how are you", "good morning", "good evening"]
        solution_triggers = ["?", "solve", "calculate", "explain", "why", "how", "find", "prove"]

        # Default mode = Chatty
        if any(word in user_question.lower() for word in chatty_keywords) and not any(
            kw in user_question.lower() for kw in solution_triggers
        ):
            mode = "chatty"
        else:
            mode = "solution"

        # --- System prompt changes depending on mode ---
        if mode == "chatty":
            system_prompt = (
                "You are Tellavista, a friendly and motivational AI tutor. "
                "For casual chats:\n"
                "- Reply in clean HTML using <p> only.\n"
                "- Be warm, short, and natural like a human friend.\n"
                "- Use emojis for friendliness.\n"
                "- DO NOT structure into steps or Final Answer.\n"
                "- Example: <p>👋 Hey! Great to see you. What's on your mind today?</p>"
            )
        else:
            system_prompt = (
                "You are Tellavista, a motivational AI tutor. "
                "Always respond in clean HTML for problem-solving. "
                "Format answers like this:\n\n"
                "<p><strong>Intro:</strong> Short motivational opener.</p>\n"
                "<h3>🔹 Step 1:</h3>\n"
                "<p>Explain clearly with short sentences or bullets.</p>\n"
                "<h3>🔹 Step 2:</h3>\n"
                "<p>Keep guiding step by step like a tutor.</p>\n"
                "<hr>\n"
                "<h2>✅ Final Answer</h2>\n"
                "<pre><strong>🎯 Show the final solution here, copyable</strong></pre>\n\n"
                "⚡ Rules:\n"
                "- Do NOT start with 'Tellavista Solution'.\n"
                "- Use <h2>, <h3>, <p>, <ul>, <li> for clarity.\n"
                "- Final Answer must be inside <pre> so it's easy to copy.\n"
                "- Use emojis for friendliness."
            )

        # --- Call OpenRouter API ---
        OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
        if not OPENROUTER_API_KEY:
            return jsonify({'error': 'OpenRouter API key not configured'}), 500

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "openai/gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_question}
            ]
        }

        response = requests.post("https://openrouter.ai/api/v1/chat/completions",
                                 headers=headers, data=json.dumps(payload))

        if response.status_code != 200:
            print(f"❌ OpenRouter API error: {response.text}")
            return jsonify({'error': 'AI service failed. Try again later.'}), 500

        api_result = response.json()
        answer = api_result["choices"][0]["message"]["content"]

        # Save Q&A to database (solution mode only)
        try:
            if mode == "solution":
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

# Create default user after initialization
create_default_user()

if __name__ == '__main__':
    print("🚀 Tellavista starting...")
    print(f"📊 Database URL: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print("🔑 Default user: test / test123")
    print("🌐 Server running on http://localhost:5000")
    app.run(debug=True, port=5000)

