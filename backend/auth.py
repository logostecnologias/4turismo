import os
import uuid
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from pydantic import BaseModel, EmailStr

from db import get_db
import login_guard
from aparencia import aparencia_do_usuario

JWT_ALGORITHM = "HS256"


# O bcrypt 4.x LEVANTA para entrada acima de 72 bytes, em vez de truncar como as
# versões antigas (ZZ-18): uma senha longa no formulário virava 500 no login (um
# caminho não tratado alcançável sem autenticação) e quebrava o cadastro. O
# bcrypt sempre usou só os primeiros 72 bytes, então truncar aqui é o
# comportamento histórico e não muda hash nenhum já gravado — só troca a exceção
# por um corte silencioso, que é o que o resto do mundo espera.
_BCRYPT_MAX = 72


def _bcrypt_bytes(password: str) -> bytes:
    return (password or "").encode("utf-8")[:_BCRYPT_MAX]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_bytes(plain), (hashed or "").encode("utf-8"))
    except (ValueError, TypeError):
        # Hash malformado no banco não pode virar 500 num caminho de login.
        return False


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def _cookie_secure() -> bool:
    """Cookies com `Secure` por padrão (ZZ-08): o JWT não trafega em HTTP puro.

    Só o desenvolvimento local, servindo por http, precisa desligar isto —
    `COOKIE_SECURE=false` no .env. Em produção, sem a variável, o padrão é
    seguro. Navegadores tratam `localhost`/`127.0.0.1` como origem segura, então
    o cookie `Secure` normalmente funciona no dev mesmo por http; a variável
    existe para o caso de um navegador que não faça isso.
    """
    return os.environ.get("COOKIE_SECURE", "true").strip().lower() not in ("0", "false", "no")


def create_access_token(user_id: str, email: str, role: str, tenant_id: str | None,
                        impersonation_id: str | None = None, token_version: int = 0) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "tenant_id": tenant_id,
        "impersonation_id": impersonation_id,
        # `tv` liga o token à versão da credencial do usuário. Trocar a senha
        # incrementa `token_version` no banco, e todo token emitido antes passa
        # a não bater — é o que dá revogação de sessão (ZZ-09), que o JWT sem
        # estado não tem sozinho.
        "tv": token_version,
        "exp": datetime.now(timezone.utc) + timedelta(hours=12),
        "type": "access",
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, token_version: int = 0) -> str:
    payload = {
        "sub": user_id,
        # Mesmo `tv` do access: trocar a senha também derruba o refresh
        # (ZZ-09/ZZ-20), senão ele reemitiria acesso para uma credencial já
        # revogada durante 7 dias.
        "tv": token_version,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "refresh",
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def _set_auth_cookies(response: Response, access: str, refresh: str):
    secure = _cookie_secure()
    response.set_cookie("access_token", access, httponly=True, secure=secure,
                        samesite="lax", max_age=12 * 3600, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=secure,
                        samesite="lax", max_age=7 * 86400, path="/")


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Não autenticado")
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Token inválido")
        db = get_db()
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise HTTPException(status_code=401, detail="Usuário não encontrado")
        user.pop("_id", None)
        user.pop("password_hash", None)

        # Revogação (ZZ-09): o token carrega a versão da credencial. Se a senha
        # foi trocada (ou a conta forçou logout), `token_version` subiu e este
        # token — emitido antes — deixa de valer. Tokens antigos sem `tv` e
        # contas sem `token_version` casam em 0, então nada quebra na migração.
        if payload.get("tv", 0) != (user.get("token_version") or 0):
            raise HTTPException(status_code=401, detail="Sessão expirada — entre novamente")

        # Suspensão passa a valer NA HORA, não só no próximo login (ZZ-09).
        # Antes, quem foi desligado seguia por até 12h com o token na mão.
        if user.get("status") == "suspended":
            raise HTTPException(status_code=403, detail="Conta suspensa")

        # If impersonating, JWT claims override DB role/tenant.
        if payload.get("impersonation_id"):
            user["role"] = payload.get("role")
            user["tenant_id"] = payload.get("tenant_id")
            user["impersonation_id"] = payload.get("impersonation_id")

        # Empresa suspensa derruba a equipe dela na hora (super admin fica fora
        # da regra: não pertence a tenant nenhum). Uma consulta por `id`, que é
        # indexado — o mesmo custo de ordem da leitura do usuário acima.
        if user.get("tenant_id") and user.get("role") != "super_admin":
            tenant = await db.tenants.find_one(
                {"id": user["tenant_id"]}, {"_id": 0, "status": 1}
            )
            if tenant and tenant.get("status") == "suspended":
                raise HTTPException(status_code=403, detail="Agência suspensa")

        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")


async def require_super_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao Super Admin")
    return user


async def require_tenant_member(user: dict = Depends(get_current_user)) -> dict:
    """Qualquer pessoa de dentro da empresa: tenant_admin ou atendente.

    Existe para o atendente alcançar a base de contatos, o funil e a agenda.
    Sem isso `responsavel_id` era decorativo: quem era responsável por um
    contato não conseguia abrir a ficha dele.

    O que **continua** restrito ao admin é configuração e movimentação em
    massa — campos personalizados, etapas do funil, importação e exportação de
    CSV. Exportar a base inteira é o caminho mais curto para ela sair da
    empresa, e não é tarefa de quem atende.
    """
    if user.get("role") not in ("tenant_admin", "agent"):
        raise HTTPException(status_code=403, detail="Acesso restrito à equipe da agência")
    if not user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Usuário sem agência vinculada")
    return user


async def accessible_connection_ids(db, user: dict) -> list[str] | None:
    """Conexões que este usuário alcança. `None` significa todas.

    Vive aqui, e não numa rota, porque é regra de permissão: o atendente só vê
    e só fala pelas conexões liberadas para ele (`user_connection_access`), e
    isso vale em toda tela que envie mensagem — não só na Central.
    """
    if user.get("role") in ("tenant_admin", "super_admin"):
        return None
    rows = await db.user_connection_access.find(
        {"tenant_id": user["tenant_id"], "user_id": user["id"]}, {"_id": 0}
    ).to_list(500)
    return [r["whatsapp_connection_id"] for r in rows]


async def require_tenant_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "tenant_admin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador da agência")
    if not user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Usuário sem agência vinculada")
    return user


