import { useState } from "react";
import { toast } from "sonner";
import { Plus, Trash, PencilSimple } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { confirmToast } from "@/lib/confirm-toast";
import { useAcao } from "@/hooks/useAcao";
import Modal from "@/components/ui/Modal";
import { DIAS_GRADE, moeda, telefone } from "./comum";
import { CampoHora, CampoDuracao, REGEX_RELOGIO, deMinutos } from "./CampoHora";
import { CamposSitePasseio, camposSite, problemaSite } from "@/components/site/comum";

// Passeios, guias e veículos: o cadastro que as Saídas inteiras consultam.
//
// A grade de saídas fica no **passeio** (ver `Servicos`). Guias e veículos
// (`Profissionais`, aba "Equipe e veículos") não têm grade: são escalados em
// cada saída. A agência entra por cima com os feriados (aba Configuração), que
// fecham todas as saídas de uma vez.

// ---------------------------------------------------------------------------
// Passeios (coleção `services`)
// ---------------------------------------------------------------------------
//
// Na fase 1 a grade é do **passeio**, não do guia: "a Lagoa Azul sai às 8h e às
// 14h, de segunda a sábado, com 30 lugares". Cada saída pode ter capacidade
// própria (a das 14h vai numa van menor), e as exceções por data fecham o dia
// (chuva, manutenção) ou trocam as saídas só naquele dia (réveillon).

const ABREV = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

function grade7(g) {
  return Array.from({ length: 7 }, (_, i) => (g && g[i] ? g[i] : []));
}

function juntar(lista) {
  if (lista.length <= 1) return lista.join("");
  return `${lista.slice(0, -1).join(", ")} e ${lista[lista.length - 1]}`;
}

/** "Seg a Sáb", "Todos os dias", "Seg, Qua e Sex". */
function resumoDias(indices) {
  if (indices.length === 7) return "Todos os dias";
  const seguidos = indices.length >= 3
    && indices.every((d, k) => k === 0 || d === indices[k - 1] + 1);
  if (seguidos) return `${ABREV[indices[0]]} a ${ABREV[indices[indices.length - 1]]}`;
  return juntar(indices.map((i) => ABREV[i]));
}

/** "08:00 e 14:00 · 30 lugares · Seg a Sáb". */
export function resumoPasseio(s) {
  const g = grade7(s.grade_saidas);
  const dias = g.map((d, i) => (d.length ? i : null)).filter((i) => i !== null);
  if (!dias.length) return null;
  const horas = [...new Set(g.flat().map((x) => x.hora))].sort();
  const mostradas = horas.length > 4 ? [...horas.slice(0, 4), "…"] : horas;
  return [
    horas.length > 4 ? mostradas.join(", ") : juntar(mostradas),
    `${s.capacidade ?? "?"} lugares`,
    resumoDias(dias),
  ].join(" · ");
}

/** Problemas da grade que o backend também recusaria (400), ditos antes. */
function problemasGrade(grade, excecoes) {
  const erros = [];
  const conferir = (lista, onde) => {
    const vistas = new Set();
    lista.forEach((x) => {
      if (!REGEX_RELOGIO.test(x.hora || "")) erros.push(`${onde}: hora incompleta`);
      else if (vistas.has(x.hora)) erros.push(`${onde}: saída das ${x.hora} repetida`);
      vistas.add(x.hora);
      if (x.capacidade != null && !(x.capacidade >= 1 && x.capacidade <= 500))
        erros.push(`${onde}: capacidade da saída das ${x.hora} fora de 1 a 500`);
    });
  };
  grade.forEach((d, i) => conferir(d, DIAS_GRADE[i]));
  excecoes.forEach((ex) => {
    if (!ex.data) erros.push("exceção sem data");
    else if (!ex.fechado) conferir(ex.saidas || [], `Exceção de ${ex.data.split("-").reverse().join("/")}`);
  });
  return erros;
}

function proximaHora(lista) {
  const ultima = [...lista].map((x) => x.hora).filter((h) => REGEX_RELOGIO.test(h || "")).sort().pop();
  if (!ultima) return "08:00";
  const h = Math.min(23, Number(ultima.slice(0, 2)) + 1);
  return `${String(h).padStart(2, "0")}:${ultima.slice(3)}`;
}

