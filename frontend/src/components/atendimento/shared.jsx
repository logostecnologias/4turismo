import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Robot, Timer, Headset, CheckCircle, Paperclip, PaperPlaneRight, X, FileArrowDown, Prohibit } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { API, formatApiErrorDetail } from "@/lib/api";

export const STATUS = [
  { key: "bot", label: "Automático", icon: Robot, color: "#0B6E8A" },
  { key: "aguardando", label: "Aguardando", icon: Timer, color: "#F59E0B" },
  { key: "em_atendimento", label: "Em Atendimento", icon: Headset, color: "#10B981" },
  { key: "finalizado", label: "Finalizado", icon: CheckCircle, color: "#475569" },
];

// Há quanto tempo a conversa está na fila, em texto curto. Escala de urgência:
// até 5min é normal, até 15min atenção, acima disso é atraso gritante.
export function waitingInfo(session) {
  if (session.status_atendimento !== "aguardando") return null;
  const since = session.aguardando_desde || session.updated_at;
  if (!since) return null;
  const mins = Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 60000));
  const label = mins < 1 ? "agora" : mins < 60 ? `${mins} min` : `${Math.floor(mins / 60)}h${String(mins % 60).padStart(2, "0")}`;
  const level = mins >= 15 ? "critico" : mins >= 5 ? "atencao" : "novo";
  const color = level === "critico" ? "#EF4444" : level === "atencao" ? "#F59E0B" : "#64748B";
  return { mins, label, level, color };
}

