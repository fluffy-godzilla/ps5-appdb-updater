import os
import datetime
import sqlite3
from ftplib import FTP, error_perm
from urllib.parse import urlparse
from flask import Flask, render_template, redirect, url_for, flash, request

app = Flask(__name__)
app.secret_key = 'super_secret_flash_key'

HTML_TEMPLATE = 'main.html'

REMOTE_PATH = '/system_data/priv/mms'

APPDB_FILE = 'app.db'
INFOPDB_FILE = 'appinfo.db'

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


def backup_and_update_db(ps5_server, script_path, db_file):
    """Handles the backup and update process for a given database file."""
    if not script_path or not os.path.exists(script_path):
        flash(f"Error: {db_file} script file not found at '{script_path}'.", "error")
        return None

    # Read the SQL script content
    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            sql_script = f.read()
    except Exception as e:
        flash(f"Error reading SQL script file(s): {e}", "error")
        return None

    # Prepare local file path for temporary storage
    local_db = f"local_{db_file}"
    if os.path.exists(local_db):
        os.remove(local_db)

    ftp_url = f"ftp://{ps5_server}{REMOTE_PATH}/"
    ftp = None
    file_existed = False
    
    try:
        ftp, remote_path = get_ftp_connection(ftp_url)
        target_path = f"{remote_path}/{db_file}"
        
        print(f"Attempting to download from {ftp_url}...")
        try:
            with open(local_db, "wb") as file:
                ftp.retrbinary(f"RETR {target_path}", file.write)
            file_existed = True
        except error_perm as e:
            if "550" in str(e):
                file_existed = False
            else:
                raise e

        # Create a timestamped backup on the server if the file existed
        if file_existed:
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_file = f"{target_path}.{timestamp}.bak"
            
            print(f"Creating remote backup at: {backup_file}")
            try:
                ftp.voidcmd(f"SITE COPY {target_path} {backup_file}")
            except Exception as e:
                raise Exception(f"Failed to create remote backup file: {e}")

        # Apply SQL updates locally
        print("Applying SQL updates locally...")
        conn = sqlite3.connect(local_db)
        cursor = conn.cursor()
        cursor.executescript(sql_script)
        conn.commit()
        conn.close()

        # Ensure target directories exist on the FTP server
        print("Verifying target directories on FTP server...")
        ensure_remote_directories(ftp, remote_path)
        
        # Upload the updated file back to the target location
        print("Uploading updated file back to target...")
        ftp.cwd('/') 
        with open(local_db, "rb") as file:
            ftp.storbinary(f"STOR {target_path}", file)
        
        # Set correct permissions on the uploaded file
        ftp.sendcmd(f"SITE CHMOD 644 {target_path}")

    except Exception as e:
        raise Exception(f"Process failed: {e}")
    finally:
        if ftp:
            try: ftp.quit()
            except Exception: pass
        if os.path.exists(local_db):
            os.remove(local_db)

    return True

