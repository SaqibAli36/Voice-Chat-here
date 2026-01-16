from flask import Flask, request, send_from_directory, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, join_room, leave_room, emit
import os
from datetime import datetime
import json
import hashlib
import hmac
import base64

app = Flask(__name__, static_folder="../frontend", static_url_path="")
CORS(app)

socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode="threading",
    logger=True,
    engineio_logger=True,
    ping_timeout=60,
    ping_interval=25
)

# ========== TENCENT TRTC CONFIGURATION ==========
# Get these from your Tencent Cloud Console: https://console.cloud.tencent.com/trtc
TRTC_CONFIG = {
    "SDK_APP_ID": os.environ.get("TRTC_SDK_APP_ID", 0),  # Replace with your SDKAppID
    "SECRET_KEY": os.environ.get("TRTC_SECRET_KEY", ""),  # Replace with your SecretKey
    "EXPIRE_TIME": 86400  # UserSig expiry time in seconds (24 hours)
}

# ========== DATA STORAGE ==========
rooms = {}  # room_id -> {users: {socket_id: user_data}, messages: [], mic_slots: {}}
# user_data: {name, joined_at, userId (for TRTC), socket_id}

# ========== HELPER FUNCTIONS ==========
def get_or_create_room(room_id):
    """Get existing room or create new one"""
    room_id = str(room_id)
    if room_id not in rooms:
        rooms[room_id] = {
            "users": {},           # socket_id -> user_data
            "messages": [],        # chat messages
            "mic_slots": {},       # slot_number -> username
            "user_slots": {},      # username -> slot_number
            "user_ids": {},        # username -> TRTC user_id
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
    return rooms[room_id]

def update_room_timestamp(room_id):
    """Update room's last updated timestamp"""
    if room_id in rooms:
        rooms[room_id]["updated_at"] = datetime.now().isoformat()

def generate_trtc_user_sig(user_id, sdk_app_id, secret_key, expire_time=86400):
    """
    Generate UserSig for Tencent TRTC
    In production, this should be done on your backend server
    """
    try:
        # Create content to sign
        current_time = int(datetime.now().timestamp())
        expire = current_time + expire_time
        
        # Create the content string
        content_to_sign = f"TLS.identifier:{user_id}\n" \
                         f"TLS.sdkappid:{sdk_app_id}\n" \
                         f"TLS.time:{current_time}\n" \
                         f"TLS.expire:{expire}\n"
        
        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            secret_key.encode('utf-8'),
            content_to_sign.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        # Base64 encode the signature
        signature_b64 = base64.b64encode(signature).decode('utf-8')
        
        # Create the final UserSig string
        user_sig = f"{current_time}:{expire}:{signature_b64}"
        
        return user_sig
        
    except Exception as e:
        print(f"Error generating UserSig: {e}")
        return None

# ========== ROUTES ==========
@app.route("/")
def index():
    """Serve the main index page"""
    return send_from_directory(app.static_folder, "index.html")

@app.route("/<path:filename>")
def serve_file(filename):
    """Serve static files"""
    return send_from_directory(app.static_folder, filename)

@app.route("/api/health")
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "rooms_count": len(rooms),
        "trtc_configured": bool(TRTC_CONFIG["SDK_APP_ID"] and TRTC_CONFIG["SECRET_KEY"])
    })