# ---------- Router ----------

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


@router.post("/login")
async def login(body: LoginIn, response: Response, request: Request):
    db = get_db()
    email = body.email.lower()
    ip = _client_ip(request)
    # Freio de força bruta (ZZ-12): antes de gastar o bcrypt, e antes de dizer
    # qualquer coisa sobre a conta.
    login_guard.checar(ip, email)
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        login_guard.registrar_falha(ip, email)
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    login_guard.registrar_sucesso(ip, email)
    if user.get("status") == "suspended":
        raise HTTPException(status_code=403, detail="Conta suspensa")
    # tenant status check for tenant_admin
    if user["role"] == "tenant_admin" and user.get("tenant_id"):
        tenant = await db.tenants.find_one({"id": user["tenant_id"]})
        if tenant and tenant.get("status") == "suspended":
            raise HTTPException(status_code=403, detail="Agência suspensa. Contate o administrador.")
    access = create_access_token(user["id"], user["email"], user["role"],
                                 user.get("tenant_id"), token_version=user.get("token_version", 0))
    refresh = create_refresh_token(user["id"], user.get("token_version", 0))
    _set_auth_cookies(response, access, refresh)
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "tenant_id": user.get("tenant_id"),
        # A tela se pinta com isto antes de qualquer outra chamada.
        **await aparencia_do_usuario(db, user),
    }


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    """Renova o access token a partir do refresh cookie, de forma revogável.

    Fecha o resíduo do ZZ-20: o refresh era emitido e nunca consumido — uma
    credencial de 7 dias parada e sem validação. Agora tem uso e é conferido:
    tipo correto, usuário existe e ativo, empresa ativa, e o `tv` bate com o do
    banco (trocar a senha o derruba). O refresh é rotacionado a cada uso.

    Não preserva o modo suporte de propósito: o refresh guarda só a identidade
    real (o Super Admin), e a impersonation é uma sessão curta com token próprio
    de 12h. Renovar fora dela devolve o papel real.
    """
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="Sem refresh token")
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        # Cobre também o expirado (ExpiredSignatureError é subclasse desta).
        raise HTTPException(status_code=401, detail="Refresh inválido ou expirado")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Token inválido")

    db = get_db()
    user = await db.users.find_one({"id": payload.get("sub")})
    if not user:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")
    if payload.get("tv", 0) != (user.get("token_version") or 0):
        raise HTTPException(status_code=401, detail="Sessão expirada — entre novamente")
    if user.get("status") == "suspended":
        raise HTTPException(status_code=403, detail="Conta suspensa")
    if user.get("tenant_id") and user.get("role") != "super_admin":
        tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "status": 1})
        if tenant and tenant.get("status") == "suspended":
            raise HTTPException(status_code=403, detail="Agência suspensa")

    tv = user.get("token_version", 0)
    access = create_access_token(user["id"], user["email"], user["role"],
                                 user.get("tenant_id"), token_version=tv)
    novo_refresh = create_refresh_token(user["id"], tv)
    _set_auth_cookies(response, access, novo_refresh)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "tenant_id": user.get("tenant_id"),
        **await aparencia_do_usuario(get_db(), user),
    }


