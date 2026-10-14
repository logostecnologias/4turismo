import { useEffect, useState } from "react"
import { Toaster as Sonner, toast } from "sonner"
import { efetivo, observarSistema } from "@/lib/tema"

const Toaster = ({
  ...props
}) => {
  // O Sonner pinta o proprio cartao a partir de `theme`. Deixado em "light" ele
  // desenharia toast claro no tema escuro, por cima das nossas variaveis.
  const [tema, setTema] = useState(efetivo)
  useEffect(() => observarSistema(setTema), [])
  useEffect(() => {
    const alvo = document.documentElement
    const obs = new MutationObserver(() => setTema(alvo.classList.contains("dark") ? "escuro" : "claro"))
    obs.observe(alvo, { attributes: true, attributeFilter: ["class"] })
    return () => obs.disconnect()
  }, [])

  return (
    <Sonner
      theme={tema === "escuro" ? "dark" : "light"}
      position="bottom-right"
      icons={{}}
      toastOptions={{
        unstyled: false,
        classNames: {
          toast: "zz-toast",
          title: "zz-toast-title",
          description: "zz-toast-desc",
          actionButton: "zz-toast-action",
          cancelButton: "zz-toast-cancel",
          closeButton: "zz-toast-close",
          success: "zz-toast-success",
          error: "zz-toast-error",
          warning: "zz-toast-warning",
          info: "zz-toast-info",
        },
      }}
      {...props} />
  );
}

export { Toaster, toast }
