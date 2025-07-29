# Riding Club Championships Server Emulator

**⚠️ ADVANCED USERS ONLY - EARLY DEVELOPMENT PHASE**

This is a server emulator for the Riding Club Championships game, reverse-engineered to enable playing locally because the official servers are unavailable. This project is in early development and requires technical knowledge to set up and use.

## Current Status

The server successfully handles the initial login process and card delivery system, but **currently disconnects after login due to unhandled client requests**. This is expected behavior as I'm still implementing handlers for all game services and their RPC calls.

## Features

- **TCP Binary Protocol**: Full Igor binary protocol implementation with VarInt encoding
- **Service-Based Architecture**: Handles 10 different game services (100-109)
- **Card System**: Implements game's card-based configuration system
- **Authentication**: Steam-based login with database persistence
- **Debug Logging**: Extensive binary protocol analysis and hex dumps
- **HTTP API**: Legacy web endpoints for compatibility

## Game Services Implementation

The game uses a service-based architecture with 10 different services. Each service handles specific game functionality:

### Implemented Services ✅

| Service ID | Service Name | Status | Description |
|------------|--------------|--------|-------------|
| 100 | ServiceLogin | ✅ **WORKING** | Steam authentication, player login |
| 101 | ServiceCards | ✅ **PARTIAL** | Card delivery system (some logic cards) |
| 108 | ServiceGame | ✅ **PARTIAL** | Basic subscription handling, no RPC support |

### Pending Services ⏳

| Service ID | Service Name | Status | Description |
|------------|--------------|--------|-------------|
| 102 | ServiceDebug | ❌ **TODO** | Debug and development tools |
| 103 | ServiceChat | ❌ **TODO** | In-game chat system |
| 104 | ServicePaddock | ❌ **TODO** | Horse care and paddock management |
| 105 | ServiceSocial | ❌ **TODO** | Friends, social features |
| 106 | ServiceCourseEditor | ❌ **TODO** | Course creation and editing |
| 107 | ServiceMatch | ❌ **TODO** | Match-making and competitions |
| 109 | ServicePlayer | ❌ **TODO** | Player data and progression |

### Service Implementation Notes

Each service contains multiple RPC (Remote Procedure Call) methods that need individual implementation. The client will call various methods on these services during gameplay, and currently most result in disconnection due to missing handlers.

## Card System Implementation

The game uses a card-based configuration system where server sends logic cards containing game rules and settings:

### Implemented Cards ✅

| Card Category | Card Type | Status | Description |
|---------------|-----------|--------|-------------|
| LogicMain (21) | CardLogicMain | ✅ **WORKING** | Core game logic and progression |
| LogicActionPoints (22) | CardLogicActionPoints | ✅ **WORKING** | Action point system configuration |
| LogicChat (30) | CardLogicChat | ✅ **WORKING** | Chat system settings and rules |
| LogicSkins (17) | CardLogicSkins | ✅ **PARTIAL** | Horse and player skin system currently responds with empty lists as I do not know specific skin ids |

### Pending Cards ⏳

| Card Category | Card Type | Status | Priority |
|---------------|-----------|--------|----------|
| Item (1) | CardItem | ❌ **TODO** | High - Game items |
| Course (2) | CardShowJump | ❌ **TODO** | Low - Jump Courses |
| Maneuvering (3) | CardAgility | ❌ **TODO** | Low - Agility courses |
| Quest (4) | CardQuest | ❌ **TODO** | Low - Quest system |
| LogicHorse (15) | CardHorse | ❌ **TODO** | High - Horse logic |
| LogicTutorial (16) | CardLogicTutorial | ❌ **TODO** | High - Tutorial system |
| LogicCourses (18) | CardLogicCourses | ❌ **TODO** | High - Course logic |
| LogicItems (19) | CardLogicItems | ❌ **TODO** | High - Item system logic |
| LogicPayment (20) | CardLogicPayment | ❌ **TODO** | Low - Payment system |
| LogicDailyLogin (23) | CardLogicDailyLogin | ❌ **TODO** | Low - Daily rewards |
| LogicStartStats (24) | CardLogicStartStats | ❌ **TODO** | High - Starting statistics |
| LogicMatch (25) | CardLogicMatch | ❌ **TODO** | High - Matchmaking/elo system |
| LogicPaddock (26) | CardLogicPaddock | ❌ **TODO** | Medium - Paddock logic? |
| LogicDailyShowdown (27) | CardLogicDailyShowdown | ❌ **TODO** | Low - Daily showdown |
| LogicCourseEditor (28) | CardLogicCourseEditor | ❌ **TODO** | Low - Course editor |
| LogicGrooming (29) | CardLogicGrooming | ❌ **TODO** | High - Horse grooming |
| LogicSteam (31) | CardLogicSteam | ❌ **TODO** | Low - Steam achievement integration |
| LogicGuild (32) | CardLogicGuild | ❌ **TODO** | Low - Guild system |
| LogicNews (33) | CardLogicNews | ❌ **TODO** | Low - News system |
| Strings (34) | CardStrings | ❌ **TODO** | Medium - Localization |

