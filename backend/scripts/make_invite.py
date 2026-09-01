"""生成邀请码：在 backend/ 目录下运行 python -m scripts.make_invite --uses 10"""

import argparse
import secrets

from app.db.database import Base, SessionLocal, engine
from app.db.models import InviteCode


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 JobCheck 邀请码")
    parser.add_argument("--uses", type=int, default=10, help="可使用次数")
    parser.add_argument("--code", type=str, default=None, help="自定义邀请码（默认随机 8 位）")
    args = parser.parse_args()

    Base.metadata.create_all(engine)
    code = args.code or secrets.token_hex(4)
    with SessionLocal() as db:
        db.add(InviteCode(code=code, max_uses=args.uses))
        db.commit()
    print(f"邀请码已创建: {code}（可用 {args.uses} 次）")


if __name__ == "__main__":
    main()
