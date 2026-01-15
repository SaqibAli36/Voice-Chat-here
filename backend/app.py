from flask import Flask, request, send_from_directory, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, leave_room, emit, disconnect
import os
import firebase_admin
from firebase_admin import credentials, auth, exceptions
from dotenv import load_dotenv
import json

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__, static_folder="../frontend", static_url_path="")
CORS(app)

socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode="threading",
    logger=True,
    engineio_logger=True
)

# ---------------- Firebase Initialization ----------------
firebase_initialized = False

try:
    # Get Firebase credentials from environment variables
    firebase_config = {
        "type": os.environ.get("FIREBASE_TYPE", "service_account"),
        "project_id": os.environ.get("FIREBASE_PROJECT_ID"),
        "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID"),
        "private_key": os.environ.get("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n"),
        "client_email": os.environ.get("FIREBASE_CLIENT_EMAIL"),
        "client_id": os.environ.get("FIREBASE_CLIENT_ID"),
        "auth_uri": os.environ.get("FIREBASE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth"),
        "token_uri": os.environ.get("FIREBASE_TOKEN_URI", "https://oauth2.googleapis.com/token"),
        "auth_provider_x509_cert_url": os.environ.get("FIREBASE_AUTH_PROVIDER_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs"),
        "client_x509_cert_url": os.environ.get("FIREBASE_CLIENT_CERT_URL"),
        "universe_domain": os.environ.get("FIREBASE_UNIVERSE_DOMAIN", "googleapis.com")
    }
    
    # Check if all required fields are present
    required_fields = ["project_id", "private_key", "client_email"]
    missing_fields = [field for field in required_fields if not firebase_config.get(field)]
    
    if missing_fields:
        print(f"⚠️  Missing Firebase environment variables: {missing_fields}")
        print("⚠️  Running without Firebase authentication")
    else:
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
        firebase_initialized = True
        print("✅ Firebase initialized successfully")
        
except Exception as e:
    print(f"❌ Firebase initialization failed: {str(e)}")
    print("⚠️  Running without Firebase authentication")

# ---------------- Authentication Middleware ----------------
def verify_firebase_token(token):
    """Verify Firebase JWT token"""
    if not firebase_initialized:
        return {"uid": "guest", "email": "guest@example.com", "name": "Guest User"}
    
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except exceptions.InvalidIdTokenError:
        return None
    except exceptions.ExpiredIdTokenError:
        return None
    except Exception as e:
        print(f"Token verification error: {e}")
        return None

# ---------------- Frontend ----------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/<path:filename>")
def serve_file(filename):
    return send_from_directory(app.static_folder, filename)

@app.route("/api/health")
def health_check():
    return jsonify({
        "status": "healthy",
        "firebase": "initialized" if firebase_initialized else "disabled",
        "timestamp": os.environ.get("RAILWAY_DEPLOYMENT_TIMESTAMP", "local")
    })

# ---------------- Rooms Management ----------------
rooms = {}

def get_room(room_id):
    """Get or create a room"""
    room_id = str(room_id)
    if room_id not in rooms:
        rooms[room_id] = {
            "users": {},  # socket_id -> user_info
            "messages": [],
            "mic_slots": {},
            "created_at": os.times().elapsed
        }
    return rooms[room_id]

# ---------------- Socket.IO Events ----------------
@socketio.on("connect")
def handle_connect():
    print(f"📱 Client connected: {request.sid}")
    emit("connected", {"sid": request.sid})

@socketio.on("disconnect")
def handle_disconnect():
    print(f"📴 Client disconnected: {request.sid}")
    # Clean up user from all rooms
    for room_id, room_data in rooms.items():
        if request.sid in room_data["users"]:
            user_info = room_data["users"][request.sid]
            leave_room_event({
                "room_id": room_id,
                "user": user_info.get("name", "Unknown")
            })

@socketio.on("authenticate")
def handle_authentication(data):
    """Verify Firebase token and set user info"""
    token = data.get("token")
    user_data = verify_firebase_token(token)
    
    if user_data:
        user_info = {
            "uid": user_data.get("uid"),
            "name": user_data.get("name", user_data.get("email", "User")),
            "email": user_data.get("email", ""),
            "photo": user_data.get("picture", ""),
            "sid": request.sid
        }
        emit("authenticated", {"success": True, "user": user_info})
        return user_info
    else:
        # Allow guest access if Firebase is not initialized
        if not firebase_initialized:
            user_info = {
                "uid": f"guest_{request.sid[:8]}",
                "name": data.get("username", "Guest"),
                "email": "",
                "photo": "",
                "sid": request.sid
            }
            emit("authenticated", {"success": True, "user": user_info})
            return user_info
        else:
            emit("authenticated", {"success": False, "error": "Invalid token"})
            return None

# ---------------- Join Room ----------------
@socketio.on("join_room_event")
def join_room_event(data):
    room_id = str(data["room_id"])
    user_name = data.get("user", "Anonymous")
    token = data.get("token")
    
    # Verify authentication if Firebase is initialized
    user_info = None
    if firebase_initialized and token:
        user_info = verify_firebase_token(token)
        if not user_info:
            emit("join_error", {"message": "Authentication failed"})
            return
    
    room = get_room(room_id)
    
    # Store user info
    room["users"][request.sid] = {
        "name": user_name,
        "uid": user_info.get("uid", f"guest_{request.sid[:8]}") if user_info else f"guest_{request.sid[:8]}",
        "sid": request.sid,
        "joined_at": os.times().elapsed
    }
    
    join_room(room_id)
    
    # Send room data to the joining user
    emit("room_data", {
        "messages": room["messages"][-50:],  # Last 50 messages
        "mic_slots": room["mic_slots"],
        "users": list(room["users"].values()),
        "room_id": room_id
    }, to=request.sid)
    
    # Notify others in the room
    msg = {"user": "System", "text": f"{user_name} has joined the room", "timestamp": os.times().elapsed}
    room["messages"].append(msg)
    emit("new_message", msg, room=room_id, include_self=False)
    
    # Update user list for everyone
    emit("user_list_update", {
        "users": list(room["users"].values()),
        "action": "join",
        "user": user_name
    }, room=room_id)

