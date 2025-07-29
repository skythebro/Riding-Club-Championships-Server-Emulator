#!/usr/bin/env python3
"""
Riding Club Championships Server Emulator
A basic server emulator to handle the game's endpoints because the official servers are offline.
"""

import logging
import socket
import threading
import struct
import sqlite3
import hashlib
import zlib
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import debug configuration
try:
    from debug_config import (
        DEBUG_ENABLED as DEBUG_MODE,
        DEBUG_TCP_COMMUNICATION,
        DEBUG_HTTP_REQUESTS,
        DEBUG_BINARY_DATA,
        DEBUG_PROTOCOL_ANALYSIS,
        DEBUG_LOG_DIRECTORY,
        DEBUG_MAX_BINARY_LOG_SIZE,
        DEBUG_TIMESTAMP_FORMAT,
        DEBUG_CONSOLE_VERBOSE,
        DEBUG_CONSOLE_HEX_LIMIT,
        DEBUG_AUTO_ROTATE_LOGS,
        DEBUG_ROTATE_AFTER_CONNECTIONS
    )
    DEBUG_DIR = Path(DEBUG_LOG_DIRECTORY)
    logger.info("Debug configuration loaded from debug_config.py")
except ImportError:
    # Fallback to hardcoded values if debug_config.py is not found
    logger.warning("debug_config.py not found, using default debug settings")
    DEBUG_MODE = True
    DEBUG_TCP_COMMUNICATION = True
    DEBUG_HTTP_REQUESTS = True
    DEBUG_BINARY_DATA = True
    DEBUG_PROTOCOL_ANALYSIS = True
    DEBUG_LOG_DIRECTORY = "./debug_logs"
    DEBUG_MAX_BINARY_LOG_SIZE = 1000
    DEBUG_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
    DEBUG_CONSOLE_VERBOSE = True
    DEBUG_CONSOLE_HEX_LIMIT = 32
    DEBUG_AUTO_ROTATE_LOGS = False
    DEBUG_ROTATE_AFTER_CONNECTIONS = 10
    DEBUG_DIR = Path(DEBUG_LOG_DIRECTORY)

# Server configuration
SERVER_CONFIG = {
    "host": "127.0.0.1",  # localhost
    "http_port": 80,      # Standard HTTP port
    "tcp_port": 27130,    # TCP socket port from config
    "policy_port": 27132  # Policy port from config
}

def calculate_crc32_hash(text: str) -> int:
    """Calculate CRC32 hash for a string (matches C# Crc32.GetHash)"""
    # Convert to bytes using UTF-8 encoding
    text_bytes = text.encode('utf-8')
    # Calculate CRC32 and return as unsigned 32-bit integer
    crc = zlib.crc32(text_bytes) & 0xffffffff
    return crc

def verify_card_hashes():
    """Verify that our card hashes match expected values"""
    logic_main_hash = calculate_crc32_hash("logic_main")
    logger.info(f"Card hash verification:")
    logger.info(f"  'logic_main' -> {logic_main_hash} (expected: 3317978623)")
    if logic_main_hash == 3317978623:
        logger.info("  ✓ Hash matches!")
    else:
        logger.warning(f"  ✗ Hash mismatch! Expected 3317978623, got {logic_main_hash}")
    return logic_main_hash

