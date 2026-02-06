import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")
# ============================================
# IMPORTS
# ============================================
import os
import json
import time
import base64
from datetime import datetime
from io import BytesIO
import tempfile
import shutil
from pathlib import Path
import re
import logging
import html
import uuid
import traceback

# PDF Processing
import PyPDF2
import fitz  # PyMuPDF for image extraction
import pdfplumber
from PIL import Image, ImageDraw, ImageFont
import pytesseract

# Flask
from flask import Flask, render_template, session, request, jsonify, send_from_directory, url_for, redirect, flash, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_session import Session
from flask_socketio import SocketIO, join_room, emit, leave_room
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text
from hashlib import sha256
from functools import wraps

# Environment
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import random

# Load environment variables
load_dotenv()

# ============================================
# FLASK APP CONFIGURATION
# ============================================
app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Proxy fix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Server-side sessions
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = 'flask_session'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SECRET_KEY'] = os.getenv('MY_SECRET', 'fallback_secret_' + str(uuid.uuid4()))
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# Initialize Flask-Session
Session(app)

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

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Debug mode - set to False in production
DEBUG_MODE = True

def debug_print(*args, **kwargs):
    """Print debug information when DEBUG_MODE is True."""
    if DEBUG_MODE:
        print(*args, **kwargs)

# Configure folders
UPLOAD_FOLDER = 'uploads'
IMAGE_FOLDER = 'static/extracted_images'
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif'}

