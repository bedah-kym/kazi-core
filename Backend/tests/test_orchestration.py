#!/usr/bin/env python
"""Test the full orchestration pipeline end-to-end"""

import os
import sys
import asyncio

sys.path.insert(0, '/app/Backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Backend.settings')

import django
django.setup()

async def test_orchestration():
    print("=" * 60)
    print("ORCHESTRATION PIPELINE TEST")
    print("=" * 60)
    
    # Test queries
    test_queries = [
        "@mathia Say hello",
        "@mathia What's the weather in Nairobi?",
        "@mathia Plan a trip to Mombasa",
    ]
    
    for query in test_queries:
        print(f"\n\n{'='*60}")
        print(f"TESTING: {query}")
        print("=" * 60)
        
        ai_query = query[7:].strip() if query.startswith('@mathia') else query
        
        try:
            # Step 1: Parse Intent
            print("\n[1] Parsing Intent...")
            from orchestration.intent_parser import parse_intent
            intent = await parse_intent(ai_query, {
                "user_id": 1,
                "username": "test_user",
                "room_id": 1
            })
            
            print(f"✅ Intent parsed:")
            print(f"   - Action: {intent.get('action')}")
            print(f"   - Confidence: {intent.get('confidence')}")
            print(f"   - Parameters: {intent.get('parameters', {})}")
            
            # Step 2: Route Intent (if high confidence)
            if intent.get('confidence', 0) >= 0.7:
                print("\n[2] Routing Intent via MCP...")
                from orchestration.mcp_router import route_intent
                result = await route_intent(intent, {
                    "user_id": 1,
                    "room_id": 1,
                    "username": "test_user"
                })
                
                print(f"✅ MCP Result:")
                print(f"   - Status: {result.get('status')}")
                print(f"   - Message: {result.get('message', '')[:100]}")
                
                # Step 3: Synthesize Response
                if result['status'] == 'success':
                    print("\n[3] Synthesizing Response...")
                    from orchestration.data_synthesizer import synthesize_response_stream
                    
                    full_response = []
                    async for chunk in synthesize_response_stream(intent, result, use_llm=True):
                        full_response.append(chunk)
                    
                    response_text = "".join(full_response)
                    print(f"✅ Synthesized Response: {response_text[:200]}...")
                else:
                    print(f"⚠️  MCP failed, should fall back to LLM")
            else:
                print(f"\n[2] Low confidence, falling back to LLM...")
                from orchestration.llm_client import get_llm_client
                llm = get_llm_client()
                
                full_response = []
                async for chunk in llm.stream_text(
                    system_prompt="You are Mathia, a helpful AI assistant.",
                    user_prompt=ai_query,
                    temperature=0.7,
                    max_tokens=200
                ):
                    full_response.append(chunk)
                
                response_text = "".join(full_response)
                print(f"✅ LLM Response: {response_text[:200]}...")
            
            print(f"\n✅ SUCCESS for: {query}")
            
        except Exception as e:
            print(f"\n❌ FAILED: {type(e).__name__}: {e}")
            import traceback
            print(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(test_orchestration())
