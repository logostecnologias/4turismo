"""O agente de IA finalizando o atendimento sozinho.

Até aqui o agente só mexia no status para um lado: transferir (`[HUMANO]`)
leva a conversa para "aguardando". Quando ele resolvia o assunto, a conversa
ficava em "bot" para sempre, e a coluna do robô no Kanban virava depósito de
conversa encerrada que ninguém encerrou.

Decisões:

  - **Só finalizar, não os outros estados.** "Aguardando" já sai pela
    transferência, que avisa o contato e escolhe o departamento; uma segunda
    porta para o mesmo estado divergiria da primeira. "Em atendimento" é uma
    pessoa assumindo, e o agente não é uma pessoa.

  - **A ferramenta muda a sessão em memória e no banco.** O `session` que
    chega aqui é o dicionário vivo de `handle_incoming_message`, e na conversa
    nova ele é gravado inteiro no fim: escrever só no banco seria sobrescrito
    logo depois. Na conversa existente o caminho do agente grava só os
    contadores, e escrever só no dicionário se perderia. Os dois juntos valem
    para os dois caminhos.

  - **Finalizar não cala o agente.** A despedida que ele escreve sai
    normalmente. A mensagem seguinte do contato encontra a conversa encerrada e
    abre outra, como depois de um nó Fim ou de um atendente clicar em Finalizar.

  - **Desligado por padrão**, como a agenda: é o agente escrevendo no sistema,
    e isso é decisão do cliente.
"""
from datetime import datetime, timezone

import ai_client

FERRAMENTAS = [
    ai_client.Ferramenta(
        nome="finalizar_atendimento",
        descricao=(
            "Encerra este atendimento no sistema. Chame quando o assunto estiver "
            "resolvido e o contato se despedir, agradecer ou confirmar que não "
            "precisa de mais nada. Depois de chamar, responda com uma despedida curta. "
            "Não chame se ainda houver pergunta sem resposta."
        ),
        parametros={
            "type": "object",
            "properties": {
                "resumo": {"type": "string",
                           "description": "Uma frase dizendo o que foi resolvido, para a equipe ler depois."},
            },
            "required": ["resumo"],
        },
    ),
]

INSTRUCOES = (
    "Encerrar atendimento: quando o assunto estiver resolvido e o contato se "
    "despedir, agradecer ou disser que era só isso, chame finalizar_atendimento "
    "com um resumo de uma frase e responda com uma despedida curta. Nunca encerre "
    "com pergunta pendente, no meio de um agendamento ou quando o contato só "
    "mandou um \"ok\" no meio da conversa."
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def executor(db, tenant_id: str, session: dict, simulado: bool = False):
    async def finalizar(args: dict) -> str:
        resumo = str(args.get("resumo") or "").strip()[:300]
        if simulado or not session:
            return "Teste: o atendimento seria finalizado agora. Despeça-se do contato."
        from flow_runtime import log_message  # tardio: flow_runtime importa ai_agents

        agora = _now()
        mudanca = {"status": "finalizada", "status_atendimento": "finalizado",
                   "finalizado_em": agora}
        session.update(mudanca)
        await db.conversation_sessions.update_one(
            {"id": session["id"], "tenant_id": tenant_id},
            {"$set": {**mudanca, "updated_at": agora}})
        await log_message(
            db, session["id"], tenant_id, "sistema",
            "Agente de IA finalizou o atendimento" + (f": {resumo}" if resumo else ""),
            mtype="system")
        return "Atendimento finalizado. Responda agora com uma despedida curta."

    async def executar(nome: str, argumentos: dict) -> str:
        if nome != "finalizar_atendimento":
            return f"ERRO: ferramenta desconhecida ({nome})."
        return await finalizar(argumentos or {})

    return executar
