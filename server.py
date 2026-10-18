import os
import datetime
import sqlite3
from ftplib import FTP, error_perm
from urllib.parse import urlparse
from flask import Flask, render_template, redirect, url_for, flash, request

app = Flask(__name__)
app.secret_key = 'super_secret_flash_key'

LOCAL_DB_FILE = "local_app.db"

HTML_TEMPLATE = 'main.html'

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
    sql_script_path = os.environ.get("SQL_SCRIPT_PATH", "")
    backups = []
    script_content = ""
    
    if sql_script_path and os.path.exists(sql_script_path):
        try:
            with open(sql_script_path, 'r', encoding='utf-8') as f:
                script_content = f.read()
        except Exception as e:
            print(f"Error reading script file for UI: {e}")
            script_content = f"-- Error loading script: {e}"
    else:
        script_content = "-- SQL_SCRIPT_PATH variable not set or file missing."
    
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
                
    return render_template(
        HTML_TEMPLATE,
        backups=backups,
        script_content=script_content, 
        script_path=sql_script_path if sql_script_path else "Not Configured"
    )

@app.route('/save_script', methods=['POST'])
def save_script():
    sql_script_path = os.environ.get("SQL_SCRIPT_PATH", "")
    new_content = request.form.get("script_content", "")
    
    if not sql_script_path:
        flash("Error: SQL_SCRIPT_PATH environment variable is not set.", "error")
        return redirect(url_for('home'))
        
    try:
        with open(sql_script_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        flash("SQL script modifications saved successfully to disk!", "success")
    except Exception as e:
        flash(f"Failed to save script changes: {e}", "error")
        
    return redirect(url_for('home'))

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
                # we don't rename but copy instead to keep the original in place, otherwise the PS5 software
                # causes the database rebuild
                ftp.voidcmd(f"SITE COPY {remote_path} {backup_path}")
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
            # replace the contents of the existing remote file
            ftp.storbinary(f"STOR {remote_path}", local_file)
        ftp.cwd(os.path.dirname(remote_path))
        # this is not necessary for the PS5, but it is a good practice to set the correct permissions on the uploaded file
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

@app.route('/delete', methods=['POST'])
def delete_backup():
    ps5_server = os.environ.get("PS5_SERVER", "")
    backup_filename = request.form.get("backup_filename")
    
    if not ps5_server:
        flash("Error: PS5_SERVER environment variable is not set.", "error")
        return redirect(url_for('home'))
        
    if not backup_filename:
        flash("Error: No backup target specified to delete.", "error")
        return redirect(url_for('home'))

    ftp_url = f"ftp://{ps5_server}/system_data/priv/mms/app.db"
    ftp = None
    
    try:
        ftp, remote_path = get_ftp_connection(ftp_url)
        remote_dir = os.path.dirname(remote_path)
        
        # Paths for renaming operations on the server side
        target_backup_path = os.path.join(remote_dir, backup_filename)
        
        # 1. Delete selected backup
        print(f"Deleting backup '{backup_filename}'...")
        ftp.delete(target_backup_path)
        
        flash(f"Successfully deleted database backup: {backup_filename}", "success")
        
    except Exception as e:
        flash(f"Delete operation failed: {e}", "error")

    finally:
        if ftp:
            try:
                ftp.quit()
            except Exception:
                pass
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
        
        # 1. move the contents of the backup files to the active databases
        try:
            ftp.voidcmd(f"SITE COPY {target_backup_path} {target_live_path}")
            print(f"Restored app.db from: {backup_filename}")
        except error_perm:
            # Active database didn't exist or wasn't accessible, safe to continue
            pass
            
        # 2. Delete selected backup
        print(f"Deleting backup '{backup_filename}'...")
        ftp.delete(target_backup_path)
        
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


# main loop to run the Flask app
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

