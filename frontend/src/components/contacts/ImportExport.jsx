import { useRef, useState } from "react";
import api, { formatApiErrorDetail } from "@/lib/api";
import { toast } from "sonner";
import {
  UploadSimple, DownloadSimple, Warning, CheckCircle, ArrowRight,
} from "@phosphor-icons/react";
import Modal from "@/components/ui/Modal";

const ALVOS = [
  ["", "— não importar —"],
  ["nome", "Nome"],
  ["numero_whatsapp", "WhatsApp"],
  ["email", "E-mail"],
  ["empresa", "Empresa"],
  ["cargo", "Cargo"],
  ["origem", "Origem"],
  ["tags", "Etiquetas"],
  ["responsavel_email", "Responsável (e-mail)"],
];

export default function ImportExport({ filtros, onClose, onImportou }) {
  const [arquivo, setArquivo] = useState(null);
  const [previa, setPrevia] = useState(null);
  const [mapa, setMapa] = useState({});
  const [duplicados, setDuplicados] = useState("ignorar");
  const [ocupado, setOcupado] = useState(false);
  const entradaRef = useRef(null);

  const exportar = async () => {
    setOcupado(true);
    try {
      // Baixado por blob e não por link direto: assim o erro do servidor
      // aparece como mensagem, em vez de o navegador abrir uma aba com JSON.
      const r = await api.get("/contacts/export", { params: filtros, responseType: "blob" });
      const url = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = "clientes.csv";
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Exportação baixada");
    } catch {
      toast.error("Não consegui exportar agora");
    } finally {
      setOcupado(false);
    }
  };

  const enviar = async (file, mapeamento, dryRun) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("dry_run", dryRun ? "true" : "false");
    fd.append("duplicados", duplicados);
    if (mapeamento) fd.append("mapeamento", JSON.stringify(mapeamento));
    const r = await api.post("/contacts/import", fd);
    return r.data;
  };

  const escolher = async (file) => {
    if (!file) return;
    setArquivo(file);
    setOcupado(true);
    try {
      const dados = await enviar(file, null, true);
      setPrevia(dados);
      setMapa(dados.mapeamento || {});
    } catch (ex) {
      setPrevia(null);
      setArquivo(null);
      if (entradaRef.current) entradaRef.current.value = "";
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setOcupado(false);
    }
  };

  const revisar = async (novoMapa) => {
    setMapa(novoMapa);
    setOcupado(true);
    try {
      setPrevia(await enviar(arquivo, novoMapa, true));
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setOcupado(false);
    }
  };

  const confirmar = async () => {
    setOcupado(true);
    try {
      const r = await enviar(arquivo, mapa, false);
      toast.success(
        `${r.criados} criado(s)` +
        (r.atualizados ? `, ${r.atualizados} atualizado(s)` : "") +
        (r.ignorados ? `, ${r.ignorados} já existiam` : "")
      );
      limpar();
      onImportou?.();
    } catch (ex) {
      toast.error(formatApiErrorDetail(ex.response?.data?.detail));
    } finally {
      setOcupado(false);
    }
  };

  const limpar = () => {
    setPrevia(null);
    setArquivo(null);
    if (entradaRef.current) entradaRef.current.value = "";
  };

  const opcoes = [...ALVOS, ...(previa?.campos_disponiveis || [])
    .map((c) => [`campo:${c.id}`, `${c.nome} (personalizado)`])];

  return (
    <Modal titulo="Importar e exportar" largo
      descricao="CSV separado por ponto e vírgula ou vírgula. A primeira linha precisa ser o cabeçalho com o nome das colunas."
      onFechar={onClose} testId="import-export"
      rodape={(
        <button type="button" onClick={exportar} disabled={ocupado}
          className="zz-btn zz-btn-secondary mr-auto disabled:opacity-40" data-testid="exportar-btn">
          <DownloadSimple size={13} weight="regular" /> Exportar {filtros && Object.keys(filtros).length > 0 ? "o filtrado" : "tudo"}
        </button>
      )}>
      {!previa ? (
        <div className="border border-dashed border-line rounded-xl bg-surface-2 p-8 text-center">
          <input ref={entradaRef} type="file" accept=".csv,text/csv" className="hidden"
            data-testid="import-file" onChange={(e) => escolher(e.target.files?.[0])} />
          <UploadSimple size={26} weight="regular" className="mx-auto mb-2 text-ink-4" />
          <button onClick={() => entradaRef.current?.click()} disabled={ocupado}
            className="zz-btn disabled:opacity-40" data-testid="escolher-arquivo">
            {ocupado ? "Lendo arquivo…" : "Escolher arquivo CSV"}
          </button>
          <div className="text-[11px] text-ink-3 mt-2">
            Nada é gravado até você conferir a prévia.
          </div>
        </div>
      ) : (
        <div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 mb-4">
            {[["Linhas no arquivo", previa.linhas, "border-line bg-surface"],
              ["Novos", previa.novos, "border-success bg-success-soft"],
              ["Já existem", previa.duplicados, "border-warning bg-warning-soft"],
              ["Com erro", previa.erros, previa.erros ? "border-danger bg-danger-soft" : "border-line bg-surface"],
            ].map(([r, v, cls]) => (
              <div key={r} className={`border rounded-lg px-3 py-2 ${cls}`}>
                <div className="text-sm font-medium text-ink-2">{r}</div>
                <div className="font-heading text-xl font-semibold">{v}</div>
              </div>
            ))}
          </div>

          <div className="mb-4">
            <div className="text-sm font-medium text-ink-3 mb-2">
              De qual coluna vem cada campo
            </div>
            <div className="border border-line rounded-lg">
              {previa.colunas.map((coluna) => (
                <div key={coluna} className="flex items-center gap-3 px-3 py-1.5 border-b border-line last:border-b-0">
                  <span className="text-sm font-semibold w-52 truncate" title={coluna}>{coluna}</span>
                  <ArrowRight size={12} weight="regular" className="text-ink-4 shrink-0" />
                  <select className="zz-input h-7 py-0 text-xs max-w-[240px]"
                    data-testid={`mapa-${coluna}`}
                    value={mapa[coluna] || ""}
                    onChange={(e) => revisar({ ...mapa, [coluna]: e.target.value })}>
                    {opcoes.map(([v, r]) => <option key={v} value={v}>{r}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </div>

          <div className="mb-4">
            <div className="text-sm font-medium text-ink-3 mb-2">
              Quem já está na base
            </div>
            <div className="flex gap-2">
              {[["ignorar", "Não mexer"], ["atualizar", "Atualizar os dados"]].map(([v, r]) => (
                <button key={v} onClick={() => { setDuplicados(v); }}
                  className={`px-3 py-1.5 text-xs font-medium border rounded-full ${
                    duplicados === v ? "bg-brand text-brand-fg border-brand" : "bg-surface border-line text-ink-2"
                  }`}>
                  {r}
                </button>
              ))}
            </div>
            {duplicados === "atualizar" && (
              <div className="text-[11px] text-ink-3 mt-1.5">
                Só sobrescreve o que vem preenchido na planilha. Etiquetas são somadas, não
                substituídas.
              </div>
            )}
          </div>

          {previa.erros > 0 && (
            <div className="border border-danger bg-danger-soft p-3 mb-4 rounded-lg">
              <div className="flex items-center gap-2 mb-2">
                <Warning size={14} weight="regular" className="text-danger" />
                <span className="text-sm font-bold">
                  {previa.erros} linha(s) não serão importadas
                </span>
              </div>
              <div className="max-h-32 overflow-y-auto space-y-0.5">
                {previa.erros_detalhe.map((e) => (
                  <div key={e.linha} className="text-xs text-ink font-mono">
                    linha {e.linha}: {e.erro}
                  </div>
                ))}
              </div>
              {previa.erros_omitidos > 0 && (
                <div className="text-[11px] text-ink-2 mt-1.5">
                  e outras {previa.erros_omitidos} não listadas aqui.
                </div>
              )}
            </div>
          )}

          {previa.amostra.length > 0 && (
            <div className="mb-4">
              <div className="text-sm font-medium text-ink-3 mb-2">
                Como vão entrar (primeiras linhas)
              </div>
              <div className="border border-line overflow-x-auto rounded-lg">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-line bg-surface-2">
                      {["Nome", "WhatsApp", "E-mail", "Empresa", "Etiquetas"].map((h) => (
                        <th key={h} className="text-left px-3 py-2.5.5 font-medium text-xs text-ink-3">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {previa.amostra.map((a, i) => (
                      <tr key={i} className="border-b border-line last:border-b-0">
                        <td className="px-3 py-2.5.5 font-semibold">{a.nome}</td>
                        <td className="px-3 py-2.5.5 font-mono">{a.numero_whatsapp}</td>
                        <td className="px-3 py-2.5.5">{a.email || "—"}</td>
                        <td className="px-3 py-2.5.5">{a.empresa || "—"}</td>
                        <td className="px-3 py-2.5.5">{(a.tags || []).join(", ") || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="flex gap-2 justify-end items-center">
            <span className="text-xs text-ink-3 mr-auto">{arquivo?.name}</span>
            <button onClick={limpar} className="zz-btn zz-btn-secondary">Escolher outro</button>
            <button onClick={confirmar} disabled={ocupado || (previa.novos === 0 && duplicados === "ignorar")}
              className="zz-btn disabled:opacity-40" data-testid="confirmar-import">
              <CheckCircle size={14} weight="regular" />
              {ocupado ? "Importando…" : `Importar ${
                duplicados === "atualizar"
                  ? previa.novos + previa.duplicados
                  : previa.novos
              } cliente(s)`}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
