import { toast } from "sonner";

/**
 * Toast de confirmação (substitui window.confirm). Resolve `true` se o
 * usuário clicar em confirmar, `false` em qualquer outro caso (cancelar,
 * fechar ou deixar expirar).
 */
export function confirmToast(message, { confirmLabel = "Confirmar", cancelLabel = "Cancelar", danger = true } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const settle = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };

    const id = toast(message, {
      duration: 15000,
      classNames: danger ? { actionButton: "zz-toast-action zz-toast-action-danger" } : undefined,
      action: {
        label: confirmLabel,
        onClick: () => settle(true),
      },
      cancel: {
        label: cancelLabel,
        onClick: () => settle(false),
      },
      onDismiss: () => settle(false),
      onAutoClose: () => settle(false),
    });

    void id;
  });
}
