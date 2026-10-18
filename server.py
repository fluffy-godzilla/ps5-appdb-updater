import os
import datetime
import sqlite3
from ftplib import FTP, error_perm
from urllib.parse import urlparse
from flask import Flask, render_template_string, redirect, url_for, flash, request

app = Flask(__name__)
app.secret_key = 'super_secret_flash_key'

LOCAL_DB_FILE = "local_app.db"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SQLite Remote Updater & Restorer</title>
    <style>
        body { font-family: Arial, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background-color: #f4f6f9; padding: 20px; box-sizing: border-box; }
        .card { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); text-align: center; width: 100%; max-width: 500px; margin-bottom: 20px; }
        .btn { color: white; border: none; padding: 12px 24px; font-size: 16px; border-radius: 4px; cursor: pointer; transition: background 0.2s; width: 100%; box-sizing: border-box; }
        .btn-update { background-color: #28a745; }
        .btn-update:hover { background-color: #218838; }
        .btn-revert { background-color: #dc3545; padding: 6px 12px; font-size: 14px; width: auto; }
        .btn-revert:hover { background-color: #c82333; }
        .alert { padding: 10px; margin-bottom: 20px; border-radius: 4px; text-align: left; word-wrap: break-word; }
        .success { color: #155724; background-color: #d4edda; border: 1px solid #c3e6cb; }
        .error { color: #721c24; background-color: #f8d7da; border: 1px solid #f5c6cb; }
        .backup-list { text-align: left; margin-top: 20px; border-top: 1px solid #eee; padding-top: 20px; }
        .backup-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px dashed #eee; font-family: monospace; font-size: 13px; }
        h2, h3 { color: #333; margin-top: 0; }
    </style>
</head>
<body>
    <div class="card">
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="alert {{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}
        
        <h2>Database Actions</h2>
        <form action="/update" method="POST">
            <button type="submit" class="btn btn-update">Backup, Update & Upload</button>
        </form>

        <div class="backup-list">
            <h3>Available Remote Backups</h3>
            {% if backups %}
                {% for backup in backups %}
                    <div class="backup-item">
                        <span>{{ backup }}</span>
                        <form action="/revert" method="POST" style="margin: 0;">
                            <input type="hidden" name="backup_filename" value="{{ backup }}">
                            <button type="submit" class="btn btn-revert">Revert</button>
                        </form>
                    </div>
                {% endfor %}
            {% else %}
                <p style="color: #777; font-size: 14px; font-style: italic;">No remote backups found in directory.</p>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

def get_ftp_connection(ftp_url):
    """Parses FTP URL and returns an authenticated FTP instance."""
    parsed = urlparse(ftp_url)
    hostname = parsed.hostname
    port = parsed.port if parsed.port else 21
    username = parsed.username if parsed.username else "anonymous"
    password = parsed.password if parsed.password else "anonymous"
    
    ftp = FTP()
    ftp.connect(hostname, port)
    ftp.login(username, password)
    return ftp, parsed.path

def ensure_remote_directories(ftp, remote_path):
    """Iterates through a path and recursively creates folders if they do not exist."""
    directory_path = os.path.dirname(remote_path)
    parts = [p for p in directory_path.split("/") if p]
    
    ftp.cwd('/')
    for part in parts:
        try:
            ftp.cwd(part)
        except error_perm:
            print(f"Directory '{part}' not found. Attempting to create it...")
            try:
                ftp.mkd(part)
                ftp.cwd(part)
            except Exception as e:
                raise Exception(f"Failed to create remote directory '{part}': {e}")

@app.route('/')
def home():
    ps5_server = os.environ.get("PS5_SERVER", "")
    backups = []
    
    if ps5_server:
        ftp_url = f"ftp://{ps5_server}/system_data/priv/mms/app.db"
        ftp = None
        try:
            ftp, remote_path = get_ftp_connection(ftp_url)
            remote_dir = os.path.dirname(remote_path)
            
            # Change directory to target folder and read file list
            ftp.cwd(remote_dir)
            files = ftp.nlst()
            
            # Filter file names matching 'app.db.*.bak'
            backups = sorted([f for f in files if f.startswith("app.db.") and f.endswith(".bak")], reverse=True)
        except Exception as e:
            print(f"Could not fetch backup file listings: {e}")
        finally:
            if ftp:
                try: ftp.quit()
                except Exception: pass
                
    return render_template_string(HTML_TEMPLATE, backups=backups)

@app.route('/update', methods=['POST'])
def update_db():
    ps5_server = os.environ.get("PS5_SERVER", "")
    sql_script_path = os.environ.get("SQL_SCRIPT_PATH", "")
    
    if not ps5_server:
        flash("Error: PS5_SERVER environment variable is not set.", "error")
        return redirect(url_for('home'))
        
    if not sql_script_path or not os.path.exists(sql_script_path):
        flash(f"Error: SQL script file not found at '{sql_script_path}'.", "error")
        return redirect(url_for('home'))
        
    try:
        with open(sql_script_path, 'r', encoding='utf-8') as f:
            sql_script = f.read()
    except Exception as e:
        flash(f"Error reading SQL script file: {e}", "error")
        return redirect(url_for('home'))

    ftp_url = f"ftp://{ps5_server}/system_data/priv/mms/app.db"
    ftp = None
    file_existed = False
    
    if os.path.exists(LOCAL_DB_FILE):
        os.remove(LOCAL_DB_FILE)
        
    try:
        ftp, remote_path = get_ftp_connection(ftp_url)
        
        print(f"Attempting to download from {ftp_url}...")
        try:
            with open(LOCAL_DB_FILE, "wb") as local_file:
                ftp.retrbinary(f"RETR {remote_path}", local_file.write)
            file_existed = True
        except error_perm as e:
            if "550" in str(e):
                file_existed = False
            else:
                raise e
            
        if file_existed:
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = f"{remote_path}.{timestamp}.bak"
            print(f"Creating remote backup at: {backup_path}")
            try:
                ftp.rename(remote_path, backup_path)
            except Exception as e:
                raise Exception(f"Failed to create remote backup file: {e}")
            
        print("Applying SQL updates locally...")
        conn = sqlite3.connect(LOCAL_DB_FILE)
        cursor = conn.cursor()
        cursor.executescript(sql_script)
        conn.commit()
        conn.close()
        
        print("Verifying target directories on FTP server...")
        ensure_remote_directories(ftp, remote_path)
        
        print("Uploading updated file back to target...")
        ftp.cwd('/') 
        with open(LOCAL_DB_FILE, "rb") as local_file:
            ftp.storbinary(f"STOR {remote_path}", local_file)
        ftp.cwd(os.path.dirname(remote_path))
        ftp.sendcmd(f"SITE CHMOD 644 {os.path.basename(remote_path)}")

        if file_existed:
            flash("Database successfully backed up, updated, and uploaded!", "success")
        else:
            flash("Remote target initialized. Created folder tree and saved fresh database!", "success")
        
    except Exception as e:
        flash(f"Process failed: {e}", "error")
    finally:
        if ftp:
            try: ftp.quit()
            except Exception: pass
        if os.path.exists(LOCAL_DB_FILE):
            os.remove(LOCAL_DB_FILE)
            
    return redirect(url_for('home'))

@app.route('/revert', methods=['POST'])
def revert_db():
    ps5_server = os.environ.get("PS5_SERVER", "")
    backup_filename = request.form.get("backup_filename")
    
    if not ps5_server:
        flash("Error: PS5_SERVER environment variable is not set.", "error")
        return redirect(url_for('home'))
        
    if not backup_filename:
        flash("Error: No backup target specified to revert.", "error")
        return redirect(url_for('home'))

    ftp_url = f"ftp://{ps5_server}/system_data/priv/mms/app.db"
    ftp = None
    
    try:
        ftp, remote_path = get_ftp_connection(ftp_url)
        remote_dir = os.path.dirname(remote_path)
        
        # Paths for renaming operations on the server side
        target_live_path = remote_path
        target_backup_path = os.path.join(remote_dir, backup_filename)
        
        # 1. Clear or rename the current active app.db out of the way if it exists
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            replaced_path = f"{target_live_path}.replaced-{timestamp}.bak"
            ftp.rename(target_live_path, replaced_path)
            print(f"Moved active app.db out of the way to: {replaced_path}")
        except error_perm:
            # Active database didn't exist or wasn't accessible, safe to continue
            pass
            
        # 2. Rename selected backup back to app.db
        print(f"Renaming backup '{backup_filename}' to active database...")
        ftp.rename(target_backup_path, target_live_path)
        
        flash(f"Successfully reverted database back to state from: {backup_filename}", "success")
        
    except Exception as e:
        flash(f"Revert operation failed: {e}", "error")



    finally:
        if ftp:
            try:
                ftp.quit()
            except Exception:
                pass
        if os.path.exists(LOCAL_DB_FILE):
            os.remove(LOCAL_DB_FILE)
            
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

