import { useCallback, useEffect, useState } from "react";
import api from "@/lib/api";
import { Profissionais } from "@/components/agenda/Catalogo";

// Guias e veículos. Não têm grade própria: são escalados em cada saída, no
// painel da saída (ver `EquipeSaida` em AgendaPage). Era uma aba de Saídas.
export default function EquipePage() {
  const [recursos, setRecursos] = useState(null);

  const carregar = useCallback(() => {
    api.get("/agenda/resources").then((r) => setRecursos(r.data || [])).catch(() => setRecursos([]));
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  return (
    <div className="zz-page" data-testid="equipe-page">
      <div className="mb-6">
        <h1 className="font-heading text-4xl font-semibold">Equipe e veículos</h1>
        <p className="text-ink-2 mt-2 text-sm">
          Os guias e os veículos da agência. Depois de cadastrados, cada saída recebe o seu
          guia e o seu veículo na tela de Saídas.
        </p>
      </div>
      {recursos === null
        ? <div className="text-sm text-ink-3">Carregando equipe…</div>
        : <Profissionais recursos={recursos} onMudou={carregar} />}
    </div>
  );
}
