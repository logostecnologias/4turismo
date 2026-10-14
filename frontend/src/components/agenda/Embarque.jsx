import { X } from "@phosphor-icons/react";
import { CampoHora, deMinutos, paraMinutos } from "./CampoHora";
import { hora } from "./comum";

// Onde e a que horas a equipe busca o grupo: os campos de embarque de uma
// reserva, usados na reserva nova e no "Editar embarque" do painel da saída.
//
// O valor da tela é `{ponto, outroNome, hora, manual, obs}`:
//   - `ponto`: "" (não informado), o id de um ponto cadastrado, ou OUTRO;
//   - `hora`: "HH:MM" ou "" (a combinar);
//   - `manual`: a hora foi digitada. Sem isso, a hora de um ponto cadastrado
//     acompanha a saída (saída − minutos antes) e trocar de saída a recalcula.
//
// **Ponto cadastrado sempre tem hora.** Pelo contrato, ponto cadastrado sem
// `horario_busca` faz o servidor calcular `saída − minutos_antes`, então não há
// como gravar "Pousada do Rio, hora a combinar": o campo não oferece limpar
// nesse caso. Lugar fora da lista sem hora é "a combinar".

export const OUTRO = "__outro";

/** Hora da saída menos N minutos, presa em 00:00 (nunca vira a véspera). */
export function horaMenos(hhmm, minutos) {
  if (!hhmm) return "";
  return deMinutos(Math.max(0, paraMinutos(hhmm) - (Number(minutos) || 0)));
}

/** Estado inicial dos campos a partir de uma reserva (ou vazio). */
export function embarqueDe(a, pontos, fuso) {
  if (!a || (!a.ponto_embarque_id && !a.ponto_embarque_nome && !a.horario_busca && !a.embarque_obs)) {
    return { ponto: "", outroNome: "", hora: "", manual: false, obs: a?.embarque_obs || "" };
  }
  const conhecido = a.ponto_embarque_id && (pontos || []).some((p) => p.id === a.ponto_embarque_id);
  const h = a.horario_busca ? hora(a.horario_busca, fuso) : "";
  if (conhecido) {
    return { ponto: a.ponto_embarque_id, outroNome: "", hora: h, manual: !!h, obs: a.embarque_obs || "" };
  }
  // Ponto que saiu da configuração: a reserva guardou o nome, e ele vira
  // "outro lugar" com o mesmo texto.
  return {
    ponto: a.ponto_embarque_nome ? OUTRO : "",
    outroNome: a.ponto_embarque_nome || "",
    hora: h,
    manual: true,
    obs: a.embarque_obs || "",
  };
}

/** A hora que vale para a tela: digitada ou calculada da saída. */
export function horaEfetiva(v, pontos, horaSaida) {
  if (v.manual || v.ponto === OUTRO) return v.hora || "";
  const p = (pontos || []).find((x) => x.id === v.ponto);
  return p ? horaMenos(horaSaida, p.minutos_antes ?? 30) : "";
}

/** Problema que o servidor recusaria, dito antes. `null` se está tudo certo. */
export function problemaEmbarque(v, pontos, horaSaida) {
  if (v.ponto === OUTRO && !v.outroNome.trim()) return "Escreva onde buscar, ou escolha um ponto da lista";
  const h = horaEfetiva(v, pontos, horaSaida);
  if (h && horaSaida && paraMinutos(h) > paraMinutos(horaSaida))
    return `A busca (${h}) não pode ser depois da saída (${horaSaida})`;
  return null;
}

