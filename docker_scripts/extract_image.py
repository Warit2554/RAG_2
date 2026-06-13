import urllib.request
import os

urls = [
    "https://upload.wikimedia.org/wikipedia/commons/e/e5/Shiba_Inu_dog_sitting_in_grass.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/b/b5/Shiba_Inu_sitting.jpg",
    "https://images.unsplash.com/photo-1583511655857-d19b40a7a54e?auto=format&fit=crop&w=800&q=80"
]

success = False
for url in urls:
    try:
        print(f"Attempting to download: {url}")
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read()
            # Save to /workspace so it's written back to the mounted host directory
            with open("/workspace/shiba_inu.jpg", "wb") as f:
                f.write(data)
        print("Success! Image downloaded.")
        success = True
        break
    except Exception as e:
        print(f"Failed to download {url}: {e}")

if not success:
    print("All image sources failed.")
    exit(1)
