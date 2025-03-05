# app.py
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import subprocess
from datetime import datetime
import csv
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///certs.db'
app.config['UPLOAD_FOLDER'] = 'uploads'
db = SQLAlchemy(app)

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), unique=True, nullable=False)
    expiry_date = db.Column(db.DateTime)
    last_checked = db.Column(db.DateTime)
    status = db.Column(db.String(50))

# Создаем базу данных и папку для загрузок
with app.app_context():
    db.create_all()
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def update_certificate(domain):
    try:
        result = subprocess.run(
            ['python', 'cert_checker.py', domain, 'temp.csv'],
            capture_output=True, text=True, timeout=15
        )
        with open('temp.csv') as f:
            reader = csv.reader(f)
            next(reader)  # Пропускаем заголовок
            for row in reader:
                expiry_date = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S') if row[1] != 'Error' else None
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
        os.remove('temp.csv')
    except Exception as e:
        print(f"Error updating {domain}: {str(e)}")

@app.route('/', methods=['GET', 'POST'])
def index():
    # Поиск
    query = request.args.get('query', '')
    date_filter = request.args.get('date_filter', '')
    
    certs = Certificate.query
    if query:
        certs = certs.filter(Certificate.domain.contains(query))
    if date_filter:
        filter_date = datetime.strptime(date_filter, '%Y-%m-%d')
        certs = certs.filter(db.func.date(Certificate.expiry_date) == filter_date.date())
    
    certs = certs.order_by(Certificate.domain.asc()).all()
    
    # Загрузка файла
    if request.method == 'POST' and 'file' in request.files:
        file = request.files['file']
        if file.filename != '':
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filepath)
            
            # Парсинг файла и добавление доменов
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
            os.remove(filepath)
            
            return redirect(url_for('index'))
    
    return render_template('index.html', certs=certs, query=query, date_filter=date_filter)

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