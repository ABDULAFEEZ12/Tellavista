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

@app.route('/talk-to-tellavista', methods=['GET', 'POST'])
def talk_to_tellavista():
    if request.method == 'GET':
        return render_template('talk_to_tellavista.html')  # For browser visits

    # POST logic for AJAX or frontend
    try:
        data = request.get_json()
        history = data.get('history', [])
        username = data.get('username', 'Guest')

        # You can later integrate OpenAI here
        reply = f"Hello {username}, I'm Telavista! How can I assist you today?"

        return jsonify({
            "choices": [
                {"message": {"content": reply}}
            ]
        })
    except Exception as e:
        return jsonify({
            "choices": [
                {"message": {"content": f"❌ Error: {str(e)}"}}
            ]
        })

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/memory', methods=['POST'])
def save_memory():
    session['theme'] = request.form.get('theme')
    session['language'] = request.form.get('language')
    session['notifications'] = 'notifications' in request.form
    return redirect('/settings')

@app.route('/telavista/memory', methods=['POST'])
def telavista_save_memory():
    # Later use a real DB/session
    print("Saving Telavista memory!")
    return redirect('/settings')

# ------------------ MATERIALS PAGE ------------------
@app.route('/materials')
@login_required
def materials():
    all_courses = ["Python", "Data Science", "AI Basics", "Math", "Physics"]
    selected_course = request.args.get("course")
    materials = []

    if selected_course:
        # Simulated fallback materials
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


# ------------------ API: BASIC MATERIALS ONLY ------------------
@app.route('/api/materials')
def get_study_materials():
    query = request.args.get("q", "python")  # Default to 'python' if not provided

    # 1. Fetch PDFs from PDFDrive
    pdfs = []
    try:
        pdf_html = requests.get(f"https://www.pdfdrive.com/search?q={query}", headers={"User-Agent": "Mozilla/5.0"}).text
        soup = BeautifulSoup(pdf_html, 'html.parser')
        for book in soup.select('.file-left')[:5]:
            title = book.select_one('img')['alt']
            link = "https://www.pdfdrive.com" + book.parent['href']
            pdfs.append({'title': title, 'link': link})
    except Exception as e:
        pdfs = [{"error": str(e)}]

    # 2. Fetch Books from Open Library
    books = []
    try:
        ol_data = requests.get(f"https://openlibrary.org/search.json?q={query}").json()
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
# ------------------ API: AI + MATERIALS ------------------
# ✅ Function to detect academic books
def is_academic_book(title, topic, department):
    title_lower = title.lower()
    topic_lower = topic.lower()
    department_lower = department.lower()

    # Keywords typical of textbooks
    academic_keywords = [
        "principles", "fundamentals", "introduction", "basics", "theory",
        "textbook", "manual", "engineering", "mathematics", "analysis",
        "guide", "mechanics", "accounting", "algebra", "economics", "physics",
        "statistics", topic_lower, department_lower
    ]

    # Keywords to block non-academic fiction
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

# ✅ Your smart study route
@app.route('/ai/materials')
def ai_materials():
    topic = request.args.get("topic")
    level = request.args.get("level")
    department = request.args.get("department")
    goal = request.args.get("goal")
    
    if not goal:
        goal = "general"

    if not topic or not level or not department:
        return jsonify({"error": "Missing one or more parameters: topic, level, department"}), 400

    # AI Explanation
    prompt = f"""
    You're an educational AI helping a {level} student in the {department} department.
    They want to learn: '{goal}' in the topic of {topic}.
    Provide a short and clear explanation to help them get started.
    End with: '📚 Here are materials to study further:'
    """

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-1106-preview",
            messages=[
                {"role": "system", "content": "You're a helpful and knowledgeable tutor."},
                {"role": "user", "content": prompt}
            ]
        )
        explanation = response.choices[0].message.content
    except Exception as e:
        explanation = f"Error generating explanation: {str(e)}"

    # ✅ Search PDFDrive
    pdfs = []
    try:
        pdf_html = requests.get(f"https://www.pdfdrive.com/search?q={topic}", headers={"User-Agent": "Mozilla/5.0"}).text
        soup = BeautifulSoup(pdf_html, 'html.parser')
        for book in soup.select('.file-left')[:10]:
            title = book.select_one('img')['alt']
            if is_academic_book(title, topic, department):
                link = "https://www.pdfdrive.com" + book.parent['href']
                pdfs.append({'title': title, 'link': link})
    except Exception as e:
        pdfs = [{"error": str(e)}]

    # ✅ Search OpenLibrary
    books = []
    try:
        ol_data = requests.get(f"https://openlibrary.org/search.json?q={topic}").json()
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

    # ✅ Fallback if nothing academic found
    if not pdfs and not books:
        return jsonify({
            "query": topic,
            "ai_explanation": explanation,
            "pdfs": [],
            "books": [],
            "message": "❌ No academic study materials found for this topic."
        })

    # ✅ Final response
    return jsonify({
        "query": topic,
        "ai_explanation": explanation,
        "pdfs": pdfs,
        "books": books
    })


