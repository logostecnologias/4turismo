import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import api, { API, formatApiErrorDetail } from "@/lib/api";
import {
  Brain, Check, PaperPlaneRight, PencilSimple, Trash, X, ChatCircleDots,
  ImageSquare, Table, MagnifyingGlass, ArrowsClockwise, CalendarCheck, CheckCircle,
  Paperclip, FileText, Warning, UploadSimple,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import { confirmToast } from "@/lib/confirm-toast";
import { useAcao } from "@/hooks/useAcao";
import Modal from "@/components/ui/Modal";

// Treino do agente, numa conversa só: o que a empresa ensina, as fotos que o
// agente pode mandar e o estoque que ele consulta.
//
// Ensinamentos: cada mensagem fica guardada e vale em toda conversa. Corrigir é
// mandar uma mensagem nova ("na verdade o frete é grátis acima de R$ 200"), não
// caçar a antiga: o prompt diz ao modelo que o mais recente ganha.
//
// Fotos: o mesmo catálogo do passo de configuração (`midias`). O texto digitado
// junto da foto é a descrição, e é a ÚNICA coisa que o modelo sabe dela: ele é
// de texto e não enxerga a imagem.
//
// Poderes: o que o agente faz no sistema além de conversar. Marcar horário na
// Agenda e finalizar o atendimento, os dois desligados por padrão porque são o
// agente escrevendo no sistema. Transferir para uma pessoa (aguardando) não é
// poder: está sempre ligado, é a saída de qualquer dúvida.
//
// Estoque: uma planilha (Excel .xlsx ou CSV) por agente. Subir de novo
// substitui a anterior, porque estoque é foto do momento. A busca da faixa é a
// mesma que o agente usa.
//
// Documentos: PDF, Word (.docx) ou texto. O texto é extraído no servidor e
// entra na base de conhecimento do agente; o arquivo em si não é guardado.
//
// **Um botão de anexo só, que aceita tudo, e o tipo do arquivo decide o
// destino.** Eram dois botões de ícone, um com `accept="image/*"` e outro com
// `accept=".csv"`. No seletor do Windows e do Mac, qualquer outro arquivo
// (xlsx, pdf, docx) aparecia apagado e não podia ser escolhido, sem nenhuma
// mensagem: o cliente achava que "anexar não funciona" (02/10/2026). Formato
// que não serve agora é escolhido e recusado com o motivo, em vez de sumir.

// Os tetos de `ai_agents` e `routes/ai.py`, repetidos só para avisar antes;
// quem recusa é o servidor.
const MAX_ITEM = 2000;
const MAX_TOTAL = 20000;
const MAX_FOTOS = 30;
const MAX_DOCUMENTOS = 60000; // `ai_documentos.MAX_TOTAL`

// O que o seletor mostra. Inclui .xls e .doc antigos de propósito: escolher e
// ouvir "salve como .xlsx" é melhor que não conseguir escolher.
const ACEITOS = [
  "image/*", ".csv", ".xlsx", ".xls", ".pdf", ".docx", ".doc", ".txt", ".md",
  "text/csv", "text/plain", "application/pdf", "application/msword", "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
].join(",");

/** Para onde o arquivo vai, pelo nome (o `type` do navegador vem vazio para
 * .xlsx em vários Windows) e, na falta dele, pelo tipo. */
function destinoDoArquivo(f) {
  const nome = (f.name || "").toLowerCase();
  if (/\.(csv|xlsx|xls)$/.test(nome)) return "planilha";
  if (/\.(pdf|docx|doc|txt|md)$/.test(nome)) return "documento";
  if ((f.type || "").startsWith("image/") || /\.(jpe?g|png|webp|gif|heic|heif)$/.test(nome)) return "foto";
  return null;
}

const quando = (iso) => {
  try {
    return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return "";
  }
};

const erroApi = (ex) => toast.error(formatApiErrorDetail(ex.response?.data?.detail));

/** As linhas do estoque, pela mesma busca do agente. Se não achar aqui, o
 * agente também não acha, e o problema está na planilha, não no modelo. */
function VerEstoque({ agente, estoque, onFechar }) {
  const [busca, setBusca] = useState("");
  const [res, setRes] = useState(null);

  useEffect(() => {
    const t = setTimeout(() => {
      api.get(`/ai/agents/${agente.id}/estoque`, { params: { busca } })
        .then((r) => setRes(r.data))
        .catch(erroApi);
    }, 250);
    return () => clearTimeout(t);
  }, [agente.id, busca]);

  const colunas = estoque?.colunas || [];
  return (
    <div className="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4" onClick={onFechar}>
      <div onClick={(e) => e.stopPropagation()}
        className="bg-surface w-full max-w-4xl zz-panel-hard flex flex-col overflow-hidden"
        style={{ height: "min(85vh, 720px)" }}
        data-testid="estoque-ver">
        <div className="border-b border-line p-4 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="font-heading text-lg font-bold tracking-tight truncate">Estoque de {agente.nome}</div>
            <div className="text-[11px] text-ink-3">
              Procure como um cliente perguntaria. É a mesma busca que o assistente faz.
            </div>
          </div>
          <button className="zz-btn zz-btn-secondary text-xs py-1.5 shrink-0" onClick={onFechar}>Fechar</button>
        </div>
        <div className="p-3 border-b border-line">
          <div className="relative">
            <MagnifyingGlass size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
            <input className="zz-input pl-9" autoFocus placeholder="Ex.: limpeza de pele"
              value={busca} onChange={(e) => setBusca(e.target.value)} data-testid="estoque-busca" />
          </div>
        </div>
        <div className="flex-1 overflow-auto">
          {!res ? (
            <div className="p-6 text-sm text-ink-3">Carregando…</div>
          ) : res.linhas.length === 0 ? (
            <div className="p-6 text-sm text-ink-3 text-center">
              Nada encontrado. O assistente também responderia que não tem.
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-surface-2">
                <tr>
                  {colunas.map((c) => (
                    <th key={c} className="text-left px-3 py-2 text-xs text-ink-3 font-semibold border-b border-line whitespace-nowrap">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {res.linhas.map((l, i) => (
                  <tr key={i} className="border-b border-line">
                    {colunas.map((c) => (
                      <td key={c} className="px-3 py-2 text-ink-2 align-top">{l[c] || ""}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {res && res.total > res.linhas.length && (
          <div className="border-t border-line px-4 py-2 text-[11px] text-ink-3">
            Mostrando {res.linhas.length} de {res.total.toLocaleString("pt-BR")}. Refine a busca para ver outros.
          </div>
        )}
      </div>
    </div>
  );
}

export default function TreinarAgente({ agente, onFechar, onMudou, onTestar }) {
  const [itens, setItens] = useState(() => agente.memoria || []);
  const [fotos, setFotos] = useState(() => agente.midias || []);
  const [estoque, setEstoque] = useState(() => agente.estoque || null);
  const [documentos, setDocumentos] = useState(() => agente.documentos || []);
  const [arrastando, setArrastando] = useState(false);
  const [texto, setTexto] = useState("");
  const [foto, setFoto] = useState(null); // {arquivo, previa}
  const [editando, setEditando] = useState(null); // {tipo: "memoria"|"foto", id, texto}
  const [vendoEstoque, setVendoEstoque] = useState(false);
  const [poderes, setPoderes] = useState(() => ({
    agenda_ativa: !!agente.agenda_ativa,
    finalizar_ativo: !!agente.finalizar_ativo,
  }));
  const fim = useRef(null);
  const campo = useRef(null);
  const inputArquivo = useRef(null);
  const navigate = useNavigate();
  // Aberto quando alguém liga "Marcar horário na Agenda" sem a Agenda ter com
  // o que trabalhar. Guarda a resposta de /poderes, que diz o que falta.
  const [agendaVazia, setAgendaVazia] = useState(null);

  // O cartão só sabe se a chave está ligada. Se as Saídas têm passeio com
  // saída na grade para o agente trabalhar, só o servidor sabe.
  useEffect(() => {
    api.get(`/ai/agents/${agente.id}/poderes`).then((r) => setPoderes(r.data)).catch(() => {});
  }, [agente.id]);

  // Ensinamentos e fotos numa linha do tempo só, na ordem em que entraram. O
  // número da foto é a posição no catálogo, que é a etiqueta [FOTO:n] do prompt.
  const linhaDoTempo = useMemo(() => {
    const t = [
      ...itens.map((m, i) => ({ tipo: "memoria", n: i + 1, item: m })),
      ...fotos.map((f, i) => ({ tipo: "foto", n: i + 1, item: f })),
      ...documentos.map((d, i) => ({ tipo: "documento", n: i + 1, item: d })),
    ];
    return t.sort((a, b) => String(a.item.created_at || "").localeCompare(String(b.item.created_at || "")));
  }, [itens, fotos, documentos]);

  useEffect(() => { fim.current?.scrollIntoView({ behavior: "smooth" }); }, [linhaDoTempo.length]);
  useEffect(() => () => { if (foto?.previa) URL.revokeObjectURL(foto.previa); }, [foto]);

  const total = itens.reduce((s, m) => s + (m.texto || "").length, 0);
  const memoriaCheia = total >= MAX_TOTAL;
  const fotosCheias = fotos.length >= MAX_FOTOS;
  const caracteresDocs = documentos.reduce((s, d) => s + (d.caracteres || 0), 0);

  const [subirDocumento, subindoDocumento] = useAcao(async (arquivo) => {
    const corpo = new FormData();
    corpo.append("file", arquivo);
    try {
      const { data } = await api.post(`/ai/agents/${agente.id}/documentos`, corpo);
      setDocumentos((l) => [...l, data]);
      onMudou?.();
      toast.success(`"${arquivo.name}" lido: ${data.caracteres.toLocaleString("pt-BR")} caracteres.`);
    } catch (ex) {
      erroApi(ex);
    }
  });

  // Toda entrada de arquivo passa por aqui: botão, arrastar e colar.
  const receberArquivo = (f) => {
    if (!f) return;
    const destino = destinoDoArquivo(f);
    if (destino === "foto") {
      if (fotosCheias) {
        toast.error(`O assistente já tem ${MAX_FOTOS} fotos, o máximo. Tire alguma antes de anexar outra.`);
        return;
      }
      setFoto({ arquivo: f, previa: URL.createObjectURL(f) });
      campo.current?.focus();
    } else if (destino === "planilha") {
      subirEstoque(f);
    } else if (destino === "documento") {
      subirDocumento(f);
    } else {
      toast.error(`"${f.name}" não é um formato aceito. Envie foto, planilha (Excel ou CSV) ` +
        "ou documento (PDF, Word ou texto).");
    }
  };

  const escolherArquivo = (e) => {
    const lista = Array.from(e.target.files || []);
    e.target.value = "";
    receberArquivo(lista[0]);
  };

  // Arrastar só reage a arquivo: texto arrastado de outro lugar segue o
  // comportamento normal do campo.
  const temArquivo = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");
  const aoArrastar = (e) => {
    if (!temArquivo(e)) return;
    e.preventDefault();
    if (!arrastando) setArrastando(true);
  };
  const aoSair = (e) => {
    if (!e.currentTarget.contains(e.relatedTarget)) setArrastando(false);
  };
  const aoSoltar = (e) => {
    if (!temArquivo(e)) return;
    e.preventDefault();
    setArrastando(false);
    const lista = Array.from(e.dataTransfer.files || []);
    if (lista.length > 1) toast("Um arquivo por vez: usei o primeiro.");
    receberArquivo(lista[0]);
  };
  const aoColar = (e) => {
    const imagem = Array.from(e.clipboardData?.files || []).find((f) => (f.type || "").startsWith("image/"));
    if (!imagem) return;
    e.preventDefault();
    receberArquivo(imagem);
  };

  const [enviar, enviando] = useAcao(async () => {
    const conteudo = texto.trim();
    if (!conteudo) return;
    try {
      if (foto) {
        const corpo = new FormData();
        corpo.append("file", foto.arquivo);
        corpo.append("descricao", conteudo);
        const { data } = await api.post(`/ai/agents/${agente.id}/midias`, corpo);
        setFotos((l) => [...l, data]);
        setFoto(null);
      } else {
        const { data } = await api.post(`/ai/agents/${agente.id}/memoria`, { texto: conteudo });
        setItens((l) => [...l, data]);
      }
      setTexto("");
      onMudou?.();
      campo.current?.focus();
    } catch (ex) {
      erroApi(ex);
    }
  });

  const [subirEstoque, subindoEstoque] = useAcao(async (arquivo) => {
    if (estoque && !(await confirmToast(
      `Substituir o estoque atual (${estoque.total.toLocaleString("pt-BR")} itens) por "${arquivo.name}"? ` +
      "O que não estiver na planilha nova deixa de existir para o assistente.",
      { confirmLabel: "Substituir" }))) return;
    const corpo = new FormData();
    corpo.append("file", arquivo);
    try {
      const { data } = await api.post(`/ai/agents/${agente.id}/estoque`, corpo);
      setEstoque(data);
      onMudou?.();
      toast.success(`Estoque carregado: ${data.total.toLocaleString("pt-BR")} itens.`);
    } catch (ex) {
      erroApi(ex);
    }
  });

  const [removerEstoque] = useAcao(async () => {
    if (!(await confirmToast("Remover o estoque? O assistente deixa de consultar a planilha.",
      { confirmLabel: "Remover" }))) return;
    try {
      await api.delete(`/ai/agents/${agente.id}/estoque`);
      setEstoque(null);
      onMudou?.();
    } catch (ex) {
      erroApi(ex);
    }
  });

  const [alternarPoder] = useAcao(async (chave) => {
    try {
      // Ligar a agenda sem passeio com saída não dá ferramenta
      // nenhuma ao agente: foi assim que um cliente achou que o agente marcava
      // visitas e ele não marcava (02/10/2026). Em vez de ligar em silêncio, a
      // tela explica e leva ao cadastro. A consulta é feita na hora do clique,
      // e não lida do estado: o serviço pode ter sido criado noutra aba.
      if (chave === "agenda_ativa" && !poderes.agenda_ativa) {
        const { data: atual } = await api.get(`/ai/agents/${agente.id}/poderes`);
        setPoderes(atual);
        if (!atual.agenda_pronta) {
          setAgendaVazia(atual);
          return;
        }
      }
      const { data } = await api.patch(`/ai/agents/${agente.id}/poderes`, { [chave]: !poderes[chave] });
      setPoderes(data);
      onMudou?.();
    } catch (ex) {
      erroApi(ex);
    }
  });

  const [salvarEdicao, salvando] = useAcao(async () => {
    const { tipo, id, texto: novo } = editando;
    if (!novo.trim()) return;
    try {
      if (tipo === "foto") {
        await api.patch(`/ai/agents/${agente.id}/midias/${id}`, { descricao: novo.trim() });
        setFotos((l) => l.map((f) => (f.id === id ? { ...f, descricao: novo.trim() } : f)));
      } else {
        await api.patch(`/ai/agents/${agente.id}/memoria/${id}`, { texto: novo.trim() });
        setItens((l) => l.map((m) => (m.id === id ? { ...m, texto: novo.trim() } : m)));
      }
      setEditando(null);
      onMudou?.();
    } catch (ex) {
      erroApi(ex);
    }
  });

  const [apagar] = useAcao(async (tipo, item) => {
    const pergunta = tipo === "foto"
      ? `Tirar a foto "${item.descricao}" do catálogo do assistente?`
      : tipo === "documento"
        ? `O assistente vai esquecer o que leu em "${item.nome}". Continuar?`
        : "O assistente vai esquecer este ensinamento. Continuar?";
    if (!(await confirmToast(pergunta, { confirmLabel: tipo === "foto" ? "Tirar" : "Esquecer" }))) return;
    try {
      if (tipo === "documento") {
        await api.delete(`/ai/agents/${agente.id}/documentos/${item.id}`);
        setDocumentos((l) => l.filter((d) => d.id !== item.id));
      } else if (tipo === "foto") {
        await api.delete(`/ai/agents/${agente.id}/midias/${item.id}`);
        setFotos((l) => l.filter((f) => f.id !== item.id));
      } else {
        await api.delete(`/ai/agents/${agente.id}/memoria/${item.id}`);
        setItens((l) => l.filter((m) => m.id !== item.id));
      }
      onMudou?.();
    } catch (ex) {
      erroApi(ex);
    }
  });

  const [esquecerTudo] = useAcao(async () => {
    if (!(await confirmToast(
      `Apagar os ${itens.length} ensinamentos de ${agente.nome}? Fotos e estoque continuam. Não dá para desfazer.`,
      { confirmLabel: "Apagar ensinamentos" }))) return;
    try {
      await api.delete(`/ai/agents/${agente.id}/memoria`);
      setItens([]);
      onMudou?.();
    } catch (ex) {
      erroApi(ex);
    }
  });

  const aoTeclar = (e) => {
    // Enter envia; Shift+Enter quebra a linha, para quem cola um texto maior.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      enviar();
    }
    if (e.key === "Escape" && foto) {
      e.stopPropagation();
      setFoto(null);
    }
  };

  const bloqueado = foto ? false : memoriaCheia;
  const placeholder = foto
    ? "Descreva a foto: lugar, passeio, detalhe, ângulo… (é por isso que o assistente decide quando enviar)"
    : memoriaCheia
      ? "Limite de ensinamentos atingido. Apague os antigos para continuar."
      : "Ensine algo ao assistente… (Enter para enviar, Shift+Enter para nova linha)";

  const acoesItem = (tipo, item, textoAtual) => (
    <span className="flex gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
      {tipo !== "documento" && (
        <button className="w-6 h-6 rounded-md flex items-center justify-center hover:bg-surface-3 hover:text-ink"
          onClick={() => setEditando({ tipo, id: item.id, texto: textoAtual })} aria-label="Corrigir">
          <PencilSimple size={12} />
        </button>
      )}
      <button className="w-6 h-6 rounded-md flex items-center justify-center hover:bg-danger-soft hover:text-danger"
        onClick={() => apagar(tipo, item)} aria-label="Apagar">
        <Trash size={12} />
      </button>
    </span>
  );

  const editor = () => (
    <div className="rounded-2xl border border-info bg-surface p-2.5 ring-1 ring-info min-w-[18rem]">
      <textarea
        className="zz-input text-sm min-h-20"
        autoFocus
        value={editando.texto}
        maxLength={MAX_ITEM}
        onChange={(e) => setEditando({ ...editando, texto: e.target.value })}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.stopPropagation(); setEditando(null); }
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); salvarEdicao(); }
        }}
      />
      <div className="flex gap-2 mt-2 justify-end">
        <button className="zz-btn zz-btn-secondary text-xs py-1.5" onClick={() => setEditando(null)}>
          <X size={13} weight="regular" /> Cancelar
        </button>
        <button className="zz-btn text-xs py-1.5" disabled={salvando || !editando.texto.trim()} onClick={salvarEdicao}>
          <Check size={13} weight="regular" /> {salvando ? "Salvando…" : "Salvar"}
        </button>
      </div>
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={onFechar}>
      <div onClick={(e) => e.stopPropagation()}
        onDragEnter={aoArrastar} onDragOver={aoArrastar} onDragLeave={aoSair} onDrop={aoSoltar}
        className="relative bg-surface w-full max-w-2xl zz-panel-hard flex flex-col overflow-hidden"
        style={{ height: "min(88vh, 760px)" }}
        data-testid="agente-treinar">
        {arrastando && (
          <div className="absolute inset-0 z-20 m-2 rounded-2xl border-2 border-dashed border-info bg-info-soft
                          flex flex-col items-center justify-center gap-2 pointer-events-none">
            <UploadSimple size={32} weight="regular" className="text-info" />
            <div className="text-sm font-bold text-info">Solte para ensinar o assistente</div>
            <div className="text-xs text-ink-2">Foto, planilha (Excel ou CSV) ou documento (PDF, Word, texto)</div>
          </div>
        )}
        <div className="border-b border-line p-4 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="font-heading text-lg font-bold tracking-tight truncate">
              Treinar {agente.nome}
            </div>
            <div className="text-[11px] text-ink-3">
              Ensinamentos, fotos, documentos e estoque valem em toda conversa a partir da próxima resposta.
            </div>
          </div>
          <div className="flex gap-2 shrink-0">
            {onTestar && (
              <button className="zz-btn zz-btn-secondary text-xs py-1.5" onClick={onTestar}
                data-testid="treinar-testar">
                <ChatCircleDots size={14} weight="regular" /> Testar
              </button>
            )}
            <button className="zz-btn zz-btn-secondary text-xs py-1.5" onClick={onFechar}>
              Fechar
            </button>
          </div>
        </div>

        <div className="border-b border-line px-4 py-2.5 flex flex-wrap items-center gap-2" data-testid="treinar-poderes">
          <span className="text-[11px] text-ink-3 mr-1">O assistente pode:</span>
          {[
            { chave: "agenda_ativa", Icone: CalendarCheck, rotulo: "Reservar nas Saídas",
              nota: "Consulta as vagas, pede a reserva (chega como Solicitada) e cancela de verdade. Os passeios e as saídas vêm da tela Saídas." },
            { chave: "finalizar_ativo", Icone: CheckCircle, rotulo: "Finalizar o atendimento",
              nota: "Encerra a conversa quando o assunto se resolve, com um resumo no histórico." },
          ].map(({ chave, Icone, rotulo, nota }) => {
            const ligado = poderes[chave];
            return (
              <button key={chave} type="button" title={nota} onClick={() => alternarPoder(chave)}
                aria-pressed={ligado} data-testid={`poder-${chave}`}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-semibold transition-colors ${
                  ligado
                    ? "border-info bg-info-soft text-info ring-1 ring-info"
                    : "border-line text-ink-3 hover:border-line-strong hover:text-ink"
                }`}>
                <Icone size={13} weight="regular" />
                {rotulo}
                {ligado && <Check size={11} weight="regular" />}
              </button>
            );
          })}
          <span className="text-[11px] text-ink-3" title="Sempre ligado: é a saída de qualquer dúvida do assistente.">
            · transferir para uma pessoa, sempre
          </span>
          {poderes.agenda_ativa && poderes.agenda_pronta === false && (
            <div className="w-full flex items-start gap-2 rounded-lg border border-warning/40 bg-warning-soft p-2.5 text-[11px] text-warning"
              data-testid="agenda-sem-servico">
              <Warning size={14} weight="regular" className="shrink-0 mt-px" />
              <span>
                As Saídas ainda não têm nenhum passeio ativo com horário de saída, então o assistente{" "}
                <strong>não consegue reservar</strong>: ele anota o passeio, o dia e quantas pessoas o cliente quer e
                passa para a equipe confirmar. Para ele reservar sozinho, cadastre na tela <strong>Saídas</strong> um
                passeio (por exemplo, "Lagoa Azul") com os horários de saída e os lugares de cada uma.
              </span>
            </div>
          )}
        </div>

        {estoque && (
          <div className="border-b border-line px-4 py-2.5 flex items-center gap-3 bg-info-soft" data-testid="estoque-faixa">
            <Table size={18} weight="regular" className="text-info shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold truncate">
                Estoque: {estoque.total.toLocaleString("pt-BR")} itens
              </div>
              <div className="text-[11px] text-ink-3 truncate">
                {estoque.arquivo} · atualizado em {quando(estoque.atualizado_em)}
              </div>
            </div>
            <div className="flex gap-1.5 shrink-0">
              <button className="zz-btn zz-btn-secondary text-xs py-1.5" onClick={() => setVendoEstoque(true)}
                data-testid="estoque-abrir">
                <MagnifyingGlass size={13} weight="regular" /> Ver itens
              </button>
              <button className="zz-btn zz-btn-secondary text-xs py-1.5" disabled={subindoEstoque}
                onClick={() => inputArquivo.current?.click()} aria-label="Substituir planilha" title="Substituir planilha">
                <ArrowsClockwise size={13} weight="regular" />
              </button>
              <button className="zz-btn zz-btn-secondary text-xs py-1.5 hover:text-danger" onClick={removerEstoque}
                aria-label="Remover estoque" title="Remover estoque">
                <Trash size={13} weight="regular" />
              </button>
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4 space-y-3 bg-surface-2">
          {linhaDoTempo.length === 0 && (
            <div className="text-center py-8 px-6">
              <Brain size={28} weight="regular" className="mx-auto text-ink-4 mb-3" />
              <p className="text-sm text-ink-2 font-semibold">Ensine o assistente como ensinaria um colaborador novo.</p>
              <p className="text-xs text-ink-3 mt-2 leading-relaxed">
                Uma regra, um fato ou um jeito de agir por mensagem. Por exemplo:<br />
                “Quando perguntarem o que levar, diga protetor solar, água e roupa de banho.”<br />
                “Nunca chame o cliente de você, sempre de senhor ou senhora.”
              </p>
              <div className="text-xs text-ink-3 mt-4 space-y-1.5">
                <p className="flex items-center justify-center gap-1.5">
                  <ImageSquare size={14} weight="regular" /> Anexe <strong>fotos</strong> com uma descrição, e ele manda na hora certa.
                </p>
                <p className="flex items-center justify-center gap-1.5">
                  <Table size={14} weight="regular" /> Suba a <strong>planilha de estoque ou de valores</strong> (Excel ou CSV), e ele consulta antes de responder.
                </p>
                <p className="flex items-center justify-center gap-1.5">
                  <FileText size={14} weight="regular" /> Anexe <strong>documentos</strong> (PDF, Word), e ele aprende o que está escrito.
                </p>
                <p className="mt-2">Use o botão <strong>Anexar arquivo</strong>, ou arraste o arquivo para esta janela.</p>
              </div>
              <p className="text-xs text-ink-3 mt-4">
                Para corrigir algo, é só mandar a versão certa: o ensinamento mais recente sempre ganha.
              </p>
            </div>
          )}

          {linhaDoTempo.map(({ tipo, n, item }) => {
            const emEdicao = editando?.tipo === tipo && editando.id === item.id;
            return (
              <div key={`${tipo}-${item.id}`} className="flex justify-end group">
                <div className="max-w-[85%] w-fit">
                  {emEdicao ? editor() : tipo === "documento" ? (
                    <div className="rounded-2xl bg-brand text-brand-fg p-3 w-72">
                      <div className="flex items-center gap-2 font-semibold text-sm">
                        <FileText size={18} weight="regular" className="shrink-0" />
                        <span className="truncate">{item.nome}</span>
                      </div>
                      <div className="text-xs opacity-80 mt-1.5 line-clamp-4 whitespace-pre-wrap break-words">{item.previa}</div>
                    </div>
                  ) : tipo === "foto" ? (
                    <div className="rounded-2xl bg-brand text-brand-fg p-1.5 w-64">
                      <img src={`${API}/media/${item.media_id}`} alt={item.descricao}
                        className="w-full max-h-56 object-cover rounded-xl bg-surface-3" />
                      <div className="px-2 pt-1.5 pb-1 text-sm whitespace-pre-wrap break-words">{item.descricao}</div>
                    </div>
                  ) : (
                    <div className="px-3.5 py-2 text-sm rounded-2xl bg-brand text-brand-fg whitespace-pre-wrap break-words">
                      {item.texto}
                    </div>
                  )}
                  <div className="flex items-center justify-end gap-2 mt-1 text-[10px] text-ink-3">
                    <span className="tabular-nums text-ink-4">
                      {tipo === "foto" ? `[FOTO:${n}]` : tipo === "documento"
                        ? `${(item.caracteres || 0).toLocaleString("pt-BR")} caracteres` : `#${n}`}
                    </span>
                    <span>{quando(item.created_at)}</span>
                    <span className="flex items-center gap-0.5 text-success">
                      <Check size={11} weight="regular" /> {tipo === "foto" ? "no catálogo" : tipo === "documento" ? "lido" : "memorizado"}
                    </span>
                    {!emEdicao && acoesItem(tipo, item, tipo === "foto" ? item.descricao : item.texto)}
                  </div>
                </div>
              </div>
            );
          })}
          <div ref={fim} />
        </div>

        <div className="border-t border-line p-3">
          {foto && (
            <div className="flex items-center gap-2.5 mb-2 rounded-xl border border-info bg-info-soft p-2">
              <img src={foto.previa} alt="" className="w-12 h-12 rounded-lg object-cover shrink-0" />
              <div className="text-[11px] text-ink-2 flex-1 min-w-0">
                <div className="font-semibold truncate">{foto.arquivo.name}</div>
                O assistente <strong>não vê</strong> a imagem: escreva abaixo o que aparece nela.
              </div>
              <button className="w-7 h-7 rounded-lg flex items-center justify-center text-ink-3 hover:bg-surface hover:text-ink shrink-0"
                onClick={() => setFoto(null)} aria-label="Cancelar foto">
                <X size={14} weight="regular" />
              </button>
            </div>
          )}
          {!foto && (
            <div className="flex items-center gap-2 mb-2">
              <button type="button" disabled={subindoEstoque || subindoDocumento}
                onClick={() => inputArquivo.current?.click()} data-testid="treinar-anexar"
                className="zz-btn zz-btn-secondary text-xs py-1.5 shrink-0">
                <Paperclip size={14} weight="regular" />
                {subindoEstoque || subindoDocumento ? "Lendo o arquivo…" : "Anexar arquivo"}
              </button>
              <span className="text-[11px] text-ink-3 truncate">
                Foto, planilha (Excel ou CSV) ou documento (PDF, Word, texto). Também dá para arrastar ou colar.
              </span>
            </div>
          )}
          <div className="flex gap-2 items-end">
            <textarea
              ref={campo}
              className="zz-input text-sm resize-none"
              rows={3}
              autoFocus
              maxLength={MAX_ITEM}
              disabled={bloqueado}
              placeholder={placeholder}
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              onKeyDown={aoTeclar}
              onPaste={aoColar}
              data-testid="treinar-input"
            />
            <button className="zz-btn shrink-0" disabled={enviando || !texto.trim() || bloqueado}
              onClick={enviar} data-testid="treinar-enviar" aria-label="Enviar">
              <PaperPlaneRight size={14} weight="regular" />
            </button>
          </div>
          <div className="flex items-center justify-between mt-1.5 text-[11px] text-ink-3 gap-3">
            <span className="truncate">
              {itens.length} {itens.length === 1 ? "ensinamento" : "ensinamentos"} ·{" "}
              {total.toLocaleString("pt-BR")} de {MAX_TOTAL.toLocaleString("pt-BR")} caracteres ·{" "}
              {fotos.length} de {MAX_FOTOS} fotos
              {documentos.length > 0 && (
                <> · documentos: {caracteresDocs.toLocaleString("pt-BR")} de {MAX_DOCUMENTOS.toLocaleString("pt-BR")} caracteres</>
              )}
            </span>
            {itens.length > 0 && (
              <button className="hover:text-danger shrink-0" onClick={esquecerTudo} data-testid="treinar-apagar-tudo">
                Apagar ensinamentos
              </button>
            )}
          </div>
        </div>

        <input ref={inputArquivo} type="file" accept={ACEITOS} className="hidden" onChange={escolherArquivo}
          data-testid="treinar-arquivo" />

        {agendaVazia && (
          <Modal
            titulo="As Saídas ainda não estão prontas"
            descricao={`Para ${agente.nome} reservar de verdade, as Saídas precisam ter o que oferecer.`}
            testId="modal-agenda-vazia"
            onFechar={() => setAgendaVazia(null)}
            salvarLabel="Cadastrar passeios"
            salvandoLabel="Abrindo as Saídas…"
            cancelarLabel="Agora não"
            onSalvar={async () => {
              // A permissão já fica ligada: assim que o cadastro existir, o
              // agente passa a reservar sem ninguém precisar voltar aqui.
              try {
                await api.patch(`/ai/agents/${agente.id}/poderes`, { agenda_ativa: true });
                onMudou?.();
              } catch (ex) {
                erroApi(ex);
                return;
              }
              navigate("/app/passeios");
            }}
          >
            <p className="text-sm text-ink-2">
              O assistente consulta as vagas e reserva sozinho a partir dos passeios cadastrados na
              tela <strong>Saídas</strong>. Hoje ela tem{" "}
              <strong>{agendaVazia.agenda_servicos ?? 0} {agendaVazia.agenda_servicos === 1 ? "passeio" : "passeios"} com saída</strong>,
              então ele não teria vaga nenhuma para oferecer.
            </p>
            <ol className="text-sm text-ink-2 space-y-2 list-decimal pl-5">
              <li>
                Cadastre o <strong>passeio</strong> que o cliente reserva, com os lugares por saída. Por
                exemplo: "Lagoa Azul, 30 lugares".
              </li>
              <li>
                Na grade do passeio, defina os <strong>horários de saída</strong> de cada dia da semana
                (por exemplo, 08:00 e 14:00, de segunda a sábado).
              </li>
              <li>Volte aqui e use <strong>Testar</strong> para pedir uma reserva e conferir.</li>
            </ol>
            <p className="text-xs text-ink-3">
              Ao continuar, a permissão já fica ligada. Enquanto as Saídas estiverem vazias, o assistente anota o que o
              cliente quer e passa para a equipe confirmar, sem nunca dizer que está reservado.
            </p>
          </Modal>
        )}

        {/* Dentro do painel, e não ao lado: o clique no fundo da busca não pode
            chegar ao fundo do treino, que fecharia os dois. */}
        {vendoEstoque && estoque && (
          <VerEstoque agente={agente} estoque={estoque} onFechar={() => setVendoEstoque(false)} />
        )}
      </div>
    </div>
  );
}
