import { useEffect, useState } from "react";
import api from "@/lib/api";
import { Sparkle } from "@phosphor-icons/react";
import ListaAgentes from "@/components/ia/ListaAgentes";

// A configuração de provedor, modelo e chave de API **não** mora aqui: mora
// dentro de cada agente. Uma empresa pode ter a chave da Anthropic no agente de
// suporte e a da OpenAI no de vendas — contas, times e orçamentos diferentes —
// e uma credencial única por empresa obrigaria os dois a dividirem a mesma.
//
// O que sobra nesta página é o que é mesmo da empresa: o consumo do mês e a
// lista de agentes.

const USD_BRL = 5.5;

export default function AIAgentsPage() {
  const [consumo, setConsumo] = useState(null);
  const [plataforma, setPlataforma] = useState(false);

  useEffect(() => {
    api.get("/ai/usage").then((r) => setConsumo(r.data)).catch(() => {});
    // A tela precisa saber se existe rede de segurança: sem chave no agente e
    // sem chave da plataforma, ele simplesmente não responde — e o cliente tem
    // que saber disso antes de ligar o agente, não depois.
    api.get("/ai/providers")
      .then((r) => setPlataforma(Boolean(r.data?.plataforma_disponivel)))
      .catch(() => {});
  }, []);

  return (
    <div className="zz-page" data-testid="ai-agents-page">
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <h1 className="font-heading text-3xl font-bold tracking-tight">Assistente de IA</h1>
          <p className="text-ink-2 mt-1 text-sm">
            Atendimento automático por WhatsApp, com transferência para uma pessoa quando precisa.
          </p>
        </div>

        {consumo && consumo.chamadas > 0 && (
          <div className="zz-panel px-5 py-3 flex items-center gap-6" data-testid="ai-consumo">
            <div>
              <div className="font-heading text-xl font-bold leading-none">
                {consumo.chamadas.toLocaleString("pt-BR")}
              </div>
              <div className="text-xs text-ink-3 mt-1">
                respostas no mês
              </div>
            </div>
            <div className="w-px self-stretch bg-line" />
            <div>
              <div className="font-heading text-xl font-bold leading-none">
                {(consumo.custo_usd * USD_BRL).toLocaleString("pt-BR", {
                  style: "currency", currency: "BRL",
                })}
              </div>
              <div className="text-xs text-ink-3 mt-1">
                custo estimado
              </div>
            </div>
          </div>
        )}
      </div>

      {plataforma && (
        <div className="zz-panel bg-surface-2 p-4 flex gap-3" data-testid="ai-fallback-note">
          <Sparkle size={18} weight="regular" className="text-ink-3 shrink-0 mt-0.5" />
          <p className="text-xs text-ink-2">
            Assistente sem chave própria usa a IA inclusa no seu plano, dentro do limite mensal
            contratado. Informar a chave da agência na configuração do assistente remove esse limite e
            passa o custo direto para a conta dela no provedor.
          </p>
        </div>
      )}

      <ListaAgentes plataformaDisponivel={plataforma} />
    </div>
  );
}