# ------------------ REELS ------------------
@app.route('/reels', methods=['GET'])
@login_required
def reels():
    """
    Display motivational/category-based reels from /reels route (UI).
    """
    categories = ["Tech", "Motivation", "Islamic", "AI"]
    selected_category = request.args.get("category")
    videos = []

    # Simulated category-based videos (replace with real content later)
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

    # Zoology
    {"course": "Zoology", "caption": "Animal Classification", "video_url": "https://example.com/videos/zoology1.mp4"},
    {"course": "Zoology", "caption": "Animal Habitats", "video_url": "https://example.com/videos/zoology2.mp4"},
    {"course": "Zoology", "caption": "Reproductive Strategies", "video_url": "https://example.com/videos/zoology3.mp4"},
    {"course": "Zoology", "caption": "Conservation Efforts", "video_url": "https://example.com/videos/zoology4.mp4"},
    {"course": "Zoology", "caption": "Animal Physiology", "video_url": "https://example.com/videos/zoology5.mp4"},
]

    matching = [r for r in all_reels if r["course"] == course]
    return jsonify({"reels": matching})


# ------------------ TRIVIA GAMES ------------------
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
    return render_template("CBT.html", user=session.get("user"), topics=topics, selected_topic=selected_topic, questions=questions)

@app.route('/teach-me-ai')
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
        response = openai.ChatCompletion.create(
            model="gpt-4-1106-preview",
            messages=[
                {"role": "system", "content": "You are an educational AI assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        return jsonify({"summary": response.choices[0].message.content})
    except Exception as e:
        return jsonify({"error": str(e)})

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

@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    username = session.get('user', {}).get('username')
    history = data.get('history')

    if not username:
        return jsonify({'error': 'Login required'}), 401
    if not history:
        return jsonify({'error': 'Chat history required'}), 400

    # Extract last user question
    user_question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), '')
    if not user_question:
        return jsonify({'error': 'No user question found'}), 400

    # --- Fetch Materials ---
    def get_pdfs(query):
        pdfs = []
        try:
            html = requests.get(
                f"https://www.pdfdrive.com/search?q={query}",
                headers={"User-Agent": "Mozilla/5.0"}
            ).text
            soup = BeautifulSoup(html, 'html.parser')
            for book in soup.select('.file-left')[:3]:
                title = book.select_one('img')['alt']
                link = "https://www.pdfdrive.com" + book.parent['href']
                pdfs.append({'title': title, 'link': link})
        except Exception as e:
            print("PDF fetch error:", e)
        return pdfs

    def get_books(query):
        books = []
        try:
            res = requests.get(f"https://openlibrary.org/search.json?q={query}").json()
            for doc in res.get("docs", [])[:2]:
                books.append({
                    "title": doc.get("title"),
                    "author": ', '.join(doc.get("author_name", [])) if doc.get("author_name") else "Unknown",
                    "link": f"https://openlibrary.org{doc.get('key')}"
                })
        except Exception as e:
            print("Book fetch error:", e)
        return books

    pdfs = get_pdfs(user_question)
    books = get_books(user_question)

    # Build study materials text
    materials_text = "📚 Study Materials:\n"
    for pdf in pdfs:
        materials_text += f"- {pdf['title']} ({pdf['link']})\n"
    for book in books:
        materials_text += f"- {book['title']} by {book['author']} ({book['link']})\n"

    # System role
    system_prompt = {
        "role": "system",
        "content": f"You are Tellavista — a wise, kind, and helpful AI tutor. Use these study materials to guide your response:\n\n{materials_text}\n\nAnswer in a clear, educational way as if you're teaching a student from scratch."
    }

    messages = [system_prompt] + history
    cache_key = sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()

    # Cache hit
    if cache_key in question_cache:
        answer = question_cache[cache_key]
        save_question_and_answer(username, user_question, answer)
        return jsonify({'choices':[{'message':{'role':'assistant','content':answer}}]})

    # Call OpenRouter API
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-4-turbo",
        "messages": messages,
        "stream": False
    }

    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        result = resp.json()
        answer = result.get('choices', [{}])[0].get('message', {}).get('content', '')

        question_cache[cache_key] = answer
        save_cache()
        save_question_and_answer(username, user_question, answer)

        return jsonify({'choices':[{'message':{'role':'assistant','content':answer}}]})
    except Exception as e:
        print(f"Error in /ask: {e}")
        return jsonify({'error': 'AI service error'}), 500

@app.route('/settings')
def settings():
    memory = {
        "traits": session.get('traits', []),
        "more_info": session.get('more_info', ''),
        "enable_memory": session.get('enable_memory', False)
    }
    return render_template('settings.html', memory=memory, theme=session.get('theme'), language=session.get('language'))

if __name__ == '__main__':
    with app.app_context():
        app.run(debug=True)

