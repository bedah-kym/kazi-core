#!/usr/bin/env python3
"""
Quick fix script to replace send_message with send_chat_message in consumers.py
"""

import re

file_path = r'Backend\chatbot\consumers.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Simple direct replacement
lines = content.split('\n')
modified = False

for i, line in enumerate(lines):
    if i > 650 and i < 670 and 'await self.send_message({' in line:
        # Found the problematic line, replace it
        indent = ' ' * 24  # Match existing indentation
        lines[i] = f"{indent}await self.send_chat_message({{"
        lines[i+1] = f"{indent}    'command': 'new_message',"
        lines[i+2] = f"{indent}    'message': {{"
        lines[i+3] = f"{indent}        'member': 'mathia',"
        # Keep content and timestamp
        # lines[i+4] through [i+6] stay same but indented more
        old_content = lines[i+4]
        old_timestamp = lines[i+5]
        old_close = lines[i+6]
        
        lines[i+4] = f"{indent}        'content': response,"
        lines[i+5] = f"{indent}        'timestamp': str(timezone.now())"
        lines[i+6] = f"{indent}    }}"
        lines.insert(i+7, f"{indent}}})") 
        modified = True
        print(f"Found and fixed at line {i+1}")
        break

if modified:
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print("Successfully fixed the broadcast issue!")
else:
    print("Could not find the pattern")
