import os
import time
import sys
from urllib.parse import urlparse
import psycopg

def wait_for_db():
    """Wait for the database to be available."""
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("DATABASE_URL not found, skipping wait.")
        return

    url = urlparse(db_url)
    dbname = url.path[1:]
    user = url.username
    password = url.password
    host = url.hostname
    port = url.port or 5432

    print(f"Waiting for database at {host}:{port}...")
    
    start_time = time.time()
    while True:
        try:
            conn = psycopg.connect(
                dbname=dbname,
                user=user,
                password=password,
                host=host,
                port=port,
                connect_timeout=3
            )
            conn.close()
            print("Database is ready!")
            break
        except psycopg.OperationalError as e:
            print(f"Database unavailable, waiting 1 second... Error: {e}")
            time.sleep(1)
            
        if time.time() - start_time > 60:
            print("Timeout waiting for database.")
            sys.exit(1)

if __name__ == "__main__":
    wait_for_db()
