import { useEffect, useState } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import Modal from "@/components/ui/Modal";
import {
  WhatsappLogo, CheckCircle, XCircle, ArrowClockwise, Plus, Trash, Key,
  WarningCircle, Eye, EyeSlash,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";

// O vínculo bot ↔ conexão é escolhido no bot (tela de Robôs / editor de fluxo).
// Aqui os bots só servem pra saber se a conexão tem quem atenda.
//
// Mesmas condições que o motor aplica em _load_bot_flow: bot pausado ou com
// fluxo não publicado simplesmente não roda.
const usavel = (b) => b && b.status === "ativo" && b.publicado;

// Os dois canais. Espelham as constantes do backend (`whatsapp_client.py`), e o
// `cloud_api` é o padrão aqui pelo mesmo motivo de lá: conexão criada antes
// desta versão não tem o campo, e ler a ausência como "não oficial" mudaria o
// canal de conexões que já estão no ar.
const CANAL_CLOUD = "cloud_api";
const CANAL_WUZAPI = "wuzapi";
const canalDe = (conn) => (conn?.canal === CANAL_WUZAPI ? CANAL_WUZAPI : CANAL_CLOUD);

export default function WhatsAppPage() {
  const [connections, setConnections] = useState([]);
  const [depts, setDepts] = useState([]);
  const [bots, setBots] = useState([]);
  const [creating, setCreating] = useState(false);
  const [newForm, setNewForm] = useState({
    nome: "", departamento_padrao_id: "", canal: CANAL_CLOUD,
  });
  const [busyId, setBusyId] = useState(null);
  const [editandoId, setEditandoId] = useState(null);

  const load = () => api.get("/whatsapp/connections").then((r) => setConnections(r.data));
  useEffect(() => {
    load();
    api.get("/departments").then((r) => setDepts(r.data)).catch(() => {});
    api.get("/bots").then((r) => setBots(r.data)).catch(() => {});
  }, []);

  // A trava de clique repetido vem do Modal, que embrulha o submit.
  const createConnection = async (e) => {
    e.preventDefault();
    if (!newForm.nome.trim()) return;
    try {
      const { data } = await api.post("/whatsapp/connections", {
        nome: newForm.nome,
        departamento_padrao_id: newForm.departamento_padrao_id || null,
        canal: newForm.canal,
      });
      setConnections((cs) => [...cs, data]);
      setNewForm({ nome: "", departamento_padrao_id: "", canal: CANAL_CLOUD });
      setCreating(false);
      // No canal não oficial a instância já foi criada pelo backend, então não
      // há formulário a abrir: o próximo passo é o QR. Abrir o de credencial
      // aqui faria o cliente encarar campos técnicos que ele não precisa
      // preencher — foi o que essa tela fazia de errado.
      setEditandoId(newForm.canal === CANAL_WUZAPI ? null : data.id);
      // O aviso vem do webhook da instância, que é criada aqui junto com a
      // conexão: ela existe e envia, mas não recebe. Some no toast de sucesso
      // seria o pior desfecho — é exatamente a falha que não aparece em
      // lugar nenhum depois (o cartão pareia, envia, e nada entra).
      if (data.aviso) toast.warning(data.aviso, { duration: 12000 });
      else toast.success(newForm.canal === CANAL_WUZAPI
        ? "Conexão criada. Agora clique em Conectar e leia o QR no celular."
        : "Conexão criada. Agora cadastre o número da Cloud API.");
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail) || "Erro ao criar conexão");
    }
  };

  const salvarCredenciais = async (id, corpo) => {
    setBusyId(id);
    try {
      const { data } = await api.post(`/whatsapp/connections/${id}/credenciais`, corpo);
      setConnections((cs) => cs.map((c) => (c.id === id ? data : c)));
      setEditandoId(null);
      if (data.aviso) toast.warning(data.aviso, { duration: 12000 });
      else toast.success("Número validado e webhook assinado.");
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail) || "Não foi possível validar",
        { duration: 10000 });
    } finally { setBusyId(null); }
  };

  const verificar = async (id) => {
    setBusyId(id);
    try {
      const { data } = await api.post(`/whatsapp/connections/${id}/verificar`);
      setConnections((cs) => cs.map((c) => (c.id === id ? data : c)));
      toast.success("Conexão verificada");
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail) || "Falha ao verificar");
      load();
    } finally { setBusyId(null); }
  };

  const salvarWuzapi = async (id, corpo) => {
    setBusyId(id);
    try {
      const { data } = await api.post(`/whatsapp/connections/${id}/wuzapi`, corpo);
      setConnections((cs) => cs.map((c) => (c.id === id ? data : c)));
      setEditandoId(null);
      if (data.aviso) toast.warning(data.aviso, { duration: 12000 });
      else toast.success("Instância validada e webhook configurado.");
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail) || "Não foi possível validar",
        { duration: 10000 });
    } finally { setBusyId(null); }
  };

  const acaoWuzapi = async (id, caminho, sucesso) => {
    setBusyId(id);
    try {
      await api.post(`/whatsapp/connections/${id}/wuzapi/${caminho}`);
      await load();
      if (sucesso) toast.success(sucesso);
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail) || "Falha na operação");
    } finally { setBusyId(null); }
  };

  const removeConnection = async (id) => {
    if (!(await confirmToast("Excluir esta conexão? O número deixa de receber e enviar por aqui.", { confirmLabel: "Excluir" }))) return;
    setBusyId(id);
    try {
      await api.delete(`/whatsapp/connections/${id}`);
      setConnections((cs) => cs.filter((c) => c.id !== id));
      toast.success("Conexão excluída");
    } finally { setBusyId(null); }
  };

  const updateField = async (id, patch) => {
    const { data } = await api.patch(`/whatsapp/connections/${id}`, patch);
    setConnections((cs) => cs.map((c) => (c.id === id ? data : c)));
    toast.success("Conexão atualizada");
  };

  return (
    <div className="zz-page" data-testid="whatsapp-page">
      <div className="flex items-end justify-between mb-8 flex-wrap gap-4">
        <div>
          <h1 className="font-heading text-4xl font-semibold">WhatsApp</h1>
          <p className="text-ink-2 mt-2 max-w-2xl">
            Cada conexão é um número de WhatsApp com seu próprio departamento padrão, pela
            <strong> API oficial da Meta</strong> ou pela <strong>ponte não oficial</strong>, que
            pareia por QR. Os fluxos são vinculados na tela de Fluxos automáticos.
          </p>
        </div>
        <button data-testid="new-connection-btn" onClick={() => setCreating((v) => !v)} className="zz-btn">
          <Plus size={16} weight="regular" /> Nova conexão
        </button>
      </div>

      {creating && (
        <Modal titulo="Nova conexão"
          descricao="Um número de WhatsApp com seu próprio departamento padrão."
          onFechar={() => setCreating(false)} onSalvar={createConnection}
          salvarLabel="Criar conexão" salvandoLabel="Criando…"
          testId="new-connection-modal"
          corpoClasse="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Nome da conexão</label>
            <input autoFocus required data-testid="new-connection-name" className="zz-input"
              value={newForm.nome} onChange={(e) => setNewForm({ ...newForm, nome: e.target.value })}
              placeholder="Ex: Vendas, Suporte, Loja 2" />
          </div>
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Departamento padrão</label>
            <select className="zz-input" value={newForm.departamento_padrao_id}
              onChange={(e) => setNewForm({ ...newForm, departamento_padrao_id: e.target.value })}>
              <option value="">— nenhum —</option>
              {depts.map((d) => <option key={d.id} value={d.id}>{d.nome}</option>)}
            </select>
          </div>
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Tipo de conexão</label>
            <select className="zz-input" data-testid="new-connection-canal" value={newForm.canal}
              onChange={(e) => setNewForm({ ...newForm, canal: e.target.value })}>
              <option value={CANAL_CLOUD}>API oficial (Cloud API da Meta)</option>
              <option value={CANAL_WUZAPI}>Não oficial (QR code)</option>
            </select>
          </div>

          {/* O aviso fica aqui, onde a escolha é feita, e não escondido na
              documentação: é o único momento em que alguém decide entre um
              número protegido e um número sob risco de bloqueio. */}
          {newForm.canal === CANAL_WUZAPI && (
            <div className="sm:col-span-2 flex gap-3 items-start border border-warning bg-warning-soft rounded-lg p-3.5">
              <WarningCircle size={18} weight="regular" className="text-warning shrink-0 mt-0.5" />
              <div className="text-xs text-ink-2 space-y-1.5">
                <div>
                  <strong className="text-ink">A conexão não oficial pareia por QR, como o WhatsApp Web,
                  e não é homologada pela Meta.</strong> O número fica sujeito a bloqueio, e o número é da
                  agência.
                </div>
                <div>
                  Em troca ela não tem a janela de 24 horas nem exige modelo aprovado: dá para escrever
                  a qualquer momento, inclusive em lembrete de saída. O que ela não
                  entrega é <strong>confirmação de entrega</strong> — não existe "entregue" nem "lida"
                  nesse canal.
                </div>
              </div>
            </div>
          )}
        </Modal>
      )}

      {connections.length === 0 && !creating && (
        <div className="border border-dashed border-line rounded-xl bg-surface p-16 text-center">
          <div className="font-heading text-2xl font-semibold mb-2">Nenhuma conexão ainda.</div>
          <div className="text-sm text-ink-2">Crie a primeira conexão de WhatsApp da agência.</div>
        </div>
      )}

      <div className="space-y-4">
        {connections.map((conn) => (
          <ConnectionCard
            key={conn.id}
            conn={conn}
            depts={depts}
            bots={bots}
            busy={busyId === conn.id}
            editando={editandoId === conn.id}
            onEditar={() => setEditandoId(editandoId === conn.id ? null : conn.id)}
            onSalvarCredenciais={(corpo) => salvarCredenciais(conn.id, corpo)}
            onVerificar={() => verificar(conn.id)}
            onRemove={() => removeConnection(conn.id)}
            onUpdateField={(patch) => updateField(conn.id, patch)}
            onSalvarWuzapi={(corpo) => salvarWuzapi(conn.id, corpo)}
            onConectar={() => acaoWuzapi(conn.id, "conectar")}
            onDesconectar={() => acaoWuzapi(conn.id, "desconectar", "Conexão encerrada. O pareamento continua válido.")}
            onSair={() => acaoWuzapi(conn.id, "sair", "Aparelho desvinculado. O próximo uso pede QR novo.")}
            onRecarregar={load}
          />
        ))}
      </div>
    </div>
  );
}

