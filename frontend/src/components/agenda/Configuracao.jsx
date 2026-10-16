import { useState } from "react";
import { toast } from "sonner";
import { Plus, Trash, FloppyDisk, ArrowUp, ArrowDown } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { CampoDuracao, emHoras } from "./CampoHora";

// Preferências de reserva da agência.
//
// **O fuso saiu da tela a pedido do cliente**, e Brasília é o padrão. Ele
// continua existindo no banco e na API (`scheduling_settings.fuso`) porque é
// dele que a grade e o lembrete tiram a hora: o robô roda no servidor e não
// tem como descobrir isso sozinho. O que mudou é que ninguém escolhe: uma
// empresa fora do horário de Brasília é caso de suporte, não de um campo que
// todos os outros clientes precisam entender para ignorar.

// `passo_min` e `intervalo_min` eram da agenda de horários (de quanto em
// quanto oferecer um horário, folga entre atendimentos) e não valem mais para
// nada na fase 1: as saídas vêm da grade de cada passeio. Saíram da tela e do
// envio, e omitir preserva o que estiver gravado.

const PRAZO_PADRAO_HORAS = 24;
const MINUTOS_ANTES_PADRAO = 30;
const MINUTOS_ANTES_MAX = 240;

const TEMPLATE_VAZIO = { nome: "", idioma: "pt_BR", variaveis: [] };

const NOTAS_MENSAGEM = {
  mensagem_cancelamento_saida:
    "Vai para cada grupo da saída quando a agência cancela a saída inteira com “Avisar os turistas” marcado.",
  mensagem_expiracao:
    "Vai quando o prazo para confirmar acaba e a vaga volta para a venda, se o aviso estiver ligado em “Como as reservas são aceitas”.",
};

function corpoTemplate(t) {
  return {
    nome: (t?.nome || "").trim(),
    idioma: t?.idioma || "pt_BR",
    variaveis: (t?.variaveis || []).filter((v) => v !== null),
  };
}

/** Problemas da lista de pontos que o servidor também recusaria, ditos antes. */
function problemaPontos(pontos) {
  const vistos = new Set();
  for (const p of pontos) {
    const nome = (p.nome || "").trim();
    if (!nome) return "Ponto de embarque sem nome";
    const chave = nome.toLocaleLowerCase("pt-BR");
    if (vistos.has(chave)) return `Ponto de embarque repetido: ${nome}`;
    vistos.add(chave);
  }
  return null;
}

