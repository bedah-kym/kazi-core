import os

file_path = r'Backend\chatbot\consumers.py'

# We'll search for the `broadcast_chunk` definition and replace the whole function body.
# The function starts with `async def broadcast_chunk(chunk_text, is_final=False):`

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

start_marker = "async def broadcast_chunk(chunk_text, is_final=False):"
start_idx = content.find(start_marker)

if start_idx == -1:
    print("Could not find broadcast_chunk function")
    exit(1)

# We need to find the start of the closure state definition which is just above it
# `stream_state = {'buffer': [], 'last_send': 0}`
# It might be a few lines above.
closure_start = content.rfind("stream_state =", 0, start_idx)
if closure_start == -1:
    print("Could not find stream_state definition")
    exit(1)

# Now find the end of the function.
# It ends with `stream_state['last_send'] = current_time` inside the if block.
# And then the next line is likely dedented or empty.
# Actually, we can just look for the end of the `if ai_query:` block or the next `else:` block?
# No, `broadcast_chunk` is defined inside `if ai_query:`.
# The next thing after `broadcast_chunk` is `if intent["confidence"] > 0.7 ...`

next_block_marker = 'if intent["confidence"] > 0.7'
end_idx = content.find(next_block_marker, start_idx)

if end_idx == -1:
    print("Could not find next block marker")
    # Try finding the comment
    next_block_marker = "# Step 2: Route if high confidence"
    end_idx = content.find(next_block_marker, start_idx)
    if end_idx == -1:
        print("Could not find next block marker (comment)")
        exit(1)

# Now we have the range to replace.
# We need to be careful about indentation.
# The `stream_state` line has indentation.

# Let's see what indentation we have
indentation = content[closure_start-24:closure_start] # approximate
# Actually, let's just use the new block with assumed indentation (24 spaces based on previous files)
# or just copy the indentation from the file.

# Read the indentation of the stream_state line
line_start = content.rfind('\n', 0, closure_start) + 1
indent_str = content[line_start:closure_start]

new_block = f"""{indent_str}# Helper to broadcast chunks (Buffered & Whitespace Filtered)
{indent_str}# We use a mutable container for closure state
{indent_str}stream_state = {{'buffer': [], 'last_send': 0, 'first_token_sent': False}}
{indent_str}
{indent_str}async def broadcast_chunk(chunk_text, is_final=False):
{indent_str}    import time
{indent_str}    
{indent_str}    # Filter leading whitespace if first token hasn't been sent
{indent_str}    if not stream_state['first_token_sent'] and not is_final:
{indent_str}        if not chunk_text.strip():
{indent_str}            return # Ignore pure whitespace at start
{indent_str}        chunk_text = chunk_text.lstrip() # Trim leading space of first word
{indent_str}        stream_state['first_token_sent'] = True
{indent_str}        
{indent_str}    stream_state['buffer'].append(chunk_text)
{indent_str}    
{indent_str}    current_time = time.time()
{indent_str}    joined_text = "".join(stream_state['buffer'])
{indent_str}    
{indent_str}    # Send if buffer > 20 chars OR > 0.2s passed OR is_final
{indent_str}    if len(joined_text) > 20 or (current_time - stream_state['last_send']) > 0.2 or is_final:
{indent_str}        if joined_text or is_final:
{indent_str}            await self.channel_layer.group_send(
{indent_str}                self.room_group_name,
{indent_str}                {{
{indent_str}                    "type": "ai_stream_chunk",
{indent_str}                    "chunk": joined_text,
{indent_str}                    "is_final": is_final
{indent_str}                }}
{indent_str}            )
{indent_str}            stream_state['buffer'] = []
{indent_str}            stream_state['last_send'] = current_time
{indent_str}
{indent_str}"""

# Replace
new_content = content[:closure_start] + new_block + content[end_idx:]

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Successfully patched consumers.py with v2 script")
