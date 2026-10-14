                                            import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { formatApiErrorDetail } from "@/lib/api";
import { ArrowRight } from "@phosphor-icons/react";
import BotaoTema from "@/components/BotaoTema";
import Marca from "@/components/Marca";

export default function LoginPage() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setErr(""); setLoading(true);
    try {
      const u = await login(email, password);
      if (u.role === "super_admin") nav("/admin");
      // Atendente também abre em "Hoje": a agenda do dia e a fila estão lá.
      else nav("/app");
    } catch (ex) {
      setErr(formatApiErrorDetail(ex.response?.data?.detail) || ex.message);
    } finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen grid grid-cols-1 lg:grid-cols-[1.1fr_1fr]">
      {/* Painel da marca: uma frase, a marca e a LGPD, sem lista de
          funcionalidades. O fundo é `--painel-login`: na paleta oficial é o
          preto ameixa, porque a logo foi desenhada sobre fundo escuro; nas
          alternativas é a marca da paleta (guardada neste navegador desde o
          último acesso). As dunas no pé são desenhadas em
          `currentColor`, e por isso acompanham qualquer paleta. */}
      <div className="hidden lg:flex relative overflow-hidden flex-col justify-between p-12 xl:p-16 2xl:p-20 zz-painel-login">
        <svg className="pointer-events-none absolute inset-x-0 bottom-0 w-full h-56" viewBox="0 0 1200 224"
          preserveAspectRatio="none" aria-hidden="true">
          <path d="M0 150 C 180 90, 330 90, 480 140 S 800 200, 960 130 S 1140 80, 1200 110 L1200 224 L0 224 Z"
            fill="currentColor" opacity="0.08" />
          <path d="M0 190 C 220 140, 380 150, 560 185 S 900 215, 1060 170 S 1170 150, 1200 160 L1200 224 L0 224 Z"
            fill="currentColor" opacity="0.10" />
        </svg>

        <Marca tamanho="lg" fundo="escuro" />

        <div className="relative max-w-2xl">
          <h1 className="font-heading text-5xl xl:text-6xl 2xl:text-7xl leading-[1.05] mb-7">
            Seus passeios vendidos pelo WhatsApp, até na alta temporada.
          </h1>
          <p className="text-lg 2xl:text-xl leading-relaxed opacity-85 max-w-xl">
            Atendimento, vagas, orçamentos e lembrete de embarque num só lugar,
            para nenhum turista ficar sem resposta.
          </p>
        </div>

        <div className="relative text-base opacity-75">Dados dos clientes protegidos conforme a LGPD</div>
      </div>

      {/* Formulário */}
      <div className="relative flex items-center justify-center p-6 lg:p-12 bg-surface">
        <div className="absolute top-5 right-5">
          <BotaoTema />
        </div>
        <div className="w-full max-w-md 2xl:max-w-lg">
          <div className="lg:hidden mb-10">
            <Marca tamanho="md" />
          </div>

          <div className="mb-10">
            <h2 className="font-heading text-4xl 2xl:text-5xl">Entrar</h2>
            <p className="text-base text-ink-2 mt-3">Acesse o atendimento e as saídas da sua agência.</p>
          </div>

          <form onSubmit={submit} className="space-y-5" data-testid="login-form">
            <div>
              <label htmlFor="login-email" className="text-sm font-medium text-ink-2 block mb-2">E-mail</label>
              <input
                id="login-email"
                data-testid="login-email"
                type="email" required autoFocus
                autoComplete="username"
                className="zz-input !py-3 !text-base"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="voce@suaagencia.com.br"
              />
            </div>
            <div>
              <label htmlFor="login-senha" className="text-sm font-medium text-ink-2 block mb-2">Senha</label>
              <input
                id="login-senha"
                data-testid="login-password"
                type="password" required
                autoComplete="current-password"
                className="zz-input !py-3 !text-base"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>

            {err && (
              <div data-testid="login-error" className="text-sm text-danger bg-danger-soft border border-danger p-2.5 rounded-lg">
                {err}
              </div>
            )}

            <button
              data-testid="login-submit"
              type="submit" disabled={loading}
              className="zz-btn w-full justify-center py-3.5 text-base"
            >
              {loading ? "Entrando..." : "Entrar"} <ArrowRight size={16} weight="regular" />
            </button>
          </form>

          {/* Nada de credencial de exemplo aqui. Esta é a única tela pública do
              produto: o que aparece nela está na internet, indexável, e ia no
              bundle de produção. O e-mail do super admin é real e continua
              sendo — publicá-lo entrega metade do login de quem pode entrar em
              qualquer empresa, e deixa o resto por conta da força bruta. Havia
              uma linha igual com o usuário comum, tirada em 222679c. */}

          {/* Âncora comum, não Link do React Router: as páginas legais são
              arquivos estáticos servidos pelo Caddy, fora do SPA. Um Link
              tentaria resolvê-las como rota e cairia no fallback do index.html.

              Ficam aqui porque é a única tela pública do produto, e a revisão
              da Meta espera alcançar a política de privacidade a partir dela. */}
          <div className="mt-10 text-sm text-ink-3 flex gap-5">
            <a href="/privacidade.html" className="hover:text-ink">Privacidade</a>
            <a href="/termos.html" className="hover:text-ink">Termos de Uso</a>
          </div>
        </div>
      </div>
    </div>
  );
}
