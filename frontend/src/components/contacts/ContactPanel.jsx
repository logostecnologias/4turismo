import { useCallback, useEffect, useRef, useState } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";
import { formatBRL, parseBRLInput } from "@/lib/currency";
import { useAcao } from "@/hooks/useAcao";
import Modal from "@/components/ui/Modal";
import Atividades from "@/components/activities/Atividades";
import { fotoDoContato } from "@/components/atendimento/shared";
import {
  X, PaperPlaneRight, Trash, Prohibit, Check, ChatCircleDots,
  NotePencil, Warning, ArrowSquareOut, Funnel, Plus, Trash as TrashIcon, ShieldCheck,
} from "@phosphor-icons/react";

const KANBAN_LABEL = {
  bot: "no fluxo automático",
  aguardando: "aguardando",
  em_atendimento: "em atendimento",
  finalizado: "finalizado",
};

function Bloco({ titulo, children, acao }) {
  return (
    <div className="border-t border-line px-5 py-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-medium text-ink-3">{titulo}</div>
        {acao}
      </div>
      {children}
    </div>
  );
}

function Linha({ label, children }) {
  return (
    <div className="grid grid-cols-3 gap-2 items-center py-1">
      <label className="text-sm font-medium text-ink-3">{label}</label>
      <div className="col-span-2">{children}</div>
    </div>
  );
}

