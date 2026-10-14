import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/index.css";
import { aplicar, aplicarPaleta } from "@/lib/tema";
import App from "@/App";

// Antes de montar o React: num useEffect o primeiro quadro ja teria sido
// pintado claro, e todo carregamento no tema escuro daria um flash branco.
// A paleta vai junto pelo mesmo motivo: sem ela o primeiro quadro sairia na
// padrao e trocaria de cor quando o `/auth/me` respondesse.
aplicar();
aplicarPaleta();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
    },
  },
});

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
