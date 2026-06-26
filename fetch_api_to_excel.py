import psycopg2

conn = psycopg2.connect(
    host="65.1.248.33",
    user="script_client", 
    password="JlR2KPVu.s/b5fTy",
    database="ap",
    port=5432
)
cur = conn.cursor()

cur.execute("SELECT DISTINCT date FROM book_savings WHERE msn = %s ORDER BY date DESC LIMIT 20", ("67000509",))
print("Dates:", cur.fetchall())

cur.execute("SELECT COUNT(*) FROM book_savings WHERE msn = %s", ("67000509",))
print("Total rows:", cur.fetchone()[0])

cur.execute("SELECT * FROM book_savings WHERE msn = %s ORDER BY entry_ts DESC LIMIT 3", ("67000509",))
print("Sample rows:", cur.fetchall())

cur.close()
conn.close()