/** Corpo do embarque para `POST /appointments` e `PATCH .../embarque`. */
export function corpoEmbarque(v) {
  const obs = (v.obs || "").trim() || null;
  if (v.ponto === OUTRO) {
    return {
      ponto_embarque_id: null,
      ponto_embarque_nome: v.outroNome.trim(),
      horario_busca: v.hora || null,
      embarque_obs: obs,
    };
  }
  if (v.ponto) {
    // Sem hora digitada vai nulo, e o servidor calcula da saída: é a mesma
    // conta que a tela mostra, feita por quem conhece a saída de verdade.
    return {
      ponto_embarque_id: v.ponto,
      ponto_embarque_nome: null,
      horario_busca: v.manual && v.hora ? v.hora : null,
      embarque_obs: obs,
    };
  }
  return { ponto_embarque_id: null, ponto_embarque_nome: null, horario_busca: null, embarque_obs: obs };
}

export function CamposEmbarque({ valor, onChange, pontos, horaSaida, testid = "embarque" }) {
  const ativos = (pontos || []).filter((p) => p.ativo !== false);
  // O ponto da reserva pode ter sido desativado depois: continua na lista
  // enquanto for o escolhido, para abrir a edição não trocar o ponto calado.
  const atual = (pontos || []).find((p) => p.id === valor.ponto && p.ativo === false);
  const lista = atual ? [...ativos, atual] : ativos;
  const ponto = lista.find((p) => p.id === valor.ponto);
  const h = horaEfetiva(valor, pontos, horaSaida);
  const problema = problemaEmbarque(valor, pontos, horaSaida);
  const set = (patch) => onChange({ ...valor, ...patch });

  return (
    <div className="space-y-3" data-testid={testid}>
      <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_auto] gap-3 items-start">
        <div>
          <label className="text-sm font-medium text-ink-2 block mb-1.5">Busca</label>
          <select className="zz-input" value={valor.ponto} data-testid={`${testid}-ponto`}
            onChange={(e) => set({ ponto: e.target.value, hora: "", manual: false })}>
            <option value="">Não informado</option>
            {lista.map((p) => (
              <option key={p.id} value={p.id}>
                {p.nome}{p.ativo === false ? " (desativado)" : ""}
              </option>
            ))}
            <option value={OUTRO}>Outro lugar…</option>
          </select>
          {ponto?.endereco && <p className="text-[11px] text-ink-3 mt-1">{ponto.endereco}</p>}
          {!lista.length && (
            <p className="text-[11px] text-ink-3 mt-1">
              Nenhum ponto de embarque cadastrado: o admin cadastra em Configuração.
            </p>
          )}
        </div>
        {valor.ponto && (
          <div>
            <label className="text-sm font-medium text-ink-2 block mb-1.5">Horário de busca</label>
            <div className="flex items-center gap-2">
              <CampoHora valor={h} testid={`${testid}-hora`}
                onChange={(v) => set({ hora: v, manual: true })} />
              {valor.ponto === OUTRO && h && (
                <button type="button" className="zz-btn-icon" title="Deixar a combinar"
                  aria-label="Deixar a combinar" data-testid={`${testid}-hora-limpar`}
                  onClick={() => set({ hora: "", manual: true })}>
                  <X size={14} weight="regular" />
                </button>
              )}
            </div>
            <p className="text-[11px] text-ink-3 mt-1">
              {valor.ponto === OUTRO
                ? (h ? "Digitado." : "Em branco: a combinar.")
                : valor.manual
                  ? "Digitado."
                  : horaSaida
                    ? `${ponto?.minutos_antes ?? 30} min antes da saída.`
                    : "Calculado quando a saída for escolhida."}
            </p>
          </div>
        )}
      </div>
      {valor.ponto === OUTRO && (
        <input className="zz-input" value={valor.outroNome} data-testid={`${testid}-outro`}
          placeholder="Hotel, pousada ou endereço"
          onChange={(e) => set({ outroNome: e.target.value })} />
      )}
      <input className="zz-input" value={valor.obs} data-testid={`${testid}-obs`}
        placeholder="Observação de embarque: apto 12, levar cadeirinha…"
        onChange={(e) => set({ obs: e.target.value })} />
      {problema && (
        <p className="text-[11px] text-danger" data-testid={`${testid}-problema`}>{problema}</p>
      )}
    </div>
  );
}
