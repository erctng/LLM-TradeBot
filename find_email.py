import re
data = open(r'C:\Users\erict\.gemini\antigravity\brain\7499f30a-c2c6-4011-82a3-a1b4c48ed924\.system_generated\logs\transcript.jsonl', encoding='utf-8').read()
print(list(set(re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', data))))
