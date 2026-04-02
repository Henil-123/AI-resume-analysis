"""
Vercel Serverless Entry Point — wraps the Flask app for Vercel Python runtime.
All routes are exposed under /api/* via vercel.json rewrites.
"""
import sys
import os

# Set VERCEL env flag
os.environ['VERCEL'] = '1'

# Add backend directory to Python path so imports work
backend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, backend_dir)

# Also add the project root for data/ access
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ['PROJECT_ROOT'] = project_root

from app import app
