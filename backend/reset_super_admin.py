"""Rotaciona a senha do Super Admin — ação deliberada, fora do ciclo de boot.

Por que existe: `seed_super_admin` foi corrigido para **nunca** reescrever a
senha de uma conta que já existe (ZZ-01). Isso é o certo — um restart não pode
desfazer uma troca de senha —, mas deixa a rotação de uma credencial já
exposta como um passo explícito. Este é esse passo: o equivalente ao
`changepassword` do Django, auditável e único, em vez de mágica no boot.

Uso (com o Mongo acessível — em dev, depois do start-dev.ps1 abrir o túnel):

    cd backend
    python reset_super_admin.py                 # usa ADMIN_PASSWORD do .env
    python reset_super_admin.py --password 'Nova!Senha123'

Lê MONGO_URL / DB_NAME / ADMIN_EMAIL do mesmo `.env` do backend. Recusa senha
com menos de 12 caracteres, o mesmo piso do seed.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient

SENHA_MINIMA = 12


async def _rotacionar(nova: str) -> int:
    email = os.environ.get("ADMIN_EMAIL", "admin@4turismo.com.br").strip().lower()
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=8000)
    db = client[os.environ["DB_NAME"]]
    try:
        u = await db.users.find_one({"email": email, "role": "super_admin"})
        if not u:
            print(f"Super Admin '{email}' não encontrado. "
                  "Suba o backend uma vez para semeá-lo (ADMIN_PASSWORD no .env).")
            return 2
        h = bcrypt.hashpw(nova.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        await db.users.update_one({"id": u["id"]}, {"$set": {"password_hash": h}})
        # Confirma no banco, sem imprimir a senha.
        v = await db.users.find_one({"id": u["id"]}, {"_id": 0, "password_hash": 1})
        ok = bcrypt.checkpw(nova.encode("utf-8"), v["password_hash"].encode("utf-8"))
        print(f"Senha do Super Admin '{email}' rotacionada. Confirmada no banco: {ok}")
        return 0 if ok else 1
    finally:
        client.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotaciona a senha do Super Admin.")
    ap.add_argument("--password", help="Nova senha. Sem isto, usa ADMIN_PASSWORD do .env.")
    args = ap.parse_args()

    nova = args.password or os.environ.get("ADMIN_PASSWORD")
    if not nova:
        print("Defina --password ou ADMIN_PASSWORD no .env.")
        return 2
    if len(nova) < SENHA_MINIMA:
        print(f"Senha muito curta: use ao menos {SENHA_MINIMA} caracteres.")
        return 2
    try:
        return asyncio.run(_rotacionar(nova))
    except Exception as e:
        print(f"Falha ao conectar/rotacionar: {e}")
        print("O Mongo está acessível? Em dev, rode isto com o start-dev.ps1 no ar (túnel 27017).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
