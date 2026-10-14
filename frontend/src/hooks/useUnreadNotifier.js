import { useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { playNotificationSound, playQueueAlert } from "@/lib/notification-sound";
import { NOME_PRODUTO } from "@/lib/marca";

const BASE_TITLE = NOME_PRODUTO;

// Polls for new incoming messages and for the size of the human queue,
// regardless of which page is open, as long as the tenant shell (AppShell)
// stays mounted. Returns { unread, aguardando } para o badge do menu.
export function useUnreadNotifier(enabled) {
  const prevUnreadRef = useRef(null);
  const prevQueueRef = useRef(null);
  const [counts, setCounts] = useState({ unread: 0, aguardando: 0 });

  useEffect(() => {
    if (!enabled) {
      document.title = BASE_TITLE;
      return;
    }
    prevUnreadRef.current = null;
    prevQueueRef.current = null;

    const check = () => {
      api.get("/atendimento/unread-count").then((r) => {
        const totalUnread = r.data.total || 0;
        const aguardando = r.data.aguardando || 0;
        // Fila tem prioridade sonora: se entrou cliente novo na espera, toca o
        // alerta de fila em vez do ping comum, pra não sobrepor os dois.
        if (prevQueueRef.current !== null && aguardando > prevQueueRef.current) {
          playQueueAlert();
        } else if (prevUnreadRef.current !== null && totalUnread > prevUnreadRef.current) {
          playNotificationSound();
        }
        prevUnreadRef.current = totalUnread;
        prevQueueRef.current = aguardando;
        setCounts({ unread: totalUnread, aguardando });
        document.title = aguardando > 0 ? `(${aguardando}) Aguardando — ${BASE_TITLE}` : BASE_TITLE;
      }).catch(() => {});
    };

    check();
    const t = setInterval(check, 3000);
    return () => {
      clearInterval(t);
      document.title = BASE_TITLE;
    };
  }, [enabled]);

  return counts;
}