## Technical Implementation Details

### Protocol Analysis
The server implements the game's custom binary protocol:
- **Igor Binary Serialization**: Custom serialization format with VarInt encoding
- **Service Routing**: Messages routed by service ID (100-109) 
- **RPC System**: Remote procedure calls with unique IDs for responses
- **Card Variants**: Polymorphic card system with category-based deserialization
- **BitField Structures**: MSB-first bit field encoding for nullable objects

### Current Achievements ✅
- ✅ **Steam Authentication**: Full login process with database persistence
- ✅ **Card Delivery**: ServiceCards properly delivers 4 logic cards to client
- ✅ **Binary Protocol**: Igor serialization/deserialization working correctly
- ✅ **Service Routing**: Message routing by service ID implemented
- ✅ **Database Integration**: SQLite database for user management
- ✅ **Debug Infrastructure**: Comprehensive logging and hex dump analysis

### Known Issues ⚠️
- **Post-Login Disconnection**: Client disconnects after successful login due to unhandled service calls
- **Missing Service Handlers**: Most services (102-109) still need RPC method implementations
- **Incomplete Card Set**: Only 4 of 30+ card types implemented

### Why So Much Logging? 🔍
The codebase contains extensive debug logging because:
- **Binary Protocol Complexity**: Igor serialization requires precise byte-level analysis
- **Reverse Engineering**: Understanding undocumented game protocol through traffic analysis  
- **BitField Debugging**: MSB-first bit encoding was discovered through extensive logging
- **Service Discovery**: Identifying which services and RPCs the client calls
- **Data Structure Analysis**: Understanding complex nested object serialization

All debug output can be controlled via `debug_config.py` and disabled in production.

## Setup Instructions (Advanced Users)

### Option 1: Quick Start (Recommended)
1. **Ensure Python 3.7+ is installed**
2. **Double-click `start_server.bat`** to automatically set up and start the server
3. **The script will handle virtual environment creation and dependency installation**

### Option 2: Manual Setup
1. **Install Python 3.7+** if not already installed
2. **Create a virtual environment:**
   ```bash
   python -m venv .venv
   ```
3. **Activate the virtual environment:**
   ```bash
   .venv\Scripts\activate  # Windows
   source .venv/bin/activate  # Linux/Mac
   ```
4. **Install dependencies:**
   ```bash
   pip install fastapi uvicorn websockets aiofiles python-multipart
   ```
5. **Configure debug settings** (optional):
   - Edit `debug_config.py` to control logging levels
   - Set `DEBUG_ENABLED = False` to disable verbose logging
6. **Run the server:**
   ```bash
   python Server.py
   ```

## Development Status & Contributing

### Current Priority Tasks 🎯

**Phase 1: Core Services (Most Critical - Game Functionality)**
1. **ServicePlayer Implementation** - Player data management and progression (CRITICAL)
2. **ServicePaddock Implementation** - Horse care and paddock exploration (CRITICAL)
3. **ServiceMatch Implementation** - Basic competition system (HIGH)

**Phase 2: Essential Logic Cards (Foundation)**
4. **CardLogicHorse Implementation** - Horse data structures and behavior (CRITICAL)
5. **CardLogicItems Implementation** - Item system and inventory logic (CRITICAL)
6. **CardLogicStartStats Implementation** - Initial player/horse statistics (HIGH)
7. **CardLogicTutorial Implementation** - Tutorial system to guide new players (HIGH)
8. **CardLogicGrooming Implementation** - Horse care mechanics (HIGH)

**Phase 3: Content & Courses**
9. **CardLogicCourses Implementation** - Course definitions (HIGH)
10. **Basic Course Creation** - Simple course generation for gameplay (MEDIUM)

**Note**: Core services are prioritized first as they handle the immediate client requests that cause disconnections. Logic cards provide the underlying data structures and can be implemented with basic placeholders initially, then enhanced as needed.

### How to Contribute 🤝

**Everyone is welcome to help!** This is a complex reverse engineering project that benefits from multiple contributors:

1. **Fork the repository**
2. **Analyze debug logs** to understand missing service calls
3. **Implement service handlers** in `Server.py`
4. **Add card implementations** following existing patterns
5. **Test with game client** and fix issues
6. **Submit pull requests** with detailed explanations

### Contribution Guidelines
- **Follow existing code patterns** for consistency
- **Add comprehensive logging** for new implementations  
- **Test thoroughly** with actual game client
- **Document your findings** in code comments
- **Include hex dumps** of relevant protocol data

