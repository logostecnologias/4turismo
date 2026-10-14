"""Flow execution engine (used for simulated test chat).

Advances the conversation through connected nodes until it reaches
a node that must wait for user input (Menu) or the flow ends.
Persists ephemeral test sessions in-memory (module-level dict) — this
is fine because it's only for the test chat inside the editor.
"""
import json
import re
import uuid
import asyncio
import httpx
from datetime import datetime, timezone

import agenda_flow
import net_guard
import whatsapp_client as whatsapp

_test_sessions: dict[str, dict] = {}


# Como o nó de Menu se apresenta no WhatsApp. `texto` é o formato original
# (lista numerada, o contato digita o número) e continua sendo o padrão: nó de
# Menu sem `formato` gravado é o de um fluxo publicado antes desta versão, e ele
# tem que continuar se comportando igual.
FORMATO_TEXTO = "texto"
FORMATO_BOTOES = "botoes"
FORMATO_LISTA = "lista"
FORMATOS = (FORMATO_TEXTO, FORMATO_BOTOES, FORMATO_LISTA)


def validar_menu(data: dict) -> list[str]:
    """Problemas que impediriam a mensagem interativa de chegar ao contato.

    O WhatsApp não recusa com erro: ele descarta a mensagem em silêncio. E um
    Menu descartado é pior que um menu feio, porque o fluxo fica esperando uma
    resposta que o contato nunca foi convidado a dar. Por isso a lista é
    checada em dois lugares — na publicação do fluxo (routes/flows.py, que
    recusa) e no envio (flow_runtime, que degrada para texto numerado em vez de
    perder a pergunta).

    Formato `texto` nunca tem problema: é só uma mensagem de texto.
    """
    formato = data.get("formato") or FORMATO_TEXTO
    if formato == FORMATO_TEXTO:
        return []

    opcoes = data.get("options") or []
    problemas: list[str] = []
    if not opcoes:
        problemas.append("menu interativo sem nenhuma opção")

    def longos(limite, campo):
        return [(o.get(campo) or "") for o in opcoes if len(o.get(campo) or "") > limite]

    if formato == FORMATO_BOTOES:
        if len(opcoes) > whatsapp.MAX_BOTOES:
            problemas.append(
                f"botões aceitam no máximo {whatsapp.MAX_BOTOES} opções "
                f"(este menu tem {len(opcoes)}) — use o formato Lista")
        passou = longos(whatsapp.LIMITE_ROTULO_BOTAO, "label")
        if passou:
            problemas.append(
                f"rótulo de botão passa de {whatsapp.LIMITE_ROTULO_BOTAO} "
                f"caracteres: {', '.join(passou)}")
    elif formato == FORMATO_LISTA:
        if len(opcoes) > whatsapp.MAX_ITENS_LISTA:
            problemas.append(
                f"uma lista aceita no máximo {whatsapp.MAX_ITENS_LISTA} opções "
                f"(este menu tem {len(opcoes)})")
        if not (data.get("botao_texto") or "").strip():
            problemas.append("a lista precisa do texto do botão que abre as opções")
        passou = longos(whatsapp.LIMITE_ROTULO_ITEM, "label")
        if passou:
            problemas.append(
                f"opção de lista passa de {whatsapp.LIMITE_ROTULO_ITEM} "
                f"caracteres: {', '.join(passou)}")
    else:
        problemas.append(f"formato de menu desconhecido: {formato}")
    return problemas


def ids_das_opcoes(opcoes: list[dict]) -> list[str]:
    """O id de cada opção — que é também o `sourceHandle` da saída dela no
    editor, e por isso é o que vai como id do botão/linha no WhatsApp."""
    return [o.get("id") or f"opt_{i}" for i, o in enumerate(opcoes)]