/** Lista de saídas de um dia: hora + capacidade opcional. */
function ListaSaidas({ saidas, capacidadePadrao, onChange, testid }) {
  const mexer = (k, patch) => onChange(saidas.map((x, n) => (n === k ? { ...x, ...patch } : x)));
  return (
    <div className="space-y-2">
      {saidas.map((x, k) => (
        <div key={k} className="flex flex-wrap items-center gap-2">
          <CampoHora valor={x.hora} testid={`${testid}-${k}-hora`}
            onChange={(v) => mexer(k, { hora: v })} />
          <input className="zz-input tabular-nums max-w-[120px]" inputMode="numeric"
            placeholder={capacidadePadrao ? `${capacidadePadrao} lugares` : "lugares"}
            value={x.capacidade ?? ""} data-testid={`${testid}-${k}-capacidade`}
            title="Em branco, usa a capacidade do passeio"
            onChange={(e) => {
              const d = e.target.value.replace(/\D/g, "");
              mexer(k, { capacidade: d === "" ? null : Math.min(500, Number(d)) });
            }} />
          <button type="button" className="zz-btn-icon" title="Remover esta saída"
            onClick={() => onChange(saidas.filter((_, n) => n !== k))}>
            <Trash size={14} weight="regular" />
          </button>
        </div>
      ))}
      <button type="button" className="zz-btn zz-btn-secondary text-[11px] py-1"
        data-testid={`${testid}-add`}
        onClick={() => onChange([...saidas, { hora: proximaHora(saidas), capacidade: null }])}>
        <Plus size={12} weight="regular" /> Saída
      </button>
    </div>
  );
}

function GradeSaidas({ grade, capacidade, onChange }) {
  const g = grade7(grade);
  const copiar = (dias) => onChange(g.map((d, i) => (dias.includes(i) ? g[0].map((x) => ({ ...x })) : d)));

  return (
    <div className="space-y-2" data-testid="grade-saidas-passeio">
      <div className="flex flex-wrap gap-2">
        <button type="button" className="zz-btn zz-btn-secondary text-[11px] py-1"
          data-testid="grade-copiar-uteis" disabled={!g[0].length}
          onClick={() => copiar([1, 2, 3, 4])}>
          Copiar segunda para os dias úteis
        </button>
        <button type="button" className="zz-btn zz-btn-secondary text-[11px] py-1"
          data-testid="grade-copiar-todos" disabled={!g[0].length}
          onClick={() => copiar([1, 2, 3, 4, 5, 6])}>
          Copiar segunda para todos os dias
        </button>
      </div>
      {DIAS_GRADE.map((nome, i) => (
        <div key={nome}
          className="grid grid-cols-1 sm:grid-cols-[110px_1fr] gap-2 items-start border border-line rounded-lg p-3">
          <div className="flex items-center justify-between sm:block">
            <span className="text-sm font-semibold">{nome}</span>
            {g[i].length === 0 && <span className="text-[11px] text-ink-3 sm:block">Sem saída</span>}
          </div>
          <ListaSaidas saidas={g[i]} capacidadePadrao={capacidade} testid={`grade-${i}`}
            onChange={(lista) => onChange(g.map((d, k) => (k === i ? lista : d)))} />
        </div>
      ))}
    </div>
  );
}

