# app.py
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
import subprocess
import sys
import csv
import re
from datetime import datetime
import threading
import os
import logging
from queue import Queue

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///domains.db'
app.config['UPLOAD_FOLDER'] = 'uploads'

db = SQLAlchemy(app)
venv_python = sys.executable
socketio = SocketIO(app, async_mode='threading')
logging.basicConfig(level=logging.INFO)

# Очередь задач
task_queue = Queue()

class Domain(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), unique=True, nullable=False)
    type = db.Column(db.String(32), unique=False)
    issuer = db.Column(db.String(255), unique=False)
    expiry_date = db.Column(db.DateTime)
    paid_till_date = db.Column(db.DateTime)
    last_checked = db.Column(db.DateTime)
    nservers = db.Column(db.String(255), unique=False)
    status = db.Column(db.String(32))

@socketio.on('connect')
def handle_connect():
    print('Клиент подключился')

def worker():
    while True:
        processing_domain = task_queue.get()
        try:
            cert_checker_proc = subprocess.run(
                [venv_python, 'cert_checker.py', processing_domain],
                capture_output=True, text=True, timeout=30
            )
            domain_checker_proc = subprocess.run(
                [venv_python, 'domain_checker.py', processing_domain],
                capture_output=True, text=True, timeout=30
            )

            if cert_checker_proc.returncode == 0:
                result = cert_checker_proc.stdout.strip().split(";")
                expiry_date = datetime.fromisoformat(result[1]) if result[1] else None
                issuer = re.search(r"O=([^,]+)", result[2]).group(1) if result[2] else None
                with app.app_context():
                    entry = Domain.query.filter_by(domain=processing_domain).first()
                    match result[0]:
                        case "HTTPS":
                            entry.domain=processing_domain
                            entry.expiry_date=expiry_date
                            entry.issuer=issuer
                            entry.type="HTTPS"
                            entry.last_checked=datetime.now()
                            entry.status='Active'
                        case "HTTP ONLY":
                            entry.domain=processing_domain,
                            entry.expiry_date=0
                            entry.type="HTTP ONLY"
                            entry.last_checked=datetime.now()
                            entry.status='Active'
                        case "Refused":
                            entry.domain=processing_domain,
                            entry.expiry_date=0
                            entry.type="Refused"
                            entry.last_checked=datetime.now()
                            entry.status='Offline'
                    db.session.commit()
                    socketio.emit('update', {
                        'domain': processing_domain,
                        'type': result[0],
                        'expiry_date': expiry_date.strftime('%Y-%m-%d %H:%M:%S') if expiry_date else 'N/A',
                        'last_checked': datetime.now().strftime('%d-%m-%Y %H:%M:%S')
                    })

            if domain_checker_proc.returncode == 0:
                result = domain_checker_proc.stdout.strip().split(";")
                paid_till_date=datetime.fromisoformat(result[1]) if result[1] else None
                with app.app_context():
                    entry = Domain.query.filter_by(domain=processing_domain).first()
                    match result[0]:
                        case "Accepted":
                            entry.domain=processing_domain
                            entry.paid_till_date=paid_till_date
                            entry.nservers=result[2]
                        case "Refused":
                            entry.domain=processing_domain
                            entry.paid_till_date=paid_till_date
                            entry.nservers="N/A"
                    db.session.commit()
                    socketio.emit('update', {
                        'domain': processing_domain,
                        'paid_till_date': paid_till_date.strftime('%Y-%m-%d %H:%M:%S') if paid_till_date else 'N/A'
                    })

        except Exception as e:
            logging.error(f"Error processing {processing_domain}: {str(e)}")
        finally:
            task_queue.task_done()

# Запускаем рабочий поток
threading.Thread(target=worker, daemon=True).start()

@app.route('/update/<domain>', methods=['GET'])
def update_single(domain):
    # Добавляем домен в очередь задач
    task_queue.put(domain)
    return '', 202  # Возвращаем пустой ответ с кодом 202 (Accepted)

@app.route('/', methods=['GET', 'POST'])
def index():
    query = request.args.get('query', '')
    filter_type = request.args.get('filter', 'all')
    sort_by = request.args.get('sort_by', 'domain')  # По умолчанию сортировка по домену
    sort_order = request.args.get('sort_order', 'asc')  # По умолчанию по возрастанию

    certs = Domain.query

    # Сортировка
    if sort_by == 'expire_date':
        if sort_order == 'asc':
            certs = certs.order_by(Domain.expiry_date.asc())
        else:
            certs = certs.order_by(Domain.expiry_date.desc())
    elif sort_by == 'last_checked':
        if sort_order == 'asc':
            certs = certs.order_by(Domain.last_checked.asc())
        else:
            certs = certs.order_by(Domain.last_checked.desc())
    else:
        # Сортировка по домену по умолчанию
        if sort_order == 'asc':
            certs = certs.order_by(Domain.domain.asc())
        else:
            certs = certs.order_by(Domain.domain.desc())

    if query:
        certs = certs.filter(Domain.domain.contains(query))
        
    if filter_type == 'active':
        certs = certs.filter(Domain.status == 'Active')
    elif filter_type == 'error':
        certs = certs.filter(Domain.status == 'Error')
    elif filter_type == 'pending':
        certs = certs.filter(Domain.status == 'Pending')
    elif filter_type == 'never':
        certs = certs.filter(Domain.last_checked == None)
    
    certs = certs.order_by(Domain.domain.asc()).all()

    if request.method == 'POST' and 'file' in request.files:
        file = request.files['file']
        if file.filename == '':
            return "No file selected", 400
        if file:
            try:
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
                file.save(filepath)
                logging.info(f"File saved to: {filepath}")

                process_uploaded_file(filepath)
                return redirect(url_for('index'))
            except Exception as e:
                logging.error(f"Error processing file: {str(e)}")
                return f"Error processing file: {str(e)}", 500
    
    return render_template(
        'index.html',
        certs=certs,
        query=query,
        current_filter=filter_type,
        sort_by=sort_by,
        sort_order=sort_order,
        now=datetime.now()
    )

def upload_domain(domain):
    db.session.add(Domain(
        domain=domain,
        status='Pending'
    ))
    db.session.commit()

def process_uploaded_file(filepath):
    try:
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith(';;'):
                    parts = line.split()
                    if parts[3] == 'A' or parts[3] == 'CNAME':
                        domain = parts[0].rstrip('.')
                        if not Domain.query.filter_by(domain=domain).first():
                            upload_domain(domain)
        logging.info("File processed successfully")
    except Exception as e:
        logging.error(f"Error processing file: {str(e)}")
        raise
    # finally:
    #     if os.path.exists(filepath):
    #         os.remove(filepath)
    #         logging.info(f"Temporary file removed: {filepath}")

@app.route('/update_all', methods=['POST'])
def update_all():
    domains = [cert.domain for cert in Domain.query.all()]
    for domain in domains:
        task_queue.put(domain)
    return '', 202

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    socketio.run(app, debug=True)