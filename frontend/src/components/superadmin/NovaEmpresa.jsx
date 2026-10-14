import { useEffect, useMemo, useRef, useState } from "react";
import api from "@/lib/api";
import { useAcao } from "@/hooks/useAcao";
import { formatBRL } from "@/lib/currency";
import { toast } from "sonner";
import {
  ArrowsClockwise, Buildings, CheckCircle, Copy, Eye, EyeSlash,
  SignIn, Warning, WhatsappLogo, X,
} from "@phosphor-icons/react";

// Espelha o `SENHA_MIN` de `backend/routes/tenants.py`. O piso de verdade é o
// do servidor; este aqui existe para o super admin saber do limite enquanto
// digita, em vez de descobrir por um 422 em inglês depois de preencher tudo.
const SENHA_MIN = 8;

// Sem `l`, `I`, `1`, `O` e `0`: a senha vai ser lida em voz alta ou copiada de
// um WhatsApp e digitada à mão pelo cliente, e é nesses cinco caracteres que
// isso falha.
const ALFABETO = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function gerarSenha(tamanho = 14) {
  const sorteio = new Uint32Array(tamanho);
  crypto.getRandomValues(sorteio);
  return Array.from(sorteio, (n) => ALFABETO[n % ALFABETO.length]).join("");
}

async function copiar(texto, aviso) {
  try {
    await navigator.clipboard.writeText(texto);
    toast.success(aviso);
  } catch {
    // `navigator.clipboard` não existe fora de HTTPS (e o painel roda em
    // localhost sem TLS em desenvolvimento). Sem este aviso o clique não faz
    // nada e parece que copiou.
    toast.error("Não foi possível copiar. Selecione o texto e copie à mão.");
  }
}

// --- Leitura dos limites do plano -------------------------------------------
// As três regras abaixo são diferentes entre si, e é de propósito. Quem lê a
// tela precisa das três certas, porque é o que a empresa vai receber.

// max_bots / max_conexoes / max_usuarios: `null` é ilimitado, e 0 barra tudo
// (`enforce_plan_limit` recusa quando a contagem chega ao teto, então com teto
// zero nada pode ser criado).
function porContagem(valor) {
  if (valor === null || valor === undefined) return { texto: "ilimitado" };
  if (Number(valor) === 0) return { texto: "nenhum", alerta: true };
  return { texto: Number(valor).toLocaleString("pt-BR") };
}

// max_tokens_ia_mes: aqui vazio e 0 querem dizer a mesma
// coisa (ilimitado), ao contrário dos de cima.
function porJanela(valor) {
  if (valor === null || valor === undefined || Number(valor) === 0) return { texto: "ilimitado" };
  return { texto: Number(valor).toLocaleString("pt-BR") };
}

// retencao_midia_dias: vazio herda o default do servidor e 0 nunca apaga.
function porRetencao(dias) {
  if (dias === null || dias === undefined || dias === "") return { texto: "padrão do servidor" };
  if (Number(dias) === 0) return { texto: "sem expiração" };
  return { texto: `${dias} dias` };
}

function limitesDoPlano(plano) {
  if (!plano) return [];
  return [
    { rotulo: "Fluxos automáticos", ...porContagem(plano.max_bots) },
    { rotulo: "Conexões WhatsApp", ...porContagem(plano.max_conexoes) },
    { rotulo: "Usuários", ...porContagem(plano.max_usuarios) },
    { rotulo: "Tokens de IA/mês", ...porJanela(plano.max_tokens_ia_mes) },
    { rotulo: "Mídia guardada", ...porRetencao(plano.retencao_midia_dias) },
  ];
}

/**
 * Criação de empresa em duas partes: o formulário e a entrega do acesso.
 *
 * A segunda parte é o motivo de isto ser um modal e não o formulário inline de
 * antes. A senha inicial não volta do servidor nunca mais (`users` guarda só o
 * hash), então o único momento em que ela existe para ser passada ao cliente é
 * agora. Fechar direto no toast, como era, empurrava esse repasse para a
 * memória de quem criou.
 */