def resolver_escolha_menu(opcoes: list[dict], texto: str,
                          escolha_id: str | None = None) -> str | None:
    """Qual saída do Menu a resposta do contato escolheu, ou None se nenhuma.

    Ordem: o id que veio do botão/lista, o número digitado, o rótulo digitado.

    O id vem primeiro porque é o único confiável. O WhatsApp corta o rótulo do
    botão em 20 caracteres, então o texto que volta de um botão longo nunca é
    igual ao rótulo configurado — casar por rótulo falharia justamente nos
    menus mais descritivos. O número e o rótulo continuam valendo: o contato
    pode digitar em vez de tocar, e é assim que o formato `texto` funciona.

    Um id que não está mais entre as opções (botão de um menu anterior da
    conversa, ou opção apagada depois do envio) conta como resposta inválida,
    não como acerto — é o caminho "invalid" do nó que trata isso.
    """
    ids = ids_das_opcoes(opcoes)
    if escolha_id and escolha_id in ids:
        return escolha_id

    m = (texto or "").strip()
    if m.isdigit():
        i = int(m) - 1
        if 0 <= i < len(ids):
            return ids[i]
    if m:
        for i, o in enumerate(opcoes):
            if (o.get("label") or "").strip().lower() == m.lower():
                return ids[i]
    return None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _interp(text: str, variables: dict) -> str:
    if not isinstance(text, str):
        return text
    def repl(m):
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{([a-zA-Z0-9_]+)\}", repl, text)


def _find_node(nodes, node_id):
    for n in nodes:
        if n["id"] == node_id:
            return n
    return None


def _find_next(edges, source_id, handle=None):
    for e in edges:
        if e["source"] == source_id:
            if handle is None or e.get("sourceHandle") == handle:
                return e["target"]
    return None


def _find_start(nodes):
    for n in nodes:
        if n["type"] == "start":
            return n
    return None