class UserDatabase:
    """Manages user data storage and retrieval"""
    
    def __init__(self, db_path: str = "./users.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    player_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    access_token_hash TEXT,
                    user_state INTEGER DEFAULT 1,
                    access_level INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_type, source_id)
                )
            ''')
            
            # Create player_data table for game-specific data
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS player_data (
                    player_id INTEGER PRIMARY KEY,
                    name TEXT DEFAULT 'Player',
                    FOREIGN KEY (player_id) REFERENCES users (player_id)
                )
            ''')
            
            # Create indexes for performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_source ON users(source_type, source_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_player_id ON users(player_id)')
            
            conn.commit()
            logger.info("Database initialized successfully")
    
    def get_or_create_user(self, source_type: str, source_id: str, access_token: str = "") -> Tuple[int, dict]:
        """Get existing user or create new one. Returns (player_id, user_data)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Hash the access token for security
            token_hash = hashlib.sha256(access_token.encode()).hexdigest() if access_token else ""
            
            # Try to find existing user
            cursor.execute('''
                SELECT u.player_id, u.user_state, u.access_level, u.created_at,
                       pd.name
                FROM users u
                LEFT JOIN player_data pd ON u.player_id = pd.player_id
                WHERE u.source_type = ? AND u.source_id = ?
            ''', (source_type, source_id))
            
            result = cursor.fetchone()
            
            if result:
                # Update last login
                player_id = result[0]
                cursor.execute('''
                    UPDATE users SET last_login = CURRENT_TIMESTAMP, access_token_hash = ?
                    WHERE player_id = ?
                ''', (token_hash, player_id))
                
                user_data = {
                    'player_id': result[0],
                    'user_state': result[1],
                    'access_level': result[2],
                    'created_at': result[3],
                    'name': result[4] or 'Player'
                }
                
                logger.info(f"Existing user logged in: {player_id} ({source_type}:{source_id})")
                return player_id, user_data
            
            else:
                # Create new user
                cursor.execute('''
                    INSERT INTO users (source_type, source_id, access_token_hash, user_state, access_level)
                    VALUES (?, ?, ?, 1, 0)
                ''', (source_type, source_id, token_hash))
                
                player_id = cursor.lastrowid
                
                default_name = f"Player{player_id}"
                cursor.execute('''
                    INSERT INTO player_data (player_id, name)
                    VALUES (?, ?)
                ''', (player_id, default_name))
                
                user_data = {
                    'player_id': player_id,
                    'user_state': 1,  # Menu
                    'access_level': 0,  # User
                    'created_at': datetime.now().isoformat(),
                    'name': default_name
                }
                
                conn.commit()
                logger.info(f"New user created: {player_id} ({source_type}:{source_id})")
                return player_id, user_data
    
    def update_player_data(self, player_id: int, **kwargs) -> bool:
        """Update player data"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Build dynamic update query
                set_clauses = []
                values = []
                
                for key, value in kwargs.items():
                    if key in ['name']:
                        set_clauses.append(f"{key} = ?")
                        values.append(value)
                
                if set_clauses:
                    query = f"UPDATE player_data SET {', '.join(set_clauses)} WHERE player_id = ?"
                    values.append(player_id)
                    cursor.execute(query, values)
                    conn.commit()
                    return True
                
                return False
                
        except Exception as e:
            logger.error(f"Error updating player data: {e}")
            return False
    
    def get_player_data(self, player_id: int) -> Optional[dict]:
        """Get complete player data"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT u.user_state, u.access_level, u.created_at, u.last_login,
                           pd.name
                    FROM users u
                    LEFT JOIN player_data pd ON u.player_id = pd.player_id
                    WHERE u.player_id = ?
                ''', (player_id,))
                
                result = cursor.fetchone()
                if result:
                    return {
                        'player_id': player_id,
                        'user_state': result[0],
                        'access_level': result[1],
                        'created_at': result[2],
                        'last_login': result[3],
                        'name': result[4] or 'Player'
                    }
                return None
                
        except Exception as e:
            logger.error(f"Error getting player data: {e}")
            return None
    
    def get_stats(self) -> dict:
        """Get database statistics"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) FROM users")
                total_users = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM users WHERE last_login >= datetime('now', '-24 hours')")
                active_24h = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-24 hours')")
                new_24h = cursor.fetchone()[0]
                
                return {
                    'total_users': total_users,
                    'active_last_24h': active_24h,
                    'new_last_24h': new_24h
                }
                
        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            return {'total_users': 0, 'active_last_24h': 0, 'new_last_24h': 0}
    
    def get_all_users(self) -> list:
        """Get all users (for debugging)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT u.player_id, u.source_type, u.source_id, u.user_state, u.access_level,
                           u.created_at, u.last_login, pd.name
                    FROM users u
                    LEFT JOIN player_data pd ON u.player_id = pd.player_id
                    ORDER BY u.last_login DESC
                    LIMIT 100
                ''')
                
                results = cursor.fetchall()
                users = []
                for row in results:
                    users.append({
                        'player_id': row[0],
                        'source_type': row[1],
                        'source_id': row[2],
                        'user_state': row[3],
                        'access_level': row[4],
                        'created_at': row[5],
                        'last_login': row[6],
                        'name': row[7] or 'Unknown',
                    })
                
                return users
                
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []

class RCCServerEmulator:
    def __init__(self):
        self.app = FastAPI(title="RCC Server Emulator", version="1.0.0")
        self.connected_clients: Dict[str, WebSocket] = {}
        self.tcp_clients: Dict[str, socket.socket] = {}
        self.tcp_server_running = False
        self.policy_server_running = False
        self.database = UserDatabase()
        
        # Verify card hash calculations
        verify_card_hashes()
        
        self.setup_debug_logging()
        self.setup_middleware()
        self.setup_routes()
    
    def setup_debug_logging(self):
        """Setup debug logging to files"""
        if DEBUG_MODE:
            # Create debug directory
            DEBUG_DIR.mkdir(exist_ok=True)
            
            # Setup file handlers for different types of logs
            from datetime import datetime
            timestamp = datetime.now().strftime(DEBUG_TIMESTAMP_FORMAT)
            
            # TCP communication log (if enabled)
            if DEBUG_TCP_COMMUNICATION:
                self.tcp_log_file = DEBUG_DIR / f"tcp_communication_{timestamp}.log"
                self.tcp_logger = logging.getLogger('tcp_debug')
                tcp_handler = logging.FileHandler(self.tcp_log_file)
                tcp_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
                self.tcp_logger.addHandler(tcp_handler)
                self.tcp_logger.setLevel(logging.DEBUG)
            else:
                self.tcp_logger = None
                self.tcp_log_file = None
            
            # HTTP communication log (if enabled)
            if DEBUG_HTTP_REQUESTS:
                self.http_log_file = DEBUG_DIR / f"http_communication_{timestamp}.log"
                self.http_logger = logging.getLogger('http_debug')
                http_handler = logging.FileHandler(self.http_log_file)
                http_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
                self.http_logger.addHandler(http_handler)
                self.http_logger.setLevel(logging.DEBUG)
            else:
                self.http_logger = None
                self.http_log_file = None
            
            # Binary data log (if enabled)
            if DEBUG_BINARY_DATA:
                self.binary_log_file = DEBUG_DIR / f"binary_data_{timestamp}.log"
                self.binary_logger = logging.getLogger('binary_debug')
                binary_handler = logging.FileHandler(self.binary_log_file)
                binary_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
                self.binary_logger.addHandler(binary_handler)
                self.binary_logger.setLevel(logging.DEBUG)
            else:
                self.binary_logger = None
                self.binary_log_file = None
            
            # Log which debug features are enabled
            enabled_features = []
            if DEBUG_TCP_COMMUNICATION:
                enabled_features.append(f"TCP Log: {self.tcp_log_file}")
            if DEBUG_HTTP_REQUESTS:
                enabled_features.append(f"HTTP Log: {self.http_log_file}")
            if DEBUG_BINARY_DATA:
                enabled_features.append(f"Binary Log: {self.binary_log_file}")
            
            logger.info(f"Debug logging enabled:")
            for feature in enabled_features:
                logger.info(f"  {feature}")
                
            if DEBUG_CONSOLE_VERBOSE:
                logger.info(f"Console hex limit: {DEBUG_CONSOLE_HEX_LIMIT} bytes")
            if DEBUG_AUTO_ROTATE_LOGS:
                logger.info(f"Auto-rotate logs after {DEBUG_ROTATE_AFTER_CONNECTIONS} connections")
        else:
            self.tcp_logger = None
            self.http_logger = None
            self.binary_logger = None
    
    def debug_log_tcp(self, message: str):
        """Log TCP-related debug information"""
        if DEBUG_MODE and DEBUG_TCP_COMMUNICATION:
            if self.tcp_logger:
                self.tcp_logger.debug(message)
            if DEBUG_CONSOLE_VERBOSE:
                logger.debug(f"[TCP] {message}")
    
    def debug_log_http(self, message: str):
        """Log HTTP-related debug information"""
        if DEBUG_MODE and DEBUG_HTTP_REQUESTS:
            if self.http_logger:
                self.http_logger.debug(message)
            if DEBUG_CONSOLE_VERBOSE:
                logger.debug(f"[HTTP] {message}")
    
    def debug_log_binary(self, data: bytes, direction: str, client_id: str, description: str = ""):
        """Log binary data with hex dump"""
        if DEBUG_MODE and DEBUG_BINARY_DATA:
            # Limit data size for logging if configured
            log_data = data
            if DEBUG_MAX_BINARY_LOG_SIZE > 0 and len(data) > DEBUG_MAX_BINARY_LOG_SIZE:
                log_data = data[:DEBUG_MAX_BINARY_LOG_SIZE]
                truncated = True
            else:
                truncated = False
            
            hex_data = log_data.hex()
            readable_data = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in log_data)
            
            message = f"{direction} - {client_id} - {description}\n"
            message += f"Length: {len(data)} bytes"
            if truncated:
                message += f" (showing first {len(log_data)} bytes)\n"
            else:
                message += "\n"
            message += f"Hex: {hex_data}\n"
            message += f"ASCII: {readable_data}\n"
            message += f"Raw bytes: {list(log_data)}\n"
            message += "-" * 80
            
            if self.binary_logger:
                self.binary_logger.debug(message)
            
            # Console output with hex limit
            if DEBUG_CONSOLE_VERBOSE:
                console_data = data
                if DEBUG_CONSOLE_HEX_LIMIT > 0 and len(data) > DEBUG_CONSOLE_HEX_LIMIT:
                    console_data = data[:DEBUG_CONSOLE_HEX_LIMIT]
                    console_hex = console_data.hex()
                    logger.debug(f"[BINARY] {direction} - {client_id} - {description} ({len(data)} bytes, showing first {len(console_data)}): {console_hex}...")
                else:
                    console_hex = console_data.hex()
                    logger.debug(f"[BINARY] {direction} - {client_id} - {description} ({len(data)} bytes): {console_hex}")
        
    def setup_middleware(self):
        """Setup CORS and other middleware"""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def setup_routes(self):
        """Setup all HTTP and WebSocket routes"""
        
        # Root endpoint
        @self.app.get("/")
        async def root():
            return {"message": "RCC Server Emulator is running", "status": "online"}
        
        # Health check endpoint
        @self.app.get("/health")
        async def health_check():
            db_stats = self.database.get_stats()
            return {
                "status": "healthy", 
                "clients_connected": len(self.connected_clients),
                "database_stats": db_stats
            }
        
        # MochiWeb endpoints (game API)
        @self.app.get("/mochiweb/")
        async def mochiweb_root():
            return {"service": "mochiweb", "status": "online"}
        
        # Card/Catalogue endpoints
        @self.app.get("/mochiweb/cards")
        async def get_cards():
            """Get all cards for the catalogue"""
            try:
                cards = [
                    {
                        "category": "LogicMain",
                        "_id": "logic_main",
                        "ladder_top_size": 100,
                        "max_best_scores": 10,
                        "player_name_max_size": 20,
                        "horse_name_max_size": 20,
                        "level_up_bonus": {
                            "money": {
                                "coins": 100,
                                "skill_tickets": 1
                            },
                            "xp": 0,
                            "ap": 25,
                            "items": {
                                ""
                            },
                        },
                        "challenge_win": {
                            "money": {
                                "coins": 100,
                                "skill_tickets": 0
                            },
                            "xp": 100,
                            "ap": 25,
                            "items": {
                                ""
                            },
                        },
                        "levels_xp": [100, 250, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000],
                        "skill_points_for_level_up": 1.0,
                        "change_avatar": {
                            "coins": 100,
                            "skill_tickets": 0
                        },
                        "flags": [],
                        "premuim": {
                            "skill_tickets_rate": 1.5,
                            "xp_rate": 1.2,
                            "loot_rate": 2.0,
                            "ap_cost_rate": 1.0,
                            "ap_restore_rate": 100.0,
                            "ap_max": 10000,
                            "strength": 1,
                            "timing": 1,
                            "speed": 1,
                            "acceleration": 1,
                            "stamina": 1,
                            "obedience": 1,
                        }
                    }
                ]
                return {"success": True, "cards": cards, "count": len(cards)}
            except Exception as e:
                logger.error(f"Error getting cards: {e}")
                return {"success": False, "error": str(e)}
        
        # Image serving endpoints
        @self.app.get("/rcc/")
        async def rcc_images_root():
            return {"service": "rcc_images", "status": "online"}
        
        @self.app.get("/rcc/{image_path:path}")
        async def serve_image(image_path: str):
            """Serve game images - returns placeholder for now"""
            # You can add actual image files to a local directory
            image_dir = Path("./images")
            image_file = image_dir / image_path
            
            if image_file.exists():
                return FileResponse(image_file)
            else:
                # Return a placeholder response
                return {"error": "Image not found", "path": image_path}
        
        # Proxy endpoints
        @self.app.get("/proxy/{path:path}")
        async def proxy_request(path: str, request: Request):
            """Basic proxy functionality"""
            logger.info(f"Proxy request: {path}")
            return {"proxied": True, "path": path, "method": request.method}
        
        # Facebook Open Graph endpoints
        @self.app.get("/rcc/open_graph/")
        async def facebook_og_root():
            return {"service": "facebook_og", "status": "online"}
        
        @self.app.get("/rcc/open_graph/{og_path:path}")
        async def facebook_og(og_path: str):
            """Facebook Open Graph metadata"""
            return {
                "og:title": "Riding Club Championships",
                "og:description": "Horse riding championship game",
                "og:type": "game",
                "og:url": f"https://localhost/rcc/open_graph/{og_path}",
                "fb:app_id": "ridingclub"
            }
        
        # Debug endpoints for development
        @self.app.get("/debug/users")
        async def list_all_users():
            """List all users in the database (debug endpoint)"""
            try:
                users = self.database.get_all_users()
                return {"success": True, "users": users, "count": len(users)}
            except Exception as e:
                logger.error(f"Error listing users: {e}")
                return {"success": False, "error": str(e)}
        
        @self.app.get("/debug/tcp_clients")
        async def list_tcp_clients():
            """List all active TCP clients (debug endpoint)"""
            clients = []
            for client_id in self.tcp_clients.keys():
                clients.append({"client_id": client_id, "status": "connected"})
            return {"success": True, "tcp_clients": clients, "count": len(clients)}
        
        @self.app.post("/debug/create_test_user")
        async def create_test_user():
            """Create a test user for debugging"""
            try:
                player_id, user_data = self.database.get_or_create_user(
                    "Debug", 
                    "test_user_001", 
                    "debug_token_123"
                )
                # Update with some test data
                self.database.update_player_data(player_id, {
                    "name": "TestPlayer",
                    "level": 5,
                    "coins": 2500,
                    "experience": 1200,
                    "data_json": {"test_progress": "level_5_completed"}
                })
                
                return {
                    "success": True, 
                    "message": "Test user created",
                    "player_id": player_id,
                    "user_data": user_data
                }
            except Exception as e:
                logger.error(f"Error creating test user: {e}")
                return {"success": False, "error": str(e)}
        
        @self.app.get("/debug/logs/recent")
        async def get_recent_debug_logs():
            """Get recent debug log entries"""
            if not DEBUG_MODE:
                return {"success": False, "error": "Debug mode not enabled"}
            
            try:
                logs = {"tcp": [], "http": [], "binary": []}
                
                # Read last 50 lines from each log file
                if hasattr(self, 'tcp_log_file') and self.tcp_log_file.exists():
                    with open(self.tcp_log_file, 'r') as f:
                        logs["tcp"] = f.readlines()[-50:]
                
                if hasattr(self, 'http_log_file') and self.http_log_file.exists():
                    with open(self.http_log_file, 'r') as f:
                        logs["http"] = f.readlines()[-50:]
                
                if hasattr(self, 'binary_log_file') and self.binary_log_file.exists():
                    with open(self.binary_log_file, 'r') as f:
                        logs["binary"] = f.readlines()[-20:]  # Binary logs are longer
                
                return {"success": True, "logs": logs}
            except Exception as e:
                logger.error(f"Error reading debug logs: {e}")
                return {"success": False, "error": str(e)}
        
        @self.app.get("/debug/card_hash/{card_id}")
        async def calculate_card_hash(card_id: str):
            """Calculate CRC32 hash for a card ID"""
            try:
                hash_value = calculate_crc32_hash(card_id)
                return {
                    "success": True,
                    "card_id": card_id,
                    "hash": hash_value,
                    "hash_hex": f"0x{hash_value:08X}",
                    "verification": {
                        "logic_main_expected": 3317978623,
                        "logic_main_actual": calculate_crc32_hash("logic_main"),
                        "matches": calculate_crc32_hash("logic_main") == 3317978623
                    }
                }
            except Exception as e:
                logger.error(f"Error calculating card hash: {e}")
                return {"success": False, "error": str(e)}
        
        # WebSocket endpoint for real-time communication
        @self.app.websocket("/websocket")
        async def websocket_endpoint(websocket: WebSocket):
            await self.handle_websocket(websocket)
    
    async def handle_websocket(self, websocket: WebSocket):
        """Handle WebSocket connections"""
        client_id = f"client_{len(self.connected_clients)}"
        await websocket.accept()
        self.connected_clients[client_id] = websocket
        
        logger.info(f"Client {client_id} connected. Total clients: {len(self.connected_clients)}")
        
        try:
            # Send welcome message
            await websocket.send_json({
                "type": "welcome",
                "client_id": client_id,
                "message": "Connected to RCC Server Emulator"
            })
            
            # Listen for messages
            while True:
                try:
                    data = await websocket.receive_json()
                    logger.info(f"Received from {client_id}: {data}")
                    
                    # Echo back or handle specific message types
                    await self.handle_websocket_message(client_id, data, websocket)
                    
                except Exception as e:
                    logger.error(f"Error handling WebSocket message: {e}")
                    break
                    
        except WebSocketDisconnect:
            logger.info(f"Client {client_id} disconnected")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            if client_id in self.connected_clients:
                del self.connected_clients[client_id]
            logger.info(f"Client {client_id} removed. Total clients: {len(self.connected_clients)}")
    
    async def handle_websocket_message(self, client_id: str, data: dict, websocket: WebSocket):
        """Handle specific WebSocket message types"""
        message_type = data.get("type", "unknown")
        
        if message_type == "ping":
            await websocket.send_json({"type": "pong", "timestamp": data.get("timestamp")})
        
        elif message_type == "game_action":
            # Handle game-specific actions
            action = data.get("action")
            logger.info(f"Game action from {client_id}: {action}")
            
            # Send response based on action
            response = {
                "type": "game_response",
                "action": action,
                "success": True,
                "data": {"message": f"Action {action} processed"}
            }
            await websocket.send_json(response)
        
        elif message_type == "chat":
            # Broadcast chat messages to all connected clients
            message = data.get("message", "")
            broadcast_data = {
                "type": "chat",
                "client_id": client_id,
                "message": message
            }
            await self.broadcast_message(broadcast_data, exclude_client=client_id)
        
        else:
            # Echo unknown messages
            await websocket.send_json({
                "type": "echo",
                "original": data,
                "message": f"Echoing message type: {message_type}"
            })
    
    async def broadcast_message(self, message: dict, exclude_client: Optional[str] = None):
        """Broadcast message to all connected clients"""
        disconnected_clients = []
        
        for client_id, websocket in self.connected_clients.items():
            if exclude_client and client_id == exclude_client:
                continue
                
            try:
                await websocket.send_json(message)
            except:
                disconnected_clients.append(client_id)
        
        # Clean up disconnected clients
        for client_id in disconnected_clients:
            del self.connected_clients[client_id]
    
    def start_tcp_server(self):
        """Start the TCP socket server for game connections"""
        def tcp_server():
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            try:
                server_socket.bind((SERVER_CONFIG['host'], SERVER_CONFIG['tcp_port']))
                server_socket.listen(5)
                self.tcp_server_running = True
                logger.info(f"TCP Server listening on {SERVER_CONFIG['host']}:{SERVER_CONFIG['tcp_port']}")
                
                while self.tcp_server_running:
                    try:
                        client_socket, address = server_socket.accept()
                        client_id = f"tcp_client_{address[0]}_{address[1]}"
                        self.tcp_clients[client_id] = client_socket
                        
                        logger.info(f"TCP Client connected: {client_id} from {address}")
                        
                        # Start a thread to handle this client
                        client_thread = threading.Thread(
                            target=self.handle_tcp_client,
                            args=(client_socket, client_id, address)
                        )
                        client_thread.daemon = True
                        client_thread.start()
                        
                    except Exception as e:
                        if self.tcp_server_running:
                            logger.error(f"Error accepting TCP connection: {e}")
                        break
                        
            except Exception as e:
                logger.error(f"TCP Server error: {e}")
            finally:
                server_socket.close()
                logger.info("TCP Server stopped")
        
        # Start TCP server in a separate thread
        tcp_thread = threading.Thread(target=tcp_server)
        tcp_thread.daemon = True
        tcp_thread.start()
    
    def handle_tcp_client(self, client_socket: socket.socket, client_id: str, address):
        """Handle individual TCP client connections"""
        try:
            logger.info(f"TCP Client {client_id} connected, sending cards data immediately...")
            self.debug_log_tcp(f"NEW CONNECTION: {client_id} from {address}")
            
            # Send card data immediately upon connection for better efficiency
            # This gives the game time to load cards before login process
            self.send_cards_to_service(client_socket, client_id)
            
            # Set socket timeout for better handling
            client_socket.settimeout(30.0)  # Longer timeout to wait for client requests
            logged_in = False
            
            while self.tcp_server_running:
                try:
                    # Try to receive data
                    data = client_socket.recv(2000000)  # Same buffer size as game
                    
                    if not data:
                        logger.info(f"TCP Client {client_id} disconnected (no data)")
                        self.debug_log_tcp(f"DISCONNECT: {client_id} - No data received")
                        break
                    
                    logger.info(f"Received TCP data from {client_id}: {len(data)} bytes")
                    self.debug_log_tcp(f"RECEIVED: {client_id} - {len(data)} bytes")
                    
                    # Log binary data with hex dump
                    self.debug_log_binary(data, "INCOMING", client_id, "Raw TCP data received")
                    
                    # Log first few bytes for debugging
                    if len(data) >= 4:
                        logger.info(f"First 8 bytes: {data[:8].hex()}")
                    
                    # Process the received data
                    response, service_id = self.process_tcp_message(data, client_id)
                    
                    if response:
                        logger.info(f"Sending response to {client_id}: {len(response)} bytes")
                        self.debug_log_tcp(f"SENDING: {client_id} - {len(response)} bytes")
                        
                        # Log outgoing binary data
                        self.debug_log_binary(response, "OUTGOING", client_id, "Response data sent")
                        
                        if len(response) <= 20:  # Log small responses completely
                            logger.info(f"Response bytes: {response.hex()}")
                        
                        client_socket.send(response)
                                                
                        # Check if this was a login response
                        # New format: VarInt length + [177, 2] + Function ID (0) + RPC ID (2 bytes) + Status (1 byte) + ReplyLogin data
                        # We need to skip the VarInt prefix to check the actual message content
                        if len(response) >= 7:  # Minimum: 1 byte VarInt + 6 bytes message content
                            # Decode VarInt to find where the actual message starts
                            varint_offset = 0
                            while varint_offset < len(response) and (response[varint_offset] & 0x80) != 0:
                                varint_offset += 1
                            varint_offset += 1  # Include the final byte
                            
                            # Check if we have enough data after the VarInt
                            # New format: VarInt + [Service_ID=100] + [Function_ID=0] + [RPC_ID] + [Status=0] + ReplyLogin data
                            if len(response) >= varint_offset + 5:
                                # Check the message content: [100, 0, RPC_ID_LOW, RPC_ID_HIGH, STATUS]
                                message_start = varint_offset
                                if (response[message_start] == 100 and  # Service ID = 100 (ServiceLogin)
                                    response[message_start + 1] == 0 and  # Function ID = 0 (Login)
                                    response[message_start + 4] == 0 and  # Status = 0 (Success)
                                    service_id in [100, 177]):
                                    
                                    logged_in = True
                                    logger.info(f"Client {client_id} successfully logged in via Service {service_id}")
                                    self.debug_log_tcp(f"LOGIN SUCCESS: {client_id} - Service={service_id}")
                                    
                                    # Log the response format for debugging
                                    rpc_id_from_response = struct.unpack('<H', response[message_start + 2:message_start + 4])[0]
                                    status_from_response = response[message_start + 4]
                                    logger.info(f"Login response format: VarInt + Service_ID=100 + Function_ID=0 + RPC_ID={rpc_id_from_response} + Status={status_from_response} + ReplyLogin data")
                                    
                                    # Don't send any additional data immediately after login
                                    # Let the client make the next request when it's ready
                                    logger.info(f"Login complete for {client_id}. Connection established, waiting for next client request...")
                                    
                                    # Cards data already sent upon initial connection for better efficiency
                                    
                                    # Log socket state after login
                                    try:
                                        # Check if socket is still connected
                                        logger.info(f"Socket state after login: connected={client_socket.fileno() != -1}")
                                        self.debug_log_tcp(f"SOCKET STATE: {client_id} - FD={client_socket.fileno()}, State=connected")
                                    except Exception as sock_err:
                                        logger.error(f"Error checking socket state: {sock_err}")
                                        self.debug_log_tcp(f"SOCKET ERROR: {client_id} - {str(sock_err)}")
                                
                        elif len(response) >= 3 and response[2] == 0:
                            logger.info(f"Service {service_id} responded successfully, but this was not a login")
                            self.debug_log_tcp(f"SERVICE SUCCESS: {client_id} - Service={service_id} responded OK")
                    else:
                        self.debug_log_tcp(f"NO RESPONSE: {client_id} - No response generated for received data")
                    
                except socket.timeout:
                    # For logged in clients, just continue waiting for their next request
                    if logged_in:
                        self.debug_log_tcp(f"TIMEOUT: {client_id} - Logged in client timeout, continuing to wait for client requests")
                        # Don't send any unsolicited data - let the client make requests when ready
                        continue
                    else:
                        self.debug_log_tcp(f"TIMEOUT: {client_id} - Socket timeout (not logged in yet)")
                    continue
                except Exception as e:
                    logger.error(f"Error handling TCP client {client_id}: {e}")
                    self.debug_log_tcp(f"ERROR: {client_id} - {str(e)}")
                    break
                    
        except Exception as e:
            logger.error(f"TCP client handler error for {client_id}: {e}")
            self.debug_log_tcp(f"HANDLER ERROR: {client_id} - {str(e)}")
        finally:
            try:
                client_socket.close()
            except:
                pass
            
            if client_id in self.tcp_clients:
                del self.tcp_clients[client_id]
            
            logger.info(f"TCP Client {client_id} cleaned up")
            self.debug_log_tcp(f"CLEANUP: {client_id} - Connection closed and cleaned up")
    
    def process_tcp_message(self, data: bytes, client_id: str) -> tuple[Optional[bytes], int]:
        """Process TCP messages from the game client"""
        try:
            if len(data) < 3:
                logger.info(f"TCP data too short from {client_id}: {len(data)} bytes")
                self.debug_log_tcp(f"MESSAGE TOO SHORT: {client_id} - {len(data)} bytes")
                return None, 0
            
            # Log the raw bytes for analysis
            logger.info(f"Raw message bytes: {data[:16].hex()} (first 16 bytes)")
            
            # Try different interpretations of the message format
            # Based on analysis: [177, 2, 100, ...] where 100 is the actual service ID
            # It seems the first two bytes might be a header/length prefix
            
            # Try parsing as header + service_id format
            potential_service_id = data[2] if len(data) > 2 else 0
            
            # Also check the first byte in case it's the standard format
            first_byte_service_id = data[0]
            
            logger.info(f"Potential service IDs: first_byte={first_byte_service_id}, third_byte={potential_service_id}")
            
            # Determine which service ID to use based on known service IDs
            service_id = first_byte_service_id
            message_offset = 0  # Standard format: [service_id][rpc_id][data...]
            
            # Check if the third byte looks like a known service ID
            if potential_service_id in [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]:
                logger.info(f"Third byte ({potential_service_id}) looks like a valid service ID, using header format")
                service_id = potential_service_id
                message_offset = 2  # Header format: [header][header][service_id][rpc_id][data...]
            elif first_byte_service_id in [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]:
                logger.info(f"First byte ({first_byte_service_id}) looks like a valid service ID, using standard format")
                service_id = first_byte_service_id
                message_offset = 0
            else:
                logger.info(f"Neither byte looks like a known service ID, defaulting to third byte: {potential_service_id}")
                service_id = potential_service_id
                message_offset = 2
            
            # The game uses service IDs starting from 100:
            # ServiceLogin = 100, ServiceCards = 101, ServiceDebug = 102, etc.
            service_name = "Unknown"
            if service_id == 100:
                service_name = "ServiceLogin"
            elif service_id == 101:
                service_name = "ServiceCards"
            elif service_id == 102:
                service_name = "ServiceDebug"
            elif service_id == 103:
                service_name = "ServiceChat"
            elif service_id == 104:
                service_name = "ServicePaddock"
            elif service_id == 105:
                service_name = "ServiceSocial"
            elif service_id == 106:
                service_name = "ServiceCourseEditor"
            elif service_id == 107:
                service_name = "ServiceMatch"
            elif service_id == 108:
                service_name = "ServiceGame"
            elif service_id == 109:
                service_name = "ServicePlayer"
            
            logger.info(f"Service ID {service_id} = {service_name}")
            self.debug_log_tcp(f"SERVICE IDENTIFIED: {client_id} - Service={service_id} ({service_name})")
            
            # Standard format parsing accounting for message offset
            rpc_id_offset = message_offset + 1  # Skip service_id
            if len(data) >= rpc_id_offset + 2:
                rpc_id = struct.unpack('<H', data[rpc_id_offset:rpc_id_offset+2])[0]
            else:
                rpc_id = 0
            
            logger.info(f"TCP Binary message from {client_id}: service={service_id}, rpc_id={rpc_id}, total_length={len(data)}")
            self.debug_log_tcp(f"MESSAGE PARSED: {client_id} - Service={service_id}, RPC={rpc_id}, Length={len(data)}")
            
            # Handle different service types using correct service IDs
            # Note: For responses, we don't include the service_id - the client's Recv method handles that
            # Pass the data with the correct offset to account for the header
            actual_data = data[message_offset:] if message_offset > 0 else data
            
            # Extract payload data (skip service_id and rpc_id)
            payload_offset = 1 + 2  # service_id (1 byte) + rpc_id (2 bytes)
            payload_data = actual_data[payload_offset:] if len(actual_data) > payload_offset else b''
            
            if service_id == 100:  # ServiceLogin
                self.debug_log_tcp(f"HANDLING LOGIN SERVICE: {client_id}")
                self.debug_log_binary(payload_data, "LOGIN_PAYLOAD", client_id, "Login payload data (ProtocolVersion + SteamID + AccessToken)")
                return self.handle_login_service(payload_data, rpc_id, client_id), service_id
            elif service_id == 108:  # ServiceGame
                self.debug_log_tcp(f"HANDLING SERVICE GAME: {client_id}")
                return self.handle_service_game(actual_data, client_id), service_id
            elif service_id in [102, 103, 104, 105, 106, 107, 109]: # 101 is skipped as the client doesn't use it, 108 handled above
                self.debug_log_tcp(f"HANDLING GENERIC SERVICE {service_id}: {client_id}")
                return self.handle_generic_service(service_id, rpc_id, payload_data, client_id), service_id
            else:
                logger.info(f"Unknown service ID: {service_id}, responding with generic success")
                self.debug_log_tcp(f"UNKNOWN SERVICE: {client_id} - Service={service_id}, using generic handler")
                return self.handle_generic_service(service_id, rpc_id, payload_data, client_id), service_id
                
        except Exception as e:
            logger.error(f"Error processing TCP message: {e}")
            self.debug_log_tcp(f"PROCESSING ERROR: {client_id} - {str(e)}")
            return None, 0
    
    def handle_generic_service(self, service_id: int, rpc_id: int, data: bytes, client_id: str) -> bytes:
        """Handle generic service requests with basic success response"""
        try:
            logger.info(f"Handling generic service {service_id} for client {client_id}")
            
            response = bytearray()
            
            # RPC ID (2 bytes, little endian) - response format doesn't include service_id
            response.extend(struct.pack('<H', rpc_id))
            
            # Status (0 for success)
            response.append(0)
            
            # Minimal success data (empty for now)
            # Different services might expect different response formats
            # For now, we'll send an empty success response
            
            logger.info(f"Sending generic success response to {client_id} for service {service_id}")
            return bytes(response)
            
        except Exception as e:
            logger.error(f"Error handling generic service: {e}")
            return self.create_error_response(rpc_id, f"Service error: {str(e)}", service_id)
    
    def handle_service_game(self, data: bytes, client_id: str) -> bytes:
        """Handle ServiceGame requests - uses function IDs, not RPC IDs"""
        try:
            logger.info(f"Handling ServiceGame request for client {client_id}")
            
            # ServiceGame messages format: [service_id][function_id][data...]
            # We already know service_id is 108, so extract function_id
            if len(data) < 2:
                logger.error(f"ServiceGame data too short: {len(data)} bytes")
                return b''
            
            function_id = data[1]  # Second byte is function_id
            logger.info(f"ServiceGame function_id: {function_id}")
            self.debug_log_tcp(f"SERVICE GAME: {client_id} - Function={function_id}")
            
            # Based on C# ServiceGame.Subscribe() sending function_id = 0
            if function_id == 0:  # Subscribe function
                logger.info(f"ServiceGame Subscribe request from {client_id}")
                self.debug_log_tcp(f"SERVICE GAME SUBSCRIBE: {client_id}")
                
                # ServiceGame responses don't use RPC IDs, just return success
                # Looking at the C# pattern, ServiceGame might not expect any response at all
                # or expects a very minimal response
                
                # Return empty response - ServiceGame.Subscribe() might be fire-and-forget
                return b''
            else:
                logger.info(f"Unknown ServiceGame function_id: {function_id}")
                return b''
            
        except Exception as e:
            logger.error(f"Error handling ServiceGame: {e}")
            self.debug_log_tcp(f"SERVICE GAME ERROR: {client_id} - {str(e)}")
            return b''
    
    def create_logicmain_card_data(self) -> bytes:
        """Create binary data for LogicMain card using Card.WriteVariant format"""
        try:
            card_data = bytearray()
            
            # Card.WriteVariant first writes the card category as a byte
            # LogicMain category - from the C# CardCategory enum definition
            # CardCategory.LogicMain = 21 (enum starts at Item=1, LogicMain is the 21st value)
            card_category = 0x15  # CardCategory.LogicMain
            card_data.append(card_category)
            
            logger.info(f"Creating LogicMain card: category={card_category}, first byte will be {card_category}")
            
            # Verify that we're actually writing the category byte
            logger.info(f"Card data after adding category: {len(card_data)} bytes (should be 1)")
            
            # After WriteVariant writes the category, it calls the card's Write method
            # For CardLogicMain, the Write method should write the base Card properties first
            
            # Base Card properties - the Key (Id field)
            card_id = "logic_main"  # This is the correct ID that generates hash 3317978623
            
            # Write the Id string using Igor.Write.String format (VarInt length + bytes)
            id_bytes = card_id.encode('utf-8')
            card_data.extend(self.encode_varint(len(id_bytes)))  # String length as VarInt
            card_data.extend(id_bytes)
            
            # Verify the card structure so far
            logger.info(f"Card data after adding category and ID: {len(card_data)} bytes")
            logger.info(f"First 15 bytes should be: 15(category) + 0A(length) + logic_main")
            logger.info(f"Actual first 15 bytes: {card_data[:15].hex()}")
            expected_start = bytes([21, 10]) + b"logic_main"
            if len(card_data) >= len(expected_start):
                actual_start = card_data[:len(expected_start)]
                logger.info(f"Expected: {expected_start.hex()}")
                logger.info(f"Actual:   {actual_start.hex()}")
                if expected_start == actual_start:
                    logger.info("✓ Card header matches expected format!")
                else:
                    logger.error("✗ Card header does NOT match expected format!")
            else:
                logger.error(f"Card data too short: {len(card_data)} bytes, expected at least {len(expected_start)}")
            
            # LogicMain specific properties in order as they appear in Write method:
            card_data.extend(struct.pack('<i', 100))    # LadderTopSize (from JSON: ladder_top_size)
            card_data.extend(struct.pack('<i', 10))     # MaxBestScores (from JSON: max_best_scores)
            card_data.extend(struct.pack('<i', 20))     # PlayerNameMaxSize (from JSON: player_name_max_size)
            card_data.extend(struct.pack('<i', 20))     # HorseNameMaxSize (from JSON: horse_name_max_size)
            
            # DEBUG: Track exact byte positions
            logger.info(f"LogicMain card data before LevelUpBonus: {len(card_data)} bytes")
            logger.info(f"Hex so far: {card_data.hex()}")
            
            # LevelUpBonus (Reward object) - Write.Writable<Reward>
            # From JSON: level_up_bonus: coins=100, skill_tickets=1, xp=0, ap=25
            level_up_items = ["fred"]
            reward_data = self.create_reward_data(100, 1, 0, 25, level_up_items)  # coins, skill_tickets, xp, ap, items
            logger.info(f"LevelUpBonus reward data length: {len(reward_data)} bytes, hex: {reward_data.hex()}")
            card_data.extend(reward_data)
            
            logger.info(f"LogicMain card data after LevelUpBonus: {len(card_data)} bytes")
            
            # ChallengeWin (Reward object)
            # From JSON: challenge_win: coins=100, skill_tickets=0, xp=100, ap=25
            challenge_win_items = ["baguette"]
            reward_data = self.create_reward_data(100, 0, 100, 25, challenge_win_items)  # coins, skill_tickets, xp, ap, items
            logger.info(f"ChallengeWin reward data length: {len(reward_data)} bytes, hex: {reward_data.hex()}")
            card_data.extend(reward_data)
            
            logger.info(f"LogicMain card data after ChallengeWin: {len(card_data)} bytes")
            logger.info(f"Expected LevelsXp to start at position: {len(card_data)}")
            
            # LevelsXp (List<int>) - Write.List<int>
            levels_xp = [100, 250, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000]
            card_data.extend(self.encode_varint(len(levels_xp)))  # List count as VarInt
            for xp in levels_xp:
                card_data.extend(struct.pack('<i', xp))  # Use signed int32 instead of unsigned
            
            # SkillPointsForLevelUp (float)
            card_data.extend(struct.pack('<f', 1.0))
            
            # ChangeAvatar (Price object) - Write.Writable<Price>
            # From JSON: change_avatar: coins=100, skill_tickets=0
            logger.info(f"LogicMain card data before ChangeAvatar: {len(card_data)} bytes")
            price_data = self.create_price_data(100, 0)  # coins, skill_tickets, sale=None
            logger.info(f"Price data length: {len(price_data)} bytes, hex: {price_data.hex()}")
            logger.info(f"Price data breakdown:")
            logger.info(f"  Expected: BitField(00) + coins(100) + skill_tickets(0) = 00 + 64000000 + 00000000")
            logger.info(f"  Actual bytes: {[hex(b) for b in price_data]}")
            if len(price_data) != 9:
                logger.error(f"PRICE DATA LENGTH ERROR: Expected 9 bytes, got {len(price_data)}")
            card_data.extend(price_data)
            logger.info(f"LogicMain card data after ChangeAvatar: {len(card_data)} bytes")
            
            # Flags (List<string>) - Write.List<string>
            # From JSON: flags = []
            flags = ["snow"] # can hold "snow" for snow effect and "heart" for heart effect but also an CardItem.Id which is the hash of a specific item 
            
            # Debug: Log the current position and what we're writing for Flags
            logger.info(f"LogicMain card data before Flags: {len(card_data)} bytes")
            logger.info(f"About to write Flags list with {len(flags)} elements")
            
            flags_varint = self.encode_varint(len(flags))
            logger.info(f"Flags count VarInt: {flags_varint.hex()} (should be 00 for empty list)")
            card_data.extend(flags_varint)  # List count as VarInt
            for flag in flags:
                flag_bytes = flag.encode('utf-8')
                card_data.extend(self.encode_varint(len(flag_bytes)))  # String length as VarInt
                card_data.extend(flag_bytes)
            
            # Add FF F0 pattern to match C# mock
            card_data.extend(bytes([0xFF, 0xF0]))
            
            logger.info(f"LogicMain card data after Flags: {len(card_data)} bytes")
            logger.info(f"Next 20 bytes after Flags position in card_data: {card_data[123:143].hex() if len(card_data) > 143 else card_data[123:].hex()}")
            
            # Premuim (Bonuses object) - Write.Writable<Bonuses>
            # From JSON: premuim with skill_tickets_rate=1.5, xp_rate=1.2
            bonuses_data = self.create_bonuses_data(
                skill_tickets_rate=1.5, xp_rate=1.2, loot_rate=2.0, 
                ap_cost_rate=1.0, ap_restore_rate=100.0, ap_max=10000,
                strength=1, timing=1, speed=1, acceleration=1, stamina=1, obedience=1
            )
            card_data.extend(bonuses_data)
            
            # Calculate hash for verification
            card_hash = calculate_crc32_hash(card_id)
            logger.info(f"Created LogicMain card data: {len(card_data)} bytes")
            logger.info(f"Card Hash: {card_hash} (0x{card_hash:08X}) - Expected: 3317978623")
            logger.info(f"Card ID: '{card_id}'")
            return bytes(card_data)
            
        except Exception as e:
            logger.error(f"Error creating LogicMain card data: {e}")
            # Return minimal valid card data on error
            minimal_data = bytearray()
            minimal_data.append(0)  # Category
            
            # Minimal card with just ID
            minimal_data.extend(struct.pack('<I', 10))  # ID length for "logic_main"
            minimal_data.extend(b"logic_main")  # Correct ID
            
            card_hash = calculate_crc32_hash("logic_main")
            logger.info(f"Minimal card data created with hash: {card_hash}")
            return bytes(minimal_data)

    def create_logic_action_points_card_data(self) -> bytes:
        """Create binary data for LogicActionPoints card using Card.WriteVariant format"""
        try:
            card_data = bytearray()
            
            # Card.WriteVariant first writes the card category as a byte
            # LogicActionPoints category = 0x16
            card_category = 0x16  # CardCategory.LogicActionPoints
            card_data.append(card_category)
            
            logger.info(f"Creating LogicActionPoints card: category={card_category}, first byte will be {card_category}")
            
            # Base Card properties - the Key (Id field)
            card_id = "logic_action_points"
            
            # Write the Id string using Igor.Write.String format (VarInt length + bytes)
            id_bytes = card_id.encode('utf-8')
            card_data.extend(self.encode_varint(len(id_bytes)))  # String length as VarInt
            card_data.extend(id_bytes)
            
            # LogicActionPoints specific properties in order as they appear in Write method:
            card_data.extend(struct.pack('<I', 100))    # MaxValue (uint32)
            card_data.extend(struct.pack('<I', 5))      # PracticeReduce (uint32)
            card_data.extend(struct.pack('<I', 10))     # RmReduce (uint32)
            card_data.extend(struct.pack('<I', 1))      # RestoreRate (uint32)
            card_data.extend(struct.pack('<I', 300))    # RestoreInterval (uint32)
            card_data.extend(struct.pack('<I', 2))      # PaddockReduce (uint32)
            card_data.extend(struct.pack('<I', 600))    # PaddockReduceInterval (uint32)
            card_data.extend(struct.pack('<f', 80.0))   # BuffThreshold (float)
            
            # Add FF F0 pattern to match C# mock (same as LogicMain)
            card_data.extend(bytes([0xFF, 0xF0]))
            
            # BuffBonuses (Bonuses object) - Igor.Write.Writable<Bonuses>
            bonuses_data = self.create_bonuses_data(
                skill_tickets_rate=1.2, xp_rate=1.1, loot_rate=1.8, 
                ap_cost_rate=0.9, ap_restore_rate=120.0, ap_max=12000,
                strength=2, timing=2, speed=2, acceleration=2, stamina=2, obedience=2
            )
            card_data.extend(bonuses_data)
            
            # Calculate hash for verification
            card_hash = calculate_crc32_hash(card_id)
            logger.info(f"Created LogicActionPoints card data: {len(card_data)} bytes")
            logger.info(f"Card Hash: {card_hash} (0x{card_hash:08X})")
            logger.info(f"Card ID: '{card_id}'")
            return bytes(card_data)
            
        except Exception as e:
            logger.error(f"Error creating LogicActionPoints card data: {e}")
            return b""

    def create_logic_chat_card_data(self) -> bytes:
        """Create binary data for LogicChat card using Card.WriteVariant format"""
        try:
            card_data = bytearray()
            
            # Card.WriteVariant first writes the card category as a byte
            # LogicChat category = 30 (0x1E in hex)
            card_category = 0x1E  # CardCategory.LogicChat
            card_data.append(card_category)
            
            logger.info(f"Creating LogicChat card: category={card_category}, first byte will be {card_category}")
            
            # Base Card properties - the Key (Id field)
            card_id = "logic_chat"
            
            # Write the Id string using Igor.Write.String format (VarInt length + bytes)
            id_bytes = card_id.encode('utf-8')
            card_data.extend(self.encode_varint(len(id_bytes)))  # String length as VarInt
            card_data.extend(id_bytes)
            
            # LogicChat specific properties in order as they appear in Write method:
            # MessageCountLimit (int) - reasonable limit for chat messages per time period
            card_data.extend(struct.pack('<i', 10))      # MessageCountLimit (signed int32)
            
            # MessageTimeLimit (float) - time window in seconds for message count limit
            card_data.extend(struct.pack('<f', 10.0))    # MessageTimeLimit (float - 10 seconds)
            
            # SpamBanTime (float) - ban duration in seconds for spam violations
            card_data.extend(struct.pack('<f', 300.0))   # SpamBanTime (float - 5 minutes)
            
            # StarsPlayers (List<uint>) - Get all player IDs from database
            try:
                # Get all users from database to populate StarsPlayers
                all_users = self.database.get_all_users()
                star_players = [user['player_id'] for user in all_users if user.get('player_id')]
                
                # If no users in database, use some default star players
                if not star_players:
                    star_players = [1]  # Default star players

                logger.info(f"LogicChat StarsPlayers: {len(star_players)} players - {star_players[:10]}...")
                
            except Exception as e:
                logger.error(f"Error getting users for StarsPlayers: {e}")
                star_players = [1]  # Fallback star players
            
            # Write StarsPlayers list (Igor.Write.List<uint>)
            card_data.extend(self.encode_varint(len(star_players)))  # List count as VarInt
            for player_id in star_players:
                card_data.extend(struct.pack('<I', player_id))  # Each player ID as uint32
            
            # Calculate hash for verification
            card_hash = calculate_crc32_hash(card_id)
            logger.info(f"Created LogicChat card data: {len(card_data)} bytes")
            logger.info(f"Card Hash: {card_hash} (0x{card_hash:08X})")
            logger.info(f"Card ID: '{card_id}'")
            logger.info(f"Chat settings: MessageCountLimit=10 (int), MessageTimeLimit=60.0s (float), SpamBanTime=300.0s (float)")
            logger.info(f"StarsPlayers count: {len(star_players)}")
            return bytes(card_data)
            
        except Exception as e:
            logger.error(f"Error creating LogicChat card data: {e}")
            return b""

    def create_logic_skins_card_data(self) -> bytes:
        """Create binary data for LogicSkins card using Card.WriteVariant format"""
        try:
            card_data = bytearray()
            
            # Card.WriteVariant first writes the card category as a byte
            # LogicSkins category = 17 (0x11 in hex)
            card_category = 0x11  # CardCategory.LogicSkins
            card_data.append(card_category)
            
            logger.info(f"Creating LogicSkins card: category={card_category}, first byte will be {card_category}")
            
            # Base Card properties - the Key (Id field)
            card_id = "skins"
            
            # Write the Id string using Igor.Write.String format (VarInt length + bytes)
            id_bytes = card_id.encode('utf-8')
            card_data.extend(self.encode_varint(len(id_bytes)))  # String length as VarInt
            card_data.extend(id_bytes)
            
            # LogicSkins specific properties in order as they appear in Write method:
            
            # 1. HorseSkins (List<string>) - Igor.Write.List<string>
            horse_skins = []  # Empty for now as requested
            card_data.extend(self.encode_varint(len(horse_skins)))  # List count as VarInt
            for skin in horse_skins:
                skin_bytes = skin.encode('utf-8')
                card_data.extend(self.encode_varint(len(skin_bytes)))  # String length as VarInt
                card_data.extend(skin_bytes)
            
            # 2. HorseTailSkins (List<string>) - Igor.Write.List<string>
            horse_tail_skins = []  # Empty for now as requested
            card_data.extend(self.encode_varint(len(horse_tail_skins)))  # List count as VarInt
            for skin in horse_tail_skins:
                skin_bytes = skin.encode('utf-8')
                card_data.extend(self.encode_varint(len(skin_bytes)))  # String length as VarInt
                card_data.extend(skin_bytes)
            
            # 3. PlayerSkins (List<string>) - Igor.Write.List<string>
            player_skins = []  # Empty for now as requested
            card_data.extend(self.encode_varint(len(player_skins)))  # List count as VarInt
            for skin in player_skins:
                skin_bytes = skin.encode('utf-8')
                card_data.extend(self.encode_varint(len(skin_bytes)))  # String length as VarInt
                card_data.extend(skin_bytes)
            
            # 4. HorseHairSkins (List<HorseHairSkin>) - Igor.Write.List<HorseHairSkin>
            # Create one sample HorseHairSkin as in the mock data
            horse_hair_skins = [
                {"main": {"r": 1.0, "g": 0.0, "b": 0.0, "a": 1.0}, "spec": {"r": 0.0, "g": 1.0, "b": 0.0, "a": 1.0}}
            ]
            
            card_data.extend(self.encode_varint(len(horse_hair_skins)))  # List count as VarInt
            for hair_skin in horse_hair_skins:
                # Write HorseHairSkin using IgorHelper.WriteColor format (4 floats each for Main and Spec)
                # Main Color (r, g, b, a as floats)
                card_data.extend(struct.pack('<f', hair_skin["main"]["r"]))  # Red
                card_data.extend(struct.pack('<f', hair_skin["main"]["g"]))  # Green
                card_data.extend(struct.pack('<f', hair_skin["main"]["b"]))  # Blue
                card_data.extend(struct.pack('<f', hair_skin["main"]["a"]))  # Alpha
                
                # Spec Color (r, g, b, a as floats)
                card_data.extend(struct.pack('<f', hair_skin["spec"]["r"]))  # Red
                card_data.extend(struct.pack('<f', hair_skin["spec"]["g"]))  # Green
                card_data.extend(struct.pack('<f', hair_skin["spec"]["b"]))  # Blue
                card_data.extend(struct.pack('<f', hair_skin["spec"]["a"]))  # Alpha
            
            # Calculate hash for verification
            card_hash = calculate_crc32_hash(card_id)
            logger.info(f"Created LogicSkins card data: {len(card_data)} bytes")
            logger.info(f"Card Hash: {card_hash} (0x{card_hash:08X})")
            logger.info(f"Card ID: '{card_id}'")
            logger.info(f"Skins lists: HorseSkins={len(horse_skins)}, HorseTailSkins={len(horse_tail_skins)}, PlayerSkins={len(player_skins)}, HorseHairSkins={len(horse_hair_skins)}")
            return bytes(card_data)
            
        except Exception as e:
            logger.error(f"Error creating LogicSkins card data: {e}")
            return b""

    def create_reward_data(self, coins: int, skill_tickets: int, xp: int, ap: int, items: list = None) -> bytes:
        """Create binary data for Reward object matching JSON structure:
        {
            "money": {"coins": int, "skill_tickets": int},
            "xp": int,
            "ap": int,
            "items": [list of item_hashes]
        }
        
        Args:
            coins: Money coins value
            skill_tickets: Money skill tickets value  
            xp: Experience points value
            ap: Action points value
            items: List of item hashes (uint32) or item ID strings. If None, creates empty list.
        """
        if items is None:
            items = []
            
        # First, create the actual reward data
        reward_data = bytearray()
        
        # BitField to indicate whether Money is null (1 bit for Money != null)
        # C# code: new BitField(new bool[] { this.Money != null })
        # Using MSB-first format: 0x80 = enabled, 0x00 = disabled
        reward_data.append(0x80)  # BitField as single byte - bit 0 set (Money is not null)
        
        # Money object (since BitField indicates it's not null, we write it)
        # Money is directly embedded, not a separate Writable object with its own prefix
        reward_data.extend(struct.pack('<i', coins))         # money.coins
        reward_data.extend(struct.pack('<i', skill_tickets)) # money.skill_tickets
        
        # XP value (Igor.Write.Int)
        reward_data.extend(struct.pack('<i', xp))            # xp
        
        # AP value (Igor.Write.Int)
        reward_data.extend(struct.pack('<i', ap))            # ap
        
        # Items list (Igor.Write.List<Key>) - write actual items count and data
        # Key objects are uint32 hash values, not strings with length prefixes
        items_varint = self.encode_varint(len(items))
        logger.info(f"  Encoding {len(items)} items as VarInt: {items_varint.hex()} (expected single byte for counts 0-127)")
        reward_data.extend(items_varint)   # items count as VarInt
        
        # Write each item as a uint32 hash
        for item in items:
            if isinstance(item, str):
                # If item is a string, calculate its CRC32 hash
                item_hash = calculate_crc32_hash(item)
                logger.info(f"  Item '{item}' -> hash {item_hash} (0x{item_hash:08X})")
            else:
                # If item is already a number, use it directly
                item_hash = int(item)
                logger.info(f"  Item hash: {item_hash} (0x{item_hash:08X})")
            
            # Write the hash as uint32 little-endian
            item_bytes = struct.pack('<I', item_hash)
            logger.info(f"    Writing Key hash as 4 bytes: {item_bytes.hex()}")
            reward_data.extend(item_bytes)
        
        # Debug: Log the exact reward structure  
        logger.info(f"Reward data structure: {len(reward_data)} bytes")
        logger.info(f"  BitField: {reward_data[0]:02x} (Money is not null)")
        logger.info(f"  Coins: {struct.unpack('<i', reward_data[1:5])[0]}")
        logger.info(f"  SkillTickets: {struct.unpack('<i', reward_data[5:9])[0]}")
        logger.info(f"  XP: {struct.unpack('<i', reward_data[9:13])[0]}")
        logger.info(f"  AP: {struct.unpack('<i', reward_data[13:17])[0]}")
        items_count_byte = reward_data[17]
        logger.info(f"  Items VarInt: {items_count_byte:02x} (count={len(items)})")
        if len(items) > 0:
            logger.info(f"  Items data starts at byte 18: {reward_data[18:].hex()}")
            # Log each item hash with detailed verification
            for i, item in enumerate(items):
                start_pos = 18 + (i * 4)  # Each uint32 is 4 bytes
                if start_pos + 4 <= len(reward_data):
                    item_bytes = reward_data[start_pos:start_pos+4]
                    item_value = struct.unpack('<I', item_bytes)[0]
                    logger.info(f"    Item {i}: {item_value} (0x{item_value:08X}) bytes: {item_bytes.hex()}")
                    
                    # Verify this matches what we expect
                    if isinstance(items[i], str):
                        expected_hash = calculate_crc32_hash(items[i])
                        if item_value == expected_hash:
                            logger.info(f"      ✓ Hash matches expected for '{items[i]}'")
                        else:
                            logger.error(f"      ✗ Hash mismatch! Expected {expected_hash}, got {item_value}")
                else:
                    logger.error(f"    Item {i}: TRUNCATED - not enough bytes in reward_data")
                    
        # Log total expected length vs actual length
        expected_length = 17 + 1 + (len(items) * 4)  # 17 bytes fixed + 1 byte VarInt + items
        logger.info(f"  Expected total length: {expected_length} bytes, Actual: {len(reward_data)} bytes")
        if expected_length != len(reward_data):
            logger.error(f"  LENGTH MISMATCH! This will cause reading issues!")
            
        logger.info(f"  Complete hex: {reward_data.hex()}")
        
        # Add a final verification that the Key data is correctly formatted
        if len(items) > 0:
            logger.info(f"  Verification: Items section starts at byte 17 (VarInt count)")
            varint_bytes = self.encode_varint(len(items))
            logger.info(f"    VarInt count: {len(items)} encoded as {varint_bytes.hex()} ({len(varint_bytes)} bytes)")
            logger.info(f"    Actual VarInt in data: {reward_data[17:17+len(varint_bytes)].hex()}")
            
            # Check each Key hash 
            keys_start = 17 + len(varint_bytes)
            for i in range(len(items)):
                hash_start = keys_start + (i * 4)
                if hash_start + 4 <= len(reward_data):
                    hash_bytes = reward_data[hash_start:hash_start+4]
                    hash_value = struct.unpack('<I', hash_bytes)[0]
                    logger.info(f"    Key {i}: position {hash_start}-{hash_start+3}, hash {hash_value}, bytes {hash_bytes.hex()}")
                else:
                    logger.error(f"    Key {i}: MISSING DATA at position {hash_start}")
                    
            # Verify total structure
            expected_items_section_length = len(varint_bytes) + (len(items) * 4)
            actual_items_section_length = len(reward_data) - 17
            logger.info(f"    Items section: expected {expected_items_section_length} bytes, actual {actual_items_section_length} bytes")
            
            if expected_items_section_length != actual_items_section_length:
                logger.error(f"    ITEMS SECTION LENGTH MISMATCH!")
        else:
            logger.info(f"  No items in this reward - VarInt should be 00")
        
        # Igor.Write.Writable<T> and Igor.Read.Readable<T> do NOT use length prefixes PLEASE DO NOT EVER CHANGE THIS WITHOUT PERMISSION
        # They directly call obj.Write(writer) and obj.Read(reader)
        # Return the reward data directly without any length prefix
        return bytes(reward_data)
    
    def create_price_data(self, coins: int, skill_tickets: int, sale: float = None) -> bytes:
        """Create binary data for Price object matching C# structure:
        {
            "coins": int,
            "skill_tickets": int,
            "sale": float? (nullable)
        }
        C# Write method:
        1. BitField (1 bit for Sale != null)
        2. Coins (int)
        3. SkillTickets (int)
        4. Sale (float, only if BitField indicates not null)
        """
        # Create the price data
        price_data = bytearray()
        
        # BitField to indicate whether Sale is null (1 bit for Sale != null)
        # Since we don't have a sale value, the bit is set to 0 (false)
        bitfield_value = 0x00 if sale is None else 0x80
        price_data.append(bitfield_value)
        
        # Coins and SkillTickets
        price_data.extend(struct.pack('<i', coins))         # coins
        price_data.extend(struct.pack('<i', skill_tickets)) # skill_tickets
        
        # Sale (only if BitField indicates it's not null)
        if sale is not None:
            price_data.extend(struct.pack('<f', sale))      # sale
        
        # Igor.Write.Writable<T> and Igor.Read.Readable<T> do NOT use length prefixes
        # They directly call obj.Write(writer) and obj.Read(reader)
        # Return the price data directly without any length prefix
        return bytes(price_data)
    
    def send_cards_to_service(self, client_socket: socket.socket, client_id: str):
        """Send cards data directly to ServiceCards (ID 101) after login"""
        try:
            logger.info(f"Sending cards data to ServiceCards for {client_id}")
            self.debug_log_tcp(f"SENDING CARDS TO SERVICE 101: {client_id}")
            
            # Create message content (without VarInt prefix first)
            message_content = bytearray()
            
            # Service ID (1 byte) - ServiceCards = 101
            # This tells the ServiceMap to route to ServiceCards
            message_content.append(101)
            
            # Function ID that ServiceCards.Recv expects
            # Looking at the ServiceCards.Recv method: byte id = reader.ReadByte();
            # Then it calls Recv_Init (0), Recv_Updated (1), or Recv_Deleted (2)
            # No RPC ID needed! ServiceCards doesn't use them.
            message_content.append(0)  # 0 = Recv_Init
            
            # Recv_Init expects: List<Card> cards = Read.List<Card>(Card.ReadVariant)(reader);
            # Cards list count - Read.Size() expects VarInt format
            message_content.extend(self.encode_varint(4))  # We're sending 4 cards (LogicMain + LogicActionPoints + LogicChat + LogicSkins)
            
            # Card data for LogicMain using Card.WriteVariant format
            # Card.WriteVariant writes category byte + card.Write() - no length prefix
            logicmain_data = self.create_logicmain_card_data()
            logger.info(f"LogicMain card data length: {len(logicmain_data)} bytes")
            logger.info(f"LogicMain card first 20 bytes: {logicmain_data[:20].hex()}")
            logger.info(f"Expected LogicMain start: category=21 (0x15), then 0A + 'logic_main'")
            if len(logicmain_data) > 0:
                logger.info(f"LogicMain actual first byte: {logicmain_data[0]} (0x{logicmain_data[0]:02X}) - should be 21 (0x15)")
            message_content.extend(logicmain_data)
            
            # Card data for LogicActionPoints using Card.WriteVariant format  
            # Card.WriteVariant writes category byte + card.Write() - no length prefix
            logicactionpoints_data = self.create_logic_action_points_card_data()
            logger.info(f"LogicActionPoints card data length: {len(logicactionpoints_data)} bytes")
            message_content.extend(logicactionpoints_data)
            
            # Card data for LogicChat using Card.WriteVariant format
            # Card.WriteVariant writes category byte + card.Write() - no length prefix
            logicchat_data = self.create_logic_chat_card_data()
            logger.info(f"LogicChat card data length: {len(logicchat_data)} bytes")
            message_content.extend(logicchat_data)
            
            # Card data for LogicSkins using Card.WriteVariant format
            # Card.WriteVariant writes category byte + card.Write() - no length prefix
            logicskins_data = self.create_logic_skins_card_data()
            logger.info(f"LogicSkins card data length: {len(logicskins_data)} bytes")
            message_content.extend(logicskins_data)
            
            # Debug: Log exact message content structure
            logger.info(f"Message content breakdown:")
            logger.info(f"  Service ID (101): 1 byte")
            logger.info(f"  Function ID (0): 1 byte") 
            logger.info(f"  Cards count VarInt (4): 1 byte")
            logger.info(f"  LogicMain card: {len(logicmain_data)} bytes")
            logger.info(f"  LogicActionPoints card: {len(logicactionpoints_data)} bytes")
            logger.info(f"  LogicChat card: {len(logicchat_data)} bytes")
            logger.info(f"  LogicSkins card: {len(logicskins_data)} bytes")
            logger.info(f"  Total message content: {len(message_content)} bytes")
            
            # Log the boundary between cards
            logicmain_end = 3 + len(logicmain_data)  # 3 = service_id + function_id + cards_count
            logicactionpoints_start = logicmain_end
            logger.info(f"LogicMain ends at byte {logicmain_end}, LogicActionPoints starts at byte {logicactionpoints_start}")
            
            if len(message_content) >= logicactionpoints_start + 10:
                boundary_bytes = message_content[logicmain_end-5:logicactionpoints_start+10]
                logger.info(f"Boundary bytes around LogicActionPoints start: {boundary_bytes.hex()}")
                logger.info(f"  Last 5 bytes of LogicMain: {message_content[logicmain_end-5:logicmain_end].hex()}")
                logger.info(f"  First 10 bytes of LogicActionPoints: {message_content[logicactionpoints_start:logicactionpoints_start+10].hex()}")
            
            # Expected first bytes of LogicActionPoints: 16 (category) + 13 (string length) + "logic_action_points"
            expected_start = bytes([22, 19]) + b"logic_action_points"  # 22 = category, 19 = string length
            actual_start = message_content[logicactionpoints_start:logicactionpoints_start+len(expected_start)]
            logger.info(f"Expected LogicActionPoints start: {expected_start.hex()}")
            logger.info(f"Actual LogicActionPoints start: {actual_start.hex()}")
            
            if expected_start != actual_start:
                logger.error(f"MISMATCH in LogicActionPoints start!")
                # Look for the pattern we expect
                search_pattern = bytes([22, 19]) + b"logic_action_points" 
                try:
                    found_index = message_content.find(search_pattern)
                    if found_index >= 0:
                        logger.error(f"Found expected pattern at index {found_index}, but expected it at {logicactionpoints_start}")
                        logger.error(f"Extra bytes before pattern: {message_content[logicactionpoints_start:found_index].hex()}")
                except:
                    logger.error(f"Could not find expected LogicActionPoints pattern in message!")
                                    
            # Now create the final message with VarInt length prefix
            final_message = bytearray()
            
            # Add VarInt length prefix (length of the message content)
            message_length = len(message_content)
            final_message.extend(self.encode_varint(message_length))
            
            # Add the actual message content
            final_message.extend(message_content)
            
            # DEBUG: Analyze the exact raw bytes being sent
            raw_bytes = list(final_message)
            raw_hex = ' '.join(final_message.hex().upper()[i:i+2] for i in range(0, len(final_message.hex()), 2))
            logger.info(f"RAW BYTES ANALYSIS:")
            logger.info(f"  Total message bytes (hex): {raw_hex}")
            logger.info(f"  Total message bytes (list): {raw_bytes}")
            logger.info(f"  Message length: {len(final_message)} bytes")
            
            # Send the message
            client_socket.send(bytes(final_message))
            
            logger.info(f"Sent cards data to ServiceCards: {len(final_message)} bytes total, {len(message_content)} content")
            logger.info(f"Message structure: VarInt({message_length}) + Service_ID=101 + Function_ID=0 + CardList(4) + LogicMainCard + LogicActionPointsCard + LogicChatCard + LogicSkinsCard")
            self.debug_log_tcp(f"CARDS TO SERVICE 101 SENT: {client_id} - {len(final_message)} bytes total, {len(message_content)} content, 4 cards (LogicMain + LogicActionPoints + LogicChat + LogicSkins)")
            self.debug_log_binary(bytes(final_message), "OUTGOING", client_id, "Cards data to ServiceCards (ID 101) with VarInt prefix - 4 cards")
            
        except Exception as e:
            logger.error(f"Error sending cards to ServiceCards for {client_id}: {e}")
            self.debug_log_tcp(f"CARDS TO SERVICE ERROR: {client_id} - {str(e)}")

    def create_bonuses_data(self, skill_tickets_rate: float, xp_rate: float, loot_rate: float, 
                           ap_cost_rate: float, ap_restore_rate: float, ap_max: int,
                           strength: int, timing: int, speed: int, acceleration: int, 
                           stamina: int, obedience: int) -> bytes:
        """Create binary data for Bonuses object matching JSON structure:
        {
            "skill_tickets_rate": float,
            "xp_rate": float,
            "loot_rate": float,
            "ap_cost_rate": float,
            "ap_restore_rate": float,
            "ap_max": int,
            "strength": int,
            "timing": int,
            "speed": int,
            "acceleration": int,
            "stamina": int,
            "obedience": int
        }
        """
        # Create the bonus data
        bonuses_data = bytearray()
        
        # All float values
        bonuses_data.extend(struct.pack('<f', skill_tickets_rate))  # skill_tickets_rate
        bonuses_data.extend(struct.pack('<f', xp_rate))             # xp_rate
        bonuses_data.extend(struct.pack('<f', loot_rate))           # loot_rate
        bonuses_data.extend(struct.pack('<f', ap_cost_rate))        # ap_cost_rate
        bonuses_data.extend(struct.pack('<f', ap_restore_rate))     # ap_restore_rate
        
        # Integer values - use SIGNED integers to match the bonus data structure
        bonuses_data.extend(struct.pack('<i', ap_max))              # ap_max (signed int)
        bonuses_data.extend(struct.pack('<i', strength))            # strength (signed int)
        bonuses_data.extend(struct.pack('<i', timing))              # timing (signed int)
        bonuses_data.extend(struct.pack('<i', speed))               # speed (signed int)
        bonuses_data.extend(struct.pack('<i', acceleration))        # acceleration (signed int)
        bonuses_data.extend(struct.pack('<i', stamina))             # stamina (signed int)
        bonuses_data.extend(struct.pack('<i', obedience))           # obedience (signed int)
        
        # Igor.Write.Writable<T> and Igor.Read.Readable<T> do NOT use length prefixes
        # They directly call obj.Write(writer) and obj.Read(reader)
        # Return the bonuses data directly without any length prefix
        return bytes(bonuses_data)
    
    def encode_varint(self, value: int) -> bytes:
        """Encode an integer as VarInt (Variable Length Integer) used by the game transport layer"""
        result = bytearray()
        while value >= 128:
            result.append((value & 0x7F) | 0x80)  # Set continuation bit
            value >>= 7
        result.append(value & 0x7F)  # Final byte without continuation bit
        return bytes(result)
    
    def handle_login_service(self, data: bytes, rpc_id: int, client_id: str) -> bytes:
        """Handle ServiceLogin messages"""
        try:
            logger.info(f"Processing login request for client {client_id}")
            self.debug_log_tcp(f"LOGIN REQUEST: {client_id} - RPC_ID={rpc_id}")
            
            # Log the login request data for analysis
            if len(data) > 0:
                self.debug_log_binary(data, "LOGIN_RAW_DATA", client_id, "Complete login request")
            
            # Parse AuthorizationTask structure: ProtocolVersion + Account + AccessToken
            # Based on analysis: [0, 34, 0, 0, 0, 5, 143, 33, 181, 10, 1, 0, 16, 1, 158, 2, ...]
            # Correct Steam ID found at offset 6: [143, 33, 181, 10, 1, 0, 16, 1] = 76561198139908495
            offset = 0
            
            # Let's examine the first few bytes to understand the structure
            if len(data) >= 16:
                logger.info(f"Data structure analysis: {data[:16].hex()} = {list(data[:16])}")
                
            # Based on C# Write.Writable<ProtocolVersion> and the fact that Steam ID is at offset 6:
            # The structure appears to be: [0][34][0][0][?][?][Steam ID (8 bytes)][...]
            # Protocol version is 34 at byte 1
            if len(data) < 14:  # Need at least 6 + 8 bytes for Steam ID
                raise ValueError(f"Data too short for complete login structure: {len(data)} bytes")
            
            # Protocol version is at byte 1
            protocol_version = data[1]  # Byte 1 = 34
            logger.info(f"Protocol version found at byte 1: {protocol_version}")
            
            # Steam ID is at offset 6 (8 bytes)
            steam_id_offset = 6
            steam_id = struct.unpack('<Q', data[steam_id_offset:steam_id_offset+8])[0]  # Q = uint64
            source_id = str(steam_id)
            
            logger.info(f"Steam ID found at offset {steam_id_offset}: {steam_id}")
            self.debug_log_tcp(f"LOGIN PROTOCOL: {client_id} - ProtocolVersion={protocol_version}")
            self.debug_log_tcp(f"LOGIN ACCOUNT: {client_id} - SteamID={steam_id}")
            
            # Access token starts after the Steam ID
            token_offset = steam_id_offset + 8  # After the 8-byte Steam ID
            
            # Check if this looks like a valid Steam ID
            if steam_id < 76561197960265728 or steam_id > 76561297960265728:
                logger.warning(f"Steam ID {steam_id} looks invalid, but using it anyway")
                source_id = f"steam_fallback_{steam_id}"
            
            # 3. Read AccessToken (Steam encrypted ticket)
            # The remaining data after the Steam ID should be the access token
            remaining_data_length = len(data) - token_offset
            if remaining_data_length <= 0:
                raise ValueError(f"No AccessToken data remaining: {len(data)} bytes total, offset {token_offset}")
            
            access_token_data = data[token_offset:]
            token_length = remaining_data_length  # Initialize token_length with default value
            self.debug_log_tcp(f"LOGIN TOKEN: {client_id} - RemainingBytes={remaining_data_length}, TokenData={access_token_data[:20].hex()}...")
            
            # For Igor protocol, strings might be prefixed with length
            # Let's try to detect if this looks like a length-prefixed string
            if remaining_data_length >= 4:
                potential_length = struct.unpack('<I', data[token_offset:token_offset+4])[0]
                if potential_length > 0 and potential_length < remaining_data_length and potential_length < 10000:  # Reasonable token size
                    # This looks like a length-prefixed token
                    token_length = potential_length
                    access_token_data = data[token_offset+4:token_offset+4+token_length]
                    self.debug_log_tcp(f"LOGIN TOKEN PREFIXED: {client_id} - TokenLength={token_length}, Token={access_token_data[:20].hex()}...")
                else:
                    # Not length-prefixed, use all remaining data
                    access_token_data = data[token_offset:]
                    token_length = len(access_token_data)
                    self.debug_log_tcp(f"LOGIN TOKEN RAW: {client_id} - TokenLength={len(access_token_data)}, Token={access_token_data[:20].hex()}...")
            else:
                access_token_data = data[token_offset:]
                token_length = len(access_token_data)
            
            # Convert token to hex string for storage
            access_token = access_token_data.hex()
            
            self.debug_log_tcp(f"LOGIN TOKEN FINAL: {client_id} - TokenLength={token_length}, Token={access_token[:20]}...")
            
            source_type = "Steam"
            
            # Validate protocol version
            if protocol_version != 34:
                logger.warning(f"Unexpected protocol version: {protocol_version}, expected 34")
            
            # Validate Steam ID
            if steam_id < 76561197960265728:  # Minimum valid Steam ID
                logger.warning(f"Invalid Steam ID: {steam_id}")
                source_id = f"steam_fallback_{steam_id}"
            
        except Exception as e:
            # Fallback parsing for malformed data
            logger.error(f"Error parsing login data: {e}")
            self.debug_log_tcp(f"LOGIN PARSE ERROR: {client_id} - {str(e)}")
            
            source_type = "Steam"
            source_id = f"steam_error_{client_id.split('_')[-1]}"
            access_token = f"token_{hash(data) % 1000000000}"
            
            self.debug_log_tcp(f"LOGIN FALLBACK: {client_id} - Using fallback ID={source_id}")
        
        try:
            # Get or create user in database
            player_id, user_data = self.database.get_or_create_user(source_type, source_id, access_token)
            
            # Create a successful login response
            # INSIGHT: The [177, 2] we saw was the VarInt length prefix from the client packet!
            # We need to build our actual message content and generate our own VarInt prefix
            
            # First, build the actual message content (without any VarInt prefix)
            message_content = bytearray()
            
            # Service ID (1 byte) - ServiceMap.Recv() reads this first to route to correct service
            message_content.append(100)  # ServiceLogin = 100
            
            # Function ID (1 byte) - 0 for Login (ServiceLogin.Recv() reads this to route to Recv_Login)
            message_content.append(0)

            # RPC ID (2 bytes, little endian) - Recv_Login() reads this with ReadUInt16()
            message_content.extend(struct.pack('<H', rpc_id))
            
            # Status (1 byte) - 0 for success, non-zero triggers error handling
            message_content.append(0)  # Success
            
            # ReplyLogin data (as expected by Read.Readable<ReplyLogin>)
            # CORRECT ORDER based on ReplyLogin.Write method:
            
            # 1. PlayerId (4 bytes, little endian uint32) - using Igor.Write.UInt format
            message_content.extend(struct.pack('<I', player_id))
            
            # 2. UserState (1 byte enum) - using Igor.Write.ByteEnum<UserState>
            user_state = user_data.get('user_state', 1)
            message_content.append(user_state)
            
            # 3. Status (Access enum as 1 byte) - using Igor.Write.ByteEnum<Access>
            access_level = user_data.get('access_level', 0)
            message_content.append(access_level)
            
            # Now create the final response with VarInt length prefix
            response = bytearray()
            
            # Add VarInt length prefix (length of the message content)
            message_length = len(message_content)
            response.extend(self.encode_varint(message_length))
            
            # Add the actual message content
            response.extend(message_content)
            
            logger.info(f"Sending login response to {client_id}: player_id={player_id}, name={user_data.get('name', 'Unknown')}")
            logger.info(f"Response structure: Function=0, RPC_ID={rpc_id}, Status=0, PlayerId={player_id}, UserState={user_state}, Access={access_level}")
            self.debug_log_tcp(f"LOGIN RESPONSE: {client_id} - PlayerID={player_id}, RPC_ID={rpc_id}, UserState={user_state}, Access={access_level}")
            
            return bytes(response)
            
        except Exception as e:
            logger.error(f"Error handling login service: {e}")
            self.debug_log_tcp(f"LOGIN ERROR: {client_id} - {str(e)}")
            return self.create_login_error_response(rpc_id, f"Login error: {str(e)}")
    
    def create_login_error_response(self, rpc_id: int, error_message: str) -> bytes:
        """Create an error response for login RPC calls"""
        try:
            # First, build the actual message content (without VarInt prefix)
            message_content = bytearray()
            
            # Service ID (1 byte) - ServiceMap.Recv() reads this first
            message_content.append(100)  # ServiceLogin = 100
            
            # Function ID (1 byte) - 0 for Login
            message_content.append(0)
            
            # RPC ID (2 bytes) - Recv_Login() reads this first
            message_content.extend(struct.pack('<H', rpc_id))
            
            # Status (255 for error) - Recv_Login() reads this second, non-zero triggers error
            message_content.append(255)
            
            # Error message (string) - using .NET string format (length + data)
            error_bytes = error_message.encode('utf-8')
            message_content.extend(struct.pack('<I', len(error_bytes)))  # String length as uint32
            message_content.extend(error_bytes)
            
            # Now create the final response with VarInt length prefix
            response = bytearray()
            
            # Add VarInt length prefix
            message_length = len(message_content)
            response.extend(self.encode_varint(message_length))
            
            # Add the actual message content
            response.extend(message_content)
            
            return bytes(response)
            
        except Exception as e:
            logger.error(f"Error creating login error response: {e}")
            return b""
    
    def create_error_response(self, rpc_id: int, error_message: str, service_id: int = 0) -> bytes:
        """Create an error response for RPC calls"""
        try:
            response = bytearray()
            
            # RPC ID (response format doesn't include service_id)
            response.extend(struct.pack('<H', rpc_id))
            
            # Status (255 for error)
            response.append(255)
            
            # Error message (string) - using .NET string format (length + data)
            error_bytes = error_message.encode('utf-8')
            response.extend(struct.pack('<I', len(error_bytes)))  # String length as uint32
            response.extend(error_bytes)
            
            return bytes(response)
            
        except Exception as e:
            logger.error(f"Error creating error response: {e}")
            return b""
    
    def stop_tcp_server(self):
        """Stop the TCP server"""
        self.tcp_server_running = False
        
        # Close all client connections
        for client_id, client_socket in list(self.tcp_clients.items()):
            try:
                client_socket.close()
            except:
                pass
    
    def start_policy_server(self):
        """Start Flash policy server on port 27132"""
        def policy_server():
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            
            try:
                server_socket.bind((SERVER_CONFIG['host'], SERVER_CONFIG['policy_port']))
                server_socket.listen(5)
                self.policy_server_running = True
                logger.info(f"Policy Server listening on {SERVER_CONFIG['host']}:{SERVER_CONFIG['policy_port']}")
                
                while self.policy_server_running:
                    try:
                        client_socket, address = server_socket.accept()
                        
                        # Flash policy file request
                        policy_file = '''<?xml version="1.0"?>
<cross-domain-policy>
    <allow-access-from domain="*" to-ports="*" />
</cross-domain-policy>\0'''
                        
                        client_socket.send(policy_file.encode('utf-8'))
                        client_socket.close()
                        
                        logger.info(f"Served policy file to {address}")
                        
                    except Exception as e:
                        if self.policy_server_running:
                            logger.error(f"Error handling policy request: {e}")
                        break
                        
            except Exception as e:
                logger.error(f"Policy Server error: {e}")
            finally:
                server_socket.close()
                logger.info("Policy Server stopped")
        
        # Start policy server in a separate thread
        policy_thread = threading.Thread(target=policy_server)
        policy_thread.daemon = True
        policy_thread.start()
    
    def stop_policy_server(self):
        """Stop the policy server"""
        self.policy_server_running = False
    
    def run(self):
        """Run the server"""
        logger.info("Starting RCC Server Emulator...")
        logger.info(f"HTTP Server will run on http://{SERVER_CONFIG['host']}:{SERVER_CONFIG['http_port']}")
        logger.info(f"TCP Server will run on {SERVER_CONFIG['host']}:{SERVER_CONFIG['tcp_port']}")
        logger.info(f"Policy Server will run on {SERVER_CONFIG['host']}:{SERVER_CONFIG['policy_port']}")
        logger.info(f"WebSocket available at ws://{SERVER_CONFIG['host']}:{SERVER_CONFIG['http_port']}/websocket")
        
        # Start TCP server in background
        self.start_tcp_server()
        
        # Start policy server in background
        self.start_policy_server()
        
        try:
            # Run the HTTP/WebSocket server (this blocks)
            uvicorn.run(
                self.app, 
                host=SERVER_CONFIG['host'], 
                port=SERVER_CONFIG['http_port'],
                log_level="info"
            )
        finally:
            # Clean up servers
            self.stop_tcp_server()
            self.stop_policy_server()

def main():
    """Main entry point"""
    print("=" * 50)
    print("Riding Club Championships Server Emulator")
    print("=" * 50)
    
    if DEBUG_MODE:
        print("🔍 DEBUG MODE ENABLED")
        print(f"Debug logs will be saved to: {DEBUG_DIR}")
        print("- TCP communication logs")
        print("- HTTP request/response logs") 
        print("- Binary data hex dumps")
        print("-" * 50)
    
    # Create images directory if it doesn't exist
    images_dir = Path("./images")
    images_dir.mkdir(exist_ok=True)
    
    # Create and run the server
    server = RCCServerEmulator()
    
    try:
        server.run()
    except KeyboardInterrupt:
        print("\nServer stopped by user")
        if DEBUG_MODE:
            print(f"Debug logs saved in: {DEBUG_DIR}")
    except Exception as e:
        print(f"Server error: {e}")
        if DEBUG_MODE:
            print(f"Debug logs saved in: {DEBUG_DIR}")

if __name__ == "__main__":
    main()