export default function NovaEmpresa({ plans, onFechar, onCriada, onEntrar }) {
  const [form, setForm] = useState({
    name: "", plan_id: "", admin_name: "", admin_email: "", admin_password: "",
  });
  const [verSenha, setVerSenha] = useState(false);
  const [erroEmail, setErroEmail] = useState("");
  const [criada, setCriada] = useState(null);
  const emailRef = useRef(null);

  const plano = useMemo(
    () => plans.find((p) => p.id === form.plan_id) || null,
    [plans, form.plan_id],
  );

  // Esc fecha só enquanto é formulário. Na tela de acesso ele fica desligado de
  // propósito: um Esc distraído ali leva embora a única cópia da senha.
  useEffect(() => {
    if (criada) return undefined;
    const aoTeclar = (e) => { if (e.key === "Escape") onFechar(); };
    window.addEventListener("keydown", aoTeclar);
    return () => window.removeEventListener("keydown", aoTeclar);
  }, [criada, onFechar]);

  const [criar, criando] = useAcao(async (e) => {
    e.preventDefault();
    setErroEmail("");
    try {
      // `plan_id` vazio vira null: string vazia seria gravada como plano da
      // empresa e a lista mostraria "sem plano" sem que nada explicasse por quê.
      const { data } = await api.post("/admin/tenants", { ...form, plan_id: form.plan_id || null });
      setCriada({ tenant: data, ...form });
      onCriada();
    } catch (ex) {
      const detalhe = ex.response?.data?.detail;
      const msg = typeof detalhe === "string" ? detalhe : "Erro ao criar agência";
      // E-mail repetido é o erro que aparece aqui na prática, e ele tem um
      // campo para apontar. Mensagem no rodapé obrigava a procurar qual dos
      // cinco campos estava errado.
      if (/email/i.test(msg)) {
        setErroEmail(msg);
        emailRef.current?.focus();
      }
      toast.error(msg);
    }
  });

  if (criada) {
    return (
      <Moldura titulo="Agência criada" icone={CheckCircle} onFechar={onFechar} travado>
        <Acesso dados={criada} onFechar={onFechar} onEntrar={onEntrar} />
      </Moldura>
    );
  }

  return (
    <Moldura titulo="Nova agência" icone={Buildings} onFechar={onFechar}>
      <form onSubmit={criar} className="flex flex-col min-h-0 flex-1" data-testid="tenant-form">
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Campo label="Nome da agência">
              <input required autoFocus data-testid="tenant-name" className="zz-input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Campo>
            <Campo label="Plano">
              {plans.length === 0 ? (
                <div className="zz-input flex items-center text-ink-3 italic">
                  Nenhum plano cadastrado
                </div>
              ) : (
                <select required data-testid="tenant-plan" className="zz-input" value={form.plan_id}
                  onChange={(e) => setForm({ ...form, plan_id: e.target.value })}>
                  <option value="" disabled>Selecione um plano</option>
                  {plans.map((p) => (
                    <option key={p.id} value={p.id}>{p.nome} · {formatBRL(p.preco_mensal)}/mês</option>
                  ))}
                </select>
              )}
            </Campo>
          </div>

          {plans.length === 0
            ? <Aviso>Crie um plano em <b>Planos</b> antes: sem plano vinculado a agência fica sem
                nenhum limite aplicado, e não vê teto nenhum em Ajustes.</Aviso>
            : <ResumoPlano plano={plano} />}

          <div>
            <Titulo>Admin da agência</Titulo>
            <p className="text-xs text-ink-3 mb-3">
              É esta pessoa que entra no painel e configura o resto. Ela pode criar os outros
              usuários depois, dentro do limite do plano.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Campo label="Nome">
                <input required data-testid="admin-name" className="zz-input" autoComplete="off"
                  value={form.admin_name}
                  onChange={(e) => setForm({ ...form, admin_name: e.target.value })} />
              </Campo>
              <Campo label="E-mail" erro={erroEmail}>
                <input required type="email" data-testid="admin-email" className="zz-input"
                  ref={emailRef} autoComplete="off" placeholder="admin@suaagencia.com.br"
                  value={form.admin_email}
                  onChange={(e) => { setErroEmail(""); setForm({ ...form, admin_email: e.target.value }); }} />
              </Campo>
            </div>

            <div className="mt-4">
              <Campo label="Senha inicial">
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <input required data-testid="admin-password" className="zz-input pr-10"
                      // `new-password` impede o gerenciador do navegador de
                      // oferecer a credencial do próprio super admin neste
                      // campo, que é o que acontecia com o autocomplete solto.
                      type={verSenha ? "text" : "password"} autoComplete="new-password"
                      minLength={SENHA_MIN} value={form.admin_password}
                      onChange={(e) => setForm({ ...form, admin_password: e.target.value })} />
                    <button type="button" onClick={() => setVerSenha(!verSenha)}
                      title={verSenha ? "Esconder" : "Mostrar"}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-3 hover:text-ink p-1">
                      {verSenha ? <EyeSlash size={16} weight="regular" /> : <Eye size={16} weight="regular" />}
                    </button>
                  </div>
                  <button type="button" data-testid="gerar-senha" className="zz-btn zz-btn-secondary"
                    onClick={() => { setForm({ ...form, admin_password: gerarSenha() }); setVerSenha(true); }}>
                    <ArrowsClockwise size={14} weight="regular" /> Gerar
                  </button>
                </div>
                <div className="text-[11px] text-ink-3 mt-1.5">
                  Mínimo de {SENHA_MIN} caracteres. Ninguém é obrigado a trocá-la no primeiro
                  acesso, então é ela que a agência vai usar: prefira a gerada.
                </div>
              </Campo>
            </div>
          </div>
        </div>

        <div className="border-t border-line px-6 py-4 flex justify-end gap-2">
          <button type="button" onClick={onFechar} className="zz-btn zz-btn-secondary">Cancelar</button>
          <button type="submit" data-testid="tenant-submit" className="zz-btn" disabled={criando}>
            {criando ? "Criando…" : "Criar agência"}
          </button>
        </div>
      </form>
    </Moldura>
  );
}

