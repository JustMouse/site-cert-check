import sys
import socket
import ssl
import csv
from datetime import datetime
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import load_der_x509_certificate

def check_https(domain, get_ssl=True):
    try:
        with socket.create_connection((domain, 443), timeout=5) as sock:
            if(get_ssl):
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE

                with context.wrap_socket(sock, server_hostname=domain) as ssock:
                    der_cert = ssock.getpeercert(binary_form=True)
                    cert = load_der_x509_certificate(der_cert, default_backend())
                    return cert
            return True
        return get_certificate(domain)
    except (socket.timeout, ConnectionRefusedError):
        return False

def check_http(domain):
    try:
        with socket.create_connection((domain, 80), timeout=5) as sock:
            return True
    except (socket.timeout, ConnectionRefusedError):
        return False
    except Exception:
        return False

def get_not_valid_after_utc(cert):
    try:
        return cert.not_valid_after_utc
    except Exception as e:
        return None

def get_issuer(cert):
    try:
        return cert.issuer
    except Exception as e:
        return None

if __name__ == "__main__":
    domain = sys.argv[1]
    try:
        cert = check_https(domain)
        if(not cert):
            raise
        print("HTTPS",end=";")
        print(get_not_valid_after_utc(cert),end=";")
        print(get_issuer(cert),end=";")
    except Exception:
        if(check_http(domain)):
            print("HTTP")
        else:
            print("Refused")