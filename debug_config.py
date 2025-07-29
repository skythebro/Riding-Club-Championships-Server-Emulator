"""
Debug Configuration for RCC Server Emulator
Modify these settings to control debug logging behavior
"""

# Main debug toggle - set to False in production
DEBUG_ENABLED = True

# Specific debug categories
DEBUG_TCP_COMMUNICATION = True      # Log all TCP connections and messages
DEBUG_HTTP_REQUESTS = True          # Log HTTP requests and responses  
DEBUG_BINARY_DATA = True           # Log hex dumps of binary data
DEBUG_PROTOCOL_ANALYSIS = True     # Detailed protocol parsing logs

# Log file settings
DEBUG_LOG_DIRECTORY = "./debug_logs"
DEBUG_MAX_BINARY_LOG_SIZE = 1000   # Maximum bytes to log in binary dumps
DEBUG_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

# Console output settings
DEBUG_CONSOLE_VERBOSE = True       # Show debug info in console
DEBUG_CONSOLE_HEX_LIMIT = 32      # Max hex bytes to show in console

# Auto-rotate logs (create new files every N connections)
DEBUG_AUTO_ROTATE_LOGS = False
DEBUG_ROTATE_AFTER_CONNECTIONS = 10
