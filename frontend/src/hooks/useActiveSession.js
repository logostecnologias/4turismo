import { useCallback, useEffect, useRef, useState } from "react";
import api from "@/lib/api";

// Conversa aberta no painel de chat, compartilhada pelo Kanban e pela Inbox —
// as duas telas carregam a conversa exatamente do mesmo jeito.
//
// A tela se atualiza de poucos em poucos segundos. Buscar o histórico inteiro
// a cada ciclo significava rebaixar centenas de mensagens (com anexos) sem
// parar; aqui a primeira carga é completa e as seguintes pedem só o que chegou
// depois da última mensagem já em tela.

// O backend corta em `$gte`, então a última mensagem volta repetida a cada
// delta: reconciliar por id é o que impede a duplicata de aparecer. Devolver o
// array anterior quando nada mudou também evita re-renderizar o chat à toa.
function mergeById(prev, incoming) {
  if (!incoming.length) return prev;
  const seen = new Set(prev.map((m) => m.id));
  const added = incoming.filter((m) => !seen.has(m.id));
  return added.length ? [...prev, ...added] : prev;
}

export function useActiveSession(activeId) {
  const [active, setActive] = useState(null);
  const [messages, setMessages] = useState([]);
  // Timestamp da última mensagem em tela e a conversa a que ele pertence: um
  // cursor da conversa anterior aplicado à nova esconderia o histórico dela.
  const cursorRef = useRef(null);
  const cursorSessionRef = useRef(null);
  // activeId corrente, para descartar resposta que chega depois de o usuário
  // já ter aberto outra conversa.
  const currentRef = useRef(activeId);
  currentRef.current = activeId;

  const load = useCallback(async (id, { full = false } = {}) => {
    if (!id) return;
    const usaCursor = !full && cursorSessionRef.current === id && cursorRef.current;
    const params = usaCursor ? { since: cursorRef.current } : {};
    const { data } = await api.get(`/atendimento/sessions/${id}`, { params });
    if (currentRef.current !== id) return;

    const chegaram = data.messages || [];
    setActive(data.session);
    setMessages((prev) => (data.partial ? mergeById(prev, chegaram) : chegaram));

    cursorSessionRef.current = id;
    // Lote vazio (nada novo desde o último ciclo) mantém o cursor onde está.
    if (chegaram.length) cursorRef.current = chegaram[chegaram.length - 1].timestamp;
  }, []);

  useEffect(() => {
    if (!activeId) {
      setActive(null);
      setMessages([]);
      cursorRef.current = null;
      cursorSessionRef.current = null;
      return;
    }
    cursorRef.current = null;
    cursorSessionRef.current = null;
    load(activeId, { full: true });
  }, [activeId, load]);

  // `refresh` para o polling e para depois de cada ação (o delta já traz a
  // mensagem de sistema que a ação gerou); `reload` quando for preciso
  // reconstruir a conversa do zero.
  const refresh = useCallback(() => load(currentRef.current), [load]);
  const reload = useCallback(() => load(currentRef.current, { full: true }), [load]);

  return { active, messages, refresh, reload };
}
