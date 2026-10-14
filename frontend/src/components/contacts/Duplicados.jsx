import { useCallback, useEffect, useState } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import { ArrowsMerge, CheckCircle } from "@phosphor-icons/react";
import Modal from "@/components/ui/Modal";

const CRITERIO = { email: "mesmo e-mail", nome: "mesmo nome" };

export default function Duplicados({ onClose, onMesclou }) {
  const [grupos, setGrupos] = useState(null);
  // Qual ficha fica, por grupo. O padrão é a mais antiga: ela tende a ter mais
  // histórico acumulado, então mover o resto para ela move menos coisa.
  const [principal, setPrincipal] = useState({});
  const [ocupado, setOcupado] = useState(false);

  const carregar = useCallback(() => {
    api.get("/contacts/duplicates").then((r) => {
      setGrupos(r.data);
      const escolha = {};
      r.data.forEach((g, i) => {
        const maisAntigo = [...g.contatos].sort(
          (a, b) => (a.created_at || "").localeCompare(b.created_at || "")
        )[0];
        escolha[i] = maisAntigo?.id;
      });
      setPrincipal(escolha);
    }).catch(() => setGrupos([]));
  }, []);

  useEffect(() => { carregar(); }, [carregar]);

  const mesclar = async (grupo, indice) => {
    const fica = principal[indice];
    if (!fica) return;
    const absorver = grupo.contatos.filter((c) => c.id !== fica);
    setOcupado(true);
    try {
      // Um por vez, e em série: cada mesclagem move documentos e muda o estado
      // do principal, então em paralelo a segunda leria a ficha desatualizada.
      for (const c of absorver) {
        await api.post(`/contacts/${fica}/merge`, { secundario_id: c.id });
      }
      toast.success(`${absorver.length} ficha(s) mesclada(s)`);
      carregar();
      onMesclou?.();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setOcupado(false);
    }
  };

  return (
    <Modal titulo="Possíveis duplicados" largo
      descricao="Fichas com o mesmo e-mail ou o mesmo nome. Escolha qual fica: as outras são absorvidas e apagadas, e conversas, orçamentos, tarefas e notas passam para ela."
      onFechar={onClose} testId="duplicados">
      {grupos === null && <div className="text-xs text-ink-3 py-4">Procurando…</div>}

      {grupos?.length === 0 && (
        <div className="border border-dashed border-line rounded-xl bg-surface-2 p-8 text-center">
          <CheckCircle size={24} weight="regular" className="mx-auto mb-2 text-success" />
          <div className="font-heading text-lg font-semibold">Nenhum duplicado.</div>
          <div className="text-xs text-ink-2 mt-1">
            Não há fichas com e-mail ou nome repetido.
          </div>
        </div>
      )}

      <div className="space-y-3">
        {(grupos || []).map((g, i) => (
          <div key={`${g.criterio}-${g.valor}`} className="border border-line rounded-lg"
            data-testid={`grupo-${i}`}>
            <div className="px-3 py-2 bg-surface-2 border-b border-line flex items-center justify-between">
              <span className="text-xs text-ink-2">
                {g.contatos.length} fichas com {CRITERIO[g.criterio] || g.criterio}:{" "}
                <strong className="font-semibold">{g.valor}</strong>
              </span>
              <button onClick={() => mesclar(g, i)} disabled={ocupado || !principal[i]}
                className="zz-btn text-xs py-1 disabled:opacity-40" data-testid={`mesclar-${i}`}>
                <ArrowsMerge size={13} weight="regular" /> Mesclar
              </button>
            </div>
            {g.contatos.map((c) => (
              <label key={c.id}
                className={`flex items-center gap-3 px-3 py-2 border-b border-line last:border-b-0 cursor-pointer ${
                  principal[i] === c.id ? "bg-success-soft" : "hover:bg-surface-2"
                }`}>
                <input type="radio" name={`principal-${i}`} checked={principal[i] === c.id}
                  onChange={() => setPrincipal({ ...principal, [i]: c.id })} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold truncate">{c.nome}</div>
                  <div className="text-[11px] tabular-nums text-ink-3">
                    {c.numero_whatsapp}
                    {c.empresa ? ` · ${c.empresa}` : ""}
                    {c.created_at ? ` · desde ${new Date(c.created_at).toLocaleDateString("pt-BR")}` : ""}
                  </div>
                </div>
                <span className="text-sm font-medium text-ink-3 shrink-0">
                  {principal[i] === c.id ? "fica" : "será absorvida"}
                </span>
              </label>
            ))}
          </div>
        ))}
      </div>
    </Modal>
  );
}
