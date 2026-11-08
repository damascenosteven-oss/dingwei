"""Create synthetic families in a fresh local acceptance DB. Never creates location points."""

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "accounts", type=Path, help="Private output file outside the worktree"
    )
    args = parser.parse_args()
    url = urlparse(os.environ["DATABASE_URL"])
    if (
        url.hostname not in {"localhost", "127.0.0.1"}
        or url.port != 55433
        or url.path != "/dingwei_qa"
        or url.scheme != "postgresql+psycopg"
    ):
        raise SystemExit("只接受本机 55433 端口的 dingwei_qa 专用数据库。")
    if args.accounts.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise SystemExit("请将模拟账号保存在仓库以外。")
    from app.database import SessionLocal
    from app.models import Elder, Family, FamilyMembership, User
    from app.security import hash_password

    accounts = []
    with SessionLocal.begin() as db:
        if db.scalar(select(Family.id).limit(1)):
            raise SystemExit("数据库已有家庭，结束初始化。")
        for index in (1, 2):
            family = Family(name=f"模拟家庭{index}")
            password = secrets.token_urlsafe(20)
            user = User(
                email=f"family{index}@example.com",
                password_hash=hash_password(password),
            )
            db.add_all([family, user])
            db.flush()
            elder = Elder(
                family_id=family.id,
                display_name=f"模拟老人{index}",
                timezone="Asia/Shanghai",
            )
            db.add_all(
                [
                    elder,
                    FamilyMembership(
                        family_id=family.id, user_id=user.id, role="admin"
                    ),
                ]
            )
            db.flush()
            accounts.append(
                dict(
                    family=str(family.id),
                    elder=str(elder.id),
                    email=user.email,
                    password=password,
                )
            )
        with args.accounts.open("x", encoding="utf-8") as out:
            os.chmod(args.accounts, 0o600)
            json.dump(accounts, out)
    print("已创建两个模拟家庭及私有账号文件。")


if __name__ == "__main__":
    main()