export default function Configuracao({ config, onSalvo }) {
  const [form, setForm] = useState(() => ({
    ...config,
    bloqueios: config.bloqueios || [],
    mensagem_lembrete: config.mensagem_lembrete || "",
    mensagem_confirmacao: config.mensagem_confirmacao || "",
    mensagem_cancelamento: config.mensagem_cancelamento || "",
    template_lembrete: config.template_lembrete || TEMPLATE_VAZIO,
    mensagem_cancelamento_saida: config.mensagem_cancelamento_saida || "",
    template_cancelamento_saida: config.template_cancelamento_saida || TEMPLATE_VAZIO,
    mensagem_expiracao: config.mensagem_expiracao || "",
    template_expiracao: config.template_expiracao || TEMPLATE_VAZIO,
    // Ausente = ligado: é o padrão do servidor.
    avisar_expiracao: config.avisar_expiracao !== false,
    pontos_embarque: (config.pontos_embarque || []).map((p) => ({
      ...p, minutos_antes: p.minutos_antes ?? MINUTOS_ANTES_PADRAO, ativo: p.ativo !== false,
    })),
    prazo_solicitacao_horas: config.prazo_solicitacao_horas ?? PRAZO_PADRAO_HORAS,
  }));
  const variaveis = config.variaveis || [];
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const [salvar, salvando] = useAcao(async () => {
    const problema = problemaPontos(form.pontos_embarque || []);
    if (problema) {
      toast.error(problema);
      return;
    }
    try {
      await api.put("/agenda/config", {
        // `fuso` não é enviado: não há campo na tela, e omitir preserva o que
        // estiver gravado (Brasília, por padrão) em vez de sobrescrevê-lo com
        // o que quer que o formulário tenha carregado.
        antecedencia_min_horas: Number(form.antecedencia_min_horas) || 0,
        // Preso na tela também: o campo é livre e um 9999 digitado sem querer
        // voltaria do backend como um 422 do pydantic em inglês.
        antecedencia_max_dias: Math.min(365, Math.max(1, Number(form.antecedencia_max_dias) || 60)),
        cancelamento_min_horas: Number(form.cancelamento_min_horas) || 0,
        // 0 é valor de verdade (a solicitação não expira): `|| 24` o apagaria.
        prazo_solicitacao_horas: Math.min(720, Math.max(0,
          Number(form.prazo_solicitacao_horas ?? PRAZO_PADRAO_HORAS) || 0)),
        lembrete_ativo: !!form.lembrete_ativo,
        lembrete_horas: Number(form.lembrete_horas) || 24,
        bloqueios: (form.bloqueios || []).filter((b) => b.data),
        // String vazia volta ao texto padrão: `ConfigIn` descarta `None`, e é
        // por isso que o campo em branco é enviado como "" e não omitido.
        mensagem_lembrete: form.mensagem_lembrete || "",
        mensagem_confirmacao: form.mensagem_confirmacao || "",
        mensagem_cancelamento: form.mensagem_cancelamento || "",
        template_lembrete: corpoTemplate(form.template_lembrete),
        mensagem_cancelamento_saida: form.mensagem_cancelamento_saida || "",
        template_cancelamento_saida: corpoTemplate(form.template_cancelamento_saida),
        avisar_expiracao: !!form.avisar_expiracao,
        mensagem_expiracao: form.mensagem_expiracao || "",
        template_expiracao: corpoTemplate(form.template_expiracao),
        // A ordem da lista é a ordem em que o turista vê os pontos no WhatsApp.
        // Item sem `id` é novo, e o servidor gera o id.
        pontos_embarque: (form.pontos_embarque || []).map((p) => ({
          ...(p.id ? { id: p.id } : {}),
          nome: p.nome.trim(),
          endereco: (p.endereco || "").trim(),
          minutos_antes: Math.min(MINUTOS_ANTES_MAX, Math.max(0, Number(p.minutos_antes) || 0)),
          ativo: p.ativo !== false,
        })),
      });
      toast.success("Configuração salva");
      onSalvo();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  return (
    <div className="space-y-6 max-w-4xl" data-testid="config-agenda">
      <Bloco titulo="Como as reservas são aceitas">
        {/* `items-start` + rótulo de altura fixa: os campos ficam com o input
            na mesma linha mesmo quando um rótulo quebra em duas.

            As durações são campos `00:00`: o formato já diz a unidade. "Saídas
            abertas por" conta dias, não duração, então continua número, com a
            unidade como sufixo. */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-x-3 gap-y-5 items-start">
          <Campo label="Antecedência mínima" alinhado
            nota="Horas:minutos. Até quanto tempo antes da saída o cliente ainda consegue reservar sozinho.">
            <CampoDuracao minutos={Math.round(Number(form.antecedencia_min_horas || 0) * 60)}
              testid="config-antecedencia"
              onChange={(m) => set({ antecedencia_min_horas: emHoras(m) })} />
          </Campo>
          <Campo label="Saídas abertas por" alinhado sufixo="dias"
            nota="Até onde o cliente consegue reservar. Longe demais enche as saídas de reservas fantasmas.">
            <input type="number" min="1" max="365" className="zz-input tabular-nums pr-14"
              value={form.antecedencia_max_dias}
              onChange={(e) => set({ antecedencia_max_dias: e.target.value })} />
          </Campo>
          <Campo label="Prazo para a agência confirmar uma solicitação" alinhado
            nota="Horas:minutos. A reserva pedida pelo WhatsApp (fluxo ou assistente) chega como Solicitada e segura a vaga por este tempo; sem confirmação, expira e a vaga volta. 00:00 = não expira: segura a vaga até alguém confirmar ou cancelar.">
            <CampoDuracao minutos={Math.round(Number(form.prazo_solicitacao_horas || 0) * 60)}
              testid="config-prazo-solicitacao"
              onChange={(m) => set({ prazo_solicitacao_horas: emHoras(m) })} />
          </Campo>
        </div>
        {Number(form.prazo_solicitacao_horas) > 0 && (
          <label className="flex items-center gap-2 text-sm cursor-pointer mt-4">
            <input type="checkbox" checked={!!form.avisar_expiracao}
              data-testid="config-avisar-expiracao"
              onChange={(e) => set({ avisar_expiracao: e.target.checked })} />
            <span>Avisar o turista pelo WhatsApp quando a solicitação vencer sem confirmação</span>
          </label>
        )}
      </Bloco>

      <Bloco titulo="Lembrete automático"
        nota="Turista que perde o passeio por não lembrar do horário é lugar vazio na saída. O cliente responde CONFIRMAR ou CANCELAR na própria conversa, e as saídas se ajustam sozinhas.">
        <label className="flex items-center gap-2 text-sm cursor-pointer mb-3">
          <input type="checkbox" checked={!!form.lembrete_ativo} data-testid="config-lembrete-ativo"
            onChange={(e) => set({ lembrete_ativo: e.target.checked })} />
          <span>Mandar lembrete pelo WhatsApp antes da saída</span>
        </label>
        {form.lembrete_ativo && (
          <>
            <Campo label="Quanto tempo antes"
              nota="Horas:minutos, de 01:00 a 99:59. 24:00 é o mais comum, um dia antes.">
              <CampoDuracao minutos={Math.round(Number(form.lembrete_horas || 0) * 60)}
                min={60} testid="config-lembrete-horas"
                onChange={(m) => set({ lembrete_horas: emHoras(m) })} />
            </Campo>

            {/* O lembrete sai um dia antes, quando quase sempre já se passaram
                24h desde a última mensagem do contato. Nessa situação o
                WhatsApp só entrega modelo aprovado: sem um configurado aqui, o
                aviso simplesmente não sai, e o motivo fica no histórico da
                conversa. Com a janela aberta (o contato acabou de escrever), o
                texto livre acima continua sendo o que vale. */}
            <Campo label="Modelo aprovado do lembrete"
              nota="Nome exato do modelo na sua conta da Meta. Fora da janela de 24h, é ele que o WhatsApp entrega; sem modelo, o lembrete não sai.">
              <input className="zz-input" data-testid="config-template-lembrete"
                placeholder="ex: lembrete_passeio"
                value={form.template_lembrete?.nome || ""}
                onChange={(e) => set({
                  template_lembrete: { ...form.template_lembrete, nome: e.target.value },
                })} />
            </Campo>
            <Campo label="Variáveis do modelo, na ordem"
              nota="Uma por linha, na ordem de {{1}}, {{2}}... Aceita as variáveis listadas em Textos das mensagens.">
              <textarea rows={3} className="zz-input tabular-nums text-xs"
                data-testid="config-template-variaveis"
                placeholder={"{contato}\n{servico}\n{data_completa}"}
                value={(form.template_lembrete?.variaveis || []).join("\n")}
                onChange={(e) => set({
                  template_lembrete: {
                    ...form.template_lembrete,
                    variaveis: e.target.value.split("\n"),
                  },
                })} />
            </Campo>
          </>
        )}
      </Bloco>

      <PontosEmbarque pontos={form.pontos_embarque || []}
        onChange={(pontos_embarque) => set({ pontos_embarque })} />

      <Bloco titulo="Feriados e fechamentos"
        nota="Fecham as saídas de todos os passeios naquele dia, e ganham das exceções de cada passeio. Para fechar um passeio só (chuva, manutenção), use as exceções dele, em Passeios.">
        <div className="space-y-2">
          {(form.bloqueios || []).map((b, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <input type="date" className="zz-input tabular-nums max-w-[170px]" value={b.data || ""}
                onChange={(e) => set({
                  bloqueios: form.bloqueios.map((x, k) =>
                    (k === i ? { ...x, data: e.target.value } : x)),
                })} />
              <input className="zz-input flex-1 min-w-[160px]" placeholder="Motivo (opcional)"
                value={b.motivo || ""}
                onChange={(e) => set({
                  bloqueios: form.bloqueios.map((x, k) =>
                    (k === i ? { ...x, motivo: e.target.value } : x)),
                })} />
              <button type="button" className="zz-btn-icon" aria-label="Remover data"
                onClick={() => set({ bloqueios: form.bloqueios.filter((_, k) => k !== i) })}>
                <Trash size={14} weight="regular" />
              </button>
            </div>
          ))}
          <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid="add-bloqueio"
            onClick={() => set({ bloqueios: [...(form.bloqueios || []), { data: "", motivo: "" }] })}>
            <Plus size={12} weight="regular" /> Adicionar data
          </button>
        </div>
      </Bloco>

      <Bloco titulo="Textos das mensagens"
        nota="Em branco, vale o texto padrão mostrado como sugestão. As variáveis abaixo são trocadas pelos dados da reserva na hora do envio.">
        {variaveis.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2" data-testid="config-variaveis">
            {variaveis.map((v) => (typeof v === "string" ? v : v?.nome || "")).filter(Boolean).map((v) => (
              <code key={v} className="text-[11px] font-mono px-2 py-0.5 rounded-md bg-surface-2 border border-line text-ink-2">
                {v}
              </code>
            ))}
          </div>
        )}
        <p className="text-[11px] text-ink-3 mb-4" data-testid="config-variaveis-embarque">
          <code className="font-mono">{"{busca}"}</code> vira “07:30 – Pousada do Rio”, ou
          “Pousada do Rio (horário a combinar com a nossa equipe)”, ou “a combinar com a nossa
          equipe” quando a reserva não tem ponto. <code className="font-mono">{"{motivo}"}</code> é
          o motivo do cancelamento da saída.
        </p>
        <div className="space-y-4">
          {[
            ["mensagem_confirmacao", "Confirmação da reserva"],
            ["mensagem_lembrete", "Lembrete, antes da saída"],
            ["mensagem_cancelamento", "Aviso de cancelamento"],
            ["mensagem_cancelamento_saida", "Saída cancelada (chuva, maré, lagoa seca)"],
            ["mensagem_expiracao", "Solicitação vencida sem confirmação"],
          ].map(([campo, rotulo]) => (
            <Campo key={campo} label={rotulo}
              nota={NOTAS_MENSAGEM[campo] || null}>
              <textarea rows={4} className="zz-input" value={form[campo] || ""}
                data-testid={`config-${campo}`}
                placeholder={config.mensagens_padrao?.[campo.replace("mensagem_", "")] || ""}
                onChange={(e) => set({ [campo]: e.target.value })} />
            </Campo>
          ))}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-start">
            <Campo label="Modelo aprovado do cancelamento de saída"
              nota="Nome exato do modelo na sua conta da Meta. Fora da janela de 24h só sai modelo aprovado; sem ele, quem não escreveu nas últimas 24h fica sem aviso, e o resultado do cancelamento diz quantos.">
              <input className="zz-input" data-testid="config-template-cancelamento-saida"
                placeholder="ex: saida_cancelada"
                value={form.template_cancelamento_saida?.nome || ""}
                onChange={(e) => set({
                  template_cancelamento_saida: { ...form.template_cancelamento_saida, nome: e.target.value },
                })} />
            </Campo>
            <Campo label="Variáveis do modelo, na ordem"
              nota="Uma por linha, na ordem de {{1}}, {{2}}...">
              <textarea rows={4} className="zz-input tabular-nums text-xs"
                data-testid="config-template-cancelamento-saida-variaveis"
                placeholder={"{contato}\n{servico}\n{data_completa}\n{motivo}"}
                value={(form.template_cancelamento_saida?.variaveis || []).join("\n")}
                onChange={(e) => set({
                  template_cancelamento_saida: {
                    ...form.template_cancelamento_saida,
                    variaveis: e.target.value.split("\n"),
                  },
                })} />
            </Campo>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-start">
            <Campo label="Modelo aprovado da solicitação vencida"
              nota="Nome exato do modelo na sua conta da Meta. Sem ele, o aviso só sai para quem escreveu nas últimas 24h, e o motivo fica no histórico da conversa.">
              <input className="zz-input" data-testid="config-template-expiracao"
                placeholder="ex: solicitacao_vencida"
                value={form.template_expiracao?.nome || ""}
                onChange={(e) => set({
                  template_expiracao: { ...form.template_expiracao, nome: e.target.value },
                })} />
            </Campo>
            <Campo label="Variáveis do modelo, na ordem"
              nota="Uma por linha, na ordem de {{1}}, {{2}}...">
              <textarea rows={3} className="zz-input tabular-nums text-xs"
                data-testid="config-template-expiracao-variaveis"
                placeholder={"{contato}\n{servico}\n{data_completa}"}
                value={(form.template_expiracao?.variaveis || []).join("\n")}
                onChange={(e) => set({
                  template_expiracao: {
                    ...form.template_expiracao,
                    variaveis: e.target.value.split("\n"),
                  },
                })} />
            </Campo>
          </div>
        </div>
      </Bloco>

      <div className="flex justify-end">
        <button type="button" className="zz-btn" onClick={salvar} disabled={salvando}
          data-testid="salvar-config-agenda">
          <FloppyDisk size={14} weight="regular" /> {salvando ? "Salvando…" : "Salvar configuração"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pontos de embarque
// ---------------------------------------------------------------------------
//
// Onde a equipe busca o turista (pousada, hotel, praça) e quanto tempo antes
// da saída. A ordem daqui é a ordem da lista que o turista recebe no WhatsApp,
// por isso dá para subir e descer. Remover um ponto não mexe nas reservas: cada
// uma guardou o nome e o horário de busca.

function PontosEmbarque({ pontos, onChange }) {
  const mexer = (i, patch) => onChange(pontos.map((p, k) => (k === i ? { ...p, ...patch } : p)));
  const mover = (i, d) => {
    const j = i + d;
    if (j < 0 || j >= pontos.length) return;
    const nova = [...pontos];
    [nova[i], nova[j]] = [nova[j], nova[i]];
    onChange(nova);
  };
  return (
    <Bloco titulo="Pontos de embarque"
      nota="Onde a equipe busca os turistas e quanto tempo antes da saída. O fluxo automático e o assistente de IA oferecem estes pontos, nesta ordem, e o horário de busca de cada reserva sai daqui. Desativado, o ponto some das opções; as reservas que já o usam continuam como estão.">
      <div className="space-y-2" data-testid="config-pontos-embarque">
        {pontos.map((p, i) => (
          <div key={p.id || `novo-${i}`}
            className={`border border-line rounded-lg p-3 grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto_auto] gap-3 items-start ${
              p.ativo === false ? "opacity-60" : ""}`}
            data-testid={`ponto-embarque-${i}`}>
            <div>
              <label className="text-xs text-ink-3 block mb-1">Nome</label>
              <input className="zz-input" value={p.nome || ""} placeholder="Pousada do Rio"
                data-testid={`ponto-embarque-${i}-nome`}
                onChange={(e) => mexer(i, { nome: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-ink-3 block mb-1">Endereço</label>
              <input className="zz-input" value={p.endereco || ""}
                placeholder="Rua, número, bairro (opcional)"
                data-testid={`ponto-embarque-${i}-endereco`}
                onChange={(e) => mexer(i, { endereco: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-ink-3 block mb-1">Antes da saída</label>
              <CampoDuracao minutos={p.minutos_antes ?? MINUTOS_ANTES_PADRAO} max={MINUTOS_ANTES_MAX}
                testid={`ponto-embarque-${i}-minutos`}
                onChange={(m) => mexer(i, { minutos_antes: m })} />
            </div>
            <div className="flex items-center gap-1 md:pt-6">
              <label className="flex items-center gap-1.5 text-xs cursor-pointer mr-2">
                <input type="checkbox" checked={p.ativo !== false}
                  data-testid={`ponto-embarque-${i}-ativo`}
                  onChange={(e) => mexer(i, { ativo: e.target.checked })} />
                Ativo
              </label>
              <button type="button" className="zz-btn-icon" aria-label="Subir" title="Subir"
                disabled={i === 0} onClick={() => mover(i, -1)}>
                <ArrowUp size={14} weight="regular" />
              </button>
              <button type="button" className="zz-btn-icon" aria-label="Descer" title="Descer"
                disabled={i === pontos.length - 1} onClick={() => mover(i, 1)}>
                <ArrowDown size={14} weight="regular" />
              </button>
              <button type="button" className="zz-btn-icon" aria-label="Remover ponto" title="Remover ponto"
                data-testid={`ponto-embarque-${i}-remover`}
                onClick={() => onChange(pontos.filter((_, k) => k !== i))}>
                <Trash size={14} weight="regular" />
              </button>
            </div>
          </div>
        ))}
        {pontos.length === 0 && (
          <p className="text-xs text-ink-3">
            Nenhum ponto cadastrado. Sem pontos ativos, o fluxo automático não pergunta onde
            buscar, e o embarque de cada reserva é anotado à mão.
          </p>
        )}
        <button type="button" className="zz-btn zz-btn-secondary text-xs" data-testid="add-ponto-embarque"
          onClick={() => onChange([...pontos, {
            nome: "", endereco: "", minutos_antes: MINUTOS_ANTES_PADRAO, ativo: true,
          }])}>
          <Plus size={12} weight="regular" /> Adicionar ponto
        </button>
        <p className="text-[11px] text-ink-3">
          “Antes da saída” em horas:minutos, até 04:00. Com 00:30 e saída às 08:00, a busca é às 07:30.
          As mudanças só valem depois de salvar a configuração.
        </p>
      </div>
    </Bloco>
  );
}

function Bloco({ titulo, nota, children }) {
  return (
    <section className="border border-line rounded-xl p-5 bg-surface">
      <h3 className="font-heading text-lg font-semibold tracking-tight">{titulo}</h3>
      {nota && <p className="text-xs text-ink-2 mt-1 mb-4">{nota}</p>}
      {!nota && <div className="mb-4" />}
      {children}
    </section>
  );
}

function Campo({ label, nota, sufixo, alinhado, children }) {
  return (
    <div>
      {/* Com `alinhado`, o rótulo ocupa sempre a altura de duas linhas e o
          texto encosta embaixo. É o que mantém os campos de uma fileira com o
          input na mesma altura, independente de o rótulo caber em uma linha ou
          duas, e não depende de ninguém lembrar de manter os rótulos curtos. */}
      <label className={`text-sm font-medium text-ink-2 mb-1.5 ${
        alinhado ? "flex items-end min-h-[2.25rem]" : "block"}`}>
        {label}
      </label>
      {sufixo ? (
        <div className="relative">
          {children}
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-ink-3 pointer-events-none">
            {sufixo}
          </span>
        </div>
      ) : children}
      {nota && <p className="text-[11px] text-ink-3 mt-1">{nota}</p>}
    </div>
  );
}
