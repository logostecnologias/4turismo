/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["class"],
    content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html"
  ],
  theme: {
    extend: {
      // Raio explícito, não derivado de `--radius`: os nomes são usados direto no
      // JSX e um `calc()` encadeado tornava impossível saber, lendo a classe,
      // quantos pixels ela dá. `--radius` continua definido no CSS porque os
      // componentes shadcn em `components/ui/` o referenciam.
      borderRadius: {
        none: '0px',
        sm: '6px',
        DEFAULT: '8px',
        md: '10px',
        lg: '14px',
        xl: '18px',
        '2xl': '24px',
        '3xl': '30px',
        full: '9999px',
      },
      boxShadow: {
        // Sombra difusa em vez do deslocamento duro do desenho brutalista
        // anterior. As cores vêm de variável porque no tema escuro uma sombra
        // preta é invisível — lá o relevo vem de borda mais clara.
        soft: '0 1px 2px var(--sombra-1)',
        card: '0 1px 2px var(--sombra-1), 0 8px 24px -14px var(--sombra-2)',
        pop: '0 2px 6px var(--sombra-1), 0 16px 40px -12px var(--sombra-2)',
        none: 'none',
      },
      colors: {
        // --- Tokens semânticos do 4turismo -----------------------------------
        // Toda cor de tela sai daqui. Cor cravada no JSX (`bg-white`,
        // `text-slate-500`) não tem como ter equivalente no tema escuro, e foi
        // exatamente o que impediu o tema escuro de existir antes.
        canvas: 'var(--bg)',
        surface: {
          DEFAULT: 'var(--surface)',
          2: 'var(--surface-2)',
          3: 'var(--surface-3)',
        },
        line: {
          DEFAULT: 'var(--line)',
          strong: 'var(--line-strong)',
        },
        ink: {
          DEFAULT: 'var(--text)',
          2: 'var(--text-2)',
          3: 'var(--text-3)',
          4: 'var(--text-4)',
        },
        brand: {
          DEFAULT: 'var(--brand)',
          fg: 'var(--brand-fg)',
          soft: 'var(--brand-soft)',
        },
        danger: {
          DEFAULT: 'var(--danger)',
          soft: 'var(--danger-soft)',
          solid: 'var(--danger-solid)',
          fg: 'var(--danger-fg)',
        },
        success: {
          DEFAULT: 'var(--success)',
          soft: 'var(--success-soft)',
          solid: 'var(--success-solid)',
          fg: 'var(--success-fg)',
        },
        warning: {
          DEFAULT: 'var(--warning)',
          soft: 'var(--warning-soft)',
          solid: 'var(--warning-solid)',
          fg: 'var(--warning-fg)',
        },
        info: { DEFAULT: 'var(--info)', soft: 'var(--info-soft)' },

        // --- Tokens shadcn (components/ui/) ---------------------------------
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))'
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))'
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))'
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))'
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))'
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))'
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))'
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        chart: {
          '1': 'hsl(var(--chart-1))',
          '2': 'hsl(var(--chart-2))',
          '3': 'hsl(var(--chart-3))',
          '4': 'hsl(var(--chart-4))',
          '5': 'hsl(var(--chart-5))'
        }
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' }
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' }
        }
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out'
      }
    }
  },
  plugins: [require("tailwindcss-animate")],
};
