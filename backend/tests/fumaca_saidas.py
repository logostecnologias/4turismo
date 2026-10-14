"""Fumaça das Saídas contra o Mongo de verdade (o do docker-compose).

Não é coletado pelo pytest (não começa com `test_`). Roda à mão:

    docker compose up -d
    cd backend && python tests/fumaca_saidas.py

O que só o banco real responde, e por isso este arquivo existe ao lado da
suíte com Mongo falso:

  - **A corrida pelos últimos lugares.** O Mongo falso nunca suspende a
    execução, então duas reservas "simultâneas" rodam uma inteira depois da
    outra e a corrida jamais acontece. Aqui 40 reservas são disparadas ao mesmo
    tempo numa saída de 10 lugares: tem de entrar exatamente 10 pessoas.
  - **O índice único de `saidas`.** É ele que faz várias conversas criando o
    contador da mesma saída terminarem com um contador só. Sem a unicidade
    nasceriam vários, cada um com parte das reservas, e a saída aceitaria gente
    além da capacidade.
  - **O `explain` das consultas quentes**: ocupação da janela e varredura de
    solicitações vencidas usam índice.

Usa um banco descartável (`turismo_fumaca`), apagado no fim.
"""
import asyncio
import os
import sys
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
os.environ["DB_NAME"] = "turismo_fumaca"

from fastapi import HTTPException  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

import agenda  # noqa: E402
import saidas  # noqa: E402

TENANT = "fumaca"
ok = 0


