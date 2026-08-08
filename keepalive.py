"""Ping the web service every 10 minutes so Render free tier doesn't spin down."""
import os
import time
import urllib.request

APP_URL = os.environ.get('APP_URL', 'https://workbuddy.onrender.com').rstrip('/')

if __name__ == '__main__':
    try:
        resp = urllib.request.urlopen(APP_URL + '/api/stats', timeout=60)
        print(f'[{time.strftime("%H:%M:%S")}] ping ok: {resp.status}')
    except Exception as e:
        print(f'[{time.strftime("%H:%M:%S")}] ping failed: {e}')
