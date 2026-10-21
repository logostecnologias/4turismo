import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import Configuracao from "@/components/agenda/Configuracao";

// Como as reservas são aceitas, lembrete, feriados, textos das mensagens e
// pontos de embarque. Era a aba "Configuração" de Saídas, nome que não dizia o
// que havia dentro; o título agora diz.
export default function RegrasReservasPage() {
  const [config, setConfig] = useState(null);

  const carregar = useCallback(() => {
    api.get("/agenda/config").then((r) => setConfig(r.data)).catch(() => {});
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  return (
    <div className="zz-page" data-testid="regras-reservas-page">
      <div className="mb-6">
        <h1 className="font-heading text-4xl font-semibold">Reservas e embarque</h1>
        <p className="text-ink-2 mt-2 text-sm">
          Pontos de embarque e horário de busca, prazo para confirmar uma solicitação,
          lembrete antes do passeio, feriados e os textos que o turista recebe no WhatsApp.
        </p>
      </div>
      {config === null
        ? <div className="text-sm text-ink-3">Carregando…</div>
        : <Configuracao config={config} onSalvo={carregar} />}
    </div>
  );
}