### Understanding the Protocol
Use the debug logs to understand what the client expects:
```bash
# Enable all debug logging in debug_config.py
DEBUG_ENABLED = True
DEBUG_TCP_COMMUNICATION = True
DEBUG_BINARY_DATA = True

# Run server and check logs in ./debug_logs/
# Look for unhandled service calls and implement them
```

### Client-Side Analysis Tools 🔧

For advanced protocol analysis and understanding client behavior, you can use client-side modding tools:

**MelonLoader v0.5.7**
- **Installation**: Install MelonLoader v0.5.7 on the game client
- **Purpose**: Enables C# mod loading for runtime analysis and patching
- **Benefits**: Create Harmony patches to intercept and log client-side method calls

**UnityExplorer v4.12.7 (by yukieiji)**
- **Installation**: Install as MelonLoader mod after setting up MelonLoader
- **Purpose**: Runtime Unity object inspection and manipulation
- **Benefits**: Examine game objects, components, and data structures in real-time

**Development Workflow:**
1. **Install MelonLoader** on the game client
2. **Add UnityExplorer** for runtime inspection
3. **Create Harmony patches** to log specific client actions:
   ```csharp
   [HarmonyPatch(typeof(ServiceLogin), "Send")]
   public static void LogServiceCall(ref object data)
   {
       MelonLogger.Msg($"ServiceLogin.Send called with: {data}");
   }
   ```
4. **Mock data creation**: Use client-side inspection to understand expected data formats
5. **Serialize mock data** to match binary format on server side
6. **Compare with server logs** to ensure protocol compatibility

This approach helps bridge the gap between client expectations and server implementation, making it easier to create accurate service handlers and card data structures.

## Game Configuration

### Client Configuration Required ⚠️
The game client must be modified to connect to your local server instead of official servers:

**Method 1: Asset Bundle Modification (Recommended)**
- Use `AssetbundleEditServer.py` to create modified `resources.assets` file
- Make sure the input/output path correctly points to the `resources.assets` 
- This patches the Unity asset bundle to point to local server
- Replace the game's asset bundle with the modified version

**Method 2: servers.txt Override (Legacy)**
- Replace game's `servers.txt` with local server configuration  
- **Note**: This method doesn't work as the game primarily uses embedded asset bundle data but may work with a simple client side patch

### Server Configuration JSON
```json
[
    {
        "Name": "local_emulator",
        "WebSocketUrl": "ws://127.0.0.1:80/websocket",
        "Host": "127.0.0.1",
        "Port": 27130,
        "PolicyPort": 27132,
        "ServerURL": "http://127.0.0.1:80/mochiweb/",
        "ImageURL": "http://127.0.0.1:80/rcc/",
        "ProxyURL": "http://127.0.0.1:80/",
        "FacebookOGUrl": "http://127.0.0.1:80/rcc/open_graph/",
        "FacebookApp": "ridingclub"
    }
]
```

### Server Endpoints

The emulator provides the following endpoints:

#### Core Game Protocol
- **TCP Binary Protocol**: `127.0.0.1:27130` - Main game communication
- **Policy Server**: `127.0.0.1:27132` - Flash policy server

#### HTTP API (Legacy Compatibility)
- **Root**: `http://127.0.0.1:80/` - Server status
- **Health Check**: `http://127.0.0.1:80/health` - Server health and statistics
- **Game API**: `http://127.0.0.1:80/mochiweb/` - Game API endpoints
- **Images**: `http://127.0.0.1:80/rcc/` - Game image serving
- **Facebook OG**: `http://127.0.0.1:80/rcc/open_graph/` - Open Graph metadata
- **WebSocket**: `ws://127.0.0.1:80/websocket` - Real-time communication

## Debug Configuration

The server includes extensive debug logging controlled by `debug_config.py`:

### Debug Categories
```python
# Main debug toggle
DEBUG_ENABLED = True

# Specific debug categories  
DEBUG_TCP_COMMUNICATION = True    # TCP connections and service calls
DEBUG_HTTP_REQUESTS = True        # HTTP API requests
DEBUG_BINARY_DATA = True         # Hex dumps of binary protocol data
DEBUG_PROTOCOL_ANALYSIS = True   # Detailed protocol parsing

# Console output settings
DEBUG_CONSOLE_VERBOSE = True     # Show debug info in console
DEBUG_CONSOLE_HEX_LIMIT = 32    # Max hex bytes in console output

# Log file settings
DEBUG_MAX_BINARY_LOG_SIZE = 1000 # Limit binary dump size
```

### Debug Log Files

When debug mode is enabled, logs are saved to `./debug_logs/` with timestamps:

- `tcp_communication_YYYYMMDD_HHMMSS.log` - Service calls, RPC messages, connection events
- `http_communication_YYYYMMDD_HHMMSS.log` - HTTP requests and responses  
- `binary_data_YYYYMMDD_HHMMSS.log` - Complete hex dumps of binary protocol data