# ---------------- Leave Room ----------------
@socketio.on("leave_room_event")
def leave_room_event(data):
    room_id = str(data["room_id"])
    user_name = data.get("user", "Anonymous")
    
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    # Remove user from room
    if request.sid in room["users"]:
        removed_user = room["users"].pop(request.sid)
        user_name = removed_user.get("name", user_name)
    
    # Remove user from mic slots
    mic_slots = room["mic_slots"]
    for slot, user in list(mic_slots.items()):
        if user == user_name:
            del mic_slots[slot]
    
    # Notify about mic update
    emit("mic_update", mic_slots, room=room_id)
    
    # Notify about user leaving
    msg = {"user": "System", "text": f"{user_name} has left the room", "timestamp": os.times().elapsed}
    room["messages"].append(msg)
    emit("new_message", msg, room=room_id)
    
    # Update user list
    emit("user_list_update", {
        "users": list(room["users"].values()),
        "action": "leave",
        "user": user_name
    }, room=room_id)
    
    leave_room(room_id)
    
    # Clean up empty room
    if not room["users"]:
        del rooms[room_id]
        print(f"🗑️  Room {room_id} deleted (empty)")

# ---------------- Chat ----------------
@socketio.on("send_message")
def handle_message(data):
    room_id = str(data["room_id"])
    user_name = data.get("user", "Anonymous")
    text = data.get("text", "").strip()
    
    if not text or room_id not in rooms:
        return
    
    # Create message
    msg = {
        "user": user_name,
        "text": text,
        "timestamp": os.times().elapsed,
        "sid": request.sid
    }
    
    # Store and broadcast
    rooms[room_id]["messages"].append(msg)
    emit("new_message", msg, room=room_id)

# ---------------- Mic Slots ----------------
@socketio.on("join_mic")
def join_mic(data):
    room_id = str(data["room_id"])
    slot = str(data["slot"])
    user = data.get("user", "Anonymous")
    
    if room_id not in rooms:
        emit("mic_error", {"message": "Room not found"}, to=request.sid)
        return
    
    room = rooms[room_id]
    mic_slots = room["mic_slots"]
    
    # Remove user from any existing slot
    for s, u in list(mic_slots.items()):
        if u == user:
            del mic_slots[s]
    
    # Check if slot is available
    if slot in mic_slots:
        emit("mic_error", {"message": f"Mic slot {slot} is already taken"}, to=request.sid)
        return
    
    # Assign slot
    mic_slots[slot] = user
    emit("mic_update", mic_slots, room=room_id)
    
    # Notify room
    msg = {"user": "System", "text": f"{user} joined mic slot {slot}", "timestamp": os.times().elapsed}
    room["messages"].append(msg)
    emit("new_message", msg, room=room_id)

@socketio.on("leave_mic")
def leave_mic(data):
    room_id = str(data["room_id"])
    slot = str(data.get("slot"))
    user = data.get("user", "Anonymous")
    
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    mic_slots = room["mic_slots"]
    
    # Remove from specific slot or all slots
    if slot:
        if slot in mic_slots and mic_slots[slot] == user:
            del mic_slots[slot]
    else:
        for s, u in list(mic_slots.items()):
            if u == user:
                del mic_slots[s]
    
    emit("mic_update", mic_slots, room=room_id)

# ---------------- WebRTC Signaling ----------------
@socketio.on("webrtc_offer")
def webrtc_offer(data):
    target_sid = data.get("target")
    if target_sid:
        emit("webrtc_offer", data, to=target_sid)

@socketio.on("webrtc_answer")
def webrtc_answer(data):
    target_sid = data.get("target")
    if target_sid:
        emit("webrtc_answer", data, to=target_sid)

@socketio.on("webrtc_ice")
def webrtc_ice(data):
    target_sid = data.get("target")
    if target_sid:
        emit("webrtc_ice", data, to=target_sid)

# ---------------- Room Management ----------------
@app.route("/api/rooms", methods=["GET"])
def list_rooms():
    """API endpoint to list active rooms"""
    room_list = []
    for room_id, room_data in rooms.items():
        room_list.append({
            "id": room_id,
            "user_count": len(room_data["users"]),
            "message_count": len(room_data["messages"]),
            "active_mics": len(room_data["mic_slots"])
        })
    return jsonify({"rooms": room_list})

# ---------------- Error Handling ----------------
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500

# ---------------- Main ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    
    print(f"🚀 Starting Voice Chat Server on port {port}")
    print(f"📁 Static folder: {app.static_folder}")
    print(f"🔧 Debug mode: {debug}")
    print(f"🔥 Firebase: {'Enabled' if firebase_initialized else 'Disabled'}")
    
    socketio.run(
        app, 
        host="0.0.0.0", 
        port=port,
        debug=debug,
        allow_unsafe_werkzeug=True
    )