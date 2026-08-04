import urllib.request, json, urllib.error
for email in ['erict@example.com', 'bot@example.com', 'erict_bot@ai4trade.local']:
    for password in ['secure_password', 'secure_password_123', 'password123']:
        req = urllib.request.Request('https://ai4trade.ai/api/claw/agents/login', data=json.dumps({'email': email, 'password': password}).encode('utf-8'), headers={'Content-Type': 'application/json'})
        try:
            print(f"Testing {email} / {password}:")
            print(urllib.request.urlopen(req).read().decode())
        except urllib.error.HTTPError as e:
            print(e.read().decode())