function CampoPersonalizado({ campo, valor, onChange }) {
  const comum = { className: "zz-input h-8 py-1 text-sm", value: valor ?? "" };
  if (campo.tipo === "selecao") {
    return (
      <select {...comum} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {(campo.opcoes || []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }
  const tipo = campo.tipo === "numero" ? "number" : campo.tipo === "data" ? "date" : "text";
  return <input {...comum} type={tipo} onChange={(e) => onChange(e.target.value)} />;
}

export default function ContactPanel({ contactId, onClose, onSaved }) {
  const [ficha, setFicha] = useState(null);
  const [form, setForm] = useState(null);
  const [usuarios, setUsuarios] = useState([]);
  const [conexoes, setConexoes] = useState([]);
  const [nota, setNota] = useState("");
  const [mensagem, setMensagem] = useState("");
  const [conexaoEnvio, setConexaoEnvio] = useState("");
  const [negocios, setNegocios] = useState([]);
  const [criandoNegocio, setCriandoNegocio] = useState(null);
  const [funis, setFunis] = useState([]);
  const [enviando, setEnviando] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [excluindo, setExcluindo] = useState(false);
  const areaRef = useRef(null);

  const carregar = useCallback(() => {
    api.get(`/contacts/${contactId}`).then((r) => {
      setFicha(r.data);
      const c = r.data.contact;
      setForm({
        nome: c.nome || "",
        email: c.email || "",
        empresa: c.empresa || "",
        cargo: c.cargo || "",
        origem: c.origem || "",
        responsavel_id: c.responsavel_id || "",
        tags: (c.tags || []).join(", "),
        campos: Object.fromEntries((r.data.campos || []).map((f) => [f.id, f.valor ?? ""])),
      });
    }).catch((ex) => {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
      onClose();
    });
  }, [contactId, onClose]);

  const carregarNegocios = useCallback(() => {
    api.get("/deals", { params: { contact_id: contactId } })
      .then((r) => setNegocios(r.data.items || [])).catch(() => {});
  }, [contactId]);

  useEffect(() => { carregar(); carregarNegocios(); }, [carregar, carregarNegocios]);

  useEffect(() => {
    api.get("/users").then((r) => setUsuarios(r.data)).catch(() => {});
    api.get("/whatsapp/connections").then((r) => setConexoes(r.data)).catch(() => {});
    api.get("/pipelines").then((r) => setFunis(r.data)).catch(() => {});
  }, []);

  const salvar = async () => {
    setSalvando(true);
    try {
      await api.patch(`/contacts/${contactId}`, {
        ...form,
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        responsavel_id: form.responsavel_id || null,
      });
      toast.success("Ficha salva");
      carregar();
      onSaved?.();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setSalvando(false);
    }
  };

  const alternarOptOut = async () => {
    const atual = ficha.contact.opt_out;
    if (!atual && !(await confirmToast(
      "Marcar que este cliente pediu para não receber mensagens em massa?",
      { confirmLabel: "Marcar" }))) return;
    try {
      await api.patch(`/contacts/${contactId}`, { opt_out: !atual });
      carregar();
      onSaved?.();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const [adicionarNota, anotando] = useAcao(async (e) => {
    e.preventDefault();
    if (!nota.trim()) return;
    try {
      await api.post(`/contacts/${contactId}/notes`, { texto: nota });
      setNota("");
      carregar();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  });

  const removerNota = async (id) => {
    if (!(await confirmToast("Excluir esta anotação?", { confirmLabel: "Excluir" }))) return;
    await api.delete(`/contacts/${contactId}/notes/${id}`);
    carregar();
  };

  // A trava de clique repetido vem do Modal.
  const criarNegocio = async () => {
    try {
      await api.post("/deals", {
        titulo: criandoNegocio.titulo,
        contact_id: contactId,
        pipeline_id: criandoNegocio.pipeline_id,
        valor: criandoNegocio.valor,
      });
      setCriandoNegocio(null);
      toast.success("Orçamento criado");
      carregarNegocios();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const excluir = async (modo) => {
    try {
      const r = await api.delete(`/contacts/${contactId}`, { params: { modo } });
      toast.success(modo === "apagar"
        ? `Cliente apagado (${r.data.conversas_apagadas} conversa(s), ${r.data.negocios_apagados} orçamento(s))`
        : `Dados pessoais removidos (${r.data.conversas_preservadas} conversa(s) preservada(s))`);
      onSaved?.();
      onClose();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const enviar = async (e) => {
    e.preventDefault();
    if (!mensagem.trim() || enviando) return;
    setEnviando(true);
    try {
      const r = await api.post(`/contacts/${contactId}/send`, {
        texto: mensagem,
        whatsapp_connection_id: conexaoEnvio || null,
      });
      setMensagem("");
      toast.success(r.data.criada ? "Conversa iniciada e mensagem enviada" : "Mensagem enviada");
      carregar();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setEnviando(false);
    }
  };

  if (!ficha || !form) {
    return (
      <aside className="fixed right-0 top-0 h-screen w-[520px] bg-surface border-l border-line shadow-pop z-40 flex items-center justify-center">
        <div className="text-sm font-medium text-ink-3">Carregando ficha…</div>
      </aside>
    );
  }

  const c = ficha.contact;
  const r = ficha.resumo;
  const conectadas = conexoes.filter((x) => x.status === "conectado");

  return (
    <aside className="fixed right-0 top-0 h-screen w-[520px] bg-surface border-l border-line shadow-pop z-40 flex flex-col"
      data-testid="contact-panel">
      <div className="px-5 py-4 border-b border-line-strong flex items-start gap-3">
        {fotoDoContato(c)
          ? <img src={fotoDoContato(c)} alt="" className="w-11 h-11 border border-line-strong object-cover rounded-full" />
          : <div className="w-11 h-11 bg-brand text-brand-fg flex items-center justify-center font-heading">
              {(c.nome || "?").charAt(0).toUpperCase()}
            </div>}
        <div className="flex-1 min-w-0">
          <div className="font-heading text-xl font-semibold truncate">{c.nome}</div>
          <div className="tabular-nums text-xs text-ink-2">{c.numero_whatsapp}</div>
        </div>
        <button onClick={onClose} data-testid="contact-panel-close" className="p-1.5 hover:bg-surface-3">
          <X size={16} weight="regular" />
        </button>
      </div>

      {c.opt_out && (
        <div className="px-5 py-2 bg-danger-soft border-b border-danger text-xs text-danger flex items-center gap-2">
          <Prohibit size={13} weight="regular" />
          Pediu para não receber mensagens em massa{c.opt_out_em ? ` desde ${new Date(c.opt_out_em).toLocaleDateString("pt-BR")}` : ""}.
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        <div className="grid grid-cols-2 border-b border-line">
          {[["Conversas", r.conversas], ["Mensagens", r.mensagens]].map(([k, v]) => (
            <div key={k} className="px-3 py-2.5 border-r border-line last:border-r-0">
              <div className="text-sm font-medium text-ink-3">{k}</div>
              <div className="font-heading text-lg font-semibold">{v}</div>
            </div>
          ))}
        </div>

        <Bloco titulo="Dados" acao={
          <button onClick={salvar} disabled={salvando} className="zz-btn text-xs py-1 disabled:opacity-40"
            data-testid="contact-save">{salvando ? "Salvando…" : "Salvar"}</button>
        }>
          <Linha label="Nome">
            <input className="zz-input h-8 py-1 text-sm" data-testid="contact-field-nome" value={form.nome}
              onChange={(e) => setForm({ ...form, nome: e.target.value })} />
          </Linha>
          <Linha label="E-mail">
            <input className="zz-input h-8 py-1 text-sm" type="email" data-testid="contact-field-email"
              value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Linha>
          <Linha label="Empresa">
            <input className="zz-input h-8 py-1 text-sm" value={form.empresa}
              onChange={(e) => setForm({ ...form, empresa: e.target.value })} />
          </Linha>
          <Linha label="Cargo">
            <input className="zz-input h-8 py-1 text-sm" value={form.cargo}
              onChange={(e) => setForm({ ...form, cargo: e.target.value })} />
          </Linha>
          <Linha label="Origem">
            <input className="zz-input h-8 py-1 text-sm" value={form.origem}
              placeholder="ex: indicação, site, Instagram"
              onChange={(e) => setForm({ ...form, origem: e.target.value })} />
          </Linha>
          <Linha label="Responsável">
            <select className="zz-input h-8 py-1 text-sm" data-testid="contact-field-responsavel"
              value={form.responsavel_id} onChange={(e) => setForm({ ...form, responsavel_id: e.target.value })}>
              <option value="">Sem responsável</option>
              {usuarios.map((u) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
            </select>
          </Linha>
          <Linha label="Etiquetas">
            <input className="zz-input h-8 py-1 text-sm" data-testid="contact-field-tags" value={form.tags}
              placeholder="vip, recorrente, primeira viagem"
              onChange={(e) => setForm({ ...form, tags: e.target.value })} />
          </Linha>
          <Linha label="Mensagens em massa">
            <button onClick={alternarOptOut} data-testid="contact-optout-toggle"
              className={`flex items-center gap-1.5 px-2 py-1 text-xs font-medium border rounded-full ${
                c.opt_out ? "border-danger bg-danger-soft text-danger" : "border-success bg-success-soft text-success"
              }`}>
              {c.opt_out ? <Prohibit size={12} weight="regular" /> : <Check size={12} weight="regular" />}
              {c.opt_out ? "não recebe" : "recebe"}
            </button>
          </Linha>
        </Bloco>

        {ficha.campos.length > 0 && (
          <Bloco titulo="Campos personalizados">
            {ficha.campos.map((campo) => (
              <Linha key={campo.id} label={campo.nome}>
                <CampoPersonalizado campo={campo} valor={form.campos[campo.id]}
                  onChange={(v) => setForm({ ...form, campos: { ...form.campos, [campo.id]: v } })} />
              </Linha>
            ))}
            <div className="text-[10px] text-ink-3 mt-2">Salve em "Dados" para gravar também estes campos.</div>
          </Bloco>
        )}

        <Bloco titulo={`Anotações (${ficha.notas.length})`}>
          <form onSubmit={adicionarNota} className="flex gap-2 mb-3">
            <input className="zz-input h-8 py-1 text-sm flex-1" data-testid="contact-note-input"
              placeholder="Ligou dia 12, quer o orçamento até sexta…"
              value={nota} onChange={(e) => setNota(e.target.value)} />
            <button type="submit" className="zz-btn text-xs py-1" disabled={anotando}>
              <NotePencil size={13} weight="regular" />
            </button>
          </form>
          {ficha.notas.length === 0 && <div className="text-xs text-ink-4">Nenhuma anotação.</div>}
          <div className="space-y-2">
            {ficha.notas.map((n) => (
              <div key={n.id} className="border border-line bg-surface-2 px-3 py-2 rounded-lg" data-testid={`note-${n.id}`}>
                <div className="flex items-start justify-between gap-2">
                  <div className="text-sm text-ink whitespace-pre-wrap flex-1">{n.texto}</div>
                  <button onClick={() => removerNota(n.id)} className="p-0.5 hover:bg-danger-soft text-danger shrink-0">
                    <Trash size={12} weight="regular" />
                  </button>
                </div>
                <div className="text-[10px] tabular-nums text-ink-3 mt-1">
                  {n.autor_nome} · {new Date(n.created_at).toLocaleString("pt-BR")}
                </div>
              </div>
            ))}
          </div>
        </Bloco>

        <Bloco titulo="Próximos passos">
          <Atividades vinculo={{ contact_id: contactId }} />
        </Bloco>

        <Bloco titulo={`Orçamentos (${negocios.length})`} acao={
          funis.length > 0 && (
            <button onClick={() => setCriandoNegocio({
              titulo: `Orçamento de ${c.nome}`, valor: 0, pipeline_id: funis[0].id,
            })} className="zz-btn text-xs py-1" data-testid="contact-new-deal">
              <Plus size={12} weight="regular" /> Novo
            </button>
          )
        }>
          {criandoNegocio && (
            <Modal titulo="Novo orçamento" descricao={`Para o cliente ${c.nome}.`}
              onFechar={() => setCriandoNegocio(null)} onSalvar={criarNegocio}
              salvarLabel="Criar" salvandoLabel="Criando…" testId="contact-deal-form">
              <div>
                <label className="text-sm font-medium text-ink-2 block mb-1.5">Título</label>
                <input required autoFocus className="zz-input" value={criandoNegocio.titulo}
                  onChange={(e) => setCriandoNegocio({ ...criandoNegocio, titulo: e.target.value })} />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium text-ink-2 block mb-1.5">Valor</label>
                  <input className="zz-input tabular-nums"
                    value={formatBRL(criandoNegocio.valor)}
                    onChange={(e) => setCriandoNegocio({
                      ...criandoNegocio, valor: parseBRLInput(e.target.value),
                    })} />
                </div>
                {funis.length > 1 && (
                  <div>
                    <label className="text-sm font-medium text-ink-2 block mb-1.5">Quadro</label>
                    <select className="zz-input" value={criandoNegocio.pipeline_id}
                      onChange={(e) => setCriandoNegocio({ ...criandoNegocio, pipeline_id: e.target.value })}>
                      {funis.map((f) => <option key={f.id} value={f.id}>{f.nome}</option>)}
                    </select>
                  </div>
                )}
              </div>
            </Modal>
          )}
          {negocios.length === 0 && !criandoNegocio && (
            <div className="text-xs text-ink-4">Nenhum orçamento para este cliente.</div>
          )}
          <div className="space-y-1.5">
            {negocios.map((n) => (
              <a key={n.id} href={`/app/funil?negocio=${n.id}`}
                className="flex items-center gap-2 border border-line px-3 py-2 hover:bg-surface-2 rounded-lg"
                data-testid={`contact-deal-${n.id}`}>
                <Funnel size={14} weight="regular" className="text-ink-3 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-semibold truncate">{n.titulo}</div>
                  <div className="text-[10px] tabular-nums text-ink-3">{formatBRL(n.valor)}</div>
                </div>
                <span className={`text-xs font-medium px-1.5 py-0.5 border rounded-full shrink-0 ${
                  n.status === "ganho" ? "border-success bg-success-soft text-success"
                    : n.status === "perdido" ? "border-danger bg-danger-soft text-danger"
                    : "border-line bg-surface-2 text-ink-2"
                }`}>
                  {{ ganho: "aprovado", perdido: "não aprovado" }[n.status] || n.status}
                </span>
              </a>
            ))}
          </div>
        </Bloco>

        <Bloco titulo={`Conversas (${ficha.conversas.length})`}>
          {ficha.conversas.length === 0 && <div className="text-xs text-ink-4">Nenhuma conversa ainda.</div>}
          <div className="space-y-1.5">
            {ficha.conversas.map((s) => (
              <a key={s.id} href={`/app/inbox?session=${s.id}`}
                className="flex items-center gap-2 border border-line px-3 py-2 hover:bg-surface-2 rounded-lg">
                <ChatCircleDots size={14} weight="regular" className="text-ink-3 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-xs truncate text-ink">
                    {s.last_message?.content || "(sem mensagens)"}
                  </div>
                  <div className="text-[10px] tabular-nums text-ink-3">
                    {KANBAN_LABEL[s.status_atendimento] || s.status_atendimento}
                    {s.origem === "direta" ? " · iniciada por nós" : ""}
                    {" · "}{new Date(s.updated_at).toLocaleDateString("pt-BR")}
                  </div>
                </div>
                <ArrowSquareOut size={12} weight="regular" className="text-ink-4 shrink-0" />
              </a>
            ))}
          </div>
        </Bloco>

        <Bloco titulo="Dados do titular (LGPD)">
          {!excluindo ? (
            <button onClick={() => setExcluindo(true)} data-testid="abrir-exclusao"
              className="zz-btn zz-btn-secondary text-xs py-1 text-danger border-danger hover:bg-danger-soft">
              <TrashIcon size={12} weight="regular" /> Remover este cliente
            </button>
          ) : (
            <div className="space-y-2">
              <button onClick={() => excluir("anonimizar")} data-testid="anonimizar"
                className="w-full text-left border border-line bg-surface px-3 py-2 hover:border-line-strong rounded-lg">
                <div className="flex items-center gap-1.5 text-sm font-semibold">
                  <ShieldCheck size={13} weight="regular" className="text-success" />
                  Remover dados pessoais
                </div>
                <div className="text-[11px] text-ink-2 mt-0.5">
                  Nome, número, e-mail, campos e notas somem; o texto das mensagens é redigido e a
                  mídia sai do disco. As conversas e os orçamentos continuam existindo, para os
                  relatórios de meses passados não mudarem. <strong>É esta que atende ao pedido
                  do titular.</strong>
                </div>
              </button>
              <button onClick={() => excluir("apagar")} data-testid="apagar"
                className="w-full text-left border border-danger bg-danger-soft px-3 py-2 hover:border-danger rounded-lg">
                <div className="flex items-center gap-1.5 text-sm font-semibold text-danger">
                  <TrashIcon size={13} weight="regular" /> Apagar tudo
                </div>
                <div className="text-[11px] text-ink mt-0.5">
                  Leva conversas, mensagens, orçamentos e histórico. A receita desses orçamentos sai
                  dos relatórios. Use para cliente de teste ou importação errada.
                </div>
              </button>
              <button onClick={() => setExcluindo(false)}
                className="text-[11px] text-ink-2 underline">Cancelar</button>
            </div>
          )}
        </Bloco>
      </div>

      <form onSubmit={enviar} className="border-t border-line p-3 bg-surface-2" data-testid="contact-send-form">
        {conectadas.length === 0 ? (
          <div className="text-xs text-ink-2 flex items-center gap-2">
            <Warning size={14} weight="regular" className="text-warning" />
            Nenhuma conexão de WhatsApp conectada — não é possível enviar agora.
          </div>
        ) : (
          <>
            {c.opt_out && (
              <div className="text-[11px] text-warning mb-2 flex items-start gap-1.5">
                <Warning size={12} weight="regular" className="mt-0.5 shrink-0" />
                Este cliente pediu para não receber mensagens em massa. Mensagem direta ainda é
                permitida: a recusa não vale para atendimento.
              </div>
            )}
            <div className="flex gap-2 items-end">
              <textarea ref={areaRef} rows={2} required data-testid="contact-send-text"
                className="zz-input flex-1 text-sm font-sans resize-none"
                placeholder="Escrever para este cliente…"
                value={mensagem} onChange={(e) => setMensagem(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); enviar(e); }
                }} />
              <button type="submit" disabled={enviando || !mensagem.trim()}
                data-testid="contact-send-btn" className="zz-btn disabled:opacity-40">
                <PaperPlaneRight size={14} weight="regular" />
              </button>
            </div>
            {conectadas.length > 1 && (
              <select className="zz-input h-7 py-0 text-xs mt-2" data-testid="contact-send-connection"
                value={conexaoEnvio} onChange={(e) => setConexaoEnvio(e.target.value)}>
                <option value="">Número da última conversa</option>
                {conectadas.map((x) => <option key={x.id} value={x.id}>{x.nome}</option>)}
              </select>
            )}
          </>
        )}
      </form>
    </aside>
  );
}
