import asyncio
import getpass

import asyncpg

from app.security import hash_password

DSN = "postgresql://postgres:Admin123@127.0.0.1:5433/demand_crm"


async def main():
    username = input("Username (3-50 chars): ").strip()
    display = input("Display name: ").strip() or username
    password = getpass.getpass("New password: ")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            """
            INSERT INTO users (username, password_hash, display_name)
            VALUES ($1, $2, $3)
            """,
            username, hash_password(password), display,
        )
        print("Created", username)
    finally:
        await conn.close()


asyncio.run(main())