
from app.database.connection import get_connection


def main():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ikn FROM tender_metadata LIMIT 1;")
                print(cur.fetchone()[0])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