export function WaitingChip({ session }) {
  const w = waitingInfo(session);
  if (!w) return null;
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-medium px-1.5 py-0.5 text-white shrink-0 ${w.level === "critico" ? "zz-alert-blink" : ""}`}
      style={{ background: w.color }}
      data-testid="waiting-chip"
    >
      <Timer size={10} weight="regular" /> {w.label}
    </span>
  );
}

const MEDIA_PREVIEW = { image: "📷 Imagem", video: "🎬 Vídeo", audio: "🎤 Áudio", document: "📎 Documento" };

// Prévia da última mensagem na lista. Anexo sem legenda não pode aparecer
// como "—", senão a conversa parece vazia.
function lastPreview(last) {
  if (!last) return "—";
  if (last.content) return last.content;
  const kind = last.meta?.media?.kind || last.type;
  return MEDIA_PREVIEW[kind] || "—";
}

export function SessionCard({ session, active, onClick }) {
  const last = session.last_message;
  return (
    <button
      onClick={onClick}
      data-testid={`session-card-${session.id}`}
      className={`w-full text-left p-2.5 border rounded-xl ${active ? "border-info ring-1 ring-info bg-surface shadow-card" : "border-line bg-surface hover:border-line-strong"}`}
    >
      <div className="flex items-center justify-between mb-1 gap-1">
        <div className="text-sm font-semibold truncate">{session.contact?.nome || session.contact?.numero_whatsapp || "—"}</div>
        {session.department && (
          <span className="text-xs font-medium px-1 py-0.5 border border-line-strong text-white shrink-0 rounded-full" style={{ background: session.department.cor }}>
            {session.department.nome.slice(0, 6)}
          </span>
        )}
      </div>
      {session.connection && (
        <div className="mb-1"><ConnectionBadge connection={session.connection} /></div>
      )}
      <div className="text-xs text-ink-3 truncate">{lastPreview(last)}</div>
      <div className="flex items-center justify-between gap-1 mt-1">
        <div className="text-[10px] text-ink-4 tabular-nums">{new Date(session.updated_at).toLocaleString("pt-BR")}</div>
        <WaitingChip session={session} />
      </div>
      {session.agent && <div className="text-[10px] text-ink-2 mt-0.5">👤 {session.agent.name}</div>}
    </button>
  );
}

const AVATAR_COLORS = ["#25D366", "#128C7E", "#34B7F1", "#F59E0B", "#8B5CF6", "#EC4899", "#EF4444", "#0EA5E9"];

function avatarColor(seed) {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = seed.charCodeAt(i) + ((hash << 5) - hash);
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

function formatListTime(iso) {
  const d = new Date(iso);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
}

// De onde sai a foto de um contato, nesta ordem.
//
// São duas origens, e elas não se montam igual: `foto_media_id` é um arquivo
// nosso e precisa do prefixo da API (o mesmo `${API}/media/...` que o anexo da
// bolha usa, e pelo mesmo motivo — o cookie httpOnly vai junto no `<img>`);
// `foto_url` é um endereço externo que veio de planilha ou do cadastro e já
// está pronto. Guardá-las num campo só obrigaria a tela a adivinhar qual é
// qual, e a montada errada vira imagem quebrada.
//
// A nossa vem primeiro porque é a que o sistema mantém em dia.
export function fotoDoContato(contato) {
  if (!contato) return null;
  if (contato.foto_media_id) return `${API}/media/${contato.foto_media_id}`;
  return contato.foto_url || null;
}

export function Avatar({ name, photoUrl, size = 44 }) {
  const [failed, setFailed] = useState(false);
  const initial = (name.trim()[0] || "?").toUpperCase();
  if (photoUrl && !failed) {
    return (
      <img
        src={photoUrl}
        alt={name}
        onError={() => setFailed(true)}
        className="rounded-full shrink-0 object-cover"
        style={{ width: size, height: size }}
      />
    );
  }
  return (
    <div
      className="rounded-full flex items-center justify-center shrink-0 text-white font-bold"
      style={{ width: size, height: size, background: avatarColor(name), fontSize: size * 0.4 }}
    >
      {initial}
    </div>
  );
}

// WhatsApp-style chat list item (rounded avatar, flat divider) — used by the
// Atendimento inbox. The Kanban's SessionCard (boxed) stays untouched.
export function SessionListItem({ session, active, onClick }) {
  const last = session.last_message;
  const name = session.contact?.nome || session.contact?.numero_whatsapp || "—";
  const waiting = waitingInfo(session);
  return (
    <button
      onClick={onClick}
      data-testid={`session-card-${session.id}`}
      className={`w-full text-left px-3 py-2.5 flex items-center gap-3 border-b border-line transition-colors ${active ? "bg-surface-2" : "hover:bg-surface-2"}`}
      style={waiting ? { borderLeft: `4px solid ${waiting.color}`, paddingLeft: "calc(0.75rem - 4px)" } : undefined}
    >
      <Avatar name={name} photoUrl={fotoDoContato(session.contact)} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-semibold truncate">{name}</div>
          <div className="text-[10px] text-ink-4 shrink-0">{formatListTime(session.updated_at)}</div>
        </div>
        {session.agent && (
          <div className="text-xs font-bold text-ink truncate mt-0.5">{session.agent.name}</div>
        )}
        <div className="flex items-center justify-between gap-2 mt-0.5">
          <div className="text-xs text-ink-3 truncate">{lastPreview(last)}</div>
          {session.unread_count > 0 && (
            <span className="inline-flex items-center justify-center min-w-[1.15rem] h-[1.15rem] px-1 rounded-full bg-[#25D366] text-white text-[10px] font-bold shrink-0">
              {session.unread_count > 99 ? "99+" : session.unread_count}
            </span>
          )}
        </div>
        {(session.department || waiting) && (
          <div className="mt-1 flex items-center gap-1 flex-wrap">
            {waiting && <WaitingChip session={session} />}
            {session.department && (
              <span
                className="text-xs font-medium px-1.5 py-0.5 text-white shrink-0"
                style={{ background: session.department.cor }}
              >
                {session.department.nome}
              </span>
            )}
          </div>
        )}
      </div>
    </button>
  );
}

export function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Visualizador de imagem em tela cheia. Abrir a URL do arquivo em outra aba
// deixava o usuário sem volta: /api/media serve o binário cru, fora do SPA, e
// em janela sem barra de navegação (PWA/webview) não há como sair. Aqui a saída
// é sempre visível: X, ESC ou clique no fundo.
function ImageLightbox({ src, filename, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    // Trava o scroll do chat atrás do overlay.
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-[200] bg-black/90 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="image-lightbox"
    >
      <div className="absolute top-3 right-3 flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
        <a
          href={src}
          download={filename || "imagem"}
          className="flex items-center gap-1 px-2 py-1.5 bg-surface border border-line-strong text-sm font-medium hover:bg-surface-2 rounded-lg"
          data-testid="lightbox-download"
        >
          <FileArrowDown size={16} weight="regular" /> Baixar
        </a>
        <button
          type="button"
          onClick={onClose}
          aria-label="Fechar"
          className="p-1.5 bg-surface border border-line-strong hover:bg-surface-2 rounded-lg"
          data-testid="lightbox-close"
        >
          <X size={18} weight="regular" />
        </button>
      </div>
      {/* clique na própria imagem não fecha, pra dar espaço de olhar/ampliar */}
      <img
        src={src}
        alt={filename || "imagem"}
        className="max-w-full max-h-full object-contain"
        onClick={(e) => e.stopPropagation()}
      />
    </div>,
    document.body
  );
}

function ImageBubble({ src, media }) {
  const [aberta, setAberta] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setAberta(true)} className="block" data-testid="media-image">
        <img
          src={src}
          alt={media.filename || "imagem"}
          className="max-w-full max-h-72 border border-line object-contain cursor-zoom-in rounded-lg"
        />
      </button>
      {aberta && <ImageLightbox src={src} filename={media.filename} onClose={() => setAberta(false)} />}
    </>
  );
}

// Anexo dentro da bolha. `media.id` aponta pro nosso /api/media (cookie
// httpOnly vai junto no <img>/<video>, então não precisa de token na URL);
// mídia vinda do nó de fluxo não tem id, só a URL pública original.
function MediaContent({ media }) {
  const src = media.id ? `${API}/media/${media.id}` : media.url;
  // Arquivo já removido pela retenção: os metadados sobrevivem justamente pra
  // bolha antiga não virar imagem quebrada.
  if (media.expired) {
    return (
      <div className="flex items-center gap-2 px-2 py-1.5 border border-dashed border-line-strong rounded-xl bg-surface-2 text-ink-3"
           data-testid="media-expired">
        <Prohibit size={16} weight="regular" className="shrink-0" />
        <div className="min-w-0">
          <div className="text-xs font-semibold truncate">{media.filename || "Arquivo"}</div>
          <div className="text-[10px]">Mídia expirada e removida</div>
        </div>
      </div>
    );
  }
  if (!src) return null;
  if (media.kind === "image") {
    return <ImageBubble src={src} media={media} />;
  }
  if (media.kind === "video") {
    return <video src={src} controls className="max-w-full max-h-72 border border-line rounded-lg" />;
  }
  if (media.kind === "audio") {
    return <audio src={src} controls className="max-w-full" />;
  }
  return (
    <a href={src} target="_blank" rel="noreferrer" download={media.filename}
       className="flex items-center gap-2 px-2 py-1.5 border border-line-strong bg-surface hover:bg-surface-2 rounded-lg">
      <FileArrowDown size={18} weight="regular" className="shrink-0" />
      <div className="min-w-0">
        <div className="text-xs font-semibold truncate">{media.filename || "arquivo"}</div>
        {media.size ? <div className="text-[10px] text-ink-3 tabular-nums">{formatBytes(media.size)}</div> : null}
      </div>
    </a>
  );
}

export function MessageBubble({ m }) {
  if (m.direction === "sistema" || m.type === "system") {
    return <div className="text-[11px] text-ink-3 italic text-center py-1">— {m.content} —</div>;
  }
  const isOut = m.direction === "enviada";
  const media = m.meta?.media;
  return (
    <div className={`flex ${isOut ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[80%] px-3 py-2 text-sm border rounded-2xl ${isOut ? "bg-success-soft border-success" : "bg-surface border-line"}`}>
        {m.meta?.agent_name && (
          <div className="text-xs font-bold text-ink mb-0.5">{m.meta.agent_name}</div>
        )}
        {media && <div className="mb-1"><MediaContent media={media} /></div>}
        {m.content && <div className="whitespace-pre-wrap">{m.content}</div>}
        <div className="text-[9px] text-ink-3 mt-1 tabular-nums flex items-center gap-1 justify-end">
          {new Date(m.timestamp).toLocaleTimeString("pt-BR")}
          {isOut && <Entrega meta={m.meta} />}
        </div>
      </div>
    </div>
  );
}

// Status de entrega, que só existe desde a API oficial: o envio devolve um id
// e o que aconteceu com a mensagem chega depois, por webhook. Falha vem com o
// código da Meta, que é o que distingue "não tem WhatsApp" de "a janela de 24h
// fechou", duas coisas que o atendente resolve de formas opostas.
function Entrega({ meta }) {
  const estado = meta?.entrega;
  if (!estado) return null;
  if (estado === "falha") {
    const detalhe = meta?.entrega_erro?.detalhe || "não entregue";
    return (
      <span className="text-danger font-bold" title={detalhe} data-testid="entrega-falha">
        não entregue
      </span>
    );
  }
  const marca = { enviada: "✓", entregue: "✓✓", lida: "✓✓" }[estado] || "";
  return (
    <span className={estado === "lida" ? "text-info font-bold" : ""} title={estado}>
      {marca}
    </span>
  );
}

// Os tetos da Cloud API, iguais aos do backend (media_store.MAX_BYTES).
const MAX_MB = { image: 5, video: 16, audio: 16, document: 100 };

// Mesma lista de `media_store.AUDIO_SUPORTADO`: fora dela o arquivo vai como
// documento, porque a Meta recusa o envio inteiro por mime não suportado.
const AUDIO_OK = ["audio/aac", "audio/mp4", "audio/mpeg", "audio/amr",
                  "audio/ogg", "audio/opus"];

function kindOf(file) {
  const mime = (file.type || "").toLowerCase();
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (AUDIO_OK.includes(mime)) return "audio";
  return "document";
}

// Compositor do chat: texto, anexo (botão ou Ctrl+V) e legenda. Usado pelo
// Kanban e pela Inbox — as duas telas mandam mensagem exatamente igual.
export function ChatComposer({ sessionId, canSend, onSent }) {
  const [input, setInput] = useState("");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [sending, setSending] = useState(false);
  const fileRef = useRef(null);

  useEffect(() => {
    if (!file || !file.type.startsWith("image/")) { setPreview(null); return; }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => { setFile(null); setInput(""); }, [sessionId]);

  const pick = (f) => {
    if (!f) return;
    const limit = MAX_MB[kindOf(f)];
    if (f.size > limit * 1024 * 1024) {
      toast.error(`Arquivo acima do limite de ${limit}MB`);
      return;
    }
    setFile(f);
  };

  const send = async () => {
    if (!canSend || sending) return;
    if (!file && !input.trim()) return;
    setSending(true);
    try {
      if (file) {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("caption", input);
        await api.post(`/atendimento/sessions/${sessionId}/send-media`, fd);
      } else {
        await api.post(`/atendimento/sessions/${sessionId}/send`, { content: input });
      }
      setInput("");
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      await onSent?.();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail) || "Falha ao enviar");
    } finally {
      setSending(false);
    }
  };

  const onPaste = (e) => {
    const f = Array.from(e.clipboardData?.files || [])[0];
    if (f) { e.preventDefault(); pick(f); }
  };

  return (
    <div className="border-t border-line">
      {file && (
        <div className="px-2 pt-2 flex items-center gap-2" data-testid="attachment-preview">
          {preview
            ? <img src={preview} alt="" className="w-12 h-12 object-cover border border-line-strong shrink-0 rounded-lg" />
            : <div className="w-12 h-12 flex items-center justify-center border border-line-strong bg-surface-2 shrink-0 rounded-lg"><Paperclip size={18} weight="regular" /></div>}
          <div className="min-w-0 flex-1">
            <div className="text-xs font-semibold truncate">{file.name}</div>
            <div className="text-[10px] text-ink-3 tabular-nums">{formatBytes(file.size)}</div>
          </div>
          <button onClick={() => { setFile(null); if (fileRef.current) fileRef.current.value = ""; }}
            className="p-1 hover:bg-surface-2" data-testid="attachment-clear">
            <X size={14} weight="regular" />
          </button>
        </div>
      )}
      <div className="p-2 flex gap-2">
        <input ref={fileRef} type="file" className="hidden" data-testid="chat-file-input"
          onChange={(e) => pick(e.target.files?.[0])} />
        <button onClick={() => fileRef.current?.click()} disabled={!canSend || sending}
          className="zz-btn zz-btn-secondary" title="Anexar arquivo" data-testid="chat-attach">
          <Paperclip size={14} weight="regular" />
        </button>
        <input value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          onPaste={onPaste}
          className="zz-input"
          placeholder={!canSend ? "Assuma a conversa para responder" : file ? "Legenda (opcional)..." : "Digite uma mensagem..."}
          disabled={!canSend || sending} data-testid="chat-input" />
        <button onClick={send} disabled={!canSend || sending || (!file && !input.trim())}
          className="zz-btn" data-testid="chat-send">
          <PaperPlaneRight size={14} weight="regular" />
        </button>
      </div>
    </div>
  );
}

export function StatusBadge({ status }) {
  const s = STATUS.find((x) => x.key === status);
  if (!s) return null;
  return (
    <span className="text-xs font-medium px-1.5 py-0.5 border border-line-strong text-white rounded-full" style={{ background: s.color }}>
      {s.label}
    </span>
  );
}

export function ConnectionBadge({ connection }) {
  return (
    <span className="text-xs font-medium px-1.5 py-0.5 border border-line bg-surface-2 text-ink rounded-full">
      {connection.nome}
    </span>
  );
}
