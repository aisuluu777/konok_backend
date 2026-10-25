"""Создаёт первого admin (или делает admin существующего пользователя).

    python scripts/create_admin.py admin@konok.kg "Имя Фамилия"

Пароль спрашивается в терминале (или берётся из переменной ADMIN_PASSWORD).
В Docker: docker compose exec api python scripts/create_admin.py admin@konok.kg "Имя"
"""

import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionFactory  # noqa: E402
from app.models import User, UserRole  # noqa: E402
from app.security import generate_code, hash_password  # noqa: E402


async def main(email: str, full_name: str, password: str) -> None:
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, full_name=full_name, checkin_pin=generate_code())
            session.add(user)
        user.password_hash = hash_password(password)
        user.role = UserRole.ADMIN
        user.is_email_confirmed = True
        user.is_active = True
        await session.commit()
    print(f"Готово: {email} — admin. Вход в админку: /admin")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Пароль (от 8 символов): ")
    if len(password) < 8:
        sys.exit("Пароль короче 8 символов")
    asyncio.run(main(sys.argv[1].lower(), sys.argv[2], password))
