from pathlib import Path

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent

# Config directory
CONFIG_DIR = BASE_DIR / "config"

# Web directory
STATIC_DIR = BASE_DIR / "web"

# Configuration Files
DEFAULT_CONFIG_PATH = str(CONFIG_DIR / "mcp_server_config.json")
TOOL_STATES_PATH = str(CONFIG_DIR / "tool_states.json")
SERVER_ORDER_PATH = str(CONFIG_DIR / "server_order.json")
