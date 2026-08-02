import app

client = app.app.test_client()
resp = client.post('/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)
print('status', resp.status_code)
print('path', resp.request.path)
print(resp.get_data(as_text=True)[:1000])
