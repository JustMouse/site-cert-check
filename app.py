# app.py (обновлённая версия)
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import subprocess
from datetime import datetime
import csv
import sys
import os
import logging

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///certs.db'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
venv_python = sys.executable

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), unique=True, nullable=False)
    expiry_date = db.Column(db.DateTime)
    last_checked = db.Column(db.DateTime)
    status = db.Column(db.String(50))

with app.app_context():
    db.create_all()
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def update_certificate(domain):
    try:
        # Запускаем проверку с таймаутом
        result = subprocess.run(
            [venv_python, 'cert_checker.py', domain, 'temp.csv'],
            capture_output=True, text=True, timeout=30
        )
        
        # Обрабатываем возможные ошибки
        if result.returncode != 0:
            logger.error(f"Error checking {domain}: {result.stderr}")
            raise subprocess.CalledProcessError(result.returncode, result.args)
            
        # Читаем результаты
        with open('temp.csv') as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                expiry_date = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S') if row[1] != 'Error' else None
                cert = Certificate.query.filter_by(domain=domain).first()
                new_status = 'Valid' if expiry_date else 'Error'
                
                if cert:
                    cert.expiry_date = expiry_date
                    cert.last_checked = datetime.now()
                    cert.status = new_status
                else:
                    cert = Certificate(
                        domain=domain,
                        expiry_date=expiry_date,
                        last_checked=datetime.now(),
                        status=new_status
                    )
                    db.session.add(cert)
                db.session.commit()
                
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout checking {domain}")
        update_failed_status(domain, 'Timeout')
    except FileNotFoundError:
        logger.error(f"Result file not found for {domain}")
        update_failed_status(domain, 'File Error')
    except Exception as e:
        logger.error(f"Error updating {domain}: {str(e)}")
        update_failed_status(domain, 'Error')
    finally:
        if os.path.exists('temp.csv'):
            os.remove('temp.csv')

def update_failed_status(domain, status):
    cert = Certificate.query.filter_by(domain=domain).first()
    if cert:
        cert.last_checked = datetime.now()
        cert.status = status
        db.session.commit()

@app.route('/', methods=['GET', 'POST'])
def index():
    # Параметры фильтрации и сортировки
    query = request.args.get('query', '')
    date_filter = request.args.get('date_filter', '')
    hide_never = request.args.get('hide_never', False)
    sort_order = request.args.get('sort', 'asc')
    
    # Построение запроса
    certs_query = Certificate.query
    
    # Фильтр по поиску
    if query:
        certs_query = certs_query.filter(Certificate.domain.contains(query))
        
    # Фильтр по дате
    if date_filter:
        filter_date = datetime.strptime(date_filter, '%Y-%m-%d')
        certs_query = certs_query.filter(db.func.date(Certificate.expiry_date) == filter_date.date())
        
    # Фильтр "Never checked"
    if hide_never:
        certs_query = certs_query.filter(Certificate.last_checked.isnot(None))
        
    # Сортировка
    if sort_order == 'asc':
        certs_query = certs_query.order_by(Certificate.domain.asc())
    else:
        certs_query = certs_query.order_by(Certificate.domain.desc())
    
    certs = certs_query.all()
    
    # Обработка загрузки файла
    if request.method == 'POST' and 'file' in request.files:
        file = request.files['file']
        if file.filename != '':
            process_uploaded_file(file)
            return redirect(url_for('index'))
    
    return render_template(
        'index.html',
        certs=certs,
        query=query,
        date_filter=date_filter,
        hide_never=hide_never,
        sort_order=sort_order
    )

def process_uploaded_file(file):
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
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
        logger.error(f"Error processing file: {str(e)}")
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

@app.route('/update', methods=['POST'])
def update_all():
    certs = Certificate.query.all()
    for cert in certs:
        update_certificate(cert.domain)
    return redirect(url_for('index'))

@app.route('/update/<domain>')
def update_single(domain):
    update_certificate(domain)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)