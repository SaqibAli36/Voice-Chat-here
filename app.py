from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'admin-voice-chat-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# Fixed room configuration
ROOM_CONFIG = {
    'room_id': 'ADMIN-VOICE-ROOM-2024',
    'admin_password': 'admin123',  # Change this in production
    'max_guests': 1  # Only one guest allowed
}

# Store active connections
active_users = {
    'admin': None,  # Admin socket ID
    'guest': None   # Guest socket ID
}

@app.route('/')
def index():
    """Redirect to admin or guest based on URL"""
    return render_template('guest.html')

@app.route('/admin')
def admin_page():
    """Admin login page"""
    return render_template('admin.html')

@app.route('/guest')
def guest_page():
    """Guest join page"""
    return render_template('guest.html')

@app.route('/api/verify-admin', methods=['POST'])
def verify_admin():
    """Verify admin password"""
    data = request.get_json()
    password = data.get('password', '')
    
    if password == ROOM_CONFIG['admin_password']:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid password'})

# WebRTC signaling
@socketio.on('connect')
def handle_connect():
    print(f"✅ User connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"❌ User disconnected: {request.sid}")
    
    # Clean up
    if active_users['admin'] == request.sid:
        active_users['admin'] = None
        print("👑 Admin disconnected")
        emit('admin-disconnected', broadcast=True)
    
    elif active_users['guest'] == request.sid:
        active_users['guest'] = None
        print("👤 Guest disconnected")
        emit('guest-disconnected', broadcast=True)

@socketio.on('register-admin')
def handle_register_admin():
    """Register admin user"""
    if active_users['admin'] is None:
        active_users['admin'] = request.sid
        print(f"👑 Admin registered: {request.sid}")
        emit('admin-registered', {'success': True})
        
        # Notify guest if present
        if active_users['guest']:
            emit('admin-joined', room=active_users['guest'])
    else:
        emit('admin-registered', {'success': False, 'error': 'Admin already exists'})

@socketio.on('register-guest')
def handle_register_guest():
    """Register guest user"""
    if active_users['guest'] is None:
        active_users['guest'] = request.sid
        print(f"👤 Guest registered: {request.sid}")
        emit('guest-registered', {'success': True})
        
        # Notify admin if present
        if active_users['admin']:
            emit('guest-joined', room=active_users['admin'])
    else:
        emit('guest-registered', {'success': False, 'error': 'Guest slot is full'})

@socketio.on('get-users')
def handle_get_users():
    """Get current users status"""
    emit('users-status', {
        'admin': active_users['admin'] is not None,
        'guest': active_users['guest'] is not None,
        'room_id': ROOM_CONFIG['room_id']
    })

# WebRTC signaling - Admin to Guest
@socketio.on('admin-offer')
def handle_admin_offer(data):
    """Forward admin's WebRTC offer to guest"""
    if active_users['guest']:
        emit('admin-offer', {
            'offer': data['offer'],
            'from': 'admin'
        }, room=active_users['guest'])

@socketio.on('guest-answer')
def handle_guest_answer(data):
    """Forward guest's WebRTC answer to admin"""
    if active_users['admin']:
        emit('guest-answer', {
            'answer': data['answer'],
            'from': 'guest'
        }, room=active_users['admin'])

# WebRTC signaling - Guest to Admin
@socketio.on('guest-offer')
def handle_guest_offer(data):
    """Forward guest's WebRTC offer to admin"""
    if active_users['admin']:
        emit('guest-offer', {
            'offer': data['offer'],
            'from': 'guest'
        }, room=active_users['admin'])

@socketio.on('admin-answer')
def handle_admin_answer(data):
    """Forward admin's WebRTC answer to guest"""
    if active_users['guest']:
        emit('admin-answer', {
            'answer': data['answer'],
            'from': 'admin'
        }, room=active_users['guest'])

# ICE candidates
@socketio.on('ice-candidate')
def handle_ice_candidate(data):
    """Forward ICE candidates between users"""
    target = data['target']
    candidate = data['candidate']
    
    if target == 'admin' and active_users['admin']:
        emit('ice-candidate', {
            'candidate': candidate,
            'from': 'guest'
        }, room=active_users['admin'])
    
    elif target == 'guest' and active_users['guest']:
        emit('ice-candidate', {
            'candidate': candidate,
            'from': 'admin'
        }, room=active_users['guest'])

if __name__ == '__main__':
    print("=" * 50)
    print("🎤 ADMIN VOICE CHAT SERVER")
    print("=" * 50)
    print(f"📁 Room ID: {ROOM_CONFIG['room_id']}")
    print(f"🔐 Admin password: {ROOM_CONFIG['admin_password']}")
    print(f"👤 Max guests: {ROOM_CONFIG['max_guests']}")
    print("=" * 50)
    print("🌐 Admin URL: http://localhost:5000/admin")
    print("🌐 Guest URL: http://localhost:5000/")
    print("=" * 50)
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)