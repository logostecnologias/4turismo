import { useState } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import {
  ArrowLeft, ArrowRight, Check, FloppyDisk, ArrowSquareOut, Key,
  ShoppingCart, Target, Headset, ChatCircleDots, CalendarBlank, Star,
  Lightning, TextAlignLeft, Article, Lifebuoy, Flag, Prohibit, MagnifyingGlass,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import FotosAgente from "./FotosAgente";

// Custo mensal estimado por modelo, para a empresa escolher com numero na
// frente em vez de pelo nome. A carga de referencia e a de um cliente medio:
// 50 atendimentos/dia x 6 respostas x 30 dias = 9.000 respostas, cada uma com
// ~1.800 tokens de entrada (dos quais ~1.500 vem do cache) e ~120 de saida.
//
// A conta e feita aqui, e nao no servidor, porque e uma projecao para a tela —
// o consumo real vem medido de `ai_usage`.
const RESPOSTAS_MES = 9000;
const ENTRADA_NAO_CACHEADA = 330;
const ENTRADA_CACHEADA = 1500;
const SAIDA = 120;
// Cambio so para dar ordem de grandeza. Nao e cotacao: o provedor cobra em
// dolar, direto na conta do cliente.
const USD_BRL = 5.5;

function custoMensal(m) {
  if (!m.entrada && !m.saida) return "Grátis";
  const cacheLeitura = m.cache_leitura ?? m.entrada * 0.1;
  const usd =
    (RESPOSTAS_MES *
      (ENTRADA_NAO_CACHEADA * m.entrada + ENTRADA_CACHEADA * cacheLeitura + SAIDA * m.saida)) /
    1_000_000;
  return (usd * USD_BRL).toLocaleString("pt-BR", {
    style: "currency", currency: "BRL", maximumFractionDigits: 0,
  });
}

// Assistente em três passos: tipo → empresa → comportamento.
//
// O prompt não é escrito num campo em branco de propósito. Ninguém sabe
// escrever um bom prompt de sistema, mas todo mundo sabe responder "é vendas
// ou é suporte?" — o backend (`ai_agents.montar_instrucoes`) costura as peças.
// É por isso que quase tudo aqui é escolha e não texto livre.

// Ícone por tipo de agente. Mora aqui, e não no catálogo do backend, porque é
// decoração de tela: o servidor não deveria saber o nome de um componente de
// ícone. Tipo sem entrada cai no genérico em vez de sumir.
const ICONES = {
  vendas: ShoppingCart,
  sdr: Target,
  suporte: Headset,
  geral: ChatCircleDots,
  agendamento: CalendarBlank,
  personalizado: Star,
};

const ICONES_TAMANHO = {
  curtas: Lightning,
  normais: TextAlignLeft,
  detalhadas: Article,
};

const ICONES_MODO = {
  fallback: Lifebuoy,
  primeiro: Flag,
  desligado: Prohibit,
};

const PASSOS = ["Tipo de assistente", "Sobre a agência", "Comportamento", "Modelo e chave"];

const VAZIO = {
  nome: "",
  tipo: "",
  objetivo: "",
  modo: "fallback",
  ativo: true,
  empresa_nome: "",
  empresa_site: "",
  empresa_segmento: "",
  empresa_descricao: "",
  tom: [],
  tamanho: "normais",
  instrucoes: "",
  nao_fazer: "",
  base_conhecimento: "",
  escalar_quando: "",
  agenda_ativa: false,
  finalizar_ativo: false,
  conexoes: [],
  departamento_id: "",
  provedor: "",
  modelo: "",
  chave_api: "",
  base_url: "",
};

function Rotulo({ children, obrigatorio }) {
  return (
    <label className="text-sm font-medium text-ink-2 block mb-1.5">
      {children}
      {obrigatorio && <span className="text-danger ml-0.5">*</span>}
    </label>
  );
}

export default function AgenteWizard({
  catalogo, provedores, plataformaDisponivel, conexoes, departamentos, agente, onFechar, onSalvo,
}) {
  const [passo, setPasso] = useState(0);
  const [salvando, setSalvando] = useState(false);
  const [form, setForm] = useState(() =>
    agente
      ? { ...VAZIO, ...agente, base_url: agente.base_url || "" }
      : { ...VAZIO },
  );

  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const provedorAtual = (provedores || []).find((p) => p.id === form.provedor);
  const editando = Boolean(agente);
  // Modelo digitado à mão (fora da lista sugerida), nos provedores que aceitam.
  const modeloSugerido = provedorAtual?.modelos.some((m) => m.id === form.modelo);
  // A chave guardada só serve se provedor e endereço forem os mesmos: o backend
  // exige a chave de novo quando o endereço da API muda.
  const chaveGuardadaServe =
    agente?.tem_chave &&
    agente.provedor === form.provedor &&
    (agente.base_url || "") === (provedorAtual?.url_livre ? form.base_url.trim().replace(/\/+$/, "") : "");

  // Modelos lidos do provedor com a chave do cliente. A lista sugerida do
  // catálogo envelhece (modelo sai do plano grátis, é descontinuado); esta é a
  // que vale para aquela conta.
  const [modelosConta, setModelosConta] = useState(null); // { provedor, ids }
  const [buscandoModelos, setBuscandoModelos] = useState(false);
  const listaDaConta = modelosConta?.provedor === form.provedor ? modelosConta.ids : null;
  const filtroModelo = modeloSugerido ? "" : form.modelo.toLowerCase();
  const listaFiltrada = (listaDaConta || [])
    .filter((id) => !filtroModelo || id === form.modelo || id.toLowerCase().includes(filtroModelo))
    .slice(0, 200);

  const buscarModelos = async () => {
    if (buscandoModelos) return;
    setBuscandoModelos(true);
    try {
      const { data } = await api.post("/ai/models", {
        provedor: form.provedor,
        chave_api: form.chave_api.trim() || null,
        base_url: form.base_url || null,
        agente_id: agente?.id || null,
      });
      setModelosConta({ provedor: form.provedor, ids: data.modelos });
      if (!data.modelos.length) toast.message("O provedor não devolveu nenhum modelo para esta chave.");
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setBuscandoModelos(false);
    }
  };

  const alternarTom = (id) =>
    set({ tom: form.tom.includes(id) ? form.tom.filter((t) => t !== id) : [...form.tom, id] });

  const alternarConexao = (id) =>
    set({
      conexoes: form.conexoes.includes(id)
        ? form.conexoes.filter((c) => c !== id)
        : [...form.conexoes, id],
    });

  // O que impede o avanço em cada passo. Barrar aqui é melhor que deixar o
  // backend recusar depois de três telas preenchidas.
  const bloqueio = [
    !form.tipo
      ? "Escolha o tipo de assistente"
      : form.tipo === "personalizado" && !form.objetivo.trim()
        ? "Descreva o objetivo do assistente personalizado"
        : null,
    null, // sobre a empresa é todo opcional: dá para criar e completar depois
    !form.nome.trim() ? "Dê um nome ao assistente" : null,
    // Sem provedor o agente cai na chave da plataforma. É um estado válido —
    // só não é, quando não existe chave da plataforma para cair.
    !form.provedor && !plataformaDisponivel
      ? "Escolha o provedor e informe a chave"
      : form.provedor && !form.modelo.trim()
        ? "Informe o modelo"
        : provedorAtual?.url_livre && !form.base_url.trim()
          ? "Informe o endereço da API"
          : form.provedor && !form.chave_api.trim() && !chaveGuardadaServe
            ? "Cole a chave de API"
            : null,
  ][passo];

  const salvar = async () => {
    setSalvando(true);
    try {
      const corpo = {
        ...form,
        departamento_id: form.departamento_id || null,
      };
      delete corpo.id;
      delete corpo.tenant_id;
      delete corpo.created_at;
      delete corpo.updated_at;
      const { data } = editando
        ? await api.patch(`/ai/agents/${agente.id}`, corpo)
        : await api.post("/ai/agents", corpo);
      toast.success(editando ? "Assistente atualizado." : "Assistente criado.");
      onSalvo(data);
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setSalvando(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-start justify-center p-4 overflow-y-auto"
      onClick={onFechar}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-surface w-full max-w-4xl my-8 zz-panel-hard overflow-hidden"
        data-testid="agente-wizard"
      >
        {/* Cabeçalho e trilha */}
        <div className="border-b border-line p-6">
          <div className="font-heading text-2xl font-semibold mb-4">
            {editando ? "Editar assistente" : "Novo assistente de IA"}
          </div>
          <div className="flex items-center gap-2">
            {PASSOS.map((p, i) => (
              <div key={p} className="flex items-center gap-2 flex-1 last:flex-none">
                <div
                  className={`w-6 h-6 shrink-0 flex items-center justify-center text-[11px] font-bold border rounded-full ${
                    i < passo
                      ? "bg-brand border-brand text-brand-fg"
                      : i === passo
                        ? "bg-surface border-line text-ink"
                        : "bg-surface border-line text-ink-4"
                  }`}
                >
                  {i < passo ? <Check size={12} weight="regular" /> : i + 1}
                </div>
                <div
                  className={`text-sm font-medium whitespace-nowrap ${
                    i === passo ? "text-ink" : "text-ink-4"
                  }`}
                >
                  {p}
                </div>
                {i < PASSOS.length - 1 && <div className="h-px bg-surface-3 flex-1 min-w-4" />}
              </div>
            ))}
          </div>
        </div>

        <div className="p-6 space-y-6">
          {/* ---------------- Passo 1 ---------------- */}
          {passo === 0 && (
            <>
              <div>
                <div className="font-heading text-lg font-semibold tracking-tight">
                  Qual o objetivo principal do assistente?
                </div>
                <p className="text-xs text-ink-2 mt-0.5">
                  É isso que define a estratégia dele na conversa. Tudo o mais ajusta por cima.
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {catalogo.tipos.map((t) => {
                  const Icone = ICONES[t.id] || Star;
                  const escolhido = form.tipo === t.id;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => set({ tipo: t.id })}
                      data-testid={`agente-tipo-${t.id}`}
                      className={`border rounded-xl p-4 text-left transition-colors h-full ${
                        escolhido
                          ? "border-info bg-info-soft ring-1 ring-info"
                          : "border-line hover:border-line-strong hover:bg-surface-2"
                      }`}
                    >
                      <div className={`w-10 h-10 rounded-xl flex items-center justify-center mb-3 ${
                        escolhido ? "bg-info text-brand-fg" : "bg-surface-3 text-ink-2"
                      }`}>
                        <Icone size={20} weight="regular" />
                      </div>
                      <div className="font-bold text-sm">{t.rotulo}</div>
                      <div className="text-xs text-ink-2 mt-1 leading-snug">{t.descricao}</div>
                    </button>
                  );
                })}
              </div>
              {form.tipo === "personalizado" && (
                <div>
                  <Rotulo obrigatorio>Objetivo do assistente</Rotulo>
                  <textarea
                    className="zz-input min-h-24"
                    autoFocus
                    data-testid="agente-objetivo"
                    placeholder="Ex: Receber pedidos de orçamento, entender o passeio de interesse, a data e o tamanho do grupo, e passar para a equipe."
                    value={form.objetivo}
                    onChange={(e) => set({ objetivo: e.target.value })}
                  />
                </div>
              )}
            </>
          )}

          {/* ---------------- Passo 2 ---------------- */}
          {passo === 1 && (
            <>
              <div>
                <div className="font-heading text-lg font-semibold tracking-tight">
                  Conte sobre a agência
                </div>
                <p className="text-xs text-ink-2 mt-0.5">
                  Tudo aqui é opcional, mas é o que o assistente vai saber. Sem isso ele é instruído a
                  transferir sempre que perguntarem valor, horário ou endereço, em vez de inventar.
                </p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <Rotulo>Nome da agência</Rotulo>
                  <input
                    className="zz-input"
                    data-testid="agente-empresa-nome"
                    placeholder="Ex: Lençóis Receptivo"
                    value={form.empresa_nome}
                    onChange={(e) => set({ empresa_nome: e.target.value })}
                  />
                </div>
                <div>
                  <Rotulo>Segmento</Rotulo>
                  <select
                    className="zz-input"
                    data-testid="agente-segmento"
                    value={form.empresa_segmento}
                    onChange={(e) => set({ empresa_segmento: e.target.value })}
                  >
                    <option value="">Selecione…</option>
                    {catalogo.segmentos.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <Rotulo>Site</Rotulo>
                <input
                  className="zz-input"
                  data-testid="agente-site"
                  placeholder="https://www.suaagencia.com.br"
                  value={form.empresa_site}
                  onChange={(e) => set({ empresa_site: e.target.value })}
                />
              </div>
              <div>
                <Rotulo>O que o assistente precisa saber</Rotulo>
                <textarea
                  className="zz-input min-h-32"
                  data-testid="agente-base"
                  placeholder={
                    "Horário de funcionamento, endereço, formas de pagamento, ponto de embarque, " +
                    "passeios e valores, o que levar, perguntas frequentes…"
                  }
                  value={form.base_conhecimento}
                  onChange={(e) => set({ base_conhecimento: e.target.value })}
                />
                <p className="text-[11px] text-ink-3 mt-1.5">
                  Esta é a única fonte de fatos do assistente: ele é instruído a não inventar nada que
                  não esteja aqui. Quanto mais completo, menos transferências.
                </p>
              </div>

              <div>
                <Rotulo>Fotos que o assistente pode enviar</Rotulo>
                <p className="text-xs text-ink-2 mb-3 -mt-1">
                  O assistente envia a foto sozinho quando a conversa pedir: o cliente quer conhecer
                  o passeio, ele mostra o lugar. Quem escolhe é ele, pela descrição de cada uma.
                </p>
                <FotosAgente agente={agente} />
              </div>
            </>
          )}

          {/* ---------------- Passo 3 ---------------- */}
          {passo === 2 && (
            <>
              <div>
                <div className="font-heading text-lg font-semibold tracking-tight">
                  Como o assistente deve se comportar?
                </div>
                <p className="text-xs text-ink-2 mt-0.5">Ajuste fino de tom, tamanho e limites.</p>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <Rotulo obrigatorio>Nome do assistente</Rotulo>
                  <input
                    className="zz-input"
                    autoFocus
                    data-testid="agente-nome"
                    placeholder="Ex: Bia"
                    value={form.nome}
                    onChange={(e) => set({ nome: e.target.value })}
                  />
                  <p className="text-[11px] text-ink-3 mt-1">
                    É assim que ele se apresenta ao cliente.
                  </p>
                </div>
              </div>

              <div>
                <Rotulo>Tom de voz</Rotulo>
                <div className="flex flex-wrap gap-2">
                  {catalogo.tons.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => alternarTom(t.id)}
                      data-testid={`agente-tom-${t.id}`}
                      className={`px-3 py-1.5 text-xs font-bold border rounded-full transition-colors ${
                        form.tom.includes(t.id)
                          ? "bg-brand border-brand text-brand-fg"
                          : "bg-surface border-line hover:border-line-strong"
                      }`}
                    >
                      {t.rotulo}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <Rotulo>Tamanho das respostas</Rotulo>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {catalogo.tamanhos.map((t) => {
                    const Icone = ICONES_TAMANHO[t.id] || TextAlignLeft;
                    const escolhido = form.tamanho === t.id;
                    return (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => set({ tamanho: t.id })}
                        data-testid={`agente-tamanho-${t.id}`}
                        className={`border rounded-xl p-4 text-left transition-colors h-full ${
                          escolhido
                            ? "border-info bg-info-soft ring-1 ring-info"
                            : "border-line hover:border-line-strong hover:bg-surface-2"
                        }`}
                      >
                        <div className={`w-9 h-9 rounded-lg flex items-center justify-center mb-2.5 ${
                          escolhido ? "bg-info text-brand-fg" : "bg-surface-3 text-ink-2"
                        }`}>
                          <Icone size={18} weight="regular" />
                        </div>
                        <div className="font-bold text-sm">{t.rotulo}</div>
                        <div className="text-[11px] text-ink-3">~{t.palavras} palavras</div>
                        <div className="text-[11px] text-ink-2 mt-1 leading-snug">{t.nota}</div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <Rotulo>Instruções específicas</Rotulo>
                <textarea
                  className="zz-input min-h-32"
                  data-testid="agente-instrucoes"
                  placeholder={
                    "Escreva livremente o que o assistente precisa seguir.\n\n" +
                    "Ex: Pergunte a data e quantas pessoas vão antes de falar de valores.\n" +
                    "Trate quem já é cliente pelo primeiro nome.\n" +
                    "Se perguntarem de parcelamento, diga que vai até 12x sem juros."
                  }
                  value={form.instrucoes}
                  onChange={(e) => set({ instrucoes: e.target.value })}
                />
                <p className="text-[11px] text-ink-3 mt-1.5">
                  Fica guardado no assistente e vai junto em <strong>toda</strong> mensagem que ele
                  responde. É onde entra o que as opções acima não cobrem.
                </p>
              </div>

              <div>
                <Rotulo>O que o assistente NÃO deve fazer</Rotulo>
                <textarea
                  className="zz-input min-h-20"
                  data-testid="agente-nao-fazer"
                  placeholder={"Uma regra por linha.\nEx: Não dar desconto\nNão garantir o nível da água das lagoas"}
                  value={form.nao_fazer}
                  onChange={(e) => set({ nao_fazer: e.target.value })}
                />
              </div>

              <div className="border-t border-line pt-5">
                <label className="flex items-start gap-3 border rounded-xl p-4 cursor-pointer transition-colors border-line hover:border-line-strong hover:bg-surface-2">
                  <input
                    type="checkbox"
                    className="mt-1"
                    data-testid="agente-agenda-ativa"
                    checked={!!form.agenda_ativa}
                    onChange={(e) => set({ agenda_ativa: e.target.checked })}
                  />
                  <span>
                    <span className="font-semibold block">Deixar o assistente reservar nas Saídas</span>
                    <span className="text-[11px] text-ink-3 block mt-1">
                      Ele passa a consultar as vagas de verdade, pedir a reserva e cancelar
                      pelo WhatsApp, em vez de anotar a preferência e transferir. A reserva
                      chega como Solicitada, para a equipe confirmar. Os passeios e os horários
                      de saída vêm da tela <strong>Saídas</strong>; sem nenhum passeio com
                      saída cadastrado, esta opção não tem efeito.
                    </span>
                  </span>
                </label>
                <label className="flex items-start gap-3 border rounded-xl p-4 mt-2 cursor-pointer transition-colors border-line hover:border-line-strong hover:bg-surface-2">
                  <input
                    type="checkbox"
                    className="mt-1"
                    data-testid="agente-finalizar-ativo"
                    checked={!!form.finalizar_ativo}
                    onChange={(e) => set({ finalizar_ativo: e.target.checked })}
                  />
                  <span>
                    <span className="font-semibold block">Deixar o assistente finalizar o atendimento</span>
                    <span className="text-[11px] text-ink-3 block mt-1">
                      Quando o assunto se resolve e o cliente se despede, ele encerra a conversa
                      sozinho e deixa um resumo no histórico. Se o cliente escrever de novo, uma
                      conversa nova começa.
                    </span>
                  </span>
                </label>
              </div>

              <div>
                <Rotulo>Transferir para uma pessoa também quando</Rotulo>
                <textarea
                  className="zz-input min-h-16"
                  data-testid="agente-escalar"
                  placeholder={"Uma situação por linha.\nEx: Perguntarem sobre passeio privativo"}
                  value={form.escalar_quando}
                  onChange={(e) => set({ escalar_quando: e.target.value })}
                />
                <p className="text-[11px] text-ink-3 mt-1.5">
                  Pedido explícito de atendente, reclamação, cancelamento e falta de informação já
                  transferem sozinhos; isto é só o que for específico da sua agência.
                </p>
              </div>

              <div className="border-t border-line pt-5 space-y-4">
                <div>
                  <Rotulo>Quando o assistente entra</Rotulo>
                  <div className="space-y-2">
                    {catalogo.modos.map((m) => {
                      const Icone = ICONES_MODO[m.id] || Lifebuoy;
                      const escolhido = form.modo === m.id;
                      return (
                        <label
                          key={m.id}
                          className={`flex items-center gap-3 border rounded-xl p-3.5 cursor-pointer transition-colors ${
                            escolhido
                              ? "border-info bg-info-soft ring-1 ring-info"
                              : "border-line hover:border-line-strong hover:bg-surface-2"
                          }`}
                        >
                          {/* O radio nativo fica so para teclado e leitor de tela:
                              o cartao inteiro e o alvo de clique, e a selecao e
                              sinalizada pela borda de informacao + o icone cheio. */}
                          <input
                            type="radio"
                            name="modo"
                            className="sr-only"
                            checked={escolhido}
                            onChange={() => set({ modo: m.id })}
                            data-testid={`agente-modo-${m.id}`}
                          />
                          <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${
                            escolhido ? "bg-info text-brand-fg" : "bg-surface-3 text-ink-2"
                          }`}>
                            <Icone size={18} weight="regular" />
                          </div>
                          <div className="min-w-0">
                            <div className="font-bold text-sm">{m.rotulo}</div>
                            <div className="text-xs text-ink-2">{m.nota}</div>
                          </div>
                        </label>
                      );
                    })}
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <Rotulo>Em quais conexões</Rotulo>
                    <div className="border border-line p-3 max-h-32 overflow-y-auto space-y-1 rounded-lg">
                      {conexoes.length === 0 && (
                        <div className="text-xs text-ink-3 p-1">Nenhuma conexão cadastrada.</div>
                      )}
                      {conexoes.map((c) => (
                        <label key={c.id} className="flex items-center gap-2 text-sm p-1 cursor-pointer">
                          <input
                            type="checkbox"
                            checked={form.conexoes.includes(c.id)}
                            onChange={() => alternarConexao(c.id)}
                            data-testid={`agente-conexao-${c.id}`}
                          />
                          {c.nome}
                        </label>
                      ))}
                    </div>
                    <p className="text-[11px] text-ink-3 mt-1">
                      Nenhuma marcada = todas, inclusive as que você criar depois.
                    </p>
                  </div>
                  <div>
                    <Rotulo>Transferir para o departamento</Rotulo>
                    <select
                      className="zz-input"
                      data-testid="agente-departamento"
                      value={form.departamento_id || ""}
                      onChange={(e) => set({ departamento_id: e.target.value })}
                    >
                      <option value="">Padrão da conexão</option>
                      {departamentos.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.nome}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>
            </>
          )}

          {/* ---------------- Passo 4 ---------------- */}
          {passo === 3 && (
            <>
              <div>
                <div className="font-heading text-lg font-bold tracking-tight">
                  Qual IA responde por este assistente?
                </div>
                <p className="text-xs text-ink-2 mt-0.5">
                  A chave é deste assistente, não da agência: dá para ter um assistente na Anthropic e
                  outro na Groq, com contas separadas. Serve qualquer serviço compatível com a API
                  da OpenAI.
                </p>
              </div>

              <div>
                <Rotulo>Provedor</Rotulo>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {(provedores || []).map((p) => (
                    <button key={p.id} type="button"
                      onClick={() => set({
                        provedor: p.id,
                        modelo: p.modelos[0]?.id || "",
                        chave_api: "",
                        base_url: p.id === agente?.provedor ? agente?.base_url || "" : "",
                      })}
                      data-testid={`agente-provedor-${p.id}`}
                      className={`border rounded-lg p-3 text-left transition-colors ${
                        form.provedor === p.id
                          ? "border-info bg-info-soft ring-1 ring-info"
                          : "border-line hover:border-line-strong"
                      }`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold text-sm">{p.rotulo}</span>
                        {p.gratuito && (
                          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-success-soft text-success">
                            Plano grátis
                          </span>
                        )}
                      </div>
                    </button>
                  ))}
                  {plataformaDisponivel && (
                    <button type="button"
                      onClick={() => set({ provedor: "", modelo: "", chave_api: "", base_url: "" })}
                      data-testid="agente-provedor-plataforma"
                      className={`border rounded-lg p-3 text-left transition-colors sm:col-span-2 lg:col-span-3 ${
                        !form.provedor
                          ? "border-info bg-info-soft ring-1 ring-info"
                          : "border-line hover:border-line-strong"
                      }`}>
                      <div className="font-bold text-sm">Usar a IA inclusa no plano</div>
                      <div className="text-xs text-ink-2 mt-0.5">
                        Sem chave própria. Vale o limite mensal de tokens do seu plano.
                      </div>
                    </button>
                  )}
                </div>
              </div>

              {provedorAtual && (
                <>
                  <div>
                    <Rotulo>Modelo</Rotulo>
                    <div className="space-y-2">
                      {provedorAtual.modelos.map((m) => (
                        <label key={m.id}
                          className={`flex items-start gap-3 border rounded-lg p-3 cursor-pointer transition-colors ${
                            form.modelo === m.id
                              ? "border-info bg-info-soft"
                              : "border-line hover:border-line-strong"
                          }`}>
                          <input type="radio" name="modelo" className="mt-1"
                            checked={form.modelo === m.id}
                            onChange={() => set({ modelo: m.id })}
                            data-testid={`agente-modelo-${m.id}`} />
                          <div className="flex-1 min-w-0">
                            <div className="font-bold text-sm">{m.rotulo}</div>
                            <div className="text-xs text-ink-2">{m.nota}</div>
                          </div>
                          <div className="text-right shrink-0">
                            <div className="font-heading font-bold text-sm">~{custoMensal(m)}</div>
                            <div className="text-[10px] text-ink-3">por mês</div>
                          </div>
                        </label>
                      ))}
                      {provedorAtual.modelo_livre && (
                        // O catálogo do provedor muda mais rápido que o nosso:
                        // qualquer id que ele aceite serve, e a chamada de teste
                        // ao salvar confirma.
                        <label
                          className={`flex items-start gap-3 border rounded-lg p-3 cursor-pointer transition-colors ${
                            !modeloSugerido
                              ? "border-info bg-info-soft"
                              : "border-line hover:border-line-strong"
                          }`}>
                          <input type="radio" name="modelo" className="mt-1"
                            checked={!modeloSugerido}
                            onChange={() => set({ modelo: "" })}
                            data-testid="agente-modelo-outro" />
                          <div className="flex-1 min-w-0">
                            <div className="font-bold text-sm">
                              {provedorAtual.modelos.length ? "Outro modelo" : "Modelo"}
                            </div>
                            <input className="zz-input font-mono mt-1.5"
                              placeholder="id do modelo, como aparece na documentação do provedor"
                              value={modeloSugerido ? "" : form.modelo}
                              onFocus={() => modeloSugerido && set({ modelo: "" })}
                              onChange={(e) => set({ modelo: e.target.value.trim() })}
                              data-testid="agente-modelo-livre" />
                            <div className="flex items-center justify-between gap-2 mt-1.5">
                              <span className="text-[11px] text-ink-3">
                                Sem estimativa de custo: o consumo é medido em tokens.
                              </span>
                              <button type="button"
                                className="zz-btn zz-btn-secondary text-xs py-1 shrink-0"
                                disabled={buscandoModelos}
                                onClick={buscarModelos}
                                data-testid="agente-buscar-modelos">
                                <MagnifyingGlass size={13} weight="regular" />
                                {buscandoModelos ? "Buscando…" : "Buscar modelos da conta"}
                              </button>
                            </div>
                            {listaDaConta && (
                              <div className="mt-2 border border-line rounded-lg max-h-56 overflow-y-auto"
                                data-testid="agente-modelos-conta">
                                {listaFiltrada.length === 0 ? (
                                  <div className="p-3 text-xs text-ink-3">
                                    Nenhum modelo com esse nome nesta conta.
                                  </div>
                                ) : (
                                  listaFiltrada.map((id) => (
                                    <button key={id} type="button"
                                      onClick={() => set({ modelo: id })}
                                      className={`block w-full text-left px-3 py-1.5 font-mono text-xs border-b border-line last:border-b-0 ${
                                        form.modelo === id ? "bg-info-soft text-info" : "hover:bg-surface-2"
                                      }`}>
                                      {id}
                                    </button>
                                  ))
                                )}
                              </div>
                            )}
                          </div>
                        </label>
                      )}
                    </div>
                    {provedorAtual.modelos.length > 0 && (
                      <p className="text-[11px] text-ink-3 mt-2">
                        Estimativa para 50 atendimentos por dia (~9.000 respostas no mês). O provedor
                        cobra em dólar, direto na conta da agência
                        {provedorAtual.gratuito
                          ? "; no plano grátis dele não há cobrança, só limite de mensagens por minuto e por dia."
                          : "."}
                      </p>
                    )}
                  </div>

                  {provedorAtual.url_livre && (
                    <div>
                      <Rotulo obrigatorio>Endereço da API</Rotulo>
                      <input className="zz-input font-mono"
                        placeholder="https://api.exemplo.com/v1"
                        value={form.base_url}
                        onChange={(e) => set({ base_url: e.target.value })}
                        data-testid="agente-base-url" />
                      <p className="text-[11px] text-ink-3 mt-1.5">
                        O endereço base do serviço compatível com a OpenAI (Together, Fireworks,
                        Cerebras e afins), sem o <span className="font-mono">/chat/completions</span>.
                        Precisa ser https e acessível pela internet.
                      </p>
                    </div>
                  )}

                  <div>
                    <div className="flex items-center justify-between gap-3 mb-1.5">
                      <Rotulo>Chave de API</Rotulo>
                      {provedorAtual.console_url && (
                        <a href={provedorAtual.console_url} target="_blank" rel="noreferrer"
                          className="zz-btn zz-btn-secondary text-xs py-1"
                          data-testid="agente-abrir-console">
                          <ArrowSquareOut size={13} weight="regular" /> Abrir o console
                        </a>
                      )}
                    </div>
                    <div className="relative">
                      <Key size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-4" />
                      <input type="password" autoComplete="off"
                        className="zz-input pl-9 font-mono"
                        placeholder={
                          chaveGuardadaServe
                            ? `Chave atual ${agente.mascara} — deixe em branco para manter`
                            : provedorAtual.prefixo
                              ? `Cole a chave (começa com ${provedorAtual.prefixo})`
                              : "Cole a chave"
                        }
                        value={form.chave_api}
                        onChange={(e) => set({ chave_api: e.target.value })}
                        data-testid="agente-chave" />
                    </div>
                    <p className="text-[11px] text-ink-3 mt-1.5">
                      Guardada cifrada e nunca exibida de volta — só os últimos dígitos. Ao salvar,
                      fazemos uma chamada de teste para confirmar que ela funciona neste modelo.
                    </p>
                  </div>
                </>
              )}
            </>
          )}
        </div>

        {/* Rodapé */}
        <div className="border-t border-line p-5 flex items-center justify-between gap-3">
          <button
            type="button"
            className="zz-btn zz-btn-secondary"
            onClick={passo === 0 ? onFechar : () => setPasso(passo - 1)}
            data-testid="agente-voltar"
          >
            <ArrowLeft size={14} weight="regular" /> {passo === 0 ? "Cancelar" : "Voltar"}
          </button>
          <div className="flex items-center gap-3">
            {bloqueio && <span className="text-xs text-ink-3">{bloqueio}</span>}
            {passo < PASSOS.length - 1 ? (
              <button
                type="button"
                className="zz-btn"
                disabled={Boolean(bloqueio)}
                onClick={() => setPasso(passo + 1)}
                data-testid="agente-proximo"
              >
                Próximo <ArrowRight size={14} weight="regular" />
              </button>
            ) : (
              <button
                type="button"
                className="zz-btn"
                disabled={Boolean(bloqueio) || salvando}
                onClick={salvar}
                data-testid="agente-salvar"
              >
                <FloppyDisk size={14} weight="regular" />
                {salvando ? (form.provedor ? "Testando a chave…" : "Salvando…")
                  : editando ? "Salvar" : "Criar assistente"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
