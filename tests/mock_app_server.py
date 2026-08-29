"""Mock frontend server on localhost:3000 for UI bug reproduction testing."""

import http.server
import socketserver
import threading
import time

HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Customer Portal - Login / Signup</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; }
        .card { max-width: 420px; margin: 0 auto; background: #1e293b; padding: 24px; border-radius: 12px; border: 1px solid #334155; }
        h2 { margin-top: 0; color: #38bdf8; }
        label { display: block; margin-top: 16px; margin-bottom: 6px; font-weight: 500; font-size: 14px; }
        input[type="text"] { width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: white; font-size: 15px; }
        button { margin-top: 20px; width: 100%; padding: 12px; border: none; border-radius: 6px; background: #0284c7; color: white; font-size: 16px; font-weight: 600; cursor: pointer; }
        /* Intentional visual glitch on alert banner: negative margin and overlapping */
        .error-alert { display: none; margin-top: -10px; margin-bottom: -15px; padding: 12px; background: rgba(239, 68, 68, 0.2); border: 1px solid #ef4444; color: #fca5a5; border-radius: 6px; font-size: 13px; position: relative; z-index: 10; }
    </style>
</head>
<body>
    <div class="card">
        <h2>Developer Portal Sign In</h2>
        <div id="error-box" class="error-alert">Error: Invalid email format! Text is clipping over input border.</div>
        <label for="email">Work Email</label>
        <input type="text" id="email" name="email" placeholder="user@company.com">
        <button id="submit-btn" onclick="submitForm()">Sign In</button>
    </div>

    <script>
        function submitForm() {
            var val = document.getElementById('email').value;
            if (!val.includes('@') || !val.includes('.')) {
                document.getElementById('error-box').style.display = 'block';
            } else {
                alert('Signed in successfully!');
            }
        }
    </script>
</body>
</html>
"""

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(HTML.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def start_server(port=3000):
    server = socketserver.TCPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


if __name__ == "__main__":
    s = start_server(3000)
    print("Mock app running on http://127.0.0.1:3000")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        s.shutdown()