@app.route("/api/trtc/usersig", methods=["POST"])
def generate_usersig():
    """Generate UserSig for TRTC client (call this from frontend)"""
    try:
        data = request.get_json()
        user_id = data.get("userId", f"user_{int(datetime.now().timestamp())}")
        
        if not TRTC_CONFIG["SDK_APP_ID"] or not TRTC_CONFIG["SECRET_KEY"]:
            return jsonify({
                "error": "TRTC not configured. Please set TRTC_SDK_APP_ID and TRTC_SECRET_KEY environment variables."
            }), 500
        
        user_sig = generate_trtc_user_sig(
            user_id=user_id,
            sdk_app_id=TRTC_CONFIG["SDK_APP_ID"],
            secret_key=TRTC_CONFIG["SECRET_KEY"],
            expire_time=TRTC_CONFIG["EXPIRE_TIME"]
        )
        
        if user_sig:
            return jsonify({
                "userSig": user_sig,
                "sdkAppId": TRTC_CONFIG["SDK_APP_ID"],
                "userId": user_id,
                "expireTime": TRTC_CONFIG["EXPIRE_TIME"]
            })
        else:
            return jsonify({"error": "Failed to generate UserSig"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/rooms", methods=["GET"])
def list_rooms():
    """List all active rooms"""
    room_list = []
    for room_id, room_data in rooms.items():
        room_list.append({
            "id": room_id,
            "user_count": len(room_data["users"]),
            "active_mics": len(room_data["mic_slots"]),
            "created_at": room_data.get("created_at"),
            "updated_at": room_data.get("updated_at")
        })
    
    return jsonify({
        "rooms": room_list,
        "total": len(room_list)
    })

@app.route("/api/room/<room_id>", methods=["GET"])
def get_room_info(room_id):
    """Get specific room information"""
    room_id = str(room_id)
    if room_id in rooms:
        room_data = rooms[room_id]
        return jsonify({
            "id": room_id,
            "users": [
                {"name": user["name"], "joined_at": user.get("joined_at")}
                for user in room_data["users"].values()
            ],
            "mic_slots": room_data["mic_slots"],
            "message_count": len(room_data["messages"]),
            "created_at": room_data.get("created_at"),
            "updated_at": room_data.get("updated_at")
        })
    return jsonify({"error": "Room not found"}), 404

# ========== SOCKET.IO EVENTS ==========
@socketio.on("connect")
def handle_connect():
    """Handle new client connection"""
    print(f"✅ Client connected: {request.sid}")
    emit("connected", {"sid": request.sid, "status": "connected"})

@socketio.on("disconnect")
def handle_disconnect():
    """Handle client disconnection"""
    print(f"❌ Client disconnected: {request.sid}")
    
    # Find and clean up user from all rooms
    for room_id, room_data in rooms.items():
        if request.sid in room_data["users"]:
            user_data = room_data["users"][request.sid]
            username = user_data.get("name", "Unknown")
            
            # Remove from mic slots if user was in a slot
            if username in room_data["user_slots"]:
                slot = room_data["user_slots"][username]
                if str(slot) in room_data["mic_slots"]:
                    del room_data["mic_slots"][str(slot)]
                del room_data["user_slots"][username]
                
                # Remove user ID mapping
                if username in room_data["user_ids"]:
                    del room_data["user_ids"][username]
                
                # Notify room about user leaving mic slot
                emit("user_left_mic", {
                    "slot": slot,
                    "userName": username
                }, room=room_id)
            
            # Remove user from room
            del room_data["users"][request.sid]
            
            # Send leave message
            msg = {
                "user": "System",
                "text": f"{username} has left the room",
                "timestamp": datetime.now().isoformat()
            }
            room_data["messages"].append(msg)
            emit("new_message", msg, room=room_id)
            
            # Update room timestamp
            update_room_timestamp(room_id)
            
            # Clean empty room
            if not room_data["users"]:
                del rooms[room_id]
                print(f"🗑️ Room {room_id} deleted (empty)")
            break

@socketio.on("join_room")
def handle_join_room(data):
    room_id = str(data.get("roomId"))
    username = data.get("userName", f"User_{request.sid[:6]}")
    
    room = get_or_create_room(room_id)
    
    # Store user data
    user_data = {
        "name": username,
        "joined_at": datetime.now().isoformat(),
        "socket_id": request.sid
    }
    
    room["users"][request.sid] = user_data
    
    # Join Socket.IO room
    join_room(room_id)
    
    # Send room data WITHOUT previous messages
    emit("room_data", {
        "micSlots": room["mic_slots"],
        "users": [
            {"name": user["name"], "joined_at": user.get("joined_at")}
            for user in room["users"].values()
        ],
        "roomId": room_id,
        "yourName": username,
        # NO MESSAGES SENT HERE
    }, to=request.sid)
    
    # Send join message to room (only for NEW user)
    msg = {
        "user": "System",
        "text": f"{username} has joined the room",
        "timestamp": datetime.now().isoformat(),
        "isSystem": True
    }
    room["messages"].append(msg)
    emit("new_message", msg, room=room_id, include_self=False)
    
@socketio.on("leave_room")
def handle_leave_room(data):
    """Handle user leaving a room"""
    room_id = str(data.get("roomId", ""))
    username = data.get("userName", "")
    
    if not room_id or room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    if request.sid not in room["users"]:
        return
    
    # Get username from stored data
    user_data = room["users"][request.sid]
    username = user_data["name"]
    
    # Remove from mic slots if user was in a slot
    if username in room["user_slots"]:
        slot = room["user_slots"][username]
        if str(slot) in room["mic_slots"]:
            del room["mic_slots"][str(slot)]
        del room["user_slots"][username]
        
        # Remove user ID mapping
        if username in room["user_ids"]:
            del room["user_ids"][username]
        
        # Notify room about user leaving mic slot
        emit("user_left_mic", {
            "slot": slot,
            "userName": username
        }, room=room_id)
    
    # Remove user from room
    del room["users"][request.sid]
    
    # Send leave message
    msg = {
        "user": "System",
        "text": f"{username} has left the room",
        "timestamp": datetime.now().isoformat()
    }
    room["messages"].append(msg)
    emit("new_message", msg, room=room_id)
    
    # Leave Socket.IO room
    leave_room(room_id)
    
    # Update room timestamp
    update_room_timestamp(room_id)
    
    # Clean empty room
    if not room["users"]:
        del rooms[room_id]
        print(f"🗑️ Room {room_id} deleted (empty)")
    
    print(f"👋 {username} left room {room_id}")

@socketio.on("send_message")
def handle_send_message(data):
    """Handle chat message"""
    room_id = str(data.get("roomId"))
    username = data.get("userName", "Anonymous")
    text = data.get("text", "").strip()
    
    if not text or not room_id or room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    # Verify user is in room
    if request.sid not in room["users"]:
        return
    
    # Create message
    msg = {
        "user": username,
        "text": text,
        "timestamp": datetime.now().isoformat(),
        "socket_id": request.sid
    }
    
    # Store message
    room["messages"].append(msg)
    
    # Broadcast to room
    emit("new_message", msg, room=room_id)
    
    # Update room timestamp
    update_room_timestamp(room_id)
    
    print(f"💬 {username} in room {room_id}: {text[:50]}...")

@socketio.on("join_mic")
def handle_join_mic(data):
    """Handle user joining a mic slot"""
    room_id = str(data.get("roomId"))
    slot = int(data.get("slot", 0))
    username = data.get("userName", "")
    user_id = data.get("userId", "")
    
    if not room_id or room_id not in rooms or slot < 1 or slot > 10:
        emit("mic_error", {"message": "Invalid room or slot"}, to=request.sid)
        return
    
    room = rooms[room_id]
    
    # Verify user is in room
    if request.sid not in room["users"]:
        emit("mic_error", {"message": "You are not in this room"}, to=request.sid)
        return
    
    # Get username from stored data if not provided
    if not username:
        username = room["users"][request.sid]["name"]
    
    # Remove user from any existing slot
    if username in room["user_slots"]:
        old_slot = room["user_slots"][username]
        if str(old_slot) in room["mic_slots"]:
            del room["mic_slots"][str(old_slot)]
        
        # Notify about leaving old slot
        emit("user_left_mic", {
            "slot": old_slot,
            "userName": username
        }, room=room_id)
    
    # Check if slot is available
    if str(slot) in room["mic_slots"]:
        current_user = room["mic_slots"][str(slot)]
        if current_user != username:
            emit("mic_error", {"message": f"Slot {slot} is already taken by {current_user}"}, to=request.sid)
            return
    
    # Assign slot to user
    room["mic_slots"][str(slot)] = username
    room["user_slots"][username] = slot
    
    # Store user ID if provided
    if user_id:
        room["user_ids"][username] = user_id
    
    # Broadcast mic update to room
    emit("mic_update", room["mic_slots"], room=room_id)
    
    # Send specific join notification
    emit("user_joined_mic", {
        "slot": slot,
        "userName": username,
        "userId": user_id or room["user_ids"].get(username, "")
    }, room=room_id)
    
    # Send chat notification
    msg = {
        "user": "System",
        "text": f"{username} joined mic slot {slot}",
        "timestamp": datetime.now().isoformat()
    }
    room["messages"].append(msg)
    emit("new_message", msg, room=room_id)
    
    # Update room timestamp
    update_room_timestamp(room_id)
    
    print(f"🎤 {username} joined mic slot {slot} in room {room_id}")

@socketio.on("leave_mic")
def handle_leave_mic(data):
    """Handle user leaving a mic slot"""
    room_id = str(data.get("roomId"))
    slot = int(data.get("slot", 0))
    username = data.get("userName", "")
    
    if not room_id or room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    # Get username from stored data if not provided
    if not username and request.sid in room["users"]:
        username = room["users"][request.sid]["name"]
    
    if not username:
        return
    
    # Remove from specific slot
    if str(slot) in room["mic_slots"] and room["mic_slots"][str(slot)] == username:
        del room["mic_slots"][str(slot)]
        if username in room["user_slots"]:
            del room["user_slots"][username]
        
        # Notify room
        emit("user_left_mic", {
            "slot": slot,
            "userName": username
        }, room=room_id)
        
        # Send chat notification
        msg = {
            "user": "System",
            "text": f"{username} left mic slot {slot}",
            "timestamp": datetime.now().isoformat()
        }
        room["messages"].append(msg)
        emit("new_message", msg, room=room_id)
        
        # Update room timestamp
        update_room_timestamp(room_id)
        
        print(f"🔇 {username} left mic slot {slot} in room {room_id}")

@socketio.on("get_user_slot")
def handle_get_user_slot(data):
    """Handle request for user's slot information"""
    room_id = str(data.get("roomId"))
    user_id = data.get("userId", "")
    username = data.get("userName", "")
    
    if not room_id or room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    # Find user by user_id or username
    target_username = None
    target_slot = None
    
    if user_id:
        # Find by user_id
        for uname, uid in room["user_ids"].items():
            if uid == user_id:
                target_username = uname
                break
    elif username:
        target_username = username
    
    # Get slot if user found
    if target_username and target_username in room["user_slots"]:
        target_slot = room["user_slots"][target_username]
    
    if target_username and target_slot:
        emit("user_slot_info", {
            "userId": user_id,
            "userName": target_username,
            "slot": target_slot
        }, to=request.sid)

@socketio.on("ping")
def handle_ping():
    """Handle ping for connection testing"""
    emit("pong", {"timestamp": datetime.now().isoformat()})

# ========== ERROR HANDLERS ==========
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({"error": "Internal server error"}), 500

@app.route("/api/trtc/config", methods=["POST"])
def trtc_config():
    """Generate TRTC configuration for frontend (new endpoint)"""
    try:
        data = request.get_json()
        user_id = data.get("userId", f"user_{int(datetime.now().timestamp())}_{os.urandom(4).hex()}")
        room_id = data.get("roomId", 0)
        
        print(f"🔧 TRTC Config Request: userId={user_id[:15]}..., roomId={room_id}")
        
        # Check if TRTC is configured
        if not TRTC_CONFIG["SDK_APP_ID"] or not TRTC_CONFIG["SECRET_KEY"]:
            print("⚠️ TRTC not configured, returning test mode")
            return jsonify({
                "sdkAppId": 0,  # Test mode
                "userId": user_id,
                "userSig": "test_signature_for_development",
                "roomId": room_id,
                "success": True,
                "mode": "test",
                "message": "TRTC not configured. Running in test mode."
            })
        
        # Generate UserSig
        user_sig = generate_trtc_user_sig(
            user_id=user_id,
            sdk_app_id=int(TRTC_CONFIG["SDK_APP_ID"]),
            secret_key=TRTC_CONFIG["SECRET_KEY"],
            expire_time=TRTC_CONFIG["EXPIRE_TIME"]
        )
        
        if not user_sig:
            raise Exception("Failed to generate UserSig")
        
        return jsonify({
            "sdkAppId": int(TRTC_CONFIG["SDK_APP_ID"]),
            "userId": user_id,
            "userSig": user_sig,
            "roomId": room_id,
            "success": True,
            "mode": "production"
        })
        
    except Exception as e:
        print(f"❌ Error in TRTC config: {str(e)}")
        return jsonify({
            "error": "Failed to generate TRTC configuration",
            "message": str(e),
            "success": False
        }), 500
        
# ========== MAIN ENTRY POINT ==========
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    
    print("=" * 60)
    print("🚀 Voice Chat Server Starting...")
    print(f"🔌 Port: {port}")
    print(f"🐞 Debug Mode: {debug}")
    print(f"🎤 TRTC Configured: {'✅ Yes' if TRTC_CONFIG['SDK_APP_ID'] and TRTC_CONFIG['SECRET_KEY'] else '❌ No'}")
    print("=" * 60)
    print("Endpoints:")
    print(f"  • Main App: http://localhost:{port}")
    print(f"  • Health: http://localhost:{port}/api/health")
    print(f"  • Rooms List: http://localhost:{port}/api/rooms")
    print(f"  • UserSig Generator: http://localhost:{port}/api/trtc/usersig")
    print("=" * 60)
    
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=debug,
        allow_unsafe_werkzeug=True
    )