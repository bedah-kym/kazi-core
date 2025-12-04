import os

file_path = r'Backend\chatbot\consumers.py'

# The block to replace:
# 1. The if condition
# 2. The ai_query extraction
# 3. The broadcast_chunk definition (with old buffer logic)

old_block_start = """                # === ORCHESTRATION: Full pipeline ===
                if message_content.startswith('@mathia'):"""

new_block = """                # === ORCHESTRATION: Full pipeline ===
                if message_content.lower().startswith('@mathia'):"""

# We'll replace the whole block down to the end of broadcast_chunk
# But finding the exact end is tricky.
# Let's replace pieces.

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace the if condition and ai_query extraction
if "if message_content.startswith('@mathia'):" in content:
    content = content.replace(
        "if message_content.startswith('@mathia'):",
        "if message_content.lower().startswith('@mathia'):"
    )
    # Also replace the ai_query line
    # Old: ai_query = message_content.replace('@mathia', '').strip()
    # New: ai_query = message_content[7:].strip()
    # But wait, replace('@mathia') only works if it matches case.
    # If we use lower().startswith, we should use slicing or regex replace.
    # Slicing is safer: message_content[7:] because len('@mathia') is 7.
    
    # Find the line
    start_idx = content.find("ai_query = message_content.replace('@mathia', '').strip()")
    if start_idx != -1:
        # Replace it
        content = content.replace(
            "ai_query = message_content.replace('@mathia', '').strip()",
            "ai_query = message_content[7:].strip()"
        )

# 2. Update buffer logic
# Old: if len(joined_text) > 10 or (current_time - stream_state['last_send']) > 0.1 or is_final:
# New: if len(joined_text) > 20 or (current_time - stream_state['last_send']) > 0.2 or is_final:

if "if len(joined_text) > 10 or (current_time - stream_state['last_send']) > 0.1 or is_final:" in content:
    content = content.replace(
        "if len(joined_text) > 10 or (current_time - stream_state['last_send']) > 0.1 or is_final:",
        "if len(joined_text) > 20 or (current_time - stream_state['last_send']) > 0.2 or is_final:"
    )

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Successfully optimized consumers.py")
