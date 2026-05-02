import os
from dotenv import load_dotenv
load_dotenv('backend/.env')

import sys
sys.path.insert(0, './backend')

from services.job_matcher import calculate_semantic_score

resume = "Experienced Python developer with Flask and SQL skills."
jd = "Looking for a Junior Python Developer."

print(f"Testing Groq with key: {os.getenv('GROQ_API_KEY')[:10]}...")
score = calculate_semantic_score(resume, jd)
print(f"Semantic Score: {score}")