async def _process_node(node, flow, session, outputs):
    """Process a single node and return next_node_id or None (waiting)."""
    ntype = node["type"]
    data = node.get("data", {})
    edges = flow["edges"]
    variables = session["variables"]

    if ntype == "start":
        return _find_next(edges, node["id"])

    if ntype == "text":
        text = _interp(data.get("text", ""), variables)
        outputs.append({"type": "text", "content": text})
        return _find_next(edges, node["id"])

    if ntype == "menu":
        question = _interp(data.get("text", ""), variables)
        options = data.get("options", [])
        # Rótulo de opção não passa por _interp de propósito: é ele que o
        # resolvedor coteja quando o contato digita em vez de tocar, e
        # interpolar aqui faria o texto exibido divergir do texto casado.
        outputs.append({
            "type": "menu",
            "content": question,
            "formato": data.get("formato") or FORMATO_TEXTO,
            "titulo": _interp(data.get("titulo", ""), variables),
            "rodape": _interp(data.get("rodape", ""), variables),
            "botao_texto": _interp(data.get("botao_texto", ""), variables),
            "titulo_secao": _interp(data.get("titulo_secao", ""), variables),
            # `options` (só rótulos) continua no output porque é o que o
            # formato texto usa e o que já era consumido; `itens` carrega o id,
            # que é o que a mensagem interativa precisa.
            "options": [o.get("label", "") for o in options],
            "itens": [{"id": oid, "label": o.get("label", ""),
                       "descricao": o.get("descricao", "")}
                      for oid, o in zip(ids_das_opcoes(options), options)],
            # O formato pedido vai junto com o que impede de usá-lo: é quem
            # envia (e o chat de teste) que decide degradar, para o problema
            # aparecer onde dá para consertar em vez de virar mensagem perdida.
            "problemas": validar_menu(data),
        })
        # Os rótulos vão guardados junto do `awaiting`, indexados pelo id.
        #
        # Não é cache: é a única fonte confiável do que o contato tocou. A
        # resposta de um botão chega do WhatsApp com o id e, na prática, **sem
        # rótulo nenhum** — e o que sobrava para gravar no histórico era o id
        # ("t2_dur"), um identificador interno no lugar da frase que o contato
        # leu na tela. Aqui o rótulo é o do próprio menu que fez a pergunta —
        # a única fonte que existe, já que o payload não traz nenhuma.
        session["awaiting"] = {
            "node_id": node["id"],
            "rotulos": {oid: o.get("label", "")
                        for oid, o in zip(ids_das_opcoes(options), options)},
        }
        return None  # wait for user input

    if ntype == "schedule":
        # O nó de Agendamento conduz quatro perguntas sozinho (serviço,
        # profissional, dia, hora) e guarda o andamento em `session["awaiting"]`,
        # do mesmo jeito que o Menu: é o que faz a resposta do contato voltar
        # para dentro dele. Devolve None enquanto está perguntando.
        saida = await agenda_flow.avancar(node, session, outputs)
        if saida is None:
            return None
        return _find_next(edges, node["id"], saida)

    if ntype == "save_variable":
        name = data.get("variable_name") or "var"
        source = data.get("source", "last_response")
        if source == "fixed":
            value = data.get("value", "")
        else:
            value = session.get("last_user_message", "")
        variables[name] = value
        return _find_next(edges, node["id"])

    if ntype == "condition":
        var_name = data.get("variable_name", "")
        op = data.get("operator", "equals")
        cmp_val = data.get("value", "")
        actual = str(variables.get(var_name, ""))
        result = False
        if op == "equals":
            result = actual == str(cmp_val)
        elif op == "contains":
            result = str(cmp_val) in actual
        elif op == "greater_than":
            try:
                result = float(actual) > float(cmp_val)
            except Exception:
                result = False
        elif op == "less_than":
            try:
                result = float(actual) < float(cmp_val)
            except Exception:
                result = False
        return _find_next(edges, node["id"], "true" if result else "false")

    if ntype == "media":
        outputs.append({
            "type": "media",
            "media_type": data.get("media_type", "image"),
            "url": data.get("url", ""),
            "caption": _interp(data.get("caption", ""), variables),
        })
        return _find_next(edges, node["id"])

    if ntype == "delay":
        seconds = int(data.get("seconds", 1))
        # O envio real espera de verdade (flow_runtime._send_outputs), com o mesmo
        # teto de 60s do editor: a espera segura a conversa.
        outputs.append({"type": "delay", "seconds": max(0, min(seconds, 60))})
        # in test mode we do not actually sleep for long
        await asyncio.sleep(min(seconds, 1) * 0.2)
        return _find_next(edges, node["id"])

    if ntype == "webhook":
        url = _interp(data.get("url", ""), variables)
        method = (data.get("method") or "GET").upper()
        # Passa por net_guard: a URL vem do cliente, e o servidor é quem faz a
        # requisição. Sem isto, um destino como http://127.0.0.1:8090 ou o
        # endpoint de metadados da nuvem seria lido e ecoado ao contato (ZZ-04).
        # O corpo só é enviado quando não é GET, como antes.
        corpo = None if method == "GET" else data.get("body", {})
        try:
            status, ctype, texto = await net_guard.fetch_text(
                url, method, json_body=corpo, max_bytes=256 * 1024, timeout=5.0
            )
            if "json" in ctype:
                try:
                    variables["webhook_response"] = json.loads(texto)
                except ValueError:
                    variables["webhook_response"] = texto
            else:
                variables["webhook_response"] = texto
            outputs.append({"type": "system", "content": f"Webhook {method} {status}"})
            return _find_next(edges, node["id"], "success")
        except net_guard.SSRFError as e:
            # Destino recusado pela política de saída: mensagem clara, sem expor
            # rede interna, e o fluxo segue pela saída de erro.
            outputs.append({"type": "system", "content": f"Webhook recusado: {e}"})
            return _find_next(edges, node["id"], "error")
        except Exception as e:
            outputs.append({"type": "system", "content": f"Erro no webhook: {e}"})
            return _find_next(edges, node["id"], "error")

    if ntype == "human_handoff":
        outputs.append({"type": "system", "content": "Conversa transferida para atendimento humano"})
        session["status"] = "aguardando_humano"
        # Sem departamento configurado no nó, mantém o que a conversa já tem
        # (o padrão da conexão, definido na criação da sessão).
        if data.get("department_id"):
            session["departamento_id"] = data["department_id"]
        return None

    if ntype == "transfer_bot":
        target = data.get("target_bot_id")
        outputs.append({
            "type": "transfer_bot",
            "target_bot_id": target,
            "content": f"Transferindo para outro bot ({target})",
        })
        # Signal the runtime to swap bots. The engine stops here; the runtime
        # will pick up 'transfer_target' and re-run from the target bot's Start.
        session["transfer_target"] = target
        session["status"] = "transferindo"
        return None

    if ntype == "end":
        outputs.append({"type": "system", "content": "Fluxo encerrado"})
        session["status"] = "finalizada"
        return None

    return _find_next(edges, node["id"])


