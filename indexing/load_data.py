"""Load CSV into SQLite."""
import os
import sqlite3
import pandas as pd


def load_csv_to_sqlite(csv_path="data/structured/f1_results.csv",
                       db_path="data/structured/f1_results.db"):
    if not os.path.exists(csv_path):
        print(f"CSV not found: {csv_path}")
        return

    print(f"Loading CSV: {csv_path}")
    df = pd.read_csv(csv_path)

    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    df.to_sql("race_results", conn, if_exists="replace", index=False)

    count = conn.cursor().execute("SELECT COUNT(*) FROM race_results").fetchone()[0]
    conn.close()
    print(f"[OK] Loaded {count} rows into SQLite: {db_path}")


if __name__ == "__main__":
    load_csv_to_sqlite()