### Example Debug Output

**TCP Service Call:**
```
2025-01-28 10:30:15 - SERVICE IDENTIFIED: tcp_client_1 - Service=100 (ServiceLogin)
2025-01-28 10:30:15 - LOGIN REQUEST: tcp_client_1 - RPC_ID=1
2025-01-28 10:30:15 - LOGIN RESPONSE: tcp_client_1 - PlayerID=12345
```

**Binary Protocol Data:**
```
INCOMING - tcp_client_1 - ServiceGame.Subscribe() call
Length: 3 bytes
Hex: 6C0100
ASCII: l..
Raw bytes: [108, 1, 0]
Protocol: Service=108, Function=0 (Subscribe)
```

**Unhandled Service Call (Causes Disconnection):**
```
2025-07-28 10:30:16 - HANDLING GENERIC SERVICE 104: tcp_client_1
2025-07-28 10:30:16 - Unknown ServicePaddock RPC - Client may disconnect
```

## Troubleshooting

### Expected Behavior ⚠️
- **Client connects successfully** ✅
- **Login process completes** ✅ 
- **Cards are delivered** ✅
- **Client disconnects after ~5 seconds** ❌ **EXPECTED** - Due to unhandled service calls

### Common Issues

#### Client Won't Connect
1. **Check server is running**: `http://127.0.0.1:80/health` should respond
2. **Verify game client configuration**: Use `AssetbundleEditServer.py` to modify client
3. **Check firewall**: Allow Python/server through Windows Firewall
4. **Port conflicts**: Ensure ports 80, 27130, 27132 are available

#### Connection Drops Immediately  
**This is expected!** The client requests services we haven't implemented yet:
1. **Check debug logs** for unhandled service calls
2. **Implement missing service handlers** in `Server.py`
3. **Add RPC method handling** for specific service calls
4. **Test incrementally** with each new service implementation

#### Permission Issues
- **Windows**: Run as Administrator to use port 80, or change to port 8080
- **Linux/Mac**: Use `sudo` for privileged ports, or configure port forwarding

### Debugging Disconnections

1. **Enable all debug logging** in `debug_config.py`
2. **Run server and connect client**
3. **Check `tcp_communication_*.log`** for last service call before disconnect
4. **Implement the missing service handler**:
   ```python
   elif service_id == XXX:  # Replace XXX with service ID
       return self.handle_service_xxx(data, rpc_id, client_id), service_id
   ```

## Development Architecture

### Adding New Services
1. **Create service handler method**:
   ```python
   def handle_service_paddock(self, data: bytes, rpc_id: int, client_id: str) -> bytes:
       # Parse RPC function ID from data
       # Implement specific RPC methods
       # Return appropriate response
   ```

2. **Add to service routing**:
   ```python
   elif service_id == 104:  # ServicePaddock
       return self.handle_service_paddock(payload_data, rpc_id, client_id), service_id
   ```

### Adding New Cards
1. **Create card data method**:
   ```python
   def create_logic_horse_card_data(self) -> bytes:
       # Follow existing card patterns
       # Use Card.WriteVariant format  
       # Return binary card data
   ```

2. **Add to card delivery**:
   ```python
   logic_horse_data = self.create_logic_horse_card_data()
   message_content.extend(logic_horse_data)
   ```

## Project Status & Roadmap

### 🚧 **Phase 1: Protocol Foundation (IN PROGRESS)**
- Steam authentication system
- Igor binary protocol implementation  
- Service routing and RPC handling
- Card delivery system
- Debug infrastructure

### ⏳ **Phase 2: Core Services (PLANNED)**
- ServicePlayer implementation (player data)
- ServicePaddock implementation (horse management)  
- ServiceMatch implementation (competitions)
- ServiceSocial implementation (friends, social features)

### ⏳ **Phase 3: Game Logic (PLANNED)**
- Course management and racing
- Item and inventory systems
- Tutorial and progression systems

### ⏳ **Phase 4: Advanced Features (FUTURE)**
- Course editor functionality
- Guild/clan systems
- Daily events and challenges
- Payment and monetization systems (Not really necessary but bypassing them is)

## Legal Notice

This emulator is for **educational and preservation purposes only**. 

- **Ensure you own a legitimate copy** of the game
- **Comply with all applicable laws** and terms of service
- **This is not affiliated** with the original game developers
- **Use at your own risk** - this software is provided as-is

## Community & Support

- **GitHub Issues**: Report bugs and request features
- **Contributions Welcome**: Fork and submit pull requests
- **Documentation**: Help improve this README and code comments
- **Testing**: Test with different client scenarios and report findings

**This project benefits from community collaboration!** Even small contributions like testing, documentation, or bug reports are valuable.
