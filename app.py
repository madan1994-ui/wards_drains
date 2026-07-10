import os
import io
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, session, flash, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import openpyxl
import cloudinary
import cloudinary.uploader

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'tanuku-drains-2026-secret')

DATABASE_URL = os.environ.get('DATABASE_URL')

cloudinary.config(
    cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key = os.environ.get('CLOUDINARY_API_KEY'),
    api_secret = os.environ.get('CLOUDINARY_API_SECRET')
)

def get_db_connection():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not set")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) DEFAULT 'user',
                ward VARCHAR(10)
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS drains (
                id SERIAL PRIMARY KEY,
                drain_id VARCHAR(50),
                ward VARCHAR(10) NOT NULL,
                location TEXT,
                status VARCHAR(50) DEFAULT 'Pending',
                work_type VARCHAR(100),
                work_date DATE,
                updated_by VARCHAR(50),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(drain_id, ward)
            )
        ''')

        # New table for multiple photos per drain
        cur.execute('''
            CREATE TABLE IF NOT EXISTS drain_photos (
                id SERIAL PRIMARY KEY,
                drain_id INTEGER REFERENCES drains(id) ON DELETE CASCADE,
                photo_url TEXT NOT NULL,
                work_type VARCHAR(100),
                status VARCHAR(50),
                uploaded_by VARCHAR(50),
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                remarks TEXT
            )
        ''')

        cur.execute("""
            INSERT INTO users (username, password_hash, role, ward)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (username) DO NOTHING
        """, ('admin', generate_password_hash('Tanuku@2026'), 'admin', None))

        ward_users = [
            ('sanitation11', 'Sanitation11@2026', '11'),
            ('sanitation12', 'Sanitation12@2026', '12'),
            ('sanitation29', 'Sanitation29@2026', '29')
        ]

        for username, password, ward in ward_users:
            cur.execute("""
                INSERT INTO users (username, password_hash, role, ward)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (username) DO NOTHING
            """, (username, generate_password_hash(password), 'user', ward))

        conn.commit()
        cur.close()
        conn.close()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database init error: {e}")

try:
    init_db()
except Exception as e:
    print(f"Database init error: {e}")

@app.route('/')
def index():
    if 'username' in session:
        return redirect('/dashboard')
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user['password_hash'], password):
            session['username'] = user['username']
            session['role'] = user['role']
            session['ward'] = user['ward']
            return redirect('/dashboard')
        else:
            return render_template('login.html', error='Invalid username or password')

    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if session.get('role') == 'admin':
        cur.execute("""
            SELECT d.*,
                   (SELECT photo_url FROM drain_photos WHERE drain_id = d.id ORDER BY uploaded_at DESC LIMIT 1) as latest_photo,
                   (SELECT COUNT(*) FROM drain_photos WHERE drain_id = d.id) as photo_count
            FROM drains d
            ORDER BY ward, drain_id
        """)
    else:
        cur.execute("""
            SELECT d.*,
                   (SELECT photo_url FROM drain_photos WHERE drain_id = d.id ORDER BY uploaded_at DESC LIMIT 1) as latest_photo,
                   (SELECT COUNT(*) FROM drain_photos WHERE drain_id = d.id) as photo_count
            FROM drains d
            WHERE ward = %s
            ORDER BY drain_id
        """, (session.get('ward'),))

    drains = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('dashboard.html', drains=drains)

@app.route('/upload_work/<int:drain_id>', methods=['GET', 'POST'])
def upload_work(drain_id):
    if 'username' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT * FROM drains WHERE id = %s", (drain_id,))
    drain = cur.fetchone()

    if not drain:
        flash('Drain not found')
        cur.close()
        conn.close()
        return redirect('/dashboard')

    if session.get('role')!= 'admin' and drain['ward']!= session.get('ward'):
        flash('You do not have access to this drain')
        cur.close()
        conn.close()
        return redirect('/dashboard')

    # Get photo history for this drain
    cur.execute("SELECT * FROM drain_photos WHERE drain_id = %s ORDER BY uploaded_at DESC", (drain_id,))
    photo_history = cur.fetchall()

    if request.method == 'POST':
        work_type = request.form.get('work_type')
        status = request.form.get('status')
        remarks = request.form.get('remarks', '')
        photo_url = None

        if 'photo' in request.files:
            photo = request.files['photo']
            if photo.filename!= '':
                try:
                    upload_result = cloudinary.uploader.upload(photo, folder="tanuku_drains")
                    photo_url = upload_result['secure_url']
                except Exception as e:
                    flash(f'Photo upload failed: {str(e)}')
                    cur.close()
                    conn.close()
                    return redirect(url_for('upload_work', drain_id=drain_id))

        # Update drains table
        cur.execute("""
            UPDATE drains
            SET status = %s, work_type = %s, work_date = CURRENT_DATE,
                updated_by = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (status, work_type, session['username'], drain_id))

        # Insert photo record if uploaded
        if photo_url:
            cur.execute("""
                INSERT INTO drain_photos (drain_id, photo_url, work_type, status, uploaded_by, remarks)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (drain_id, photo_url, work_type, status, session['username'], remarks))

        conn.commit()
        cur.close()
        conn.close()
        flash('Work updated successfully')
        return redirect('/dashboard')

    cur.close()
    conn.close()
    return render_template('upload_work.html', drain=drain, photo_history=photo_history)

@app.route('/import_excel', methods=['GET', 'POST'])
def import_excel():
    if 'username' not in session:
        return redirect('/login')

    # Only admin can upload drain master data now
    if session.get('role')!= 'admin':
        flash('Only admin can import drain data')
        return redirect('/dashboard')

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected')
            return redirect('/import_excel')

        file = request.files['file']

        if file.filename == '':
            flash('No file selected')
            return redirect('/import_excel')

        if file and file.filename.endswith('.xlsx'):
            try:
                wb = openpyxl.load_workbook(file)
                sheet = wb.active

                conn = get_db_connection()
                cur = conn.cursor()

                count = 0
                skipped = 0
                for row in sheet.iter_rows(min_row=2, values_only=True):
                    drain_id = str(row[2]) if row[2] else ''
                    ward = str(row[1]) if row[1] else ''
                    location = str(row[3]) if row[3] else ''

                    if not drain_id or not ward:
                        skipped += 1
                        continue

                    cur.execute("""
                        INSERT INTO drains (drain_id, ward, location, status, updated_by)
                        VALUES (%s, %s, %s, 'Pending', %s)
                        ON CONFLICT (drain_id, ward)
                        DO UPDATE SET location = EXCLUDED.location, updated_by = EXCLUDED.updated_by, updated_at = CURRENT_TIMESTAMP
                    """, (drain_id, ward, location, session['username']))
                    count += 1

                # Auto-create ward users if not exists
                cur.execute("SELECT DISTINCT ward FROM drains")
                all_wards = [row[0] for row in cur.fetchall()]
                for ward in all_wards:
                    if ward:
                        username = f'sanitation{ward}'
                        password = f'Sanitation{ward}@2026'
                        cur.execute("""
                            INSERT INTO users (username, password_hash, role, ward)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (username) DO NOTHING
                        """, (username, generate_password_hash(password), 'user', ward))

                conn.commit()
                cur.close()
                conn.close()

                flash(f'Successfully imported {count} drains. Skipped {skipped} rows. Data reflected in ward logins.')
                return redirect('/dashboard')

            except Exception as e:
                flash(f'Error importing Excel: {str(e)}')
                return redirect('/import_excel')
        else:
            flash('Please upload a.xlsx file')
            return redirect('/import_excel')

    return render_template('import_excel.html')

@app.route('/photo_report')
def photo_report():
    if 'username' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if session.get('role') == 'admin':
        cur.execute("""
            SELECT dp.*, d.drain_id, d.ward, d.location
            FROM drain_photos dp
            JOIN drains d ON dp.drain_id = d.id
            ORDER BY d.ward, dp.uploaded_at DESC
        """)
    else:
        cur.execute("""
            SELECT dp.*, d.drain_id, d.ward, d.location
            FROM drain_photos dp
            JOIN drains d ON dp.drain_id = d.id
            WHERE d.ward = %s
            ORDER BY dp.uploaded_at DESC
        """, (session.get('ward'),))

    photos = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('photo_report.html', photos=photos)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/fix_database_constraint')
def fix_database_constraint():
    if session.get('role')!= 'admin':
        return "Login as admin first"
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("ALTER TABLE drains ADD CONSTRAINT drains_drain_id_ward_key UNIQUE (drain_id, ward);")
        conn.commit()
        cur.close()
        conn.close()
        return "✅ SUCCESS: UNIQUE constraint added. Ward import will now work. DELETE this /fix_database_constraint route from app.py NOW and redeploy."
    except Exception as e:
        if "already exists" in str(e):
            return "✅ Already fixed: Constraint already exists. Ward import should work now. DELETE this route from app.py."
        return f"Error: {str(e)}"

if __name__ == '__main__':
    app.run(debug=False)
