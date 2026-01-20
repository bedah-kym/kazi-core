import requests
import os

url = "https://raw.githubusercontent.com/ilyankou/passport-index-dataset/master/passport-index-tidy.csv"
path = "Backend/travel/passport-index-tidy.csv"

print(f"Downloading to {path}...")
try:
    r = requests.get(url)
    r.raise_for_status()
    with open(path, 'wb') as f:
        f.write(r.content)
    print("✅ Download successful.")
except Exception as e:
    print(f"❌ Download failed: {e}")
