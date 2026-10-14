"""Aplica os limites (max_bots/max_conexoes/max_usuarios) do plano vinculado
ao tenant. Tenant sem plan_id (ou plano sem o campo de limite) fica sem
restrição — trata como "ilimitado" em vez de bloquear."""
from fastapi import HTTPException

# (collection, campo do plano, rótulo em português)
LIMITS = [
    ("bots", "max_bots", "fluxos automáticos"),
    ("whatsapp_connections", "max_conexoes", "conexões WhatsApp"),
    ("users", "max_usuarios", "usuários"),
]


async def plan_excess(db, tenant_id: str, plan: dict) -> list[str]:
    """Onde o tenant já passa dos limites do plano informado. Usado ao trocar o
    plano de uma empresa: a troca é permitida, mas o super admin precisa saber
    que ela fica acima do teto — o excedente continua funcionando, porque
    enforce_plan_limit só barra criações novas."""
    out = []
    for collection, field, label in LIMITS:
        limit = plan.get(field)
        if limit is None:
            continue
        count = await db[collection].count_documents({"tenant_id": tenant_id})
        if count > limit:
            out.append(f"{count} {label} (máx. {limit})")
    return out


async def enforce_plan_limit(db, tenant_id: str, collection: str, field: str, label: str):
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "plan_id": 1})
    if not tenant or not tenant.get("plan_id"):
        return
    plan = await db.plans.find_one({"id": tenant["plan_id"]}, {"_id": 0})
    if not plan or plan.get(field) is None:
        return
    limit = plan[field]
    count = await db[collection].count_documents({"tenant_id": tenant_id})
    if count >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Limite do plano \"{plan['nome']}\" atingido: máx. {limit} {label}",
        )


async def uso_do_plano(db, tenant_id: str, plan: dict | None) -> list[dict]:
    """Quanto a empresa já usou de cada limite do plano, para a tela mostrar.

    Só **lê** — não barra nada. O valor de existir é que o cliente veja o teto
    antes de esbarrar nele: hoje ele descobre o limite levando um 403 ao criar
    o quarto robô.

    Cada linha sai de quem já aplica aquele limite, nunca de uma contagem
    escrita de novo aqui:

    - robôs, conexões e usuários vêm da tabela `LIMITS` acima, a mesma que
      `enforce_plan_limit` percorre;
    - tokens de IA vêm de `ai_runtime.consumo_do_mes`, que é o que a cota de IA
      consulta.

    Recontar por conta própria faria o painel e o 403 divergirem, e não haveria
    como saber qual dos dois está certo.

    O de tokens fica fora de `LIMITS` de propósito — aquela tabela conta
    documentos existentes por tenant, e este é sobre uma janela de tempo. Aqui
    eles se juntam porque a tela mostra todos lado a lado.

    `limite` nulo significa ilimitado (plano sem o campo, campo em 0, ou
    empresa sem plano vinculado) — a mesma leitura que os enforcers fazem.
    """
    import ai_runtime

    plan = plan or {}

    def _teto(campo):
        v = plan.get(campo)
        return int(v) if v else None  # None ou 0 = ilimitado

    linhas = []
    for collection, campo, label in LIMITS:
        linhas.append({
            "chave": campo,
            "label": label.capitalize(),
            "usado": await db[collection].count_documents({"tenant_id": tenant_id}),
            "limite": _teto(campo),
            "janela": None,
        })

    # O teto de IA só vale quando quem paga somos nós. Com a conta da própria
    # empresa conectada, `resolver_credencial` nem chega a `_checar_cota` — e
    # mostrar um teto que ninguém aplica faria o cliente parar de usar o agente
    # por medo de um limite que não existe para ele.
    conta_propria = await db.ai_connections.find_one(
        {"tenant_id": tenant_id, "status": "conectada"}, {"_id": 0, "id": 1}
    )
    consumo = await ai_runtime.consumo_do_mes(db, tenant_id)
    linhas.append({
        "chave": "max_tokens_ia_mes",
        "label": "Tokens de IA",
        "usado": consumo["tokens"],
        "limite": None if conta_propria else _teto("max_tokens_ia_mes"),
        "janela": "mês",
        "nota": "conta da empresa" if conta_propria else None,
    })
    return linhas
