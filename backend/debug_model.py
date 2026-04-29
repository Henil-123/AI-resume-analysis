import logging
from sentence_transformers import SentenceTransformer
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    logger.info("Starting model load...")
    start = time.time()
    model = SentenceTransformer('all-MiniLM-L6-v2')
    end = time.time()
    logger.info(f"Model loaded successfully in {end-start:.2f}s")
    
    text = "This is a test sentence."
    logger.info("Encoding text...")
    emb = model.encode(text)
    logger.info("Encoding successful")
except Exception as e:
    logger.error(f"Error: {e}")
