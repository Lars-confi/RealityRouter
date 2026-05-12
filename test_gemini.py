import urllib.request
import os

key = "dummy_key"
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"

req = urllib.request.Request(url)
req.add_header("User-Agent", "python-requests/2.31.0")

try:
    urllib.request.urlopen(req)
except Exception as e:
    print(f"Error: {e}")
    if hasattr(e, 'read'):
        print(e.read().decode())