def check(nome, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  OK  {nome}")
    else:
        print(f"  FALHOU  {nome}  -> {extra}")
        raise SystemExit(1)


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    try:
        await client.admin.command("ping")
    except Exception as e:
        print(f"Mongo fora do ar ({os.environ['MONGO_URL']}): {e}. Rode `docker compose up -d`.")
        raise SystemExit(2)
    db = client["turismo_fumaca"]
    await client.drop_database("turismo_fumaca")
    try:
        import server
        for colecao, chaves, opcoes in server.INDEXES:
            await db[colecao].create_index(chaves, **opcoes)
        check("os índices da tabela INDEXES são aceitos pelo Mongo real", True)

        config = await agenda.configuracao(db, TENANT)
        tz = agenda.fuso_de(config)
        dia = datetime.now(tz).date() + timedelta(days=10)
        passeio = {"id": "p-lagoa", "tenant_id": TENANT, "nome": "Lagoa Azul", "ativo": True,
                   "capacidade": 10, "preco": 150.0, "duracao_min": 480,
                   "grade_saidas": [[{"hora": "08:00", "capacidade": None}]] * 7,
                   "excecoes_saidas": []}
        await db.services.insert_one(dict(passeio))
        inicio = agenda._iso(datetime.combine(dia, time(8, 0), tzinfo=tz))

        for i in range(40):
            await db.contacts.insert_one({"id": f"c{i}", "tenant_id": TENANT,
                                          "nome": f"Turista {i}",
                                          "numero_whatsapp": f"55119000000{i:02d}"})

        async def tenta(i):
            contato = {"id": f"c{i}", "nome": f"Turista {i}"}
            try:
                await saidas.reservar(db, TENANT, passeio=passeio, contato=contato,
                                      inicio=inicio, adultos=1, config=config, origem="bot")
                return True
            except HTTPException as e:
                assert e.status_code == 409, e.detail
                return False

        resultados = await asyncio.gather(*(tenta(i) for i in range(40)))
        check("40 reservas simultâneas numa saída de 10: entram exatamente 10",
              sum(resultados) == 10, sum(resultados))
        n_contadores = await db.saidas.count_documents({"tenant_id": TENANT})
        check("e existe um contador só para a saída (índice único)", n_contadores == 1, n_contadores)
        c = await db.saidas.find_one({"tenant_id": TENANT})
        reservas = await db.appointments.count_documents({"tenant_id": TENANT,
                                                          "status": {"$in": list(agenda.OCUPAM)}})
        check("contador = reservas gravadas", c["reservados"] == reservas == 10,
              (c["reservados"], reservas))

        # Grupos de tamanhos diferentes disputando o resto de outra saída.
        dia2 = dia + timedelta(days=1)
        inicio2 = agenda._iso(datetime.combine(dia2, time(8, 0), tzinfo=tz))

        async def grupo(i, n):
            try:
                await saidas.reservar(db, TENANT, passeio=passeio,
                                      contato={"id": f"c{i}", "nome": "x"}, inicio=inicio2,
                                      adultos=n, config=config, origem="manual")
                return n
            except HTTPException:
                return 0

        entrou = sum(await asyncio.gather(*(grupo(i, 1 + i % 4) for i in range(20))))
        c2 = await db.saidas.find_one({"tenant_id": TENANT, "inicio": inicio2})
        check("grupos variados simultâneos nunca passam da capacidade",
              entrou == c2["reservados"] and entrou <= 10, (entrou, c2["reservados"]))

        # Cancelamentos simultâneos da mesma reserva devolvem o lugar uma vez só.
        alvo = await db.appointments.find_one({"tenant_id": TENANT, "inicio": inicio,
                                               "status": "agendado"})
        await asyncio.gather(*(saidas.cancelar(db, TENANT, alvo["id"]) for _ in range(5)),
                             return_exceptions=True)
        c = await db.saidas.find_one({"tenant_id": TENANT, "inicio": inicio})
        check("cinco cancelamentos simultâneos devolvem o lugar uma vez",
              c["reservados"] == 9, c["reservados"])

        # Índices nas consultas quentes.
        plano = await db.saidas.find({"tenant_id": TENANT, "service_id": {"$in": ["p-lagoa"]},
                                      "inicio": {"$gte": inicio, "$lt": inicio2}}).explain()
        texto = str(plano.get("queryPlanner", {}).get("winningPlan"))
        check("a ocupação da janela usa índice", "IXSCAN" in texto, texto[:200])
        plano = await db.appointments.find({"status": "agendado",
                                            "expira_em": {"$ne": None, "$lte": inicio}}).explain()
        texto = str(plano.get("queryPlanner", {}).get("winningPlan"))
        check("a varredura de solicitações vencidas usa índice", "IXSCAN" in texto, texto[:200])

        # Fase 2: cancelar a saída inteira enquanto o bot reserva nela. A
        # garantia é a ordem (marca cancelada, depois cancela as reservas) mais
        # o `cancelada != True` no filtro do `$inc`: no fim, nenhuma reserva
        # pode ter sobrado viva dentro da saída cancelada.
        dia3 = dia + timedelta(days=2)
        inicio3 = agenda._iso(datetime.combine(dia3, time(8, 0), tzinfo=tz))
        for i in range(3):
            await saidas.reservar(db, TENANT, passeio=passeio, contato={"id": f"c{i}", "nome": "x"},
                                  inicio=inicio3, adultos=1, config=config, origem="manual")

        async def reserva_tardia(i):
            try:
                await saidas.reservar(db, TENANT, passeio=passeio,
                                      contato={"id": f"c{i}", "nome": "x"}, inicio=inicio3,
                                      adultos=1, config=config, origem="bot")
            except HTTPException:
                pass

        # A janela entre o `$inc` e a gravação da reserva é de milissegundos, e
        # sem ajuda a corrida quase nunca acontece: o teste passaria com o furo
        # aberto. O atraso abaixo é a rede lenta entre as duas operações, e é
        # ele que torna o teste capaz de falhar.
        ocupa_original = saidas._ocupa

        async def ocupa_lento(*a, **kw):
            r = await ocupa_original(*a, **kw)
            await asyncio.sleep(0.05)
            return r

        saidas._ocupa = ocupa_lento
        try:
            async def cancela_no_meio():
                await asyncio.sleep(0.02)  # depois dos `$inc`, antes das gravações
                await saidas.cancelar_saida(db, TENANT, passeio["id"], inicio3,
                                            motivo="Chuva forte")

            await asyncio.gather(cancela_no_meio(), *(reserva_tardia(i) for i in range(10, 25)))
        finally:
            saidas._ocupa = ocupa_original
        vivas = await db.appointments.count_documents(
            {"tenant_id": TENANT, "inicio": inicio3, "status": {"$in": list(agenda.OCUPAM)}})
        c3 = await db.saidas.find_one({"tenant_id": TENANT, "inicio": inicio3})
        # A janela que o teste procura: uma reserva que fez o `$inc` antes da
        # marcação, mas gravou o documento depois de o cancelamento ler as
        # reservas. Sem a conferência pós-gravação de `reservar`, ela fica viva.
        check("cancelar a saída com o bot reservando ao mesmo tempo: nenhuma reserva sobra viva",
              vivas == 0 and c3["reservados"] == 0 and c3["cancelada"],
              (vivas, c3["reservados"]))
        check("e nenhuma reserva nova entra depois da marcação",
              await saidas._ocupa(db, TENANT, passeio["id"], inicio3, 1, 99) is None)

        n = await saidas.expirar_solicitacoes(db, agenda._agora() + timedelta(days=3))
        c = await db.saidas.find_one({"tenant_id": TENANT, "inicio": inicio})
        check("a varredura expira as solicitações e zera a saída",
              n == 9 and c["reservados"] == 0, (n, c["reservados"]))
    finally:
        await client.drop_database("turismo_fumaca")
        client.close()
    print(f"\n{ok} verificações OK contra o Mongo real")


if __name__ == "__main__":
    asyncio.run(main())
