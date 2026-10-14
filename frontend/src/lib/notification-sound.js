let ctx;

function beeps(freqs, gapSec, volume) {
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    const now = ctx.currentTime;
    freqs.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = now + i * gapSec;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(volume, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.18);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.2);
    });
  } catch {
    // audio unsupported/blocked — fail silently
  }
}

// Fila humana: som mais longo e insistente que o ping de mensagem, pra não
// confundir "chegou mensagem numa conversa minha" com "tem cliente esperando".
export function playQueueAlert() {
  beeps([1046, 784, 1046, 784], 0.16, 0.28);
}

export function playNotificationSound() {
  beeps([880, 1320], 0.12, 0.2);
}
