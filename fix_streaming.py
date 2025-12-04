import os

file_path = r'Backend\chatbot\consumers.py'

# The old block to replace
old_block_start = """                    if ai_query:
                        # Step 1: Parse intent
                        intent = await parse_intent(ai_query, {"""

# The new block to insert
new_block = """                    if ai_query:
                        # Step 1: Parse intent
                        intent = await parse_intent(ai_query, {
                            "user_id": member_user.id,
                            "username": member_username,
                            "room_id": room_id
                        })
                        
                        logger.info(f"Intent: {intent}")
                        
                        # Helper to broadcast chunks
                        async def broadcast_chunk(chunk_text, is_final=False):
                            await self.channel_layer.group_send(
                                self.room_group_name,
                                {
                                    "type": "ai_stream_chunk",
                                    "chunk": chunk_text,
                                    "is_final": is_final
                                }
                            )

                        # Step 2: Route if high confidence & not general chat
                        if intent["confidence"] > 0.7 and intent["action"] != "general_chat":
                            # Route through MCP
                            result = await route_intent(intent, {
                                "user_id": member_user.id,
                                "room_id": room_id,
                                "username": member_username
                            })
                            
                            logger.info(f"MCP result: {result['status']}")
                            
                            if result["status"] == "success":
                                # Step 3: Synthesize natural language response (STREAMING)
                                from orchestration.data_synthesizer import synthesize_response_stream
                                
                                async for chunk in synthesize_response_stream(
                                    intent, 
                                    result, 
                                    use_llm=True
                                ):
                                    await broadcast_chunk(chunk)
                                    
                            else:
                                await broadcast_chunk(f"❌ {result['message']}")
                        else:
                            # Fallback to LLM for general chat or low confidence (STREAMING)
                            from orchestration.llm_client import get_llm_client
                            llm_client = get_llm_client()
                            
                            async for chunk in llm_client.stream_text(
                                system_prompt="You are Mathia, a helpful AI assistant. Be concise and friendly.",
                                user_prompt=ai_query,
                                temperature=0.7,
                                max_tokens=500
                            ):
                                await broadcast_chunk(chunk)
                        
                        # End stream
                        await broadcast_chunk("", is_final=True)
"""

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the start of the block
start_idx = content.find(old_block_start)
if start_idx == -1:
    print("Could not find start of block")
    # Try to find with less context
    old_block_start_short = "if ai_query:"
    start_idx = content.find(old_block_start_short, content.find("ai_query = message_content.replace"))
    if start_idx == -1:
        print("Could not find start of block (short)")
        exit(1)

# Find the end of the block
# The block ends before the `else:` of the `if message_content.startswith('@mathia'):`?
# No, it ends before `else:` of `if command == 'typing':`? No.
# It ends at the end of the `if ai_query:` block.
# The original code had:
# await self.send_chat_message({ ... })
# which was the last thing in the `if ai_query:` block.

# Let's look for the end marker
end_marker = """                        await self.send_chat_message({
                            'command': 'new_message',
                            'message': {
                                'member': 'mathia',
                                'content': response,
                                'timestamp': str(timezone.now())
                            }
                        })"""

end_idx = content.find(end_marker, start_idx)
if end_idx == -1:
    print("Could not find end of block")
    # Try finding just the send_chat_message part
    end_marker_short = "'member': 'mathia',"
    end_idx = content.find(end_marker_short, start_idx)
    if end_idx == -1:
        print("Could not find end of block (short)")
        exit(1)
    # Adjust to find the closing braces
    end_idx = content.find("})", end_idx) + 2
    end_idx = content.find("})", end_idx) + 2 # Outer closing brace

else:
    end_idx += len(end_marker)

# Replace
new_content = content[:start_idx] + new_block + content[end_idx:]

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Successfully patched consumers.py with streaming logic")
