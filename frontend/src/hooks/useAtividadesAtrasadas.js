import { useEffect, useState } from "react";
import api from "@/lib/api";

// Um minuto, não os 3s da fila de atendimento: atividade vence em hora
// marcada, não em tempo real — quem está atrasado às 14:00 continua atrasado
// às 14:01, e não há alerta a dar nesse intervalo.
const INTERVALO = 60000;

/** Quantas atividades pendentes já passaram do vencimento (selo do menu). */
export function useAtividadesAtrasadas(enabled) {
  const [atrasadas, setAtrasadas] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const check = () => {
      api.get("/activities/summary")
        .then((r) => setAtrasadas(r.data.atrasadas || 0))
        // Silencioso de propósito: o atendente comum não tem acesso à rota, e
        // um 403 esperado não deve virar erro na tela.
        .catch(() => {});
    };
    check();
    const t = setInterval(check, INTERVALO);
    return () => clearInterval(t);
  }, [enabled]);

  return atrasadas;
}
