
from flask import Flask, render_template, request, redirect, url_for, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from pyreportjasper import PyReportJasper
from datetime import datetime
from queue import Queue
import json
import uuid
import sys
import subprocess
import re
import threading
import os
import logging
import tempfile
import xml.etree.ElementTree as ET

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///domains.db'
app.config['UPLOAD_FOLDER'] = 'uploads'

db = SQLAlchemy(app)
venv_python = sys.executable
socketio = SocketIO(app, async_mode='threading')
logging.basicConfig(level=logging.INFO)


task_queue = Queue()


class Domain(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), unique=True, nullable=False)
    paid_till_date = db.Column(db.DateTime)
    nservers = db.Column(db.String(255))
    status = db.Column(db.String(32))
    last_certificate_id = db.Column(db.Integer, db.ForeignKey('certificate.id'))
    
    
    last_certificate = db.relationship(
        'Certificate', 
        foreign_keys=[last_certificate_id],
        post_update=True
    )


class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domain_id = db.Column(db.Integer, db.ForeignKey('domain.id'), nullable=False)
    type = db.Column(db.String(32))
    issuer = db.Column(db.String(255))
    expiry_date = db.Column(db.DateTime)
    last_checked = db.Column(db.DateTime)
    
    
    domain = db.relationship(
        'Domain', 
        foreign_keys=[domain_id],
        backref=db.backref('certificates', lazy='dynamic')
    )

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
                
                
                if len(result) < 3:
                    logging.error(f"Invalid cert result for {processing_domain}: {result}")
                    continue

                try:
                    expiry_date = datetime.fromisoformat(result[1]) if result[1] else None
                except ValueError:
                    expiry_date = None

                issuer = None
                if result[2]:
                    match = re.search(r"O=([^,]+)", result[2])
                    if match:
                        issuer = match.group(1)

                with app.app_context():
                    entry = Domain.query.filter_by(domain=processing_domain).first()
                    if not entry:
                        logging.warning(f"Domain {processing_domain} not found in DB")
                        continue

                    
                    new_cert = Certificate(
                        type=result[0],
                        expiry_date=expiry_date,
                        issuer=issuer,
                        last_checked=datetime.now(),
                        domain_id=entry.id
                    )
                    db.session.add(new_cert)
                    
                    
                    entry.last_certificate = new_cert
                    entry.last_checked = datetime.now()
                    
                    
                    if result[0] == "HTTPS":
                        entry.status = 'Active'
                    elif result[0] == "HTTP ONLY":
                        entry.status = 'Active'
                    elif result[0] == "Refused":
                        entry.status = 'Offline'
                    
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


threading.Thread(target=worker, daemon=True).start()

@app.route('/update/<domain>', methods=['GET'])
def update_single(domain):
    
    task_queue.put(domain)
    return '', 202  

@app.route('/', methods=['GET', 'POST'])
def index():
    query = request.args.get('query', '')
    filter_type = request.args.get('filter', 'all')
    sort_by = request.args.get('sort_by', 'domain')  
    sort_order = request.args.get('sort_order', 'asc')  

    certs = Domain.query.outerjoin(
    Certificate, 
    Domain.last_certificate_id == Certificate.id  
    )

    
    if sort_by == 'expire_date':
        order = Certificate.expiry_date.asc() if sort_order == 'asc' else Certificate.expiry_date.desc()
        certs = certs.order_by(order)
    elif sort_by == 'last_checked':
        order = Certificate.last_checked.asc() if sort_order == 'asc' else Certificate.last_checked.desc()
        certs = certs.order_by(order)
    else:
        
        order = Domain.domain.asc() if sort_order == 'asc' else Domain.domain.desc()
        certs = certs.order_by(order)

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
    
    
    
    

@app.route('/update_all', methods=['POST'])
def update_all():
    domains = [cert.domain for cert in Domain.query.all()]
    for domain in domains:
        task_queue.put(domain)
    return '', 202

@app.route('/generate_pdf')
def generate_pdf():
    
    query = request.args.get('query', '')
    filter_type = request.args.get('filter', 'all')
    sort_by = request.args.get('sort_by', 'domain')
    sort_order = request.args.get('sort_order', 'asc')

    certs = Domain.query.outerjoin(Certificate, Domain.last_certificate_id == Certificate.id)
    
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

    
    if sort_by == 'expire_date':
        order = Certificate.expiry_date.asc() if sort_order == 'asc' else Certificate.expiry_date.desc()
        certs = certs.order_by(order)
    elif sort_by == 'last_checked':
        order = Certificate.last_checked.asc() if sort_order == 'asc' else Certificate.last_checked.desc()
        certs = certs.order_by(order)
    else:
        order = Domain.domain.asc() if sort_order == 'asc' else Domain.domain.desc()
        certs = certs.order_by(order)

    try:
        
        
        report_data = []
        for d in certs.all():
            cert = d.last_certificate
            report_data.append({
                'domain': d.domain,
                'expiry_date': cert.expiry_date.strftime('%Y-%m-%d') if cert and cert.expiry_date else 'N/A',
                'days_left': str((cert.expiry_date - datetime.now()).days) if cert and cert.expiry_date else 'N/A',
                'last_checked': cert.last_checked.strftime('%Y-%m-%d %H:%M:%S') if cert and cert.last_checked else 'Never',
                'paid_till': d.paid_till_date.strftime('%Y-%m-%d') if d.paid_till_date else 'N/A',
                'issuer': cert.issuer if cert else 'N/A',
                'status': d.status
            })

        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as data_file:
            json.dump(report_data, data_file, ensure_ascii=False)
            data_path = data_file.name

        output_file = os.path.join(tempfile.gettempdir(), f'domain_report_{uuid.uuid4().hex}')
        input_file = os.path.join(app.root_path, 'reports', 'domain_report.jrxml')

        
        jasper = PyReportJasper()
        jasper.config(
            input_file=input_file,
            output_file=output_file,
            output_formats=["pdf"],
            db_connection={
                'driver': 'json',
                'data_file': data_path
            }
        )

        
        jasper.process_report()

        
        pdf_path = f"{output_file}.pdf"
        with open(pdf_path, 'rb') as f:
            pdf_content = f.read()

        response = make_response(pdf_content)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = 'attachment; filename=domain_report.pdf'
        return response

    except Exception as e:
        app.logger.error(f"PDF generation failed: {str(e)}\n{str(e)}")
        return f"Error generating PDF: {str(e)}", 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    socketio.run(app, debug=True)