import { useCallback, useRef, useState } from "react";

// Trava de clique repetido para qualquer ação que escreve no servidor.
//
// Nasceu de um bug de verdade: dois cliques em "Salvar profissional" criaram
// dois profissionais. Entre o primeiro clique e a resposta do servidor existe
// uma janela de centenas de milissegundos em que o botão continua clicável, e
// nela cabe o segundo clique inteiro.
//
// **A trava é um `useRef`, não um `useState`.** É a parte que importa. Marcar
// `setSalvando(true)` no começo do handler parece resolver, mas o valor só vale
// depois que o React renderiza de novo: dois cliques disparados no mesmo quadro
// leem `salvando === false` os dois. O `ref` muda no ato, na mesma linha, antes
// de qualquer await, então o segundo clique morre na primeira condição. O
// `useState` continua existindo aqui só para desabilitar o botão e trocar o
// rótulo, que é efeito visual.
//
// Devolve `[executar, ocupado]`. `executar` engole a chamada repetida em vez de
// enfileirá-la: enfileirar criaria o segundo registro alguns segundos depois,
// que é exatamente o que se quer evitar.
//
// Não substitui proteção no servidor. Duas abas abertas, um F5 no meio do envio
// ou um retry de rede passam por fora desta trava. Onde criar duplicata é caro,
// o backend também recusa (é o caso de serviço e profissional na agenda).

export function useAcao(fn) {
  const rodando = useRef(false);
  const [ocupado, setOcupado] = useState(false);

  const executar = useCallback(
    async (...args) => {
      if (rodando.current) return undefined;
      rodando.current = true;
      setOcupado(true);
      try {
        return await fn(...args);
      } finally {
        // Libera mesmo se `fn` levantar: senão um erro deixaria o botão morto
        // até a tela ser recarregada.
        rodando.current = false;
        setOcupado(false);
      }
    },
    [fn],
  );

  return [executar, ocupado];
}

export default useAcao;
