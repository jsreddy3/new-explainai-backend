import asyncio
import asyncpg
from urllib.parse import urlparse

async def check_database():
    # Parse the DATABASE_URL
    url = "postgres://u9k7l4p13golq:pd4757f9111e02e1a9d474903548397813077d0bf5834bcca4bd622ebdd18a182@cd1goc44htrmfn.cluster-czrs8kj4isg7.us-east-1.rds.amazonaws.com:5432/dbob48i3qpiu2n"
    result = urlparse(url)
    username = result.username
    password = result.password
    database = result.path[1:]
    hostname = result.hostname
    port = result.port

    # Connect to the database
    conn = await asyncpg.connect(
        user=username,
        password=password,
        database=database,
        host=hostname,
        port=port,
        ssl='prefer'
    )

    # Query documents
    rows = await conn.fetch('''
        SELECT id, title, owner_id, created_at 
        FROM documents 
        WHERE id = '017bebe9-8fed-4295-a666-ef9f8a718747'
    ''')

    print("\nDocument check:")
    if not rows:
        print("No document found with that ID")
    for row in rows:
        print(f"ID: {row['id']}")
        print(f"Title: {row['title']}")
        print(f"Owner: {row['owner_id']}")
        print(f"Created: {row['created_at']}")

    # List all documents
    print("\nAll documents:")
    all_docs = await conn.fetch('SELECT id, title FROM documents')
    for doc in all_docs:
        print(f"ID: {doc['id']}, Title: {doc['title']}")

    await conn.close()

asyncio.run(check_database())