async def _run_from(node_id, flow, session, outputs, guard=0):
    while node_id and guard < 50:
        node = _find_node(flow["nodes"], node_id)
        if node is None:
            break
        next_id = await _process_node(node, flow, session, outputs)
        session["node_atual_id"] = node["id"]
        if next_id is None:
            break
        node_id = next_id
        guard += 1


async def run_test_message(flow, session_id, message, start, escolha_id=None):
    outputs: list[dict] = []
    if start or not session_id or session_id not in _test_sessions:
        session_id = str(uuid.uuid4())
        start_node = _find_start(flow["nodes"])
        if not start_node:
            return {"session_id": session_id, "outputs": [{"type": "system", "content": "Fluxo sem nó de Início"}], "status": "erro"}
        session = {
            "id": session_id,
            "flow_id": flow["id"],
            "node_atual_id": start_node["id"],
            "variables": {},
            "status": "ativa",
            "awaiting": None,
            "last_user_message": "",
            # O nó de Agendamento consulta a agenda **de verdade** desta empresa:
            # é o que faz o teste provar que a grade e os serviços estão certos.
            # `simulado` é o que impede a outra metade: gravar o agendamento, ,
            # porque testar um fluxo não pode ocupar o horário de um cliente.
            "tenant_id": flow.get("tenant_id"),
            "simulado": True,
        }
        _test_sessions[session_id] = session
        await _run_from(start_node["id"], flow, session, outputs)
        return {"session_id": session_id, "outputs": outputs, "status": session["status"], "variables": session["variables"]}

    session = _test_sessions[session_id]
    session["last_user_message"] = message or ""

    awaiting = session.get("awaiting")
    if awaiting and awaiting.get("node_id"):
        # Determine chosen handle from Menu
        menu_node = _find_node(flow["nodes"], awaiting["node_id"])
        if menu_node and menu_node["type"] == "schedule":
            # O nó de Agendamento resolve a própria resposta: ele tem várias
            # etapas e só devolve uma saída quando termina (ou desiste).
            saida = await agenda_flow.avancar(menu_node, session, outputs,
                                              resposta=message, escolha_id=escolha_id)
            if saida is not None:
                next_id = _find_next(flow["edges"], menu_node["id"], saida)
                if next_id:
                    await _run_from(next_id, flow, session, outputs)
        elif menu_node and menu_node["type"] == "menu":
            options = menu_node.get("data", {}).get("options", [])
            # Mesmo resolvedor do caminho real (flow_runtime): o chat de teste
            # existe para provar o fluxo publicado, e duas regras de
            # correspondência diferentes fariam ele provar outra coisa.
            handle = resolver_escolha_menu(options, message, escolha_id)
            if handle is None:
                # invalid response -> fallback handle
                next_id = _find_next(flow["edges"], menu_node["id"], "invalid")
                if next_id is None:
                    outputs.append({"type": "text", "content": "Resposta inválida. Tente novamente."})
                    return {"session_id": session_id, "outputs": outputs, "status": "ativa", "variables": session["variables"]}
                session["awaiting"] = None
                await _run_from(next_id, flow, session, outputs)
            else:
                next_id = _find_next(flow["edges"], menu_node["id"], handle)
                session["awaiting"] = None
                if next_id:
                    await _run_from(next_id, flow, session, outputs)
        else:
            session["awaiting"] = None

    return {
        "session_id": session_id,
        "outputs": outputs,
        "status": session["status"],
        "variables": session["variables"],
    }
