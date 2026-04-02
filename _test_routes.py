"""Quick diagnostic: list all Flask routes and test them."""
import sys, os
os.chdir(os.path.join(os.path.dirname(__file__), 'backend'))
sys.path.insert(0, '.')

from app import app

print("=== Registered Flask Routes ===")
for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
    methods = ','.join(sorted(rule.methods - {'OPTIONS', 'HEAD'}))
    print(f"  {methods:10s} {rule.rule}")

# Test with Flask test client
client = app.test_client()
print("\n=== Testing Endpoints ===")

tests = [
    ("GET", "/health"),
    ("GET", "/stats"),
    ("GET", "/profiles"),
    ("GET", "/export/csv"),
    ("GET", "/candidates"),
    ("DELETE", "/clear"),
]

for method, path in tests:
    if method == "GET":
        resp = client.get(path)
    elif method == "DELETE":
        resp = client.delete(path)
    body = resp.get_data(as_text=True)[:120]
    print(f"  {method} {path}: {resp.status_code} - {body}")