@app.route('/')
def home():
    appdb_script_path = os.environ.get("APPDB_SCRIPT_PATH", "")
    infodb_script_path = os.environ.get("INFODB_SCRIPT_PATH", "")
    ps5_server = os.environ.get("PS5_SERVER", "")
    appdb_script_content = ""
    infodb_script_content = ""
    backups = []
    
    if appdb_script_path and os.path.exists(appdb_script_path):
        try:
            with open(appdb_script_path, 'r', encoding='utf-8') as f:
                appdb_script_content = f.read()
        except Exception as e:
            print(f"Error reading script file for UI: {e}")
            appdb_script_content = f"-- Error loading script: {e}"
    else:
        appdb_script_content = "-- APPDB_SCRIPT_PATH variable not set or file missing."


    if infodb_script_path and os.path.exists(infodb_script_path):
        try:
            with open(infodb_script_path, 'r', encoding='utf-8') as f:
                infodb_script_content = f.read()
        except Exception as e:
            print(f"Error reading script file for UI: {e}")
            infodb_script_content = f"-- Error loading script: {e}"
    else:
        infodb_script_content = "-- INFODB_SCRIPT_PATH variable not set or file missing."


    if ps5_server:
        ftp_url = f"ftp://{ps5_server}{REMOTE_PATH}/"
        ftp = None
        try:
            ftp, remote_path = get_ftp_connection(ftp_url)
            remote_dir = os.path.dirname(remote_path)
            
            # Change directory to target folder and read file list
            ftp.cwd(remote_dir)
            files = ftp.nlst()
            
            # Filter file names matching 'app*.bak'
            backups = sorted([f for f in files if f.startswith("app") and f.endswith(".bak")], reverse=True)
        except Exception as e:
            print(f"Could not fetch backup file listings: {e}")
        finally:
            if ftp:
                try: ftp.quit()
                except Exception: pass
                
    return render_template(
        HTML_TEMPLATE,
        backups=backups,
        appdb_script_content=appdb_script_content,
        infodb_script_content=infodb_script_content,
        appdb_script_path=appdb_script_path if appdb_script_path else "Not Configured",
        infodb_script_path=infodb_script_path if infodb_script_path else "Not Configured"
    )

@app.route('/save_script', methods=['POST'])
def save_script():
    script_target = request.form.get("script_target", "")
    new_content = request.form.get("script_content", "")

    if script_target == "appdb":
        sql_script_path = os.environ.get("APPDB_SCRIPT_PATH", "")
    elif script_target == "infodb":
        sql_script_path = os.environ.get("INFODB_SCRIPT_PATH", "")
    
    if not sql_script_path:
        flash("Error: APPDB_SCRIPT_PATH or INFODB_SCRIPT_PATH environment variable is not set.", "error")
        return redirect(url_for('home'))

    try:
        with open(sql_script_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        flash(f"{sql_script_path} SQL script modifications saved successfully to disk!", "success")
    except Exception as e:
        flash(f"Failed to save script changes: {e}", "error")
        
    return redirect(url_for('home'))

@app.route('/update', methods=['POST'])
def update_db():
    ps5_server = os.environ.get("PS5_SERVER", "")
    appdb_script_path = os.environ.get("APPDB_SCRIPT_PATH", "")
    infodb_script_path = os.environ.get("INFODB_SCRIPT_PATH", "")

    if (backup_and_update_db(ps5_server, appdb_script_path, APPDB_FILE) and 
        backup_and_update_db(ps5_server, infodb_script_path, INFOPDB_FILE)):
        flash("Successfully updated both databases on the PS5 server.", "success")
            
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

    ftp_url = f"ftp://{ps5_server}{REMOTE_PATH}/"
    ftp = None
    
    try:
        ftp, remote_path = get_ftp_connection(ftp_url)
        remote_dir = os.path.dirname(remote_path)
        
        # Paths for renaming operations on the server side
        target_backup_path = os.path.join(remote_dir, backup_filename)
        
        # 1. Delete selected backup
        print(f"Deleting backup '{backup_filename}' at {target_backup_path}...")
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

    ftp_url = f"ftp://{ps5_server}{REMOTE_PATH}/"
    ftp = None
    
    try:
        ftp, remote_path = get_ftp_connection(ftp_url)
        remote_dir = os.path.dirname(remote_path)
        
        # Paths for renaming operations on the server side
        target = APPDB_FILE if backup_filename.startswith(APPDB_FILE) else INFOPDB_FILE
        target_live_path = os.path.join(remote_dir, target)
        target_backup_path = os.path.join(remote_dir, backup_filename)
        
        # 1. move the contents of the backup files to the active databases
        try:
            ftp.voidcmd(f"SITE COPY {target_backup_path} {target_live_path}")
            print(f"Restored DB from: {backup_filename}")
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
            
    return redirect(url_for('home'))


# main loop to run the Flask app
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

