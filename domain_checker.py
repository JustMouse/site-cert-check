import whois
import sys

def get_expiration_date(whois):
    try:
        return whois.expiration_date
    except Exception:
        return None

if __name__ == "__main__":
    domain = sys.argv[1]
    try:
        result = whois.whois(domain)
        print("Accepted",end=";")
        print(get_expiration_date(result),end=";")
        print(result.name_servers[0]+","+result.name_servers[1],end=";")
    except Exception as e:
        print("Refused")