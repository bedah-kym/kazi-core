import os

file_path = r'Backend\chatbot\consumers.py'

# Read the file
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Check line 661 (index 660)
print(f"Line 661: {lines[660].strip()}")

# Replace the specific lines
if 'await self.send_message({' in lines[660]:
    # Replace lines 661-665
    indent = '                        '
    new_lines = [
        f"{indent}await self.send_chat_message({{\n",
        f"{indent}    'command': 'new_message',\n",
        f"{indent}    'message': {{\n",
        f"{indent}        'member': 'mathia',\n",
        f"{indent}        'content': response,\n",
        f"{indent}        'timestamp': str(timezone.now())\n",
        f"{indent}    }}\n",
        f"{indent}}})\n"
    ]
    
    # Replace lines
    lines[660:665] = new_lines
    
    # Write back
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print("SUCCESS: Fixed the broadcast bug!")
else:
    print("ERROR: Line 661 doesn't match expected pattern")
    print(f"Content: {lines[660]}")