function ExcecoesSaidas({ excecoes, capacidade, onChange }) {
  const mexer = (i, patch) => onChange(excecoes.map((x, k) => (k === i ? { ...x, ...patch } : x)));
  return (
    <div>
      <div className="flex items-start justify-between gap-3 mb-2">
        <div>
          <div className="text-sm font-medium text-ink-2">Exceções por data</div>
          <p className="text-[11px] text-ink-3">
            Fechar o passeio num dia (chuva, manutenção) ou trocar as saídas só naquele dia.
            Feriado que fecha todos os passeios fica em Configuração.
          </p>
        </div>
        <button type="button" className="zz-btn zz-btn-secondary text-xs shrink-0"
          data-testid="add-excecao"
          onClick={() => onChange([...excecoes, { data: "", fechado: true, motivo: "", saidas: [] }])}>
          <Plus size={12} weight="regular" /> Exceção
        </button>
      </div>
      <div className="space-y-2">
        {excecoes.map((ex, i) => (
          <div key={i} className="border border-line rounded-lg p-3 space-y-2" data-testid={`excecao-${i}`}>
            <div className="flex flex-wrap items-center gap-2">
              <input type="date" className="zz-input tabular-nums max-w-[170px]"
                value={ex.data} onChange={(e) => mexer(i, { data: e.target.value })} />
              <select className="zz-input max-w-[220px]" value={ex.fechado ? "1" : "0"}
                onChange={(e) => mexer(i, { fechado: e.target.value === "1" })}>
                <option value="1">Sem saídas neste dia</option>
                <option value="0">Saídas diferentes neste dia</option>
              </select>
              <input className="zz-input flex-1 min-w-[140px]" placeholder="Motivo (opcional)"
                value={ex.motivo || ""} onChange={(e) => mexer(i, { motivo: e.target.value })} />
              <button type="button" className="zz-btn-icon" title="Remover exceção"
                onClick={() => onChange(excecoes.filter((_, k) => k !== i))}>
                <Trash size={14} weight="regular" />
              </button>
            </div>
            {!ex.fechado && (
              <div className="pl-1">
                <ListaSaidas saidas={ex.saidas || []} capacidadePadrao={capacidade}
                  testid={`excecao-${i}-saida`}
                  onChange={(lista) => mexer(i, { saidas: lista })} />
                <p className="text-[11px] text-ink-3 mt-1">
                  Neste dia valem só estas saídas, no lugar das da grade.
                </p>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function paraNumero(v) {
  if (v === "" || v == null) return null;
  const n = Number(String(v).replace(",", "."));
  return Number.isFinite(n) ? n : null;
}

export function Servicos({ servicos, onMudou }) {
  const [editando, setEditando] = useState(null);

  const salvar = async () => {
    const ordena = (lista) => [...lista].sort((a, b) => (a.hora < b.hora ? -1 : 1))
      .map((x) => ({ hora: x.hora, capacidade: x.capacidade ?? null }));
    const grade = grade7(editando.grade_saidas).map(ordena);
    const excecoes = (editando.excecoes_saidas || []).map((ex) => ({
      data: ex.data, fechado: !!ex.fechado, motivo: ex.motivo || "",
      saidas: ex.fechado ? [] : ordena(ex.saidas || []),
    }));
    const problemas = problemasGrade(grade, excecoes);
    if (!editando.nome.trim()) return toast.error("Dê um nome ao passeio");
    if (problemas.length) return toast.error(problemas[0]);
    const capacidade = Number(editando.capacidade);
    if (!(capacidade >= 1 && capacidade <= 500)) return toast.error("Capacidade por saída: de 1 a 500 lugares");
    const erroSite = problemaSite(editando);
    if (erroSite) return toast.error(erroSite);

    const corpo = {
      nome: editando.nome.trim(),
      descricao: editando.descricao || null,
      duracao_min: Math.max(5, Number(editando.duracao_min) || 60),
      preco: paraNumero(editando.preco),
      preco_crianca: paraNumero(editando.preco_crianca),
      capacidade,
      grade_saidas: grade,
      excecoes_saidas: excecoes,
      ativo: editando.ativo !== false,
      ordem: Number(editando.ordem) || 0,
      // Sem a tela para eles, mas o PATCH regrava o passeio inteiro: sem
      // mandar o que veio do servidor, salvar apagava a cor e os recursos.
      cor: editando.cor ?? null,
      recursos: editando.recursos || [],
      // Página do passeio no site (fase 3). Vai no mesmo corpo porque o PATCH
      // regrava o passeio inteiro com o `ServicoIn`.
      ...camposSite(editando),
    };
    try {
      if (editando.id) await api.patch(`/agenda/services/${editando.id}`, corpo);
      else await api.post("/agenda/services", corpo);
      setEditando(null);
      toast.success("Passeio salvo");
      onMudou();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  // Dois cliques na lixeira abriam duas confirmações, e a segunda exclusão
  // voltava 404 com toast de erro em cima de uma operação que tinha dado certo.
  const [excluir, excluindo] = useAcao(async (s) => {
    if (!(await confirmToast(`Excluir "${s.nome}"?`, { confirmLabel: "Excluir" }))) return;
    try {
      await api.delete(`/agenda/services/${s.id}`);
      onMudou();
    } catch (e) {
      // 409 é o passeio com reserva à frente. A mensagem do backend já diz
      // quantas e sugere desativar.
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const abrir = (s) => setEditando({
    ...s,
    preco: s.preco ?? "",
    preco_crianca: s.preco_crianca ?? "",
    capacidade: s.capacidade ?? 20,
    grade_saidas: grade7(s.grade_saidas),
    excecoes_saidas: s.excecoes_saidas || [],
  });

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <p className="text-sm text-ink-2 max-w-3xl">
          O que a agência vende. Cada passeio tem os <strong>horários de saída</strong> da semana e
          quantos <strong>lugares</strong> cabem em cada saída: é disso que saem as vagas oferecidas
          no atendimento, no fluxo automático e pelo assistente de IA.
        </p>
        <button type="button" className="zz-btn shrink-0" data-testid="novo-servico"
          onClick={() => setEditando({
            nome: "", descricao: "", duracao_min: 480, preco: "", preco_crianca: "",
            capacidade: 20, grade_saidas: grade7(null), excecoes_saidas: [], ativo: true,
            ordem: servicos.length,
            site_visivel: true, site_resumo: "", site_descricao: "", site_inclui: [],
            site_levar: [], site_fotos: [], site_slug: "",
          })}>
          <Plus size={14} weight="regular" /> Novo passeio
        </button>
      </div>

      {servicos.length === 0 ? (
        <Vazio texto="Nenhum passeio cadastrado. Sem pelo menos um com saídas, o fluxo automático e o assistente de IA não têm o que reservar." />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {servicos.map((s) => {
            const resumo = resumoPasseio(s);
            return (
              <div key={s.id} className={`border border-line rounded-xl p-4 bg-surface ${
                s.ativo === false ? "opacity-60" : ""}`} data-testid="cartao-servico">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-semibold truncate">{s.nome}</div>
                    <div className="text-xs text-ink-3 mt-0.5 tabular-nums">
                      {deMinutos(s.duracao_min)} h
                      {s.preco != null ? ` · ${moeda(s.preco)} adulto` : ""}
                      {s.preco != null && s.preco_crianca != null
                        ? ` · ${Number(s.preco_crianca) === 0 ? "criança não paga" : `${moeda(s.preco_crianca)} criança`}`
                        : ""}
                      {s.ativo === false ? " · desativado" : ""}
                    </div>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <button type="button" className="zz-btn-icon" title="Editar" onClick={() => abrir(s)}
                      data-testid="editar-servico">
                      <PencilSimple size={14} weight="regular" />
                    </button>
                    <button type="button" className="zz-btn-icon" title="Excluir" onClick={() => excluir(s)}
                      disabled={excluindo}>
                      <Trash size={14} weight="regular" />
                    </button>
                  </div>
                </div>
                {s.descricao && <p className="text-xs text-ink-2 mt-2">{s.descricao}</p>}
                <div className={`text-[11px] mt-2 tabular-nums ${resumo ? "text-ink-2" : "text-warning"}`}
                  data-testid="resumo-servico">
                  {resumo || "Sem saídas na grade: não aparece para reserva"}
                </div>
                {s.excecoes_saidas?.length > 0 && (
                  <div className="text-[11px] text-ink-3 mt-1">
                    {s.excecoes_saidas.length} exceção(ões) de data
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {editando && (
        <Modal titulo={editando.id ? "Editar passeio" : "Novo passeio"} largo
          onFechar={() => setEditando(null)} onSalvar={salvar}>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Campo label="Nome">
              <input className="zz-input" value={editando.nome} data-testid="servico-nome" required
                onChange={(e) => setEditando({ ...editando, nome: e.target.value })} />
            </Campo>
            <Campo label="Duração (horas:minutos)">
              <CampoDuracao minutos={editando.duracao_min} min={5} testid="servico-duracao"
                onChange={(m) => setEditando({ ...editando, duracao_min: m })} />
            </Campo>
          </div>
          <Campo label="Descrição">
            <input className="zz-input" value={editando.descricao || ""}
              placeholder="Aparece na lista que o cliente recebe no WhatsApp."
              onChange={(e) => setEditando({ ...editando, descricao: e.target.value })} />
          </Campo>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 items-start">
            <Campo label="Preço por adulto">
              <input type="number" min="0" step="0.01" className="zz-input tabular-nums"
                value={editando.preco} data-testid="servico-preco" placeholder="sem preço"
                onChange={(e) => setEditando({ ...editando, preco: e.target.value })} />
            </Campo>
            <Campo label="Preço por criança">
              <input type="number" min="0" step="0.01" className="zz-input tabular-nums"
                value={editando.preco_crianca} data-testid="servico-preco-crianca"
                placeholder="igual ao adulto"
                onChange={(e) => setEditando({ ...editando, preco_crianca: e.target.value })} />
              <p className="text-[11px] text-ink-3 mt-1">
                Em branco, criança paga o mesmo que adulto. 0 = criança não paga.
              </p>
            </Campo>
            <Campo label="Lugares por saída">
              <input type="number" min="1" max="500" className="zz-input tabular-nums"
                value={editando.capacidade} data-testid="servico-capacidade"
                onChange={(e) => setEditando({ ...editando, capacidade: e.target.value })} />
              <p className="text-[11px] text-ink-3 mt-1">
                Padrão de cada saída; uma saída pode ter o próprio número na grade.
              </p>
            </Campo>
            <Campo label="Ordem na lista">
              <input type="number" className="zz-input tabular-nums" value={editando.ordem}
                onChange={(e) => setEditando({ ...editando, ordem: e.target.value })} />
            </Campo>
          </div>

          <div>
            <div className="text-sm font-medium text-ink-2">Saídas da semana</div>
            <p className="text-[11px] text-ink-3 mb-2">
              O horário de cada saída e, se for diferente do padrão, quantos lugares ela tem.
            </p>
            <GradeSaidas grade={editando.grade_saidas} capacidade={editando.capacidade}
              onChange={(g) => setEditando({ ...editando, grade_saidas: g })} />
          </div>

          <ExcecoesSaidas excecoes={editando.excecoes_saidas || []} capacidade={editando.capacidade}
            onChange={(x) => setEditando({ ...editando, excecoes_saidas: x })} />

          <Ativo valor={editando.ativo !== false}
            onChange={(v) => setEditando({ ...editando, ativo: v })}
            nota="Desativado, some do fluxo automático e da tela de reserva, mas as reservas já feitas continuam valendo." />

          <div className="border-t border-line pt-4">
            <div className="text-sm font-medium text-ink-2">Página no site</div>
            <p className="text-[11px] text-ink-3 mb-3">
              O que o turista vê na página deste passeio no site da agência. O preço e os
              horários saem do cadastro acima.
            </p>
            <CamposSitePasseio valor={editando} onChange={setEditando} />
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Equipe e veículos (coleção `resources`)
// ---------------------------------------------------------------------------
//
// Guia é recurso do tipo `pessoa` e veículo é do tipo `sala` (o valor gravado
// é herdado da agenda de horários). Na fase 2 eles não têm mais grade semanal
// nem exceções: quem decide quando há saída é o passeio, e o guia e o veículo
// são **escalados** em cada saída, no painel dela. Os campos `grade` e
// `excecoes` que estiverem gravados são devolvidos como vieram, sem tela.

const TIPOS_EQUIPE = [
  ["pessoa", "Guia"],
  ["sala", "Veículo"],
];

function rotuloTipo(tipo) {
  if (tipo === "pessoa") return "Guia";
  if (tipo === "sala") return "Veículo";
  return "Equipamento";
}

export function Profissionais({ recursos, onMudou }) {
  const [editando, setEditando] = useState(null);

  const salvar = async () => {
    const nome = (editando.nome || "").trim();
    if (!nome) {
      toast.error("Dê um nome");
      return;
    }
    const veiculo = editando.tipo === "sala";
    const capacidade = veiculo && String(editando.capacidade ?? "").trim() !== ""
      ? Number(editando.capacidade) : null;
    if (capacidade != null && !(Number.isInteger(capacidade) && capacidade >= 1 && capacidade <= 500)) {
      toast.error("Lugares do veículo: de 1 a 500");
      return;
    }
    const corpo = {
      nome,
      tipo: editando.tipo || "pessoa",
      descricao: (editando.descricao || "").trim() || null,
      telefone: (editando.telefone || "").trim() || null,
      capacidade,
      ativo: editando.ativo !== false,
    };
    try {
      if (editando.id) await api.patch(`/agenda/resources/${editando.id}`, corpo);
      else await api.post("/agenda/resources", corpo);
      setEditando(null);
      toast.success(veiculo ? "Veículo salvo" : "Guia salvo");
      onMudou();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  };

  const [excluir, excluindo] = useAcao(async (r) => {
    if (!(await confirmToast(`Excluir "${r.nome}"?`, { confirmLabel: "Excluir" }))) return;
    try {
      await api.delete(`/agenda/resources/${r.id}`);
      onMudou();
    } catch (e) {
      toast.error(formatApiErrorDetail(e.response?.data?.detail));
    }
  });

  const grupos = TIPOS_EQUIPE.map(([tipo, rotulo]) => [
    tipo, rotulo, recursos.filter((r) => (r.tipo || "pessoa") === tipo),
  ]);
  const outros = recursos.filter((r) => !TIPOS_EQUIPE.some(([t]) => t === (r.tipo || "pessoa")));

  const novo = (tipo) => setEditando({
    nome: "", tipo, descricao: "", telefone: "", capacidade: "", ativo: true,
  });

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <p className="text-sm text-ink-2 max-w-3xl">
          Os guias e os veículos da agência. Cada saída tem o seu guia e o seu veículo,
          escolhidos no painel da saída, e eles aparecem na lista de embarque.
        </p>
        <div className="flex flex-wrap gap-2 shrink-0">
          <button type="button" className="zz-btn zz-btn-secondary" data-testid="novo-veiculo"
            onClick={() => novo("sala")}>
            <Plus size={14} weight="regular" /> Novo veículo
          </button>
          <button type="button" className="zz-btn" data-testid="novo-profissional"
            onClick={() => novo("pessoa")}>
            <Plus size={14} weight="regular" /> Novo guia
          </button>
        </div>
      </div>

      {recursos.length === 0 ? (
        <Vazio texto="Nenhum guia ou veículo cadastrado. Eles são opcionais: servem para escalar a equipe de cada saída e sair na lista de embarque." />
      ) : (
        <div className="space-y-6">
          {[...grupos, ...(outros.length ? [["outros", "Outros", outros]] : [])]
            .filter(([, , lista]) => lista.length)
            .map(([tipo, rotulo, lista]) => (
              <section key={tipo} data-testid={`equipe-grupo-${tipo}`}>
                <h3 className="text-sm font-semibold text-ink-2 mb-2">
                  {rotulo === "Guia" ? "Guias" : rotulo === "Veículo" ? "Veículos" : rotulo}
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                  {lista.map((r) => (
                    <div key={r.id} className={`border border-line rounded-xl p-4 bg-surface ${
                      r.ativo === false ? "opacity-60" : ""}`} data-testid="cartao-profissional">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="font-semibold truncate">{r.nome}</div>
                          <div className="text-xs text-ink-3 mt-0.5 tabular-nums">
                            {[
                              rotuloTipo(r.tipo),
                              r.tipo === "sala" && r.capacidade ? `${r.capacidade} lugares` : null,
                              r.telefone ? telefone(r.telefone) : null,
                              r.ativo === false ? "desativado" : null,
                            ].filter(Boolean).join(" · ")}
                          </div>
                        </div>
                        <div className="flex gap-1 shrink-0">
                          <button type="button" className="zz-btn-icon" title="Editar" aria-label="Editar"
                            data-testid="editar-profissional"
                            onClick={() => setEditando({
                              ...r,
                              telefone: r.telefone || "",
                              descricao: r.descricao || "",
                              capacidade: r.capacidade ?? "",
                            })}>
                            <PencilSimple size={14} weight="regular" />
                          </button>
                          <button type="button" className="zz-btn-icon" title="Excluir" aria-label="Excluir"
                            onClick={() => excluir(r)} disabled={excluindo}>
                            <Trash size={14} weight="regular" />
                          </button>
                        </div>
                      </div>
                      {r.descricao && <p className="text-xs text-ink-2 mt-2">{r.descricao}</p>}
                    </div>
                  ))}
                </div>
              </section>
            ))}
        </div>
      )}

      {editando && (
        <Modal
          titulo={editando.id
            ? (editando.tipo === "sala" ? "Editar veículo" : "Editar guia")
            : (editando.tipo === "sala" ? "Novo veículo" : "Novo guia")}
          onFechar={() => setEditando(null)} onSalvar={salvar}>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Campo label="Nome">
              <input className="zz-input" value={editando.nome} data-testid="profissional-nome" required
                placeholder={editando.tipo === "sala" ? "Van 15 lugares (placa ABC1D23)" : "Nome do guia"}
                onChange={(e) => setEditando({ ...editando, nome: e.target.value })} />
            </Campo>
            <Campo label="Tipo">
              <select className="zz-input" value={editando.tipo || "pessoa"} data-testid="profissional-tipo"
                onChange={(e) => setEditando({ ...editando, tipo: e.target.value })}>
                {TIPOS_EQUIPE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                {editando.tipo === "equipamento" && <option value="equipamento">Equipamento</option>}
              </select>
            </Campo>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Campo label={editando.tipo === "sala" ? "Telefone do motorista" : "Telefone"}>
              <input className="zz-input tabular-nums" value={editando.telefone || ""}
                data-testid="profissional-telefone" placeholder="(98) 99999-8888"
                onChange={(e) => setEditando({ ...editando, telefone: e.target.value })} />
            </Campo>
            {editando.tipo === "sala" && (
              <Campo label="Lugares">
                <input className="zz-input tabular-nums" inputMode="numeric"
                  value={editando.capacidade ?? ""} data-testid="profissional-capacidade"
                  placeholder="ex: 15"
                  onChange={(e) => setEditando({
                    ...editando, capacidade: e.target.value.replace(/\D/g, "").slice(0, 3),
                  })} />
                <p className="text-[11px] text-ink-3 mt-1">
                  Só para conferência: o painel avisa quando o grupo da saída não cabe no veículo.
                  As vagas continuam saindo da capacidade do passeio.
                </p>
              </Campo>
            )}
          </div>
          <Campo label="Descrição">
            <input className="zz-input" value={editando.descricao || ""} data-testid="profissional-descricao"
              placeholder={editando.tipo === "sala" ? "4x4, ar-condicionado…" : "Idiomas, especialidade…"}
              onChange={(e) => setEditando({ ...editando, descricao: e.target.value })} />
          </Campo>
          <Ativo valor={editando.ativo !== false}
            onChange={(v) => setEditando({ ...editando, ativo: v })}
            nota="Desativado, some das opções de equipe das saídas; as saídas em que já está escalado continuam mostrando o nome." />
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function Vazio({ texto }) {
  return (
    <div className="border border-dashed border-line rounded-xl p-10 text-center text-sm text-ink-3">
      {texto}
    </div>
  );
}

function Campo({ label, children }) {
  return (
    <div>
      <label className="text-sm font-medium text-ink-2 block mb-1.5">
        {label}
      </label>
      {children}
    </div>
  );
}

function Ativo({ valor, onChange, nota }) {
  return (
    <label className="flex items-start gap-2 text-sm cursor-pointer">
      <input type="checkbox" checked={valor} className="mt-0.5"
        onChange={(e) => onChange(e.target.checked)} />
      <span>Ativo<span className="block text-[11px] text-ink-3">{nota}</span></span>
    </label>
  );
}
