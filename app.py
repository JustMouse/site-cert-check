# app.py
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
import subprocess
import sys
import csv
from datetime import datetime
import threading
import os
import logging
from queue import Queue

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///certs.db'
app.config['UPLOAD_FOLDER'] = 'uploads'

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='threading')
logging.basicConfig(level=logging.INFO)

# Очередь задач
task_queue = Queue()

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), unique=True, nullable=False)
    expiry_date = db.Column(db.DateTime)
    last_checked = db.Column(db.DateTime)
    status = db.Column(db.String(50))

def worker():
    while True:
        domain = task_queue.get()
        try:
            venv_python = sys.executable
            result = subprocess.run(
                [venv_python, 'cert_checker.py', domain, 'temp.csv'],
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode == 0:
                with open('temp.csv') as f:
                    reader = csv.reader(f)
                    next(reader)
                    row = next(reader)
                    expiry_date = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S') if row[1] != 'Error' else None
                    
                    with app.app_context():
                        cert = Certificate.query.filter_by(domain=domain).first()
                        if cert:
                            cert.expiry_date = expiry_date
                            cert.last_checked = datetime.now()
                            cert.status = 'Valid' if expiry_date else 'Error'
                        else:
                            cert = Certificate(
                                domain=domain,
                                expiry_date=expiry_date,
                                last_checked=datetime.now(),
                                status='Valid' if expiry_date else 'Error'
                            )
                            db.session.add(cert)
                        db.session.commit()
                        socketio.emit('update', {
                            'domain': domain,
                            'status': 'valid' if expiry_date else 'error',
                            'expiry_date': expiry_date.strftime('%Y-%m-%d %H:%M:%S') if expiry_date else 'N/A',
                            'last_checked': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
        except Exception as e:
            logging.error(f"Error processing {domain}: {str(e)}")
        finally:
            task_queue.task_done()

# Запускаем рабочий поток
threading.Thread(target=worker, daemon=True).start()

@app.route('/', methods=['GET', 'POST'])
def index():
    query = request.args.get('query', '')
    filter_type = request.args.get('filter', 'all')
    
    certs = Certificate.query
    
    if query:
        certs = certs.filter(Certificate.domain.contains(query))
        
    if filter_type == 'valid':
        certs = certs.filter(Certificate.status == 'Valid')
    elif filter_type == 'error':
        certs = certs.filter(Certificate.status == 'Error')
    elif filter_type == 'pending':
        certs = certs.filter(Certificate.status == 'Pending')
    elif filter_type == 'never':
        certs = certs.filter(Certificate.last_checked == None)
    
    certs = certs.order_by(Certificate.domain.asc()).all()
    
    return render_template(
        'index.html',
        certs=certs,
        query=query,
        current_filter=filter_type
    )

@app.route('/update/<domain>')
def update_single(domain):
    task_queue.put(domain)
    return '', 202

def process_uploaded_file(file):
    try:
        filepath = f"{app.config['UPLOAD_FOLDER']}/{file.filename}"
        file.save(filepath)
        
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith(';;'):
                    domain = line.split()[0].rstrip('.')
                    if not Certificate.query.filter_by(domain=domain).first():
                        db.session.add(Certificate(
                            domain=domain,
                            status='Pending'
                        ))
        db.session.commit()
    except Exception as e:
        logging.error(f"File processing error: {str(e)}")

@app.route('/update_all', methods=['POST'])
def update_all():
    domains = [cert.domain for cert in Certificate.query.all()]
    for domain in domains:
        task_queue.put(domain)
    return '', 202

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    socketio.run(app, debug=True)