function ResumoPlano({ plano }) {
  if (!plano) {
    return (
      <div className="border border-dashed border-line rounded-lg px-4 py-6 text-center text-sm text-ink-3">
        Escolha um plano para ver o que a agência vai receber.
      </div>
    );
  }
  return (
    <div className="border border-line rounded-lg bg-surface-2 overflow-hidden" data-testid="resumo-plano">
      <div className="flex items-baseline justify-between px-4 py-3 border-b border-line">
        <span className="font-heading">{plano.nome}</span>
        <span className="text-sm font-mono text-ink-2">{formatBRL(plano.preco_mensal)}/mês</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-3 px-4 py-4">
        {limitesDoPlano(plano).map((l) => (
          <div key={l.rotulo}>
            <div className="text-sm font-medium text-ink-3">{l.rotulo}</div>
            <div className={`text-sm font-semibold ${l.alerta ? "text-danger" : ""}`}>{l.texto}</div>
          </div>
        ))}
      </div>
      {plano.status !== "active" && (
        <div className="px-4 pb-4 -mt-1 text-xs text-warning">
          Este plano está inativo. A agência funciona, mas o plano não deveria estar em uso.
        </div>
      )}
    </div>
  );
}

/**
 * O acesso do cliente, uma vez só. Daqui não se volta para o formulário: a
 * empresa já existe, e o que resta é entregar a credencial e abrir o caminho do
 * próximo passo real (conectar o WhatsApp), que é o que ficava esquecido.
 */
