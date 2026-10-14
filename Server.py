#!/usr/bin/env python3
"""
Riding Club Championships Server Emulator
A basic server emulator to handle the game's endpoints because the official servers are offline.
"""

import json
import logging
import socket
import sys

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass
import threading
import time
import sqlite3
import hashlib
import zlib
from typing import Dict, Optional, Tuple
from datetime import datetime

from game_features import GameFeatures
from igor_client import IgorClient
from server_config import load_config, login_decision
from storage import connect_db
from paths import APP_DIR

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

ROOT = APP_DIR
IMAGE_DIR = ROOT / "images"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

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
    DEBUG_DIR = ROOT / DEBUG_LOG_DIRECTORY
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
    DEBUG_DIR = ROOT / DEBUG_LOG_DIRECTORY

# server.json overrides these. The default is this machine only.
JOIN_CONFIG = load_config()
SERVER_CONFIG = {
    "host": JOIN_CONFIG["bind_host"],
    "http_port": JOIN_CONFIG["http_port"],
    "tcp_port": JOIN_CONFIG["tcp_port"],
    "policy_port": JOIN_CONFIG["policy_port"],
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
    logger.info("Card hash verification:")
    logger.info(f"  'logic_main' -> {logic_main_hash} (expected: 3317978623)")
    if logic_main_hash == 3317978623:
        logger.info("  ✓ Hash matches!")
    else:
        logger.warning(f"  ✗ Hash mismatch! Expected 3317978623, got {logic_main_hash}")
    return logic_main_hash

class UserDatabase:
    """Manages user data storage and retrieval"""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or ROOT / "users.db")
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        with connect_db(self.db_path) as conn:
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

            for column, declaration in (("locale", "TEXT"), ("friend_ids", "TEXT")):
                try:
                    cursor.execute(f"ALTER TABLE player_data ADD COLUMN {column} {declaration}")
                except sqlite3.OperationalError:
                    pass
            
            conn.commit()
            logger.info("Database initialized successfully")
    
    def get_or_create_user(self, source_type: str, source_id: str, access_token: str = "") -> Tuple[int, dict]:
        """Get existing user or create new one. Returns (player_id, user_data)"""
        with connect_db(self.db_path) as conn:
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
                # An admin reset removes the rider row. The next login starts that rider over.
                fresh = cursor.execute(
                    "INSERT OR IGNORE INTO player_data (player_id, name) VALUES (?, ?)",
                    (player_id, f"Player{player_id}"),
                ).rowcount
                user_data['new'] = bool(fresh)
                
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
                    'name': default_name,
                    'new': True,
                }
                
                conn.commit()
                logger.info(f"New user created: {player_id} ({source_type}:{source_id})")
                return player_id, user_data

    def account_exists(self, source_type: str, source_id: str) -> bool:
        with connect_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE source_type = ? AND source_id = ?",
                (source_type, str(source_id)),
            ).fetchone()
        return row is not None
    
    def update_player_data(self, player_id: int, **kwargs) -> bool:
        """Update player data"""
        try:
            with connect_db(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Build dynamic update query
                set_clauses = []
                values = []
                
                for key, value in kwargs.items():
                    if key in ('name', 'locale', 'friend_ids'):
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
            with connect_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT u.user_state, u.access_level, u.created_at, u.last_login,
                           pd.name, pd.locale
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
                        'name': result[4] or 'Player',
                        'locale': result[5] or ''
                    }
                return None
                
        except Exception as e:
            logger.error(f"Error getting player data: {e}")
            return None
    
    def get_stats(self) -> dict:
        """Get database statistics"""
        try:
            with connect_db(self.db_path) as conn:
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
            with connect_db(self.db_path) as conn:
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
    def __init__(self, db_path: Optional[str] = None, debug_logging: bool = DEBUG_MODE):
        self.debug_logging = debug_logging
        self.app = FastAPI(title="RCC Server Emulator", version="1.0.0")
        self.connected_clients: Dict[str, WebSocket] = {}
        self.tcp_clients: Dict[str, socket.socket] = {}
        self.client_players: Dict[str, int] = {}
        self.pending_profile: Dict[str, dict] = {}
        self.igor = IgorClient(JOIN_CONFIG.get("game_dir") or None)
        self.outbox = {}
        # Tests set this to keep pushes for clients that have no socket.
        self.capture_outbox = False
        self.send_locks: Dict[str, threading.Lock] = {}
        self.tcp_server_running = False
        self.tcp_listen_socket = None
        self.policy_server_running = False
        self.database = UserDatabase(db_path)
        self.join_config = JOIN_CONFIG
        self.features = GameFeatures(self)
        
        # Verify card hash calculations
        verify_card_hashes()
        
        self.setup_debug_logging()
        self.setup_middleware()
        self.setup_routes()
    
    def setup_debug_logging(self):
        """Setup debug logging to files"""
        if self.debug_logging:
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
            
            logger.info("Debug logging enabled:")
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
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @self.app.middleware("http")
        async def debug_routes_local_only(request: Request, call_next):
            # /debug lists Steam ids and raw packet logs. Only this machine may read it.
            if request.url.path.startswith(("/debug", "/admin")) and (request.client is None or request.client.host not in LOOPBACK_HOSTS):
                return JSONResponse({"error": "debug and admin routes are local only"}, status_code=403)
            return await call_next(request)

    def setup_routes(self):
        """Setup all HTTP and WebSocket routes"""
        
        # Root endpoint
        @self.app.get("/")
        async def root():
            return {"message": "RCC Server Emulator is running", "status": "online"}
        
        # Health check endpoint
        @self.app.post("/admin/{command}")
        async def admin_command(command: str, request: Request):
            from admin import Admin, AdminError
            try:
                body = await request.json()
            except Exception:
                body = {}
            try:
                with self.features.lock:
                    result = Admin(self.database.db_path, self).run(command, body or {})
            except (AdminError, KeyError, ValueError) as exc:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            logger.info(f"ADMIN {command} {body}: {result if command != 'list' else 'ok'}")
            return {"ok": True, "result": result}

        @self.app.get("/health")
        async def health_check():
            db_stats = self.database.get_stats()
            return {
                "status": "healthy", 
                "clients_connected": len(self.tcp_clients),
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
            image_file = (IMAGE_DIR / image_path).resolve()
            
            if image_file.is_relative_to(IMAGE_DIR) and image_file.is_file():
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
            self.tcp_listen_socket = server_socket
            
            try:
                server_socket.bind((SERVER_CONFIG['host'], SERVER_CONFIG['tcp_port']))
                server_socket.listen(5)
                self.tcp_server_running = True
                logger.info(f"TCP Server listening on {SERVER_CONFIG['host']}:{SERVER_CONFIG['tcp_port']}")
                
                while self.tcp_server_running:
                    try:
                        client_socket, address = server_socket.accept()
                        client_id = f"tcp_client_{address[0]}_{address[1]}"
                        if len(self.tcp_clients) >= self.join_config.get("max_clients", 64):
                            logger.warning(f"TCP Client {client_id} refused: server is full")
                            client_socket.close()
                            continue
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
                        if not self.tcp_server_running:
                            break
                        # One failed accept should not stop the server for everyone.
                        logger.error(f"Error accepting TCP connection: {e}")
                        
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
            idle_before_login = 0
            
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
                    
                    messages = self.igor.feed(client_id, data)
                    replies = bytearray()
                    for message in messages:
                        try:
                            reply = self.handle_igor_message(message, client_id)
                        except Exception as exc:
                            # One bad message should not drop the replies already built, or the rider.
                            logger.exception(f"Message {message.get('name')} from {client_id} failed")
                            self.debug_log_tcp(f"MESSAGE ERROR: {client_id} - {message.get('name')} {exc}")
                            continue
                        if message.get("name") == "Login" and client_id in self.client_players:
                            logged_in = True
                            logger.info(f"Client {client_id} logged in as player {self.client_players.get(client_id)}")
                            self.debug_log_tcp(f"LOGIN SUCCESS: {client_id} - Player={self.client_players.get(client_id)}")
                        if reply:
                            replies.extend(reply)
                    if replies:
                        response = bytes(replies)
                        logger.info(f"Sending response to {client_id}: {len(response)} bytes")
                        self.debug_log_tcp(f"SENDING: {client_id} - {len(response)} bytes")
                        self.debug_log_binary(response, "OUTGOING", client_id, "Response data sent")
                        if len(response) <= 40:
                            logger.info(f"Response bytes: {response.hex()}")
                        self._send_raw(client_id, client_socket, response)
                    else:
                        self.debug_log_tcp(f"NO RESPONSE: {client_id} - void call or incomplete frame")
                    
                except socket.timeout:
                    # For logged in clients, just continue waiting for their next request
                    if logged_in:
                        self.debug_log_tcp(f"TIMEOUT: {client_id} - Logged in client timeout, continuing to wait for client requests")
                        # Don't send any unsolicited data - let the client make requests when ready
                        continue
                    idle_before_login += 1
                    self.debug_log_tcp(f"TIMEOUT: {client_id} - Socket timeout (not logged in yet)")
                    if idle_before_login >= 2:
                        # The real client logs in within a second. A silent socket only holds a thread.
                        logger.info(f"TCP Client {client_id} closed: no login after 60 seconds")
                        break
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
            self.send_locks.pop(client_id, None)
            left = self.client_players.pop(client_id, None)
            self.pending_profile.pop(client_id, None)
            try:
                self.features.on_disconnect(client_id)
                if left is not None and left not in self.client_players.values():
                    self.features.global_chat.discard(left)
                self.features._broadcast_online_count()
            except Exception:
                pass
            try:
                self.igor.drop(client_id)
            except Exception:
                pass
            
            logger.info(f"TCP Client {client_id} cleaned up")
            self.debug_log_tcp(f"CLEANUP: {client_id} - Connection closed and cleaned up")
    
    def handle_igor_message(self, message: dict, client_id: str) -> Optional[bytes]:
        """Reply only when the client opened an RPC. Void calls stay silent."""
        name = message.get("name")
        logger.info(
            "Igor %s from %s service=%s function=%s",
            name, client_id, message.get("service"), message.get("function"),
        )
        self.debug_log_tcp(
            f"IGOR: {client_id} - {name} service={message.get('service')} function={message.get('function')} rpc={message.get('rpc')}"
        )
        if name == "Login":
            source_name = "Steam" if message.get("source") == 5 else f"source_{message.get('source')}"
            source_id = str(message.get("sourceId") or "")
            exists = source_name == "Steam" and self.database.account_exists(source_name, source_id)
            allowed, reason = login_decision(source_id, self.join_config, exists)
            if source_name != "Steam":
                allowed, reason = False, "That login did not include a Steam account."
            if allowed:
                from admin import ban_reason
                banned = ban_reason(self.database.db_path, source_id)
                if banned:
                    allowed, reason = False, banned
            if not allowed:
                logger.warning(f"Login refused for {client_id} ({source_name} {source_id}): {reason}")
                self.debug_log_tcp(f"LOGIN REFUSED: {client_id} - {reason}")
                return self.igor.reply_fail(message["service"], message["function"], message["rpc"], reason)
            player_id, user_data = self.database.get_or_create_user(
                source_name, message["sourceId"], message.get("token") or ""
            )
            if user_data.get("new"):
                self.features.apply_new_player(player_id)
            self.client_players[client_id] = player_id
            pending = self.pending_profile.pop(client_id, None)
            if pending:
                self.database.update_player_data(player_id, **pending)
            return self.igor.reply_login(
                message["rpc"], player_id, user_data.get("user_state", 1), user_data.get("access_level", 0)
            )
        if name == "UpdateAccountInfo":
            profile = {"name": message.get("playerName") or "Player", "locale": message.get("locale") or ""}
            player_id = self.client_players.get(client_id)
            if player_id:
                self.database.update_player_data(player_id, **profile)
            else:
                self.pending_profile[client_id] = profile
            return None
        if name == "SubscribeFriends":
            player_id = self.client_players.get(client_id)
            friend_ids = message.get("friendIds") or []
            if player_id:
                self.database.update_player_data(
                    player_id, friend_ids=json.dumps(friend_ids)
                )
            self.features.push_known_friends(client_id, friend_ids)
            if player_id:
                guild_id = self.features._player_guild(player_id)
                if guild_id:
                    self.features._push_guild_to(client_id, guild_id)
                self.features._sync_guild_room(player_id)
            return None
        if name == "GetSpecialOffers":
            return self.igor.reply_special_offers(message["rpc"])
        if name == "GetLikes":
            # Login.DailyLoginTask waits until this push sets Player.Me.DailyBonus.
            # NeedAccept is true until today's gift is taken, which opens the sheet.
            return self.features.on_GetLikes(message, client_id, self.client_players.get(client_id))
        handled = self.features.dispatch(message, client_id)
        if handled is not None or name not in (None, "Unknown"):
            if handled is None:
                self.debug_log_tcp(f"NO REPLY: {client_id} - {name} rest={message.get('rest', '')[:80]}")
            return handled
        self.debug_log_tcp(f"NO REPLY: {client_id} - {name} rest={message.get('rest', '')[:80]}")
        return None

    def _send_raw(self, client_id: str, sock: socket.socket, data: bytes):
        """Write a whole frame. Pushes come from other threads, so frames must not interleave."""
        lock = self.send_locks.setdefault(client_id, threading.Lock())
        with lock:
            sock.sendall(data)

    def send_to(self, client_id, data: bytes):
        """Deliver a server push to one connected client, or keep it for a test outbox."""
        sock = self.tcp_clients.get(client_id)
        if sock is not None:
            try:
                self._send_raw(client_id, sock, data)
                self.debug_log_tcp(f"PUSH: {client_id} - {len(data)} bytes")
                return
            except Exception as exc:
                logger.error(f"Push to {client_id} failed: {exc}")
        if self.capture_outbox:
            self.outbox.setdefault(client_id, bytearray()).extend(data)
        else:
            self.debug_log_tcp(f"PUSH DROPPED: {client_id} is not connected - {len(data)} bytes")
    
    def send_cards_to_service(self, client_socket: socket.socket, client_id: str):
        """Send cards data directly to ServiceCards (ID 101) after login"""
        try:
            logger.info(f"Sending cards data to ServiceCards for {client_id}")
            self.debug_log_tcp(f"SENDING CARDS TO SERVICE 101: {client_id}")
            frame = self.igor.push_cards()
            self._send_raw(client_id, client_socket, frame)
            self.debug_log_tcp(
                f"CARDS TO SERVICE 101 SENT: {client_id} - {len(frame)} bytes from Card.Write"
            )
            logger.info(f"Sent Card.Write init to {client_id}: {len(frame)} bytes")
        except Exception as e:
            logger.error(f"Error sending cards to ServiceCards for {client_id}: {e}")
            self.debug_log_tcp(f"CARDS TO SERVICE ERROR: {client_id} - {str(e)}")

    def start_housekeeping(self):
        """Back up users.db at start and every backup_hours, and drop old packet logs."""
        from storage import backup_db, prune_logs

        hours = float(self.join_config.get("backup_hours") or 0)
        keep = int(self.join_config.get("backups_keep") or 0)

        def loop():
            while True:
                try:
                    if hours:
                        path = backup_db(self.database.db_path, keep=keep)
                        logger.info(f"Backup written: {path}")
                    prune_logs(DEBUG_DIR, self.join_config.get("log_days"))
                except Exception as exc:
                    logger.error(f"Housekeeping failed: {exc}")
                if not hours:
                    return
                time.sleep(hours * 3600)

        threading.Thread(target=loop, daemon=True).start()

    def stop_tcp_server(self):
        """Stop the TCP server"""
        self.tcp_server_running = False
        listen = self.tcp_listen_socket
        self.tcp_listen_socket = None
        if listen is not None:
            try:
                listen.close()
            except Exception:
                pass
        
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
        self.start_housekeeping()
        
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

def main(argv=None):
    """RCCServer [join HOST] [--game-dir PATH]"""
    import argparse
    import shutil

    parser = argparse.ArgumentParser(description="Riding Club Championships server")
    parser.add_argument("--game-dir", help="game folder with rcc.exe and rcc_Data (default: found automatically)")
    sub = parser.add_subparsers(dest="command")
    join = sub.add_parser("join", help="write the servers.txt players need to join this server")
    join.add_argument("host", nargs="?", help="address players use to reach this server")
    join.add_argument("--out", help="where to write servers.txt")
    sub.add_parser("admin", help="ban, mute, give coins, back up ... (admin --help)", add_help=False)
    args, rest = parser.parse_known_args(argv)

    if args.command == "admin":
        import admin
        sys.exit(admin.cli(rest, str(ROOT / "users.db"), JOIN_CONFIG["http_port"]))
    if rest:
        parser.error("unrecognized arguments: %s" % " ".join(rest))

    if args.command == "join":
        import client_config
        client_config.main([item for item in (args.host, "--out" if args.out else None, args.out) if item])
        return

    config_file = ROOT / "server.json"
    example = ROOT / "server.json.example"
    if not config_file.exists() and example.exists():
        shutil.copyfile(example, config_file)
        print(f"Created {config_file}. Edit it to let other people join.")
        JOIN_CONFIG.update(load_config())
    if args.game_dir:
        JOIN_CONFIG["game_dir"] = args.game_dir

    from paths import find_game_dir
    game = find_game_dir(JOIN_CONFIG.get("game_dir") or None)
    debug = JOIN_CONFIG.get("debug_logs")
    if debug is None:
        # Packet logs record every packet. Keep them for development, off in a packaged server.
        debug = DEBUG_MODE and not getattr(sys, "frozen", False)

    print("=" * 50)
    print("Riding Club Championships server")
    print("=" * 50)
    print(f"Game:    {game or 'NOT FOUND - set game_dir in server.json'}")
    print(f"Game TCP {SERVER_CONFIG['host']}:{SERVER_CONFIG['tcp_port']}   HTTP {SERVER_CONFIG['http_port']}")
    if SERVER_CONFIG["host"] in ("127.0.0.1", "localhost"):
        print("Only this PC can join. Set bind_host to 0.0.0.0 in server.json to let others in.")
    print("Players need servers.txt next to rcc.exe. Make it with:  join <your address>")
    if debug:
        print(f"Packet logs: {DEBUG_DIR}")
    print("-" * 50)
    if game is None:
        print("The game folder is needed for the card host. Stopping.")
        sys.exit(1)

    IMAGE_DIR.mkdir(exist_ok=True)
    server = RCCServerEmulator(debug_logging=debug)
    try:
        server.run()
    except KeyboardInterrupt:
        print()
        print("Server stopped")
    finally:
        server.igor.stop()


if __name__ == "__main__":
    main()