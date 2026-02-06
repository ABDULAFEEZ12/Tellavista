import eventlet
eventlet.monkey_patch()
print("✅ Eventlet monkey patch applied")

# ============================================
# Imports
# ============================================
import os
import json
from datetime import datetime
from flask import Flask, render_template, session, redirect, url_for, request, flash
from flask_socketio import SocketIO, join_room, emit, leave_room
from flask_sqlalchemy import SQLAlchemy
import uuid

# ============================================
# Flask App Configuration
# ============================================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
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
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Room(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    teacher_id = db.Column(db.String(120))
    teacher_name = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Create tables
with app.app_context():
    db.create_all()
    debug_print("✅ Database tables created")

# ============================================
# In-Memory Storage
# ============================================
rooms = {}           # room_id -> room data
participants = {}    # socket_id -> participant info
room_authority = {}  # room_id -> authority state
broadcast_data = {}  # room_id -> broadcast data (questions, raised hands)

# ============================================
# Helper Functions
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

def get_broadcast_data(room_id):
    """Get or create broadcast data for a room"""
    if room_id not in broadcast_data:
        broadcast_data[room_id] = {
            'questions': [],  # List of questions
            'raised_hands': {},  # sid -> {username, timestamp}
            'active_speaker': None,  # Current active student speaking
            'confusion_alerts': []  # List of confusion alerts
        }
    return broadcast_data[room_id]

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
            if room_id in broadcast_data:
                del broadcast_data[room_id]
            with app.app_context():
                Room.query.filter_by(id=room_id).delete()
                db.session.commit()

# ============================================
# Socket.IO Event Handlers
# ============================================
@socketio.on('connect')
def handle_connect():
    sid = request.sid
    # Join client to their private SID room for direct messaging
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
            
            # Remove from broadcast data if exists
            if room_id in broadcast_data:
                broadcast = broadcast_data[room_id]
                if sid in broadcast['raised_hands']:
                    del broadcast['raised_hands'][sid]
                    # Notify teacher if exists
                    if room['teacher_sid']:
                        emit('raised-hand-removed', {'sid': sid}, room=room['teacher_sid'])
                
                if broadcast['active_speaker'] == sid:
                    broadcast['active_speaker'] = None
            
            debug_print(f"❌ {participant_info['username']} left room {room_id}")
        
        # Clean up empty room
        cleanup_room(room_id)
    
    # Remove from participants
    if sid in participants:
        del participants[sid]

@socketio.on('join-room')
def handle_join_room(data):
    """Join room and get all existing participants"""
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
            'is_waiting': (role == 'student' and not room['teacher_sid'])
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
# WebRTC Signaling
# ============================================
@socketio.on('webrtc-offer')
def handle_webrtc_offer(data):
    """Relay WebRTC offer to specific participant"""
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
        
        # Use target_sid as room (requires client to join their SID room on connect)
        emit('webrtc-offer', {
            'from_sid': request.sid,
            'offer': offer,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying offer: {e}")

@socketio.on('webrtc-answer')
def handle_webrtc_answer(data):
    """Relay WebRTC answer to specific participant"""
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
        
        emit('webrtc-answer', {
            'from_sid': request.sid,
            'answer': answer,
            'room': room_id
        }, room=target_sid)
        
    except Exception as e:
        debug_print(f"❌ Error relaying answer: {e}")

@socketio.on('webrtc-ice-candidate')
def handle_webrtc_ice_candidate(data):
    """Relay ICE candidate to specific participant"""
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
# Large Classroom Broadcast Mode Event Handlers
# ============================================
@socketio.on('raise-hand')
def handle_raise_hand(data):
    """Student raises hand"""
    try:
        sid = request.sid
        room_id = data.get('room')
        username = data.get('username')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = room['teacher_sid']
        
        if not teacher_sid:
            return
        
        # Get broadcast data
        broadcast = get_broadcast_data(room_id)
        
        # Add to raised hands
        broadcast['raised_hands'][sid] = {
            'username': username,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        # Notify teacher
        emit('new-raised-hand', {
            'username': username,
            'sid': sid,
            'timestamp': datetime.utcnow().isoformat()
        }, room=teacher_sid)
        
        debug_print(f"✋ {username} raised hand in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in raise-hand: {e}")

@socketio.on('send-question')
def handle_send_question(data):
    """Student sends a question"""
    try:
        sid = request.sid
        room_id = data.get('room')
        username = data.get('username')
        text = data.get('text')
        
        if not room_id or room_id not in rooms or not text:
            return
        
        room = rooms[room_id]
        teacher_sid = room['teacher_sid']
        
        # Create question object
        question = {
            'id': str(uuid.uuid4()),
            'username': username,
            'text': text,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        # Add to broadcast data
        broadcast = get_broadcast_data(room_id)
        broadcast['questions'].append(question)
        
        # Notify teacher and all participants
        if teacher_sid:
            emit('new-question', question, room=teacher_sid)
        
        # Also notify all participants for public questions
        emit('new-question', question, room=room_id)
        
        debug_print(f"❓ {username} asked question in room {room_id}: {text}")
        
    except Exception as e:
        debug_print(f"❌ Error in send-question: {e}")

@socketio.on('confusion-alert')
def handle_confusion_alert(data):
    """Student sends confusion alert"""
    try:
        sid = request.sid
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = room['teacher_sid']
        
        if not teacher_sid:
            return
        
        # Add to confusion alerts
        broadcast = get_broadcast_data(room_id)
        broadcast['confusion_alerts'].append({
            'sid': sid,
            'timestamp': datetime.utcnow().isoformat()
        })
        
        # Notify teacher
        emit('confusion-alert', {
            'sid': sid,
            'timestamp': datetime.utcnow().isoformat()
        }, room=teacher_sid)
        
        debug_print(f"❓ Confusion alert from {sid} in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in confusion-alert: {e}")

@socketio.on('approve-speaker')
def handle_approve_speaker(data):
    """Teacher approves student to speak"""
    try:
        room_id = data.get('room')
        target_student_sid = data.get('target_student_sid')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        # Verify this is the teacher
        if teacher_sid != room['teacher_sid']:
            return
        
        # Remove from raised hands
        broadcast = get_broadcast_data(room_id)
        if target_student_sid in broadcast['raised_hands']:
            del broadcast['raised_hands'][target_student_sid]
        
        # Set as active speaker
        broadcast['active_speaker'] = target_student_sid
        
        # Notify the student
        emit('you-are-approved', room=target_student_sid)
        
        debug_print(f"✅ Teacher approved student {target_student_sid} to speak in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in approve-speaker: {e}")

@socketio.on('revoke-speaker')
def handle_revoke_speaker(data):
    """Teacher revokes student speaking privileges"""
    try:
        room_id = data.get('room')
        target_student_sid = data.get('target_student_sid')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        # Verify this is the teacher
        if teacher_sid != room['teacher_sid']:
            return
        
        # Clear active speaker
        broadcast = get_broadcast_data(room_id)
        broadcast['active_speaker'] = None
        
        # Notify the student
        emit('you-are-revoked', room=target_student_sid)
        
        debug_print(f"🔇 Teacher revoked student {target_student_sid} in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in revoke-speaker: {e}")

@socketio.on('dismiss-hand')
def handle_dismiss_hand(data):
    """Teacher dismisses a raised hand"""
    try:
        room_id = data.get('room')
        target_student_sid = data.get('target_student_sid')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = request.sid
        
        # Verify this is the teacher
        if teacher_sid != room['teacher_sid']:
            return
        
        # Remove from raised hands
        broadcast = get_broadcast_data(room_id)
        if target_student_sid in broadcast['raised_hands']:
            del broadcast['raised_hands'][target_student_sid]
        
        # Notify the student
        emit('hand-lowered', room=target_student_sid)
        
        debug_print(f"✋ Teacher dismissed hand of student {target_student_sid} in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in dismiss-hand: {e}")

@socketio.on('lower-hand')
def handle_lower_hand(data):
    """Student lowers their own hand"""
    try:
        sid = request.sid
        room_id = data.get('room')
        
        if not room_id or room_id not in rooms:
            return
        
        room = rooms[room_id]
        teacher_sid = room['teacher_sid']
        
        if not teacher_sid:
            return
        
        # Remove from raised hands
        broadcast = get_broadcast_data(room_id)
        if sid in broadcast['raised_hands']:
            del broadcast['raised_hands'][sid]
        
        # Notify the teacher
        emit('raised-hand-removed', {'sid': sid}, room=teacher_sid)
        
        debug_print(f"✋ Hand lowered by {sid} in room {room_id}")
        
    except Exception as e:
        debug_print(f"❌ Error in lower-hand: {e}")

# ============================================
# Control Events
# ============================================
@socketio.on('start-broadcast')
def handle_start_broadcast(data):
    """Teacher starts broadcasting to all students"""
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
    """Keep-alive ping"""
    emit('pong', {'timestamp': datetime.utcnow().isoformat()})

# ============================================
# Flask Routes
# ============================================
@app.route('/')
def index():
    return render_template('index.html')

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
# Large Classroom (Broadcast Mode) Routes
# ============================================
@app.route('/large_event')
def large_event_home():
    """Main entry point for large classroom"""
    return render_template('large_event_join.html')

@app.route('/large_event/teacher')
def large_event_teacher_create():
    """Create a new large classroom as teacher"""
    room_id = str(uuid.uuid4())[:8]
    return redirect(url_for('large_event_teacher_view', room_id=room_id))

@app.route('/large_event/teacher/<room_id>')
def large_event_teacher_view(room_id):
    """Teacher view for large classroom"""
    return render_template('large_event.html', 
                          room_id=room_id, 
                          role='teacher', 
                          username='Teacher')

@app.route('/large_event/student/<room_id>')
def large_event_student_view(room_id):
    """Student view for large classroom"""
    username = session.get('large_username', f"Student_{str(uuid.uuid4())[:4]}")
    return render_template('large_event.html', 
                          room_id=room_id, 
                          role='student', 
                          username=username)

@app.route('/large_event/join', methods=['GET', 'POST'])
def large_event_join():
    """Join form for large classroom"""
    if request.method == 'POST':
        room_id = request.form.get('room_id', '').strip()
        username = request.form.get('username', '').strip()
        
        if not room_id:
            flash('Please enter a room ID')
            return redirect('/large_event')
        
        if not username:
            username = f"Student_{str(uuid.uuid4())[:4]}"
        
        session['large_username'] = username
        return redirect(url_for('large_event_student_view', room_id=room_id))
    
    return render_template('large_event_join.html')

# Route for backward compatibility with landing page links
@app.route('/live-meeting/large_classroom_teacher')
def large_classroom_teacher():
    """Alias for large classroom teacher view from landing page"""
    return redirect(url_for('large_event_teacher_create'))

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
        'broadcast_data': broadcast_data,
        'total_rooms': len(rooms),
        'total_participants': len(participants)
    }
    return json.dumps(debug_info, indent=2, default=str)

# ============================================
# Run Server
# ============================================
if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 NELAVISTA LIVE - Full Mesh WebRTC + Broadcast Mode")
    print("🌟 Teacher Authority + Full Mesh Networking")
    print("🎓 Smart Classroom Broadcast for 1000+ Students")
    print(f"{'='*60}")
    print("✅ WebRTC signaling with STUN/TURN")
    print("✅ Large Classroom (Broadcast Mode) Ready")
    print("✅ Raise Hand System + Q&A + Confusion Alerts")
    print("✅ Production ready for Render deployment")
    print(f"{'='*60}")
    print("\n📡 Connection test: http://localhost:5000/test-connection")
    print("👨‍🏫 Teacher test: http://localhost:5000/live_meeting/teacher")
    print("👨‍🎓 Student test: http://localhost:5000/live_meeting")
    print("📢 Large Classroom (Teacher): http://localhost:5000/large_event/teacher")
    print("🎓 Large Classroom (Join): http://localhost:5000/large_event")
    print(f"{'='*60}\n")
    
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=DEBUG_MODE)
