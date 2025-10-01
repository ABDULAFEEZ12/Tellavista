from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, flash
)
import os
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import json
import requests
from bs4 import BeautifulSoup
from hashlib import sha256
import openai

# Load environment variables
load_dotenv()

# ----------------------
# Flask + DB Setup
# ----------------------
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ----------------------
# OpenAI / OpenRouter API
# ----------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set!")

# ----------------------
# Models
# ----------------------
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class UserQuestions(db.Model):
    __tablename__ = 'user_questions'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())

# ----------------------
# Helpers
# ----------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def save_question_and_answer(username, question, answer):
    entry = UserQuestions(username=username, question=question, answer=answer)
    db.session.add(entry)
    db.session.commit()

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

# ----------------------
# Routes
# ----------------------
@app.route('/')
def index():
    return render_template('index.html')

# -------- SIGNUP --------
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not email or not password:
            flash('Please fill out all fields.')
            return redirect(url_for('signup'))

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash('Username or email already exists.')
            return redirect(url_for('signup'))

        new_user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.session.add(new_user)
        db.session.commit()

        flash('Signup successful! Please login.')
        return redirect(url_for('login'))

    return render_template('signup.html')

# -------- LOGIN --------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username_or_email = request.form.get('username_or_email', '').strip()
        password = request.form.get('password', '').strip()

        user = User.query.filter(
            (db.func.lower(User.username) == username_or_email.lower()) |
            (db.func.lower(User.email) == username_or_email.lower())
        ).first()

        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['user'] = {'username': user.username, 'email': user.email}
            flash('Logged in successfully!')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password.')
            return redirect(url_for('login'))

    return render_template('login.html')

# -------- LOGOUT --------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# -------- MY QUESTIONS --------
@app.route('/my-questions')
@login_required
def my_questions():
    username = session['user']['username']
    questions = UserQuestions.query.filter_by(username=username).order_by(UserQuestions.timestamp.desc()).all()
    return render_template('my_questions.html', questions=questions)

# -------- PROFILE --------
@app.route('/profile')
@login_required
def profile():
    user = session.get('user', {})
    return render_template('profile.html', user=user)

# -------- TALK TO TELAVISTA --------
@app.route('/talk-to-tellavista', methods=['GET', 'POST'])
def talk_to_tellavista():
    if request.method == 'GET':
        return render_template('talk_to_tellavista.html')

    try:
        data = request.get_json()
        history = data.get('history', [])
        username = data.get('username', 'Guest')

        user_question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), '')
        if not user_question:
            return jsonify({'error': 'No question found'}), 400

        # Check cache
        cache_key = sha256(json.dumps(history, sort_keys=True).encode()).hexdigest()
        if cache_key in question_cache:
            answer = question_cache[cache_key]
            save_question_and_answer(username, user_question, answer)
            return jsonify({'choices':[{'message':{'role':'assistant','content':answer}}]})

        # Call OpenRouter AI
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "openai/gpt-4-turbo",
            "messages": [{"role":"system","content":"You are Tellavista, an AI tutor."}] + history,
            "stream": False
        }

        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        result = resp.json()
        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

        question_cache[cache_key] = answer
        save_cache()
        save_question_and_answer(username, user_question, answer)

        return jsonify({'choices':[{'message':{'role':'assistant','content':answer}}]})
    except Exception as e:
        return jsonify({'choices':[{'message':{'role':'assistant','content':f"Error: {str(e)}"}}]})

# -------- MATERIALS --------
@app.route('/materials')
@login_required
def materials():
    all_courses = ["Python", "Data Science", "AI Basics", "Math", "Physics"]
    selected_course = request.args.get("course")
    materials = []

    if selected_course:
        materials = [
            {"title": f"{selected_course} Intro", "description": f"Basics of {selected_course}", "link": "https://www.youtube.com"},
            {"title": f"{selected_course} Tutorial", "description": f"Complete guide on {selected_course}", "link": "https://www.youtube.com"}
        ]

    return render_template("materials.html", courses=all_courses, selected_course=selected_course, materials=materials)

# -------- AI MATERIALS --------
@app.route('/ai/materials')
def ai_materials():
    topic = request.args.get("topic")
    level = request.args.get("level")
    department = request.args.get("department")
    goal = request.args.get("goal") or "general"

    if not topic or not level or not department:
        return jsonify({"error": "Missing topic, level, or department"}), 400

    prompt = f"Teach a {level} student in {department} about {topic}, goal: {goal}."

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-1106-preview",
            messages=[{"role":"user","content":prompt}]
        )
        explanation = response.choices[0].message.content
    except Exception as e:
        explanation = f"Error: {str(e)}"

    return jsonify({"topic": topic, "explanation": explanation})

# -------- RUN APP --------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        app.run(debug=True)
