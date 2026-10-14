"""The account and profile tables, and the calls that read and write them."""

import hashlib
import logging
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

from paths import APP_DIR
from storage import connect_db

logger = logging.getLogger("Server")
ROOT = APP_DIR


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
