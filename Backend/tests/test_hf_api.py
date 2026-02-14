#!/usr/bin/env python
"""Test HuggingFace API connectivity and authentication"""

import os
import sys
import asyncio
import httpx

sys.path.insert(0, '/app/Backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')

async def test_hf_api():
    hf_token = os.environ.get('HF_API_TOKEN', '')
    
    print("=" * 60)
    print("HUGGING FACE API TEST")
    print("=" * 60)
    
    if not hf_token:
        print("❌ HF_API_TOKEN not found in environment")
        return
    
    print(f"✅ Token found: {hf_token[:8]}...")
    
    # Test endpoint
    url = "https://router.huggingface.co/v1/chat/completions"
    model = "meta-llama/Llama-3.1-8B-Instruct"
    
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Say 'Hello, this is a test.' in exactly those words."}
        ],
        "max_tokens": 50,
        "temperature": 0.1
    }
    
    print(f"\n📡 Testing HF API at: {url}")
    print(f"📦 Model: {model}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"\n⏳ Sending test request...")
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {hf_token}",
                    "Content-Type": "application/json",
                },
                json=payload
            )
            
            print(f"\n📊 Response Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                print(f"✅ API Call Successful!")
                print(f"📝 Response: {content[:200]}")
            else:
                print(f"❌ API Call Failed")
                print(f"📋 Response: {response.text[:500]}")
                
    except httpx.ConnectError as e:
        print(f"❌ Connection Error: {e}")
        print("   This suggests network/DNS issues within the container")
    except httpx.TimeoutException:
        print(f"❌ Timeout Error: Request took too long")
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(test_hf_api())
