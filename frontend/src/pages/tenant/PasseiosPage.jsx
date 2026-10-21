import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { Servicos } from "@/components/agenda/Catalogo";

// Passeios: nome, preço, capacidade e a grade de saídas de cada um.
//
// Era uma aba escondida dentro de Saídas, e a agência só descobria que precisava
// cadastrar passeio ao clicar nela (21/10/2026). Virou item próprio na barra
// lateral, porque sem passeio não existe saída nenhuma para vender.
export default function PasseiosPage() {
  const [servicos, setServicos] = useState(null);

  const carregar = useCallback(() => {
    api.get("/agenda/services").then((r) => setServicos(r.data || [])).catch(() => setServicos([]));
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  return (
    <div className="zz-page" data-testid="passeios-page">
      <div className="mb-6">
        <h1 className="font-heading text-4xl font-semibold">Passeios</h1>
        <p className="text-ink-2 mt-2 text-sm">
          O que a agência vende: preço, quantos lugares cada saída tem e em que dias e horários
          o passeio sai. É daqui que as Saídas, o WhatsApp, o assistente de IA e o site tiram
          as vagas.
        </p>
      </div>
      {servicos === null
        ? <div className="text-sm text-ink-3">Carregando passeios…</div>
        : <Servicos servicos={servicos} onMudou={carregar} />}
    </div>
  );
}
