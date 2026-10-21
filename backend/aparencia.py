"""Aparência: a paleta da empresa e o claro/escuro de cada pessoa.

Duas escolhas, dois donos, e por isso dois lugares no banco:

- **Paleta** (`tenants.aparencia.paleta`) é a identidade da agência: cor e
  tipografia, a mesma para toda a equipe dela. Só o admin troca.
- **Modo** (`users.preferencias.modo`) é de cada pessoa: claro, escuro ou o do
  sistema. Acompanha o usuário em qualquer computador.

Os ids de paleta têm de bater com `frontend/src/lib/paletas.js` e com os blocos
`:root[data-paleta]` de `index.css`. Um id fora da lista é recusado na escrita
e, se aparecer no banco por outro caminho, lido como a padrão: uma paleta que o
CSS não conhece deixaria a tela sem cor nenhuma de marca.
"""

PALETA_PADRAO = "oficial"
PALETAS = ("oficial", "sol", "azulejo")
MODOS = ("claro", "escuro", "sistema")


def paleta_valida(valor) -> str:
    return valor if valor in PALETAS else PALETA_PADRAO


async def aparencia_do_usuario(db, user: dict) -> dict:
    """O que o login e o `/auth/me` devolvem para a tela se pintar.

    Em modo suporte, `user` é o Super Admin com o `tenant_id` da empresa
    visitada: a paleta é a da empresa (ele vê o que o cliente vê) e o modo é o
    dele. Super admin fora do modo suporte não tem empresa: paleta nula, e a
    tela usa a padrão.
    """
    paleta = None
    tenant_id = user.get("tenant_id")
    if tenant_id:
        t = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "aparencia": 1})
        paleta = paleta_valida(((t or {}).get("aparencia") or {}).get("paleta"))
    modo = (user.get("preferencias") or {}).get("modo")
    return {"paleta": paleta, "modo": modo if modo in MODOS else None}
