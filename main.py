import sys
import socket
import ssl
import csv
from datetime import datetime
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import load_der_x509_certificate

def get_cert_expiry(domain):
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
    except socket.timeout:
        print(f"  Error: Connection timed out for {domain}")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None

def process_dns_dump(file_path, output_csv):
    with open(file_path, 'r') as f, open(output_csv, 'w', newline='') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(['Domain', 'Expiry Date'])

        for line in f:
            line = line.strip()
            if not line or line.startswith(';;'):
                continue
            
            parts = line.split()
            if not parts:
                continue
            
            domain = parts[0].rstrip('.')
            print(f"Processing: {domain}")
            
            expiry_date = get_cert_expiry(domain)
            if expiry_date:
                formatted_date = expiry_date.strftime('%Y-%m-%d %H:%M:%S')
                print(f"  Certificate expires on: {formatted_date}")
                csv_writer.writerow([domain, formatted_date])
            else:
                print("  Failed to get certificate expiration date")
                csv_writer.writerow([domain, "Error"])
            print()

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python script.py <dns_dump_file> <output_csv_file>")
        sys.exit(1)
    
    dns_dump_file = sys.argv[1]
    output_csv_file = sys.argv[2]
    process_dns_dump(dns_dump_file, output_csv_file)