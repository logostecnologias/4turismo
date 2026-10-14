import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import api, { formatApiErrorDetail } from "@/lib/api";
import Modal from "@/components/ui/Modal";
import { toast } from "sonner";
import { MagnifyingGlass, Prohibit, Plus, SlidersHorizontal, X, ArrowsDownUp, ArrowsMerge } from "@phosphor-icons/react";
import ContactPanel from "@/components/contacts/ContactPanel";
import FieldsManager from "@/components/contacts/FieldsManager";
import ImportExport from "@/components/contacts/ImportExport";
import Duplicados from "@/components/contacts/Duplicados";

const POR_PAGINA = 50;

export default function ContactsPage() {
  const [contatos, setContatos] = useState([]);
  const [total, setTotal] = useState(0);
  const [busca, setBusca] = useState("");
  const [tagFiltro, setTagFiltro] = useState("");
  const [responsavelFiltro, setResponsavelFiltro] = useState("");
  const [tags, setTags] = useState([]);
  const [usuarios, setUsuarios] = useState([]);
  const [pagina, setPagina] = useState(0);
  const [aberto, setAberto] = useState(null);
  const [mostrarCampos, setMostrarCampos] = useState(false);
  const [mostrarCsv, setMostrarCsv] = useState(false);
  const [mostrarDup, setMostrarDup] = useState(false);
  const [novo, setNovo] = useState(null);
  // Link direto vindo do funil ("ver a ficha do contato deste negócio").
  const [params] = useSearchParams();
  useEffect(() => {
    const id = params.get("contato");
    if (id) setAberto(id);
  }, [params]);

  const carregar = useCallback(() => {
    api.get("/contacts", {
      params: {
        q: busca || undefined,
        tag: tagFiltro || undefined,
        responsavel_id: responsavelFiltro || undefined,
        skip: pagina * POR_PAGINA,
        limit: POR_PAGINA,
      },
    }).then((r) => {
      setContatos(r.data.items || []);
      setTotal(r.data.total || 0);
    }).catch(() => {});
  }, [busca, tagFiltro, responsavelFiltro, pagina]);

  const carregarTags = () => api.get("/contacts/tags").then((r) => setTags(r.data)).catch(() => {});

  // Debounce na busca: cada tecla digitada dispararia uma consulta com regex
  // sobre a base inteira.
  useEffect(() => {
    const t = setTimeout(carregar, 300);
    return () => clearTimeout(t);
  }, [carregar]);

  useEffect(() => {
    carregarTags();
    api.get("/users").then((r) => setUsuarios(r.data)).catch(() => {});
  }, []);
  useEffect(() => { setPagina(0); }, [busca, tagFiltro, responsavelFiltro]);

  // A trava de clique repetido vem do Modal.
  const criar = async () => {
    try {
      const r = await api.post("/contacts", novo);
      setNovo(null);
      toast.success("Cliente cadastrado");
      carregar();
      setAberto(r.data.id);
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    }
  };

  const nomeUsuario = (id) => {
    const u = usuarios.find((x) => x.id === id);
    return u ? (u.name || u.email) : null;
  };

  const paginas = Math.ceil(total / POR_PAGINA);

  return (
    <div className={`p-8 ${aberto ? "pr-[540px]" : ""}`} data-testid="contacts-page">
      <div className="flex items-end justify-between mb-6">
        <div>
          <h1 className="font-heading text-4xl font-semibold">Clientes</h1>
          <p className="text-ink-2 mt-2 text-sm">
            Clique num cliente para abrir a ficha, anotar, atribuir responsável e enviar mensagem.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => { setMostrarDup((v) => !v); setMostrarCsv(false); setMostrarCampos(false); }}
            className="zz-btn zz-btn-secondary" data-testid="toggle-dup-btn">
            <ArrowsMerge size={14} weight="regular" /> Duplicados
          </button>
          <button onClick={() => { setMostrarCsv((v) => !v); setMostrarCampos(false); setMostrarDup(false); }}
            className="zz-btn zz-btn-secondary" data-testid="toggle-csv-btn">
            <ArrowsDownUp size={14} weight="regular" /> CSV
          </button>
          <button onClick={() => { setMostrarCampos((v) => !v); setMostrarCsv(false); setMostrarDup(false); }}
            className="zz-btn zz-btn-secondary" data-testid="toggle-fields-btn">
            <SlidersHorizontal size={14} weight="regular" /> Campos
          </button>
          <button onClick={() => setNovo({ nome: "", numero_whatsapp: "" })} className="zz-btn"
            data-testid="new-contact-btn">
            <Plus size={14} weight="regular" /> Novo cliente
          </button>
        </div>
      </div>

      {mostrarDup && (
        <Duplicados onClose={() => setMostrarDup(false)}
          onMesclou={() => { carregar(); carregarTags(); }} />
      )}

      {mostrarCsv && (
        <ImportExport
          // A exportação leva os mesmos filtros da tela: quem filtrou por
          // etiqueta espera baixar aquela lista, não a base inteira.
          filtros={{
            q: busca || undefined,
            tag: tagFiltro || undefined,
            responsavel_id: responsavelFiltro || undefined,
          }}
          onClose={() => setMostrarCsv(false)}
          onImportou={() => { carregar(); carregarTags(); }}
        />
      )}

      {mostrarCampos && (
        <FieldsManager onClose={() => setMostrarCampos(false)} onChanged={carregar} />
      )}

      {novo && (
        <Modal titulo="Novo cliente"
          descricao="O número é a identidade do cliente: um já cadastrado é recusado em vez de virar ficha dupla."
          onFechar={() => setNovo(null)} onSalvar={criar}
          salvarLabel="Criar" salvandoLabel="Criando…" testId="new-contact-form"
          corpoClasse="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Nome</label>
            <input required autoFocus className="zz-input" data-testid="new-contact-name"
              value={novo.nome} onChange={(e) => setNovo({ ...novo, nome: e.target.value })} />
          </div>
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">
              WhatsApp (com DDI e DDD)
            </label>
            <input required className="zz-input tabular-nums" data-testid="new-contact-number"
              placeholder="5511999998888" value={novo.numero_whatsapp}
              onChange={(e) => setNovo({ ...novo, numero_whatsapp: e.target.value })} />
          </div>
        </Modal>
      )}

      <div className="flex gap-3 items-center mb-4 flex-wrap">
        <div className="relative flex-1 min-w-[220px] max-w-sm">
          <MagnifyingGlass size={14} weight="regular" className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-4" />
          <input className="zz-input pl-8" placeholder="Nome, número, e-mail ou empresa…" data-testid="contact-search"
            value={busca} onChange={(e) => setBusca(e.target.value)} />
        </div>
        <select className="zz-input max-w-[180px]" data-testid="contact-tag-filter"
          value={tagFiltro} onChange={(e) => setTagFiltro(e.target.value)}>
          <option value="">Todas as etiquetas</option>
          {tags.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select className="zz-input max-w-[200px]" data-testid="contact-owner-filter"
          value={responsavelFiltro} onChange={(e) => setResponsavelFiltro(e.target.value)}>
          <option value="">Todos os responsáveis</option>
          {usuarios.map((u) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
        </select>
        {(busca || tagFiltro || responsavelFiltro) && (
          <button onClick={() => { setBusca(""); setTagFiltro(""); setResponsavelFiltro(""); }}
            className="p-1.5 hover:bg-surface-3" title="Limpar filtros">
            <X size={14} weight="regular" />
          </button>
        )}
        <div className="text-xs tabular-nums text-ink-3 ml-auto">{total.toLocaleString("pt-BR")} cliente(s)</div>
      </div>

      {contatos.length === 0 ? (
        <div className="border border-dashed border-line rounded-xl bg-surface p-16 text-center">
          <div className="font-heading text-2xl font-semibold mb-2">
            {busca || tagFiltro || responsavelFiltro ? "Nenhum cliente encontrado." : "Nenhum cliente ainda."}
          </div>
          <div className="text-sm text-ink-2">
            {busca || tagFiltro || responsavelFiltro
              ? "Ajuste a busca ou os filtros."
              : "Clientes são criados automaticamente quando alguém envia a primeira mensagem. Ou cadastre um agora."}
          </div>
        </div>
      ) : (
        <div className="border border-line bg-surface rounded-lg">
          <div className="grid grid-cols-12 px-4 py-2 border-b border-line text-sm font-medium text-ink-3">
            <div className="col-span-3">Nome</div>
            <div className="col-span-2">Número</div>
            <div className="col-span-2">Empresa</div>
            <div className="col-span-2">Responsável</div>
            <div className="col-span-3">Etiquetas</div>
          </div>
          {contatos.map((c) => (
            <button key={c.id} onClick={() => setAberto(c.id)} data-testid={`contact-${c.id}`}
              className={`w-full text-left grid grid-cols-12 px-4 py-2.5 border-b border-line items-center hover:bg-surface-2 ${
                aberto === c.id ? "bg-surface-2" : ""
              }`}>
              <div className="col-span-3 font-semibold truncate flex items-center gap-1.5">
                {c.opt_out && <Prohibit size={12} weight="regular" className="text-danger shrink-0"
                  title="Pediu para não receber mensagens em massa" />}
                {c.nome}
              </div>
              <div className="col-span-2 tabular-nums text-sm text-ink">{c.numero_whatsapp}</div>
              <div className="col-span-2 text-sm text-ink-2 truncate">{c.empresa || "—"}</div>
              <div className="col-span-2 text-xs text-ink-2 truncate">
                {nomeUsuario(c.responsavel_id) || <span className="text-ink-4">sem responsável</span>}
              </div>
              <div className="col-span-3 flex items-center gap-1 flex-wrap">
                {(c.tags || []).length === 0
                  ? <span className="text-xs text-ink-4">—</span>
                  : c.tags.map((t) => (
                      <span key={t} className="text-[11px] font-semibold px-1.5 py-0.5 border border-line bg-surface-2 rounded-full">
                        {t}
                      </span>
                    ))}
              </div>
            </button>
          ))}
        </div>
      )}

      {paginas > 1 && (
        <div className="flex items-center justify-between mt-4">
          <button disabled={pagina === 0} onClick={() => setPagina((p) => p - 1)}
            className="zz-btn zz-btn-secondary disabled:opacity-40">Anterior</button>
          <span className="text-xs tabular-nums text-ink-3">Página {pagina + 1} de {paginas}</span>
          <button disabled={pagina + 1 >= paginas} onClick={() => setPagina((p) => p + 1)}
            className="zz-btn zz-btn-secondary disabled:opacity-40">Próxima</button>
        </div>
      )}

      {aberto && (
        <ContactPanel
          contactId={aberto}
          onClose={() => setAberto(null)}
          onSaved={() => { carregar(); carregarTags(); }}
        />
      )}
    </div>
  );
}