for folder in [UPLOAD_FOLDER, IMAGE_FOLDER, 'flask_session']:
    os.makedirs(folder, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['IMAGE_FOLDER'] = IMAGE_FOLDER

# ============================================
# HELPER FUNCTIONS
# ============================================
def allowed_file(filename):
    """Check whether the file has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def cleanup_stale_files():
    """Remove files older than 24 hours."""
    try:
        current_time = time.time()
        for folder in [UPLOAD_FOLDER, IMAGE_FOLDER, 'flask_session']:
            if os.path.exists(folder):
                for filename in os.listdir(folder):
                    file_path = os.path.join(folder, filename)
                    if os.path.isfile(file_path):
                        file_age = current_time - os.path.getmtime(file_path)
                        if file_age > 24 * 3600:
                            os.remove(file_path)
                            debug_print(f"🗑️ Removed stale file: {filename}")
    except Exception as e:
        debug_print(f"⚠️ Stale file cleanup error: {e}")

# ============================================
# PDF PROCESSING MODULES (Turbo AI-Style)
# ============================================
def extract_text_from_pdf_turbo(file_stream):
    """Extract text from PDF with smart processing."""
    text = ""
    
    try:
        file_stream.seek(0)
        with pdfplumber.open(BytesIO(file_stream.read())) as pdf:
            for page_num, page in enumerate(pdf.pages[:30]):  # Limit to 30 pages
                page_text = page.extract_text()
                if page_text:
                    # Add page marker for structure
                    text += f"=== PAGE {page_num + 1} ===\n{page_text}\n\n"
                
                if len(text) > 30000:
                    text = text[:30000] + "\n...[Content truncated for optimal processing]"
                    break
        
        # Clean text
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        text = re.sub(r'[ \t]{2,}', ' ', text)
        
    except Exception as e:
        debug_print(f"❌ PDF text extraction error: {e}")
        text = f"[Note: Some PDF content may not be extracted correctly]\n\n"
    
    return text.strip()

def extract_text_from_pdf(file):
    """Extract text from PDF file with smart limitations (for main platform)."""
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

def extract_images_from_pdf(file_stream, session_id):
    """Extract images from PDF and save them."""
    images_info = []
    
    try:
        file_stream.seek(0)
        pdf_content = file_stream.read()
        pdf_document = fitz.open(stream=pdf_content, filetype="pdf")
        
        # Create session-specific folder
        session_folder = os.path.join(app.config['IMAGE_FOLDER'], session_id)
        os.makedirs(session_folder, exist_ok=True)
        
        image_count = 0
        
        for page_num in range(min(len(pdf_document), 30)):  # Limit to 30 pages
            page = pdf_document.load_page(page_num)
            image_list = page.get_images()
            
            for img_index, img in enumerate(image_list[:5]):  # Max 5 images per page
                xref = img[0]
                base_image = pdf_document.extract_image(xref)
                
                if base_image:
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    
                    # Generate filename
                    image_name = f"page_{page_num+1}_img_{img_index+1}.{image_ext}"
                    image_path = os.path.join(session_folder, image_name)
                    
                    # Save image
                    with open(image_path, "wb") as f:
                        f.write(image_bytes)
                    
                    # Generate URL
                    image_url = url_for('serve_extracted_image', 
                                      filename=f"{session_id}/{image_name}")
                    
                    images_info.append({
                        "path": image_path,
                        "url": image_url,
                        "alt": f"Diagram from page {page_num + 1}",
                        "page": page_num + 1,
                        "index": img_index + 1,
                        "filename": image_name
                    })
                    image_count += 1
                    
                    if image_count >= 15:  # Limit total images
                        break
            
            if image_count >= 15:
                break
        
        pdf_document.close()
        debug_print(f"✅ Extracted {image_count} images")
        
    except Exception as e:
        debug_print(f"❌ PDF image extraction error: {e}")
    
    return images_info

def extract_tables_from_pdf(file_stream):
    """Extract tables from PDF and convert to markdown."""
    tables_info = []
    
    try:
        file_stream.seek(0)
        with pdfplumber.open(BytesIO(file_stream.read())) as pdf:
            for page_num, page in enumerate(pdf.pages[:20]):
                tables = page.extract_tables()
                
                for table_num, table in enumerate(tables[:3]):
                    if table and len(table) > 0:
                        # Convert to markdown
                        markdown_table = convert_table_to_markdown(table)
                        table_text = convert_table_to_text(table)
                        
                        if markdown_table:
                            tables_info.append({
                                "markdown": markdown_table,
                                "text": table_text,
                                "page": page_num + 1,
                                "index": table_num + 1,
                                "rows": len(table),
                                "columns": len(table[0]) if table[0] else 0
                            })
        
        debug_print(f"✅ Extracted {len(tables_info)} tables")
        
    except Exception as e:
        debug_print(f"❌ PDF table extraction error: {e}")
    
    return tables_info

def convert_table_to_markdown(table):
    """Convert table data to Markdown format."""
    if not table or len(table) == 0:
        return ""
    
    # Clean table data
    cleaned_table = []
    for row in table:
        cleaned_row = []
        for cell in row:
            if cell is None:
                cleaned_row.append("")
            else:
                # Clean cell text
                cell_text = str(cell).strip()
                # Remove excessive whitespace
                cell_text = re.sub(r'\s+', ' ', cell_text)
                cleaned_row.append(cell_text)
        
        # Only add row if it has content
        if any(cell for cell in cleaned_row):
            cleaned_table.append(cleaned_row)
    
    if not cleaned_table or len(cleaned_table) < 2:
        return ""
    
    # Create markdown table
    markdown_lines = []
    
    # Header row
    markdown_lines.append("| " + " | ".join(cleaned_table[0]) + " |")
    
    # Separator row
    markdown_lines.append("| " + " | ".join(["---"] * len(cleaned_table[0])) + " |")
    
    # Data rows
    for row in cleaned_table[1:]:
        # Ensure row has same number of columns as header
        while len(row) < len(cleaned_table[0]):
            row.append("")
        while len(row) > len(cleaned_table[0]):
            row.pop()
        
        markdown_lines.append("| " + " | ".join(row) + " |")
    
    return "\n".join(markdown_lines)

def convert_table_to_text(table):
    """Convert table data to readable text format."""
    if not table:
        return ""
    
    text_lines = []
    for row in table:
        row_text = " | ".join([str(cell).strip() if cell else "" for cell in row])
        if row_text.strip():
            text_lines.append(row_text)
    
    return "\n".join(text_lines)

def analyze_document_structure(text):
    """Analyze document to identify sections and key information."""
    sections = {
        "document_title": "",
        "main_topics": [],
        "definitions": [],
        "processes": [],
        "comparisons": [],
        "examples": [],
        "tables_found": 0,
        "key_terms": []
    }
    
    # Extract document title (first non-empty line)
    lines = text.split('\n')
    for line in lines[:10]:
        if line.strip() and len(line.strip()) > 5:
            sections["document_title"] = line.strip()
            break
    
    # Look for key terms (bold, capitalized, or in quotes)
    for line in lines:
        # Look for definitions
        if "definition" in line.lower() or "means" in line.lower() or "refers to" in line.lower():
            if len(line.strip()) > 10:
                sections["definitions"].append(line.strip())
        
        # Look for processes
        if "process" in line.lower() or "step" in line.lower() or "procedure" in line.lower():
            sections["processes"].append(line.strip())
        
        # Look for comparisons
        if "vs" in line.lower() or "versus" in line.lower() or "compared to" in line.lower():
            sections["comparisons"].append(line.strip())
    
    # Extract potential main topics (longer lines that seem like headings)
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if (len(line_stripped) > 20 and len(line_stripped) < 150 and 
            not line_stripped.startswith('===') and
            line_stripped.endswith('.')):
            sections["main_topics"].append(line_stripped)
    
    # Limit lists to reasonable sizes
    for key in sections:
        if isinstance(sections[key], list):
            sections[key] = sections[key][:20]
    
    return sections

# ============================================
# UPDATED TURBO AI-STYLE PROMPT
# ============================================
PDF_ANALYSIS_PROMPT = """You are Nellavista, an elite university-level lecturer and curriculum designer with expertise in transforming raw academic material into comprehensive, lecture-style notes.

## 🎯 YOUR MISSION
Transform the provided document into ULTIMATE STUDY NOTES that:
1. **Turn bullet points into flowing, textbook-style explanations**
2. **Add structure with clear headings and subheadings**
3. **Explain concepts in student-friendly language**
4. **Create comparison tables from scattered information**
5. **Connect ideas for better understanding**
6. **Include practical examples and real-world applications**
7. **Prepare students for exams with key insights**

## 📋 OUTPUT STRUCTURE REQUIREMENTS

### PART 1: DOCUMENT OVERVIEW
# 📚 [Document Title] - Comprehensive Study Guide

**Brief Overview**: [2-3 paragraph summary covering the entire topic scope]

**Key Learning Objectives**:
- [List 4-6 main things students should learn]
- [Connect each objective to practical applications]

### PART 2: CORE CONCEPTS (LECTURE STYLE)
## 🎯 [Main Topic 1]

**What is [Topic 1]?**
- [Clear definition in simple language]
- [Why this concept matters]
- [Key components/features]

**How it Works**:
- [Step-by-step explanation]
- [Visual description if diagrams mentioned]
- [Practical examples]

## 🎯 [Main Topic 2] (and so on...)

### PART 3: DETAILED EXPLANATIONS
## 🔍 Deep Dive into [Complex Sub-topic]

**Understanding the Mechanism**:
- [Detailed process explanation]
- [Key players involved]
- [Inputs and outputs]

**Common Student Questions & Answers**:
- Q: [Common confusion point]
  A: [Clear, thorough explanation]
- Q: [Another confusion point]
  A: [Clear, thorough explanation]

### PART 4: COMPARISON TABLES
## 📊 Key Comparisons

[CREATE COMPARISON TABLES for ANY concepts that can be compared. Example format:]

| Feature/Characteristic | Type A | Type B | Type C | Type D |
|------------------------|--------|--------|--------|--------|
| **Characteristic 1**   | Value A1 | Value B1 | Value C1 | Value D1 |
| **Characteristic 2**   | Value A2 | Value B2 | Value C2 | Value D2 |
| **Characteristic 3**   | Value A3 | Value B3 | Value C3 | Value D3 |

### PART 5: PROCESS FLOWS
## 🔄 Step-by-Step Processes

**Process Name**:
1. **Step 1**: [Description with explanation]
2. **Step 2**: [Description with explanation]
3. **Step 3**: [Description with explanation]
4. **Step 4**: [Description with explanation]

### PART 6: EXAM PREPARATION
## 🧠 Exam-Focused Notes

**Must-Know Facts**:
- [Key formulas/theorems/concepts that ALWAYS appear on exams]
- [Common exam question patterns]
- [Frequent student mistakes and how to avoid them]

**Study Strategies**:
- How to memorize [specific challenging topic]
- How to apply [concept] in problem-solving
- Quick revision checklist for last-minute study

### PART 7: PRACTICAL APPLICATIONS
## 💡 Real-World Connections

[How this knowledge is used in:]
- **Research**: [Applications in current research]
- **Industry**: [Practical industry applications]
- **Daily Life**: [Everyday relevance]

## 🎨 FORMATTING RULES

1. **Headings**: Use emoji + descriptive heading (e.g., "🎯 Core Concepts")
2. **Subheadings**: Use ### level with clear titles
3. **Bullet Points**: For lists, key features, comparisons
4. **Numbered Lists**: For processes, steps, sequences
5. **Tables**: ALWAYS create tables for comparisons (minimum 3 columns)
6. **Bold Terms**: **Bold** key terms when first introduced
7. **Examples**: Provide 2-3 examples for EACH main concept
8. **Analogies**: Use simple analogies for complex ideas
9. **Visual Descriptions**: Describe any diagrams/tables mentioned

## 👥 TEACHING APPROACH

**For Slow Learners**:
- Break complex ideas into simple parts
- Use everyday analogies
- Repeat key points in different ways
- Show clear connections between concepts
- Provide "In Simple Terms" explanations

**For Fast Learners**:
- Include "Think Deeper" side notes
- Show connections to advanced topics
- Provide extension questions
- Include "Beyond the Basics" insights

## 🚫 STRICT CONTENT RULES

1. **DO NOT** just copy bullet points - EXPLAIN them
2. **DO NOT** use unexplained academic jargon
3. **DO** create flowing paragraphs from disconnected points
4. **DO** anticipate and answer student questions
5. **DO** connect concepts that appear separate in the document
6. **DO** add missing context that helps understanding
7. **DO** create visual descriptions of any mentioned diagrams
8. **DO** transform all raw data into organized tables

## ✅ FINAL QUALITY CHECK
Before finishing, ask yourself:
1. "Can a complete beginner understand this?"
2. "Can a top student learn something new?"
3. "Would this help someone pass an exam on this topic?"
4. "Are all concepts connected in a logical flow?"

Your output should be a COMPLETE STANDALONE STUDY GUIDE that makes the original document unnecessary for exam preparation."""

# ============================================
# COMPREHENSIVE NOTE GENERATOR
# ============================================
def generate_turbo_style_notes(text, tables, images, filename, document_analysis):
    """Generate Turbo AI-style comprehensive notes."""
    
    try:
        # Prepare enhanced content for AI
        tables_summary = ""
        if tables:
            tables_summary = f"## 📊 EXTRACTED TABLES ({len(tables)} found)\n\n"
            for i, table in enumerate(tables[:3], 1):
                tables_summary += f"### Table {i} Preview:\n```\n{table['text'][:200]}...\n```\n\n"
        
        images_summary = f"📸 {len(images)} images extracted (diagrams, charts, illustrations)" if images else "No images extracted"
        
        # Create comprehensive prompt
        enhanced_prompt = f"""
        DOCUMENT ANALYSIS REQUEST
        ==========================
        
        DOCUMENT TITLE: {filename}
        MAIN TOPIC: {document_analysis.get('document_title', 'Academic Material')}
        
        DOCUMENT STRUCTURE:
        - Main Topics Identified: {len(document_analysis.get('main_topics', []))}
        - Key Definitions Found: {len(document_analysis.get('definitions', []))}
        - Processes Described: {len(document_analysis.get('processes', []))}
        - Comparisons Found: {len(document_analysis.get('comparisons', []))}
        
        EXTRACTED CONTENT:
        ```
        {text[:20000]}...
        ```
        
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
# AI TUTOR ROUTES
# ============================================
@app.route('/talk-to-nelavista')
@login_required
def talk_to_nelavista():
    """Render AI chat interface."""
    return render_template('talk-to-nelavista.html')

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
        
        # Get message (always present)
        message = request.form.get('message', '').strip()
        if not message:
            return jsonify({
                "success": True,
                "answer": GRACEFUL_FALLBACK
            })
        
        # Get and validate chat history
        history_json = request.form.get('history', '[]')
        chat_history = []
        try:
            if history_json:
                chat_history = json.loads(history_json)
                # Validate and clean history
                clean_history = []
                for msg in chat_history:
                    if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                        role = msg['role'].lower()
                        content = str(msg['content']).strip()
                        if role in ['user', 'assistant'] and content:
                            clean_history.append({
                                'role': role,
                                'content': content
                            })
                chat_history = clean_history
        except:
            chat_history = []
        
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
        
        pdf_context = ""
        if has_pdfs and file_texts:
            pdf_context = "\n\n".join(file_texts)
        
        # Build system prompt
        system_prompt = """You are Nelavista, an advanced AI tutor created by Afeez Adewale Tella for Nigerian university students (100–400 level).

ROLE:
You are a specialized analytical tutor and researcher.

STYLE: Analytical Expert
Follow these rules strictly.

GLOBAL RULES:
- Always respond in clean HTML using <p>, <ul>, <li>, <strong>, <h2>, <h3>, and <table> when appropriate.
- Respond naturally to greetings when the user greets you, but do not add greetings unnecessarily.
- Do not use labels like Step 1, Step 2, Intro, or Final Answer.
- No emojis in academic explanations.
- Use clear, calm, and friendly academic language.
- Explain ideas patiently, like a lecturer guiding students through the topic.
- Avoid hype, exaggeration, or unnecessary filler.

STRUCTURE REQUIREMENTS:
- Begin with a brief <strong>Key Idea</strong> statement introducing the core conclusion.
- Use clear headers and bullet points to organize information.
- Break complex ideas into logical components and explain the reasoning behind them.
- Use short paragraphs (1–2 sentences).

REASONING:
- Show the reasoning path clearly but naturally.
- Explain why conclusions follow from facts or data.

DATA & ACCURACY:
- Include formulas, definitions, metrics, or comparisons when relevant.
- Do not speculate or invent facts.

GENERAL INSTRUCTIONS:
- Provide accurate, structured academic explanations.
- Use relevant examples only when they add clarity.
- Maintain a logical flow from premises to conclusions.

ENDING:
- End naturally after the explanation. Do not add summaries beyond the TL;DR."""
        
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
                    memory_layer="GENERAL"
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
        
        # Build system prompt
        system_prompt = """You are Nelavista, an advanced AI tutor created by Afeez Adewale Tella for Nigerian university students (100–400 level).

ROLE:
You are a specialized analytical tutor and researcher.

STYLE: Analytical Expert
Follow these rules strictly.

GLOBAL RULES:
- Always respond in clean HTML using <p>, <ul>, <li>, <strong>, <h2>, <h3>, and <table> when appropriate.
- Respond naturally to greetings when the user greets you, but do not add greetings unnecessarily.
- Do not use labels like Step 1, Step 2, Intro, or Final Answer.
- No emojis in academic explanations.
- Use clear, calm, and friendly academic language.
- Explain ideas patiently, like a lecturer guiding students through the topic.
- Avoid hype, exaggeration, or unnecessary filler.

STRUCTURE REQUIREMENTS:
- Begin with a brief <strong>Key Idea</strong> statement introducing the core conclusion.
- Use clear headers and bullet points to organize information.
- Break complex ideas into logical components and explain the reasoning behind them.
- Use short paragraphs (1–2 sentences).

REASONING:
- Show the reasoning path clearly but naturally.
- Explain why conclusions follow from facts or data.

DATA & ACCURACY:
- Include formulas, definitions, metrics, or comparisons when relevant.
- Do not speculate or invent facts.

GENERAL INSTRUCTIONS:
- Provide accurate, structured academic explanations.
- Use relevant examples only when they add clarity.
- Maintain a logical flow from premises to conclusions.

ENDING:
- End naturally after the explanation. Do not add summaries beyond the TL;DR."""
        
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
# OTHER ROUTES
# ============================================
@app.route('/about')
def about():
    """Render about page."""
    return render_template('about.html')

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
        {"course": "Computer Science", "caption": "Introduction to Programming", "video_url": "https://youtu.be/zOjov-2OZ0E?si=Jc0sjcUY3UJcNnRG"},
        {"course": "Medicine & Surgery", "caption": "Human Anatomy Overview", "video_url": "https://youtu.be/NB6idAXbXAQ?si=Er6Y_Lr5uzXm_31E"},
        {"course": "Law", "caption": "Introduction to Law School", "video_url": "https://youtu.be/1Y4X2TqU9Rc?si=6d0fM7bm9R7f9gJL"},
        {"course": "Engineering", "caption": "Mechanical Engineering Basics", "video_url": "https://youtu.be/G0N-7zTQK-Y?si=ypN2XZlgjJQbGLao"},
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
    print("   5. 📚 Study Materials Library")
    print("   6. 🎬 Educational Reels")
    print("   7. 📝 CBT Test System")
    print(f"{'='*70}")
    print("\n📡 Access at: http://localhost:5000")
    print("👨‍🏫 Teacher test: http://localhost:5000/live_meeting/teacher")
    print("📊 Turbo Analyzer: http://localhost:5000/analyze")
    print("🤖 AI Tutor: http://localhost:5000/talk-to-nelavista")
    print(f"{'='*70}")
    print("\n⚠️  IMPORTANT: Install required packages:")
    print("   pip install PyPDF2 pdfplumber pymupdf Pillow pytesseract")
    print("   pip install flask-socketio flask-session flask-sqlalchemy")
    print("   pip install python-dotenv requests beautifulsoup4")
    print(f"{'='*70}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=DEBUG_MODE)

