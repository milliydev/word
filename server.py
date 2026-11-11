"""KompSavod: public/ papkadagi statik saytni beradi (Firebase versiyasi).

/kirish -> public/kirish.html kabi "toza" manzillarni qo'llaydi.
"""
import os

from flask import Flask, abort, send_from_directory

PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

app = Flask(__name__, static_folder=None)


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def site(path):
    path = path.strip("/")
    if not path:
        return send_from_directory(PUBLIC, "index.html")
    full = os.path.join(PUBLIC, path)
    if os.path.isfile(full):
        return send_from_directory(PUBLIC, path)
    if os.path.isfile(full + ".html"):
        return send_from_directory(PUBLIC, path + ".html")
    abort(404)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"KompSavod: http://localhost:{port}")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port, threaded=True)