function ConnectionCard({ conn, depts, bots, busy, editando, onEditar, onSalvarCredenciais,
                          onVerificar, onRemove, onUpdateField,
                          onSalvarWuzapi, onConectar, onDesconectar, onSair, onRecarregar }) {
  const botsDaConexao = bots.filter((b) => b.connection_id === conn.id);
  // Espelha _pick_bot_for_new_session: só bot desta conexão, ativo e publicado.
  const temBotPrimeiraMensagem = botsDaConexao.some(
    (b) => usavel(b) && (b.trigger || "first_message") === "first_message"
  );
  const canal = canalDe(conn);
  const naoOficial = canal === CANAL_WUZAPI;
  // "Cadastrado" quer dizer coisas diferentes em cada canal: na Cloud API é
  // ter um número da Meta; no não oficial é ter a instância apontada. Sem essa
  // distinção o cartão do canal novo apareceria sempre como não configurado.
  const cadastrado = naoOficial ? Boolean(conn.wuzapi_id) : Boolean(conn.phone_number_id);

  return (
    <div className="border border-line-strong bg-surface p-6 zz-panel-hard rounded-lg" data-testid={`connection-card-${conn.id}`}>
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="w-11 h-11 bg-success-solid text-success-fg flex items-center justify-center border border-line-strong shrink-0 rounded-full">
            <WhatsappLogo size={24} weight="regular" />
          </div>
          <div>
            <div className="font-heading text-xl font-semibold flex items-center gap-2 flex-wrap">
              {conn.nome}
              {/* O canal fica visível no cartão porque ele muda o que a
                  conexão consegue fazer — janela, modelo, status de entrega —
                  e com duas conexões na tela não há outro jeito de saber qual
                  é qual. */}
              <span className={`text-xs font-mono font-medium px-2 py-0.5 rounded-full border ${
                naoOficial ? "border-warning text-warning bg-warning-soft"
                           : "border-line text-ink-3"}`}>
                {naoOficial ? "não oficial" : "API oficial"}
              </span>
            </div>
            <div className="text-xs font-mono text-ink-3">
              Status: <StatusBadge status={conn.status} />
              {conn.qualidade && <QualidadeBadge nota={conn.qualidade} />}
            </div>
            {conn.numero_exibicao && (
              <div className="text-xs text-ink mt-1">
                <span className="font-mono">{conn.numero_exibicao}</span>
                {conn.nome_verificado && <span className="text-ink-2"> · {conn.nome_verificado}</span>}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* "Verificar" reconsulta a Meta e só existe na Cloud API. No canal
              não oficial o equivalente é o painel de pareamento abaixo, que já
              consulta a ponte — um botão aqui chamaria uma rota que recusa. */}
          {cadastrado && !naoOficial && (
            <button onClick={onVerificar} disabled={busy} className="zz-btn zz-btn-secondary"
              data-testid={`verify-btn-${conn.id}`}>
              <ArrowClockwise size={14} weight="regular" /> {busy ? "Verificando…" : "Verificar"}
            </button>
          )}
          {/* No canal não oficial este botão é escotilha, não caminho: a
              instância é criada junto com a conexão, então ele só aparece para
              conexão que ficou sem instância — a criada antes desta versão, ou
              uma cujo provisionamento falhou. O cliente do fluxo normal nunca
              o vê, e é o ponto: id e token de instância são operação de
              infraestrutura, não passo de quem só quer ligar o WhatsApp. */}
          {(!naoOficial || !cadastrado) && (
            <button onClick={onEditar} disabled={busy}
              className={naoOficial ? "zz-btn zz-btn-secondary" : "zz-btn"}
              data-testid={`creds-btn-${conn.id}`}>
              <Key size={14} weight="regular" />{" "}
              {naoOficial
                ? "Apontar instância existente"
                : (cadastrado ? "Credenciais" : "Cadastrar número")}
            </button>
          )}
          <button onClick={onRemove} disabled={busy} className="p-3 border border-line hover:bg-danger-soft text-danger rounded-lg" title="Excluir conexão" data-testid={`delete-btn-${conn.id}`}>
            <Trash size={14} weight="regular" />
          </button>
        </div>
      </div>

      {conn.status === "erro" && conn.ultimo_erro && (
        <div className="mt-4 text-xs text-danger bg-danger-soft border border-danger p-3 rounded-lg flex gap-2"
          data-testid={`conn-error-${conn.id}`}>
          <WarningCircle size={16} weight="regular" className="shrink-0 mt-px" />
          <div>
            <strong>A Meta recusou esta conexão.</strong> {conn.ultimo_erro}
          </div>
        </div>
      )}

      {cadastrado && conn.webhook_assinado === false && (
        <div className="mt-4 text-xs text-warning bg-warning-soft border border-warning p-3 rounded-lg">
          {naoOficial ? (
            <>
              <strong>Webhook não configurado na instância.</strong> O número envia mensagens,
              mas as respostas dos clientes não chegam. Salve a instância de novo — a
              WuzAPI aceita a configuração e grava vazio quando o formato do campo não é o
              que ela espera, então a falha não aparece em outro lugar.
            </>
          ) : (
            <>
              <strong>Webhook não assinado.</strong> O número envia mensagens, mas as respostas
              dos clientes não chegam até o app ser assinado na conta da Meta. Cadastre as
              credenciais de novo com um token que tenha a permissão
              <span className="font-mono"> whatsapp_business_management</span>.
            </>
          )}
        </div>
      )}

      {editando && (naoOficial
        ? <FormWuzapi conn={conn} onSalvar={onSalvarWuzapi} onFechar={onEditar} />
        : <FormCredenciais conn={conn} onSalvar={onSalvarCredenciais} onFechar={onEditar} />
      )}

      {naoOficial && cadastrado && !editando && (
        <PainelPareamento conn={conn} busy={busy} onConectar={onConectar}
          onDesconectar={onDesconectar} onSair={onSair} onRecarregar={onRecarregar} />
      )}

      <div className="mt-4 pt-4 border-t border-line grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <div>
          <label className="text-sm font-medium text-ink-3 block mb-1.5">Departamento padrão</label>
          <select className="zz-input" style={{ padding: "0.4rem 0.6rem" }}
            value={conn.departamento_padrao_id || ""}
            onChange={(e) => onUpdateField({ departamento_padrao_id: e.target.value || null })}
            data-testid={`dept-select-${conn.id}`}>
            <option value="">— nenhum —</option>
            {depts.map((d) => <option key={d.id} value={d.id}>{d.nome}</option>)}
          </select>
        </div>
      </div>

      {!temBotPrimeiraMensagem && (
        <div className="mt-3 text-xs text-warning bg-warning-soft border border-warning p-2 rounded-lg" data-testid={`no-first-bot-warning-${conn.id}`}>
          <strong>Sem fluxo de primeira mensagem.</strong> Mensagens que não casarem com a palavra-chave vão
          direto para a fila de atendimento humano, sem nenhuma resposta automática para o cliente.
        </div>
      )}
    </div>
  );
}

// O token nunca volta do servidor, só a máscara. Por isso o campo nasce vazio
// numa conexão já cadastrada: deixá-lo em branco mantém o que está guardado, e
// preenchê-lo troca a credencial. Devolver o token para "preencher o campo de
// edição" o faria trafegar a cada abertura de tela.
function FormWuzapi({ conn, onSalvar, onFechar }) {
  const [form, setForm] = useState({ wuzapi_id: conn.wuzapi_id || "", token: "", hmac_key: "" });
  const [vendoToken, setVendoToken] = useState(false);

  const enviar = async () => {
    await onSalvar({
      wuzapi_id: form.wuzapi_id.trim(),
      token: form.token.trim() || null,
      hmac_key: form.hmac_key.trim() || null,
    });
  };

  return (
    <Modal titulo="Apontar instância existente" onFechar={onFechar} onSalvar={enviar}
      salvarLabel="Salvar instância" salvandoLabel="Validando…"
      testId={`wuzapi-form-${conn.id}`}>
      <div className="text-xs text-ink-2">
        Esta conexão está sem instância. Conexão nova cria a instância sozinha — este
        formulário existe para <strong>apontar uma que já existe</strong> no servidor da
        ponte, criada à mão ou sobrando de um provisionamento que falhou. A instância é
        validada agora: se o token não responder, nada é gravado.
      </div>
      {/* O aviso da chave de assinatura fica aqui, antes dos campos, porque é a
          única coisa deste formulário que não dá para consertar depois: a ponte
          só aceita a chave no momento em que a instância é criada, e a
          listagem dela não diz se tem uma. Ou seja, ninguém — nem a plataforma —
          consegue conferir isto olhando; só se descobre quando a mensagem do
          contato não chega. Daí dizer de antemão, e apontar o caminho que
          funciona sozinho. */}
      <div className="text-xs text-warning bg-warning-soft border border-warning p-3 rounded-lg">
        <strong>A chave de assinatura precisa ser a mesma com que a instância foi criada.</strong>{" "}
        A ponte só a aceita na criação, e não há como consultá-la depois — se ela não
        bater, o número pareia, envia e as respostas dos clientes são recusadas sem
        aparecer em lugar nenhum. Se você não tem essa chave, exclua esta conexão e crie
        outra: aí a plataforma provisiona a instância e a chave sozinho.
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="text-sm font-medium text-ink-3 block mb-1.5">
            ID da instância
          </label>
          {/* O exemplo e generico de proposito. Um placeholder com a cara de um
              id real faz o campo vazio parecer preenchido, e a pessoa clica em
              salvar sem digitar nada. Aconteceu na primeira vez que esta tela
              foi usada. */}
          <input required className="zz-input" value={form.wuzapi_id}
            placeholder="id da instancia na ponte"
            onChange={(e) => setForm({ ...form, wuzapi_id: e.target.value })}
            data-testid={`wuzid-${conn.id}`} />
        </div>
        <div>
          <label className="text-sm font-medium text-ink-3 block mb-1.5">
            Token da instância {conn.token_mascara && <span className="font-mono normal-case text-ink-2">({conn.token_mascara})</span>}
          </label>
          <div className="relative">
            <input className="zz-input pr-10" type={vendoToken ? "text" : "password"}
              autoComplete="new-password" value={form.token}
              placeholder={conn.token_mascara ? "deixe vazio para manter" : "token da instância"}
              onChange={(e) => setForm({ ...form, token: e.target.value })}
              data-testid={`wuztoken-${conn.id}`} />
            <button type="button" onClick={() => setVendoToken((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-3 hover:text-ink"
              aria-label={vendoToken ? "Esconder token" : "Mostrar token"}>
              {vendoToken ? <EyeSlash size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>
        <div className="sm:col-span-2">
          <label className="text-sm font-medium text-ink-3 block mb-1.5">
            Chave de assinatura do webhook
          </label>
          <input className="zz-input" type="password" autoComplete="new-password"
            value={form.hmac_key}
            placeholder={conn.wuzapi_id ? "deixe vazio para manter a atual" : "a chave usada ao criar a instância"}
            onChange={(e) => setForm({ ...form, hmac_key: e.target.value })}
            data-testid={`wuzhmac-${conn.id}`} />
        </div>
      </div>
    </Modal>
  );
}


function PainelPareamento({ conn, busy, onConectar, onDesconectar, onSair, onRecarregar }) {
  const [qr, setQr] = useState(conn.qrcode_atual || null);
  const [status, setStatus] = useState(conn.status || "desconectado");

  // O QR do WhatsApp expira em segundos, então a tela consulta em laço
  // enquanto estiver pareando — e só enquanto: um polling permanente ficaria
  // batendo na ponte por nada em toda conexão já conectada.
  //
  // O intervalo é curto (3s) porque aqui a espera é de alguém parado na frente
  // da tela com o celular na mão, ao contrário dos 60s do selo de atividades.
  useEffect(() => {
    if (status !== "pareando") return undefined;
    let vivo = true;
    const bater = async () => {
      try {
        const { data } = await api.get(`/whatsapp/connections/${conn.id}/wuzapi/qr`);
        if (!vivo) return;
        setQr(data.qr || null);
        if (data.status === "conectado") {
          setStatus("conectado");
          toast.success(`Número ${data.numero || ""} pareado.`.trim());
          onRecarregar?.();
        }
      } catch {
        /* falha de consulta não apaga o QR que já está na tela */
      }
    };
    bater();
    const t = setInterval(bater, 3000);
    return () => { vivo = false; clearInterval(t); };
  }, [status, conn.id, onRecarregar]);

  useEffect(() => { setStatus(conn.status || "desconectado"); }, [conn.status]);

  const conectado = status === "conectado";

  return (
    <div className="mt-4 pt-4 border-t border-line" data-testid={`pareamento-${conn.id}`}>
      {conectado ? (
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="text-xs text-ink-2">
            Aparelho pareado{conn.numero_exibicao ? <> como <span className="font-mono text-ink">{conn.numero_exibicao}</span></> : null}.
            {" "}Este canal <strong>não confirma entrega</strong>: as mensagens aparecem como
            enviadas e não há "entregue" nem "lida".
          </div>
          <div className="flex gap-2">
            <button onClick={onDesconectar} disabled={busy} className="zz-btn zz-btn-secondary">
              Desconectar
            </button>
            <button onClick={onSair} disabled={busy} className="zz-btn zz-btn-secondary text-danger">
              Desvincular aparelho
            </button>
          </div>
        </div>
      ) : status === "pareando" ? (
        <div className="flex items-start gap-5 flex-wrap">
          <div className="border border-line-strong rounded-lg bg-white p-2 shrink-0">
            {qr ? (
              <img src={qr} alt="QR code para parear o WhatsApp" width={200} height={200}
                data-testid={`qr-${conn.id}`} />
            ) : (
              <div className="w-[200px] h-[200px] grid place-items-center text-xs text-ink-3">
                gerando código…
              </div>
            )}
          </div>
          <div className="text-xs text-ink-2 space-y-2 max-w-md">
            <div className="font-bold text-ink text-sm">Leia o código no celular do número</div>
            <div>WhatsApp → <strong>Configurações</strong> → <strong>Dispositivos conectados</strong> → <strong>Conectar dispositivo</strong>.</div>
            <div>
              O código vence em cerca de 20 segundos e a tela troca sozinha por um novo
              enquanto você não ler — não precisa clicar de novo.
            </div>
            <button onClick={onDesconectar} disabled={busy} className="zz-btn zz-btn-secondary mt-1">
              Cancelar pareamento
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="text-xs text-ink-2">
            Instância cadastrada e sem aparelho pareado. Conectar faz a ponte pedir o QR.
          </div>
          <button onClick={onConectar} disabled={busy} className="zz-btn"
            data-testid={`conectar-${conn.id}`}>
            <ArrowClockwise size={16} weight="regular" /> {busy ? "Conectando…" : "Conectar e gerar QR"}
          </button>
        </div>
      )}
    </div>
  );
}


function FormCredenciais({ conn, onSalvar, onFechar }) {
  const [form, setForm] = useState({
    phone_number_id: conn.phone_number_id || "",
    waba_id: conn.waba_id || "",
    token: "",
  });
  const [vendoToken, setVendoToken] = useState(false);

  const enviar = async () => {
    await onSalvar({
      phone_number_id: form.phone_number_id.trim(),
      waba_id: form.waba_id.trim(),
      token: form.token.trim() || null,
    });
  };

  return (
    <Modal titulo={conn.phone_number_id ? "Credenciais da conexão" : "Cadastrar número"}
      onFechar={onFechar} onSalvar={enviar}
      salvarLabel="Validar e salvar" salvandoLabel="Validando com a Meta…"
      testId={`creds-form-${conn.id}`}>
      <div className="text-xs text-ink-2">
        Os três valores estão no painel da Meta, em <strong>WhatsApp &gt; Configuração da API</strong>.
        O número é validado agora: se algo estiver errado, nada é gravado.
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="text-sm font-medium text-ink-3 block mb-1.5">
            ID do número (phone number ID)
          </label>
          <input required className="zz-input" inputMode="numeric"
            value={form.phone_number_id} placeholder="106540352242922"
            onChange={(e) => setForm({ ...form, phone_number_id: e.target.value })}
            data-testid={`pnid-${conn.id}`} />
        </div>
        <div>
          <label className="text-sm font-medium text-ink-3 block mb-1.5">
            ID da conta (WABA ID)
          </label>
          <input required className="zz-input" inputMode="numeric"
            value={form.waba_id} placeholder="102290129340398"
            onChange={(e) => setForm({ ...form, waba_id: e.target.value })}
            data-testid={`waba-${conn.id}`} />
        </div>
        <div className="sm:col-span-2">
          <label className="text-sm font-medium text-ink-3 block mb-1.5">
            Token de acesso {conn.token_mascara && <span className="font-mono normal-case text-ink-2">({conn.token_mascara})</span>}
          </label>
          <div className="relative">
            <input className="zz-input pr-10" type={vendoToken ? "text" : "password"}
              autoComplete="new-password"
              value={form.token} required={!conn.token_mascara}
              placeholder={conn.token_mascara ? "deixe vazio para manter" : "EAAG…"}
              onChange={(e) => setForm({ ...form, token: e.target.value })}
              data-testid={`token-${conn.id}`} />
            <button type="button" onClick={() => setVendoToken((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-3 hover:text-ink"
              title={vendoToken ? "Ocultar" : "Mostrar"}>
              {vendoToken ? <EyeSlash size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

function StatusBadge({ status }) {
  const map = {
    conectado: "text-success",
    erro: "text-danger",
    // `pareando` só existe no canal não oficial: é o intervalo entre pedir o
    // QR e alguém lê-lo no celular. Sem ele a conexão apareceria como "sem
    // número" justamente enquanto o cliente está com o aparelho na mão.
    pareando: "text-warning",
    desconectado: "text-ink-3",
  };
  const label = {
    conectado: "conectado",
    erro: "com erro",
    pareando: "aguardando leitura do QR",
    desconectado: "sem número",
  }[status] || "—";
  return (
    <span className={`inline-flex items-center gap-1 font-bold ${map[status] || "text-ink-3"}`}>
      {status === "conectado" && <CheckCircle size={12} weight="regular" />}
      {status === "erro" && <XCircle size={12} weight="regular" />}
      {label}
    </span>
  );
}

// A nota de qualidade é da Meta e vale dinheiro: nota baixa reduz o limite
// diário de conversas iniciadas pela empresa, e chega a suspender o número.
// Quem precisa saber a tempo de mudar o conteúdo do que envia é o cliente.
//
// `UNKNOWN` é o quarto valor, e é o estado real de número que ainda não enviou
// nada: a Meta calcula a nota a partir do que os destinatários fazem (bloquear,
// denunciar), e sem tráfego não há o que medir. Ele chegava à tela como
// "UNKNOWN", em inglês — identificador interno da Meta na cara do cliente, o
// mesmo erro do id de botão de menu numa bolha de conversa. E lido como
// problema, quando é ausência de dado: número novo não tem nota baixa, não tem
// nota.
//
// Valor fora do mapa cai no mesmo texto em vez de aparecer cru: se a Meta
// inventar uma nota nova, o cliente não tem por que ler o nome dela em inglês.
// O valor bruto fica no `title`, que é onde o suporte precisa dele.
function QualidadeBadge({ nota }) {
  const cor = { GREEN: "text-success", YELLOW: "text-warning", RED: "text-danger" }[nota] || "text-ink-3";
  const rotulo = {
    GREEN: "qualidade alta",
    YELLOW: "qualidade média",
    RED: "qualidade baixa",
  }[nota] || "sem nota ainda";
  return <span className={`ml-2 font-bold ${cor}`} title={nota}>· {rotulo}</span>;
}
