# These values bound the app-side remote vector request and response. Raising
# timeouts/response caps permits larger or slower calls; TTLs are range-checked.
VECTOR_SEARCH_TIMEOUT_SECONDS = 5
VECTOR_TOKEN_TTL_SECONDS = 90
VECTOR_TOKEN_TTL_MIN = 60
VECTOR_TOKEN_TTL_MAX = 120
MAX_RESPONSE_BYTES = 1_000_000
