import psycopg2
cur = conn.cursor()

cur.execute("SELECT DISTINCT date FROM book_savings WHERE msn = %s ORDER BY date DESC LIMIT 20", ("67000509",))
print("Dates:", cur.fetchall())

cur.execute("SELECT COUNT(*) FROM book_savings WHERE msn = %s", ("67000509",))
print("Total rows:", cur.fetchone()[0])

cur.execute("SELECT * FROM book_savings WHERE msn = %s ORDER BY entry_ts DESC LIMIT 3", ("67000509",))
print("Sample rows:", cur.fetchall())

cur.close()
conn.close()
