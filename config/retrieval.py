# These values bound document searches and adaptive retries. Raising limits may
# add latency and model context; changing the threshold alters retry deduplication.
INITIAL_TOP_K = 5
RETRY_TOP_K = 4
MAX_EXTRA_ROUNDS = 2
MAX_TOTAL_CHUNKS = 8
ADAPTIVE_RETRIEVAL_ENABLED = True
NEAR_DUPLICATE_JACCARD_THRESHOLD = 0.9
DEFAULT_SEARCH_TOP_K = 5
