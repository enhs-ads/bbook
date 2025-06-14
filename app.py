from flask import Flask, render_template, request, redirect, session, url_for, g
from flask_socketio import SocketIO, emit, join_room
import sqlite3
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'secretkey'
socketio = SocketIO(app)
DB_NAME = 'messenger.db'

UPLOAD_FOLDER = 'static/profile_pics'
os.makedirs(os.path.join(app.root_path, UPLOAD_FOLDER), exist_ok=True)

# --- Database Helper ---
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_NAME, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db:
        db.close()

# --- Routes ---
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect('/chat')
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=? AND password=?", 
                          (request.form['username'], request.form['password'])).fetchone()
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['profile_pic'] = user['profile_pic']
            return redirect('/chat')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        db = get_db()
        username = request.form['username']
        password = request.form['password']
        profile_pic = request.files['profile_pic']

        filename = None
        if profile_pic and profile_pic.filename != '':
            filename = secure_filename(profile_pic.filename)
            filepath = os.path.join(app.root_path, UPLOAD_FOLDER, filename)
            profile_pic.save(filepath)

        db.execute("INSERT INTO users (username, password, profile_pic) VALUES (?, ?, ?)",
                   (username, password, filename))
        db.commit()
        return redirect('/login')
    return render_template('register.html')

@app.route('/chat')
def chat():
    if 'user_id' not in session:
        return redirect('/login')
    db = get_db()
    current_user = session['username']
    interacted_users = db.execute('''
        SELECT DISTINCT
            CASE WHEN sender = ? THEN receiver ELSE sender END AS user
        FROM messages
        WHERE sender = ? OR receiver = ?
    ''', (current_user, current_user, current_user)).fetchall()

    user_profiles = {}
    for u in interacted_users:
        profile = db.execute("SELECT profile_pic FROM users WHERE username=?", (u['user'],)).fetchone()
        user_profiles[u['user']] = profile['profile_pic'] if profile and profile['profile_pic'] else 'default.png'

    return render_template('chat.html', username=current_user,
                           interacted_users=interacted_users, user_profiles=user_profiles)

@app.route('/chat/<username>')
def private_chat(username):
    if 'user_id' not in session:
        return redirect('/login')
    if username == session['username']:
        return redirect('/chat')

    db = get_db()
    current_user = session['username']
    other_user = username

    messages = db.execute('''
        SELECT m.*, u1.profile_pic AS sender_pic
        FROM messages m
        JOIN users u1 ON m.sender = u1.username
        WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)
        ORDER BY timestamp ASC
    ''', (current_user, other_user, other_user, current_user)).fetchall()

    other_user_profile = db.execute("SELECT profile_pic FROM users WHERE username=?", (other_user,)).fetchone()

    return render_template('chat_with.html',
                           current_user=current_user,
                           other_user=other_user,
                           other_user_pic=other_user_profile['profile_pic'] if other_user_profile and other_user_profile['profile_pic'] else 'default.png',
                           messages=messages)

@app.route('/search', methods=['GET', 'POST'])
def search():
    users = []
    user_profiles = {}
    if request.method == 'POST':
        db = get_db()
        query = request.form['query']
        users = db.execute("SELECT username, profile_pic FROM users WHERE username LIKE ?", 
                           ('%' + query + '%',)).fetchall()
        for user in users:
            user_profiles[user['username']] = user['profile_pic'] if user['profile_pic'] else 'default.png'
    return render_template('search.html', users=users, user_profiles=user_profiles)

@app.route('/delete-message/<int:message_id>')
def delete_message(message_id):
    if 'user_id' not in session:
        return redirect('/login')
    db = get_db()
    db.execute("DELETE FROM messages WHERE id=?", (message_id,))
    db.commit()
    return redirect(request.referrer)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- SocketIO Events ---
@socketio.on('join_room')
def handle_join_room(data):
    join_room(data['room'])

@socketio.on('send_private_message')
def handle_private_message(data):
    db = get_db()
    db.execute("INSERT INTO messages (sender, receiver, message) VALUES (?, ?, ?)",
               (data['username'], data['receiver'], data['message']))
    db.commit()
    emit('receive_private_message', {
        'message': data['message'],
        'username': data['username']
    }, room=data['room'])

# --- Database Setup ---
if __name__ == '__main__':
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Create users table
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )''')

        # Add profile_pic column if missing
        c.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in c.fetchall()]
        if 'profile_pic' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN profile_pic TEXT")
            print("✅ Added 'profile_pic' column to users table")

        # Create messages table
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            receiver TEXT,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        conn.commit()

    socketio.run(app, debug=True)