@router.post("/exit-impersonation")
async def exit_impersonation(response: Response, user: dict = Depends(get_current_user)):
    """Encerra a sessão de suporte e restaura o Super Admin.

    Corrige ZZ-02, que era a falha mais grave depois da senha padrão: esta era
    a única rota de escrita **sem autenticação**, e devolvia um token de Super
    Admin recém-assinado para qualquer um que apresentasse um `impersonation_id`
    — que, além disso, valia para sempre (nunca conferia `finalizado_em`).

    Três barreiras agora:

      1. **Autenticada** (`Depends(get_current_user)`): é preciso um token
         válido. A identidade do dono não vem mais de um cookie solto e sim da
         claim `impersonation_id` de dentro do próprio JWT assinado, que só o
         endpoint de impersonation emite.

      2. **Do dono**: o log tem de pertencer a este Super Admin
         (`super_admin_id == user["id"]`).

      3. **Uso único e atômico**: o encerramento é um `update_one` filtrando
         `finalizado_em: None`. Se `modified_count` não for 1, o log não existe,
         não é seu, ou já foi encerrado — e a resposta é a mesma recusa, sem
         reemitir token. Isso fecha o replay: um id já usado não serve de novo.
    """
    log_id = user.get("impersonation_id")
    if not log_id:
        raise HTTPException(status_code=400, detail="Não está em modo suporte")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    res = await db.impersonation_log.update_one(
        {"id": log_id, "super_admin_id": user["id"], "finalizado_em": None},
        {"$set": {"finalizado_em": now}},
    )
    if res.modified_count != 1:
        raise HTTPException(
            status_code=400,
            detail="Sessão de suporte inválida ou já encerrada",
        )

    # `user["id"]` é sempre o Super Admin real: get_current_user carrega o
    # documento por `sub` (o SA) e só sobrescreve role/tenant na impersonation.
    sa = await db.users.find_one({"id": user["id"]})
    if not sa or sa.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Somente o Super Admin encerra o modo suporte")

    access = create_access_token(sa["id"], sa["email"], "super_admin", None,
                                 token_version=sa.get("token_version", 0))
    refresh = create_refresh_token(sa["id"], sa.get("token_version", 0))
    _set_auth_cookies(response, access, refresh)
    response.delete_cookie("impersonation_id", path="/")
    response.delete_cookie("impersonated_role", path="/")
    return {"ok": True}


SENHA_ADMIN_MINIMA = 12


async def seed_super_admin():
    """Cria o Super Admin no primeiro boot, e **só** no primeiro.

    Duas mudanças em relação à versão anterior, que era o pior buraco do
    sistema (ZZ-01):

      1. **Sem senha padrão.** Não há mais `admin@123` embutido. Sem
         `ADMIN_PASSWORD` no ambiente e sem admin ainda semeado, o boot é
         abortado de propósito — um default embutido é uma credencial pública.
         É o mesmo contrato de `JWT_SECRET`, que já falha alto quando falta.

      2. **Nunca reescreve a senha de uma conta que já existe.** Antes, um
         `elif` regravava o hash a cada boot para casar com a env, então um
         restart desfazia qualquer troca de senha feita pelo painel — a conta
         mais poderosa do sistema era, na prática, imutável e pública. Trocar
         senha é operação de tela; o boot não tem nada a ver com isso.

    Consequência desejada: se o Super Admin for apagado, o próximo boot exige
    `ADMIN_PASSWORD` de novo para resemear. Rotação de senha é feita pelo
    painel (ou por um reset explícito fora do ciclo de boot), nunca aqui.
    """
    db = get_db()
    email = os.environ.get("ADMIN_EMAIL", "admin@4turismo.com.br").strip().lower()
    existing = await db.users.find_one({"email": email})
    if existing is not None:
        return

    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        raise RuntimeError(
            "ADMIN_PASSWORD não definido: defina uma senha forte no ambiente "
            "para semear o Super Admin. O boot é abortado de propósito — um "
            "valor padrão embutido viraria uma credencial pública."
        )
    if len(password) < SENHA_ADMIN_MINIMA:
        raise RuntimeError(
            f"ADMIN_PASSWORD muito curta: use ao menos {SENHA_ADMIN_MINIMA} caracteres."
        )

    await db.users.insert_one({
        "id": str(uuid.uuid4()),
        "email": email,
        "name": "Super Admin",
        "role": "super_admin",
        "tenant_id": None,
        "status": "active",
        "password_hash": hash_password(password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