function Acesso({ dados, onFechar, onEntrar }) {
  const { tenant } = dados;
  const endereco = `${window.location.origin}/login`;
  const bloco = [
    `Acesso ao painel ${tenant.name}`,
    `Endereço: ${endereco}`,
    `E-mail: ${dados.admin_email}`,
    `Senha: ${dados.admin_password}`,
  ].join("\n");

  return (
    <>
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
        <div className="flex items-start gap-3 rounded-lg border border-warning bg-warning-soft px-4 py-3">
          <Warning size={18} weight="fill" className="text-warning shrink-0 mt-0.5" />
          <div className="text-sm text-ink-2">
            <b className="text-ink">Copie a senha agora.</b> O sistema guarda só o hash dela, então
            esta é a última vez que ela aparece. Se sair sem copiar, o caminho é definir outra em
            Usuários, dentro da agência.
          </div>
        </div>

        <div className="border border-line rounded-lg divide-y divide-line" data-testid="acesso-criado">
          <Linha rotulo="Agência" valor={tenant.name} />
          <Linha rotulo="Plano" valor={tenant.plan
            ? `${tenant.plan.nome} · ${formatBRL(tenant.plan.preco_mensal)}/mês`
            : "sem plano vinculado"} />
          <Linha rotulo="Endereço" valor={endereco} />
          <Linha rotulo="E-mail" valor={dados.admin_email} mono />
          <Linha rotulo="Senha" valor={dados.admin_password} mono destaque />
        </div>

        <button type="button" className="zz-btn zz-btn-secondary w-full justify-center"
          data-testid="copiar-acesso"
          onClick={() => copiar(bloco, "Acesso copiado. Já pode colar para a agência.")}>
          <Copy size={14} weight="regular" /> Copiar acesso completo
        </button>

        <div>
          <Titulo>Próximo passo</Titulo>
          <p className="text-xs text-ink-3 mb-3">
            A agência nasce vazia: sem conexão de WhatsApp ela não recebe mensagem nenhuma, e
            nenhum fluxo automático roda.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Atalho icone={WhatsappLogo} titulo="Conectar o WhatsApp"
              nota="Entra na agência e abre a tela do WhatsApp, para conectar o número agora."
              onClick={() => onEntrar(tenant, "/app/whatsapp")} />
            <Atalho icone={SignIn} titulo="Entrar na agência"
              nota="Modo suporte, no painel da agência, para configurar o resto."
              onClick={() => onEntrar(tenant, "/app")} />
          </div>
        </div>
      </div>

      <div className="border-t border-line px-6 py-4 flex justify-end">
        <button type="button" className="zz-btn" onClick={onFechar} data-testid="acesso-concluir">
          Concluir
        </button>
      </div>
    </>
  );
}

function Linha({ rotulo, valor, mono, destaque }) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <span className="text-sm font-medium text-ink-3 w-24 shrink-0">{rotulo}</span>
      <span className={`text-sm flex-1 break-all ${mono ? "font-mono" : ""} ${destaque ? "font-bold" : ""}`}>
        {valor}
      </span>
      <button type="button" title="Copiar" className="zz-btn-icon shrink-0"
        onClick={() => copiar(valor, `${rotulo} copiado`)}>
        <Copy size={14} weight="regular" />
      </button>
    </div>
  );
}

function Atalho({ icone: Icone, titulo, nota, onClick }) {
  return (
    <button type="button" onClick={onClick}
      className="text-left border border-line rounded-lg px-4 py-3 hover:border-info hover:bg-info-soft transition-colors">
      <div className="flex items-center gap-2 font-semibold text-sm">
        <Icone size={16} weight="regular" /> {titulo}
      </div>
      <div className="text-xs text-ink-3 mt-1">{nota}</div>
    </button>
  );
}

// A moldura do modal. `travado` esconde o X: na entrega do acesso a saída é
// pelo "Concluir", pelo mesmo motivo que o Esc fica desligado ali.
function Moldura({ titulo, icone: Icone, children, onFechar, travado }) {
  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-surface border border-line rounded-2xl w-full max-w-2xl max-h-[92vh] flex flex-col shadow-pop"
        data-testid="nova-empresa-modal">
        <div className="flex items-center justify-between px-6 py-4 border-b border-line">
          <h2 className="font-heading text-xl font-semibold flex items-center gap-2">
            <Icone size={20} weight="regular" /> {titulo}
          </h2>
          {!travado && (
            <button type="button" onClick={onFechar} className="zz-btn-icon" title="Fechar">
              <X size={18} weight="regular" />
            </button>
          )}
        </div>
        {children}
      </div>
    </div>
  );
}

function Campo({ label, erro, children }) {
  return (
    <div>
      <label className="text-sm font-medium text-ink-2 block mb-1.5">{label}</label>
      {children}
      {erro && <div className="text-xs text-danger mt-1.5">{erro}</div>}
    </div>
  );
}

function Titulo({ children }) {
  return (
    <div className="text-sm font-medium text-ink-3 mb-2">{children}</div>
  );
}

function Aviso({ children }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-warning bg-warning-soft px-4 py-3 text-sm text-ink-2">
      <Warning size={18} weight="fill" className="text-warning shrink-0 mt-0.5" />
      <div>{children}</div>
    </div>
  );
}
