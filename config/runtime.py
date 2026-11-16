# These values control local bind addresses, ports, and process wait intervals.
# Changing ports/hosts affects local endpoints; longer waits delay failure detection.
API_HOST = "127.0.0.1"
API_PORT = 8000
VECTOR_HOST = "127.0.0.1"
VECTOR_PORT = 8001
STREAMLIT_PORT = 8501
STARTUP_TIMEOUT_SECONDS = 30
HEALTH_PROBE_TIMEOUT_SECONDS = 1
POLL_INTERVAL_SECONDS = 0.25
SHUTDOWN_TIMEOUT_SECONDS = 8
