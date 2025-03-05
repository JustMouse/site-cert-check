# cert_checker.py (обновленная версия)
import sys
import socket
import ssl
import csv
from datetime import datetime
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import load_der_x509_certificate

def check_cert(domain):
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        with socket.create_connection((domain, 443), timeout=10) as sock:
            sock.settimeout(10)
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)
                cert = load_der_x509_certificate(cert_der, default_backend())
                return cert.not_valid_after
    except Exception as e:
        return None

if __name__ == "__main__":
    domain = sys.argv[1]
    output_file = sys.argv[2]
    
    expiry_date = check_cert(domain)
    with open(output_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Domain', 'Expiry Date'])
        writer.writerow([
            domain,
            expiry_date.strftime('%Y-%m-%d %H:%M:%S') if expiry_date else 'Error'
        ])