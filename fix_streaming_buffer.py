import os

file_path = r'Backend\chatbot\consumers.py'

# We need to add 'import time' at the top if not present
# But modifying imports with simple replace is risky if we don't know where they are.
# However, we can use `time.time()` inside the function if we import it there or use `timezone.now().timestamp()`?
# `timezone` is already imported. `timezone.now().timestamp()` works.

# The block to replace is the `broadcast_chunk` helper and the loop.
# We need to capture the context to replace it correctly.

old_block_start = """                        # Helper to broadcast chunks
                        async def broadcast_chunk(chunk_text, is_final=False):
                            await self.channel_layer.group_send(
                                self.room_group_name,
                                {
                                    "type": "ai_stream_chunk",
                                    "chunk": chunk_text,
                                    "is_final": is_final
                                }
                            )"""

new_block = """                        # Helper to broadcast chunks (Buffered)
                        # We use a mutable container for closure state
                        stream_state = {'buffer': [], 'last_send': 0}
                        
                        async def broadcast_chunk(chunk_text, is_final=False):
                            import time
                            stream_state['buffer'].append(chunk_text)
                            
                            current_time = time.time()
                            joined_text = "".join(stream_state['buffer'])
                            
                            # Send if buffer > 10 chars OR > 0.1s passed OR is_final
                            if len(joined_text) > 10 or (current_time - stream_state['last_send']) > 0.1 or is_final:
                                if joined_text or is_final:
                                    await self.channel_layer.group_send(
                                        self.room_group_name,
                                        {
                                            "type": "ai_stream_chunk",
                                            "chunk": joined_text,
                                            "is_final": is_final
                                        }
                                    )
                                    stream_state['buffer'] = []
                                    stream_state['last_send'] = current_time"""

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the block
start_idx = content.find(old_block_start)
if start_idx == -1:
    print("Could not find broadcast_chunk definition")
    # It might be indented differently?
    # Let's try to find just the function def line
    start_idx = content.find("async def broadcast_chunk(chunk_text, is_final=False):")
    if start_idx == -1:
        print("Could not find broadcast_chunk function")
        exit(1)
    # Backtrack to comment
    comment_start = content.rfind("# Helper to broadcast chunks", 0, start_idx)
    if comment_start != -1:
        start_idx = comment_start

# Find the end of the function
# It ends with the closing parenthesis of group_send and the closing brace of the dict
# and the closing parenthesis of group_send call.
#                             )
#                         )

end_marker = """                                }
                            )"""
end_idx = content.find(end_marker, start_idx)
if end_idx == -1:
    print("Could not find end of broadcast_chunk")
    exit(1)
end_idx += len(end_marker)

# Replace
new_content = content[:start_idx] + new_block + content[end_idx:]

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Successfully added buffering to consumers.py")
