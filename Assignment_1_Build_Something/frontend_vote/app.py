from flask import Flask, request, jsonify, render_template_string
import os, pymysql
DB_HOST = os.getenv("DB_HOST", "db")
DB_USER = os.getenv("DB_USER", "votes_user")
DB_PASS = os.getenv("DB_PASS", "CHANGE_ME_STRONG")
DB_NAME = os.getenv("DB_NAME", "votesDB")
PORT = int(os.getenv("PORT", "8080"))
app = Flask(__name__)
def db(): return pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME, autocommit=True)
@app.route("/api/health")
def health():
    try:
        with db() as conn: conn.ping()
        return {"status":"ok"}, 200
    except Exception as e:
        return {"status":"error","detail":str(e)}, 500
@app.route("/api/votes", methods=["POST"])
def submit_vote():
    body = request.get_json(silent=True) or {}
    opt = body.get("option")
    if opt not in ("a","b"):
        return {"error":"option must be 'a' or 'b'"}, 400
    with db() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO votes(option_value) VALUES (%s)", (opt,))
    return {"status":"stored"}, 201
HTML = """
<!doctype html><meta charset="utf-8">
<h1>Vote</h1>
<form onsubmit="send(event)">
  <label><input type="radio" name="opt" value="a"> Belgian Malinois </label><br>
  <label><input type="radio" name="opt" value="b"> German Shepherd </label><br><br>
  <button>Submit</button>
</form>
<p id="msg"></p>
<script>
async function send(e){
  e.preventDefault();
  const opt = new FormData(e.target).get('opt');
  const r = await fetch('/api/votes',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({option:opt})});
  const j = await r.json();
  document.getElementById('msg').textContent = j.status || (j.error || 'done');
}
</script>
"""
@app.route("/")
def index(): return render_template_string(HTML